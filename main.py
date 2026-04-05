"""
main.py
Main entry point for the Job Search Automation tool.
Handles daily scheduling, orchestrates search + scoring, launches dashboard.
"""

import os
import sys
import time
import yaml
import json
import logging
import argparse
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

# Add scripts to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "scripts"))

import database as db
from resume_reader import load_resume, save_search_profiles
from job_scraper import search_all_sources, fetch_job_description
from job_matcher import score_jobs_batch, rank_jobs, generate_daily_summary

CUSTOM_ROLES_FILE = os.path.join(os.path.dirname(__file__), "data", "custom_roles.json")

logger = logging.getLogger(__name__)


def setup_logging(config: dict):
    """Configure logging."""
    log_config = config.get("logging", {})
    level = getattr(logging, log_config.get("level", "INFO").upper(), logging.INFO)
    log_file = log_config.get("log_file", "./logs/job_search.log")

    # Resolve relative log paths relative to the app root, not CWD
    if not os.path.isabs(log_file):
        log_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), log_file)

    os.makedirs(os.path.dirname(log_file), exist_ok=True)

    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
    )


def load_config(config_path: str = "config/config.yaml") -> dict:
    """Load YAML configuration, then override Azure credentials from .env."""
    # Load .env from the project root (silently ignored if absent)
    env_path = Path(__file__).parent / ".env"
    load_dotenv(dotenv_path=env_path)

    if not os.path.exists(config_path):
        # Try relative to script location
        config_path = os.path.join(os.path.dirname(__file__), config_path)

    with open(config_path, "r") as f:
        config = yaml.safe_load(f)

    # Override azure credentials from environment variables
    azure = config.setdefault("azure", {})
    if os.getenv("AZURE_ENDPOINT"):
        azure["endpoint"] = os.environ["AZURE_ENDPOINT"]
    if os.getenv("AZURE_API_KEY"):
        azure["api_key"] = os.environ["AZURE_API_KEY"]
    if os.getenv("AZURE_MODEL_NAME"):
        azure["model_name"] = os.environ["AZURE_MODEL_NAME"]
    if os.getenv("AZURE_API_VERSION"):
        azure["api_version"] = os.environ["AZURE_API_VERSION"]

    logger.info(f"Config loaded from {config_path}")

    # Store the directory containing the config file so relative paths
    # (e.g. ./resumes) can be resolved correctly regardless of CWD.
    config["_config_dir"] = os.path.abspath(os.path.dirname(config_path))

    return config


def init_llm_client(config: dict):
    """Initialize Azure AI Foundry LLM client."""
    try:
        from llm_client import AzureAIFoundryClient
        client = AzureAIFoundryClient(config)
        return client
    except Exception as e:
        logger.warning(f"LLM client initialization failed: {e}")
        logger.warning("Continuing without LLM (keyword matching only)")
        return None


def run_job_search(config: dict, extra_roles: list = None) -> dict:
    """
    Main job search pipeline:
    1. Load resume
    2. Search all sources
    3. Fetch descriptions for company-page jobs
    4. Score & rank with LLM
    5. Save to database
    6. Return summary
    """
    start_time = time.time()
    run_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    logger.info(f"{'='*60}")
    logger.info(f"JOB SEARCH RUN: {run_date}")
    logger.info(f"{'='*60}")

    # ── Step 1: Load Resume ────────────────────────────────────────
    llm_client = init_llm_client(config)
    logger.info("Loading resume...")
    resume_data = load_resume(config, llm_client)

    if resume_data.get("name"):
        logger.info(f"Resume loaded for: {resume_data['name']}")
        logger.info(f"Target roles: {resume_data.get('target_roles', [])}")
        logger.info(f"Skills found: {len(resume_data.get('skills', {}).get('technical', []) if isinstance(resume_data.get('skills'), dict) else resume_data.get('skills', []))}")

    # ── Step 1b: Save Search Profiles ──────────────────────────────
    profiles_path = save_search_profiles(resume_data)
    job_profiles = resume_data.get("job_profiles", {})
    primary  = job_profiles.get("primary_profiles", [])
    adjacent = job_profiles.get("adjacent_profiles", [])
    telecom  = job_profiles.get("telecom_profiles", [])
    logger.info(f"Search profiles document saved: {profiles_path}")
    if primary or adjacent or telecom:
        logger.info(f"  [HIGH PRIORITY] Non-telecom primary profiles ({len(primary)}):")
        for p in primary:
            logger.info(f"    [{p.get('fit_score', '?')}] {p.get('title')} │ {p.get('industry','')} → {p.get('search_titles', [])}")
        logger.info(f"  [MEDIUM PRIORITY] Adjacent pivot profiles ({len(adjacent)}):")
        for p in adjacent:
            logger.info(f"    [{p.get('fit_score', '?')}] {p.get('title')} │ {p.get('industry','')} → gaps: {p.get('skill_gaps', [])}")
        logger.info(f"  [LOW PRIORITY] Telecom fallback profiles ({len(telecom)}):")
        for p in telecom:
            logger.info(f"    [{p.get('fit_score', '?')}] {p.get('title')} → {p.get('search_titles', [])}")
    else:
        logger.info("  No LLM-identified profiles (LLM unavailable or failed); using local fallback.")

    # ── Step 1c: Inject Custom Roles (PRIMARY — always searched first) ─────────
    custom_roles = []
    try:
        if os.path.exists(CUSTOM_ROLES_FILE):
            with open(CUSTOM_ROLES_FILE) as _f:
                custom_roles = json.load(_f)
    except Exception:
        pass
    if extra_roles:
        custom_roles = list(dict.fromkeys(list(extra_roles) + custom_roles))
    if custom_roles:
        logger.info(f"Custom roles ({len(custom_roles)}): {custom_roles}")
        _jp = resume_data.setdefault("job_profiles", {})
        _primary = _jp.setdefault("primary_profiles", [])
        # Remove any stale custom profiles from a previous run
        _primary[:] = [p for p in _primary if not p.get("is_custom")]
        _seniority_markers = {"senior", "principal", "lead", "staff", "director", "head", "vp", "chief"}
        for _role in reversed(custom_roles):
            _lc = _role.lower()
            # Normalise PM abbreviation (e.g. "AI Infrastructure PM" → "AI Infrastructure Product Manager")
            _base = _role.strip()
            if _lc.endswith(" pm"):
                _base = _role.rsplit(" ", 1)[0] + " Product Manager"
            elif _lc == "pm":
                _base = "Product Manager"
            elif " pm " in _lc:
                _base = _role.replace(" PM ", " Product Manager ").replace(" pm ", " Product Manager ")
            _base_lc = _base.lower()
            _variations = [_base]
            # Keep original string if normalisation changed it (e.g. "AI Infrastructure PM")
            if _base != _role and _role not in _variations:
                _variations.insert(1, _role)
            # Add seniority variants only when no explicit seniority word already present
            _has_seniority = any(w in _base_lc.split() for w in _seniority_markers)
            if not _has_seniority:
                _variations.append(f"Senior {_base}")
                _variations.append(f"Principal {_base}")
            # For any PM role, always add Group PM and Director of PM as common corporate titles
            if "product manager" in _base_lc:
                _variations.append("Group Product Manager")
                _variations.append("Director of Product Management")
            _variations = list(dict.fromkeys(_variations))[:6]
            # Keywords: meaningful words from the normalised title (>3 chars)
            _keywords = [w.strip("()-") for w in _base_lc.split() if len(w.strip("()-")) > 3]
            _primary.insert(0, {
                "title": _role,
                "search_titles": _variations,
                "keywords": _keywords,
                "industry": "Custom",
                "fit_score": 99,
                "is_custom": True,
            })
        resume_data["target_roles"] = list(dict.fromkeys(
            custom_roles + resume_data.get("target_roles", [])
        ))
        # Store custom profile count so scraper can prioritise them
        _jp["_custom_count"] = len(custom_roles)

    # ── Step 2: Scrape Jobs ─────────────────────────────────────────
    logger.info("Searching for jobs...")
    raw_jobs = search_all_sources(config, resume_data)
    logger.info(f"Found {len(raw_jobs)} unique job postings")

    # ── Step 3: Enrich with Full Descriptions ─────────────────────
    # Only fetch descriptions for company-direct listings (better signal)
    company_jobs = [j for j in raw_jobs if j.get("source") not in ("Indeed", "LinkedIn", "Built In")]
    board_jobs = [j for j in raw_jobs if j.get("source") in ("Indeed", "LinkedIn", "Built In")]

    enriched = 0
    for job in company_jobs[:30]:  # Limit to avoid being too slow
        if not job.get("description") or len(job.get("description", "")) < 200:
            desc = fetch_job_description(job.get("url", ""))
            if desc:
                job["description"] = desc
                enriched += 1
        time.sleep(0.5)

    if enriched:
        logger.info(f"Enriched {enriched} jobs with full descriptions")

    all_jobs = company_jobs + board_jobs

    # ── Step 4: Score & Rank ─────────────────────────────────────
    min_score = config.get("search", {}).get("min_match_score", 40)
    logger.info(f"Scoring {len(all_jobs)} jobs (min score: {min_score})...")

    scored_jobs = score_jobs_batch(
        all_jobs,
        resume_data,
        llm_client,
        min_score=min_score,
        max_workers=3,
    )

    ranked_jobs = rank_jobs(scored_jobs)

    # ── Step 5: Save to Database ───────────────────────────────────
    new_count, updated_count = db.upsert_jobs(ranked_jobs)
    logger.info(f"Database: {new_count} new jobs, {updated_count} updated")

    # Cleanup old jobs
    keep_days = config.get("output", {}).get("keep_days", 30)
    db.cleanup_old_jobs(keep_days)

    # ── Step 6: Generate Summary ──────────────────────────────────
    duration = time.time() - start_time
    summary = generate_daily_summary(ranked_jobs, resume_data)
    summary["duration_seconds"] = round(duration, 1)
    summary["new_jobs"] = new_count

    db.record_search_run(summary, duration)

    # ── Step 7: Save JSON Results ──────────────────────────────────
    _app_root = os.path.dirname(os.path.abspath(__file__))
    results_folder = config.get("output", {}).get("results_folder", "./data/results")
    if not os.path.isabs(results_folder):
        results_folder = os.path.join(_app_root, results_folder)
    os.makedirs(results_folder, exist_ok=True)

    results_file = os.path.join(
        results_folder,
        f"results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    )
    with open(results_file, "w") as f:
        json.dump({
            "summary": summary,
            "jobs": ranked_jobs[:100],
        }, f, indent=2, default=str)

    # ── Print Results Summary ──────────────────────────────────────
    print_summary(summary, ranked_jobs[:10])

    return summary


def print_summary(summary: dict, top_jobs: list[dict]):
    """Pretty-print results to console."""
    try:
        from rich.console import Console
        from rich.table import Table
        from rich.panel import Panel
        from rich import box

        console = Console()

        console.print(Panel(
            f"[bold green]✓ Job Search Complete[/bold green]\n"
            f"[dim]{summary['run_date']}[/dim]",
            expand=False
        ))

        console.print(f"\n[bold]📊 Summary:[/bold]")
        console.print(f"  Total jobs found: [cyan]{summary['total_jobs_found']}[/cyan]")
        console.print(f"  🔴 High priority:  [red bold]{summary['high_priority_count']}[/red bold]")
        console.print(f"  🟡 Medium priority: [yellow]{summary['medium_priority_count']}[/yellow]")
        console.print(f"  🟢 Low priority:   [green]{summary['low_priority_count']}[/green]")
        console.print(f"  ⏱  Duration: {summary.get('duration_seconds', 0):.1f}s")

        # Profile category breakdown
        primary_profiles  = summary.get("primary_profiles", [])
        adjacent_profiles = summary.get("adjacent_profiles", [])
        telecom_profiles  = summary.get("telecom_profiles", [])
        if primary_profiles or adjacent_profiles or telecom_profiles:
            console.print(f"\n[bold]\U0001f3af Job Profiles:[/bold]")
            if primary_profiles:
                console.print(f"  [green]\u2b24 HIGH — Non-Telecom Primary ({summary.get('primary_profile_jobs', 0)} jobs):[/green]")
                for p in primary_profiles:
                    console.print(f"    • {p}")
            if adjacent_profiles:
                console.print(f"  [yellow]\u2b24 MEDIUM — Adjacent Pivots ({summary.get('adjacent_profile_jobs', 0)} jobs):[/yellow]")
                for p in adjacent_profiles:
                    console.print(f"    • {p}")
            if telecom_profiles:
                console.print(f"  [dim]\u2b24 LOW — Telecom Fallback ({summary.get('telecom_profile_jobs', 0)} jobs):[/dim]")
                for p in telecom_profiles:
                    console.print(f"    • {p}")

        if top_jobs:
            table = Table(title="Top Matches", box=box.ROUNDED)
            table.add_column("Score", style="cyan", width=7)
            table.add_column("Priority", width=8)
            table.add_column("Profile", width=10)
            table.add_column("Title", style="white", width=32)
            table.add_column("Company", style="yellow", width=20)
            table.add_column("Location", width=18)

            priority_colors = {"high": "red", "medium": "yellow", "low": "green"}
            profile_colors  = {"primary": "green", "adjacent": "yellow", "other": "dim"}

            for job in top_jobs:
                priority = job.get("apply_priority", "low")
                p_color  = priority_colors.get(priority, "white")
                category = job.get("profile_category", "other")
                c_color  = profile_colors.get(category, "dim")
                table.add_row(
                    str(job.get("match_score", 0)),
                    f"[{p_color}]{priority}[/{p_color}]",
                    f"[{c_color}]{category}[/{c_color}]",
                    job.get("title", "")[:31],
                    job.get("company", "")[:19],
                    job.get("location", "")[:17],
                )

            console.print(table)

        console.print(f"\n[dim]View full results at http://localhost:"
                      f"{config_global.get('output', {}).get('dashboard_port', 5051) if config_global else 5051}[/dim]")

    except ImportError:
        # Fallback without rich
        print(f"\n{'='*50}")
        print(f"Job Search Complete: {summary['run_date']}")
        print(f"Total: {summary['total_jobs_found']} | High: {summary['high_priority_count']} | Medium: {summary['medium_priority_count']}")
        print("\nTop Matches:")
        for job in top_jobs[:5]:
            print(f"  [{job.get('match_score',0)}%] {job.get('title')} @ {job.get('company')}")
        port = config_global.get('output', {}).get('dashboard_port', 5051) if config_global else 5051
        print(f"View results at http://localhost:{port}")
        print('='*50)


config_global = None  # Store config for print_summary access


def setup_scheduler(config: dict):
    """Set up APScheduler for daily runs."""
    try:
        from apscheduler.schedulers.blocking import BlockingScheduler
        from apscheduler.triggers.cron import CronTrigger
        import pytz

        sched_config = config.get("scheduler", {})
        run_time = sched_config.get("run_time", "08:00")
        timezone = sched_config.get("timezone", "America/New_York")

        hour, minute = map(int, run_time.split(":"))

        scheduler = BlockingScheduler(timezone=timezone)
        scheduler.add_job(
            func=run_job_search,
            args=[config],
            trigger=CronTrigger(hour=hour, minute=minute, timezone=timezone),
            id="daily_job_search",
            name="Daily Job Search",
            replace_existing=True,
        )

        logger.info(f"Scheduler configured: runs daily at {run_time} {timezone}")
        return scheduler

    except ImportError:
        logger.warning("APScheduler not installed. Run: pip install apscheduler")
        return None


def start_dashboard(config: dict):
    """Launch the web dashboard in a background thread."""
    import threading
    import socket
    from api_server import start_server

    port = config.get("output", {}).get("dashboard_port", 5051)

    # Check if port is already in use
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if s.connect_ex(("127.0.0.1", port)) == 0:
            logger.warning(f"Port {port} is already in use — dashboard may already be running at http://localhost:{port}")
            logger.info(f"Open http://localhost:{port} in your browser")
            return None

    thread = threading.Thread(
        target=start_server,
        kwargs={"host": "0.0.0.0", "port": port, "debug": False, "config": config},
        daemon=True,
    )
    thread.start()
    logger.info(f"Dashboard started at http://localhost:{port}")
    return thread


def main():
    global config_global
    parser = argparse.ArgumentParser(
        description="Job Search Automation Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Commands:
  (no args)       Run once and start scheduled daily runs + dashboard
  --run-once      Run job search once then exit
  --dashboard     Start dashboard server only (no search)
  --schedule      Start scheduler only (no immediate search)
  --config PATH   Path to config.yaml (default: config/config.yaml)
        """
    )
    parser.add_argument("--run-once", action="store_true", help="Run search once and exit")
    parser.add_argument("--dashboard", action="store_true", help="Start dashboard only")
    parser.add_argument("--schedule", action="store_true", help="Start scheduler (no initial run)")
    parser.add_argument("--config", default="config/config.yaml", help="Config file path")
    parser.add_argument("--roles", nargs="+", metavar="ROLE",
                        help="Extra job roles to search for, e.g. --roles 'AI Product Manager' 'Business Development'")
    args = parser.parse_args()

    # Load config
    config = load_config(args.config)
    config_global = config

    # Setup logging
    setup_logging(config)

    # Initialize database
    db.init_db()

    # ── Dashboard only ─────────────────────────────────────────────
    if args.dashboard:
        logger.info("Starting dashboard server only...")
        start_dashboard(config)
        logger.info("Dashboard running. Press Ctrl+C to stop.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        return

    # ── Run once ───────────────────────────────────────────────────
    if args.run_once:
        logger.info("Running job search once...")
        run_job_search(config, extra_roles=args.roles)
        return

    # ── Default: Run now + schedule + dashboard ───────────────────
    logger.info("Job Search Automation starting...")
    _locs = config.get('search', {}).get('locations') or [config.get('search', {}).get('location', 'Not set')]
    logger.info(f"  Search locations: {', '.join(_locs)}")
    logger.info(f"  Companies in list: {len([c for c in config.get('companies', []) if c.get('enabled')])}")
    logger.info(f"  Daily run time: {config.get('scheduler', {}).get('run_time', '08:00')}")

    # Start dashboard
    start_dashboard(config)

    # Run immediately unless --schedule flag
    if not args.schedule:
        logger.info("Running initial job search...")
        run_job_search(config, extra_roles=args.roles)

    # Start scheduler
    sched_enabled = config.get("scheduler", {}).get("enabled", True)
    if sched_enabled:
        scheduler = setup_scheduler(config)
        if scheduler:
            logger.info("Starting daily scheduler (press Ctrl+C to stop)...")
            try:
                scheduler.start()
            except KeyboardInterrupt:
                logger.info("Scheduler stopped by user.")
                scheduler.shutdown()
    else:
        logger.info("Scheduler disabled. Dashboard running. Press Ctrl+C to stop.")
        try:
            while True:
                time.sleep(60)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
