"""
api_server.py
Flask REST API that powers the job search dashboard.
"""

import os
import sys
import json
import logging
from datetime import datetime
from flask import Flask, jsonify, request, send_from_directory
try:
    from flask_cors import CORS
    _has_cors = True
except ImportError:
    _has_cors = False

# Add scripts dir to path
sys.path.insert(0, os.path.dirname(__file__))
import database as db

logger = logging.getLogger(__name__)

CUSTOM_ROLES_FILE = os.path.join(os.path.dirname(__file__), "../data/custom_roles.json")
SEARCH_PROFILES_FILE = os.path.join(os.path.dirname(__file__), "../data/search_profiles.json")

# Holds the fully-loaded config (with .env credentials) passed in from main.py at startup.
_live_config: dict = {}

app = Flask(__name__, static_folder="../dashboard")
if _has_cors:
    CORS(app)
else:
    @app.after_request
    def add_cors(response):
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
        response.headers["Access-Control-Allow-Methods"] = "GET,POST,PATCH,DELETE,OPTIONS"
        return response


# ─── Job Endpoints ─────────────────────────────────────────────────────────────

@app.route("/api/jobs", methods=["GET"])
def get_jobs():
    """Get paginated, filtered job list."""
    min_score = int(request.args.get("min_score", 0))
    priority = request.args.get("priority")
    source = request.args.get("source")
    company = request.args.get("company")
    status = request.args.get("status")
    days_back = int(request.args.get("days_back", 30))
    limit = min(int(request.args.get("limit", 50)), 200)
    offset = int(request.args.get("offset", 0))
    sort_by = request.args.get("sort_by", "match_score")

    jobs = db.get_jobs(
        min_score=min_score,
        priority=priority,
        source=source,
        company=company,
        status=status,
        days_back=days_back,
        limit=limit,
        offset=offset,
        sort_by=sort_by,
    )

    # Sanitize match_data for response
    for job in jobs:
        if isinstance(job.get("match_data"), str):
            try:
                job["match_data"] = json.loads(job["match_data"])
            except Exception:
                job["match_data"] = {}

    return jsonify({"jobs": jobs, "count": len(jobs), "offset": offset})


@app.route("/api/jobs/<job_id>", methods=["GET"])
def get_job(job_id):
    """Get a single job by ID."""
    job = db.get_job_by_id(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(job)


@app.route("/api/jobs/<job_id>/status", methods=["PATCH"])
def update_job(job_id):
    """Update job application status."""
    data = request.get_json()
    db.update_job_status(
        job_id=job_id,
        status=data.get("status", "new"),
        applied=data.get("applied", False),
        notes=data.get("notes", ""),
    )
    return jsonify({"success": True})


@app.route("/api/jobs/<job_id>/undismiss", methods=["POST"])
def undismiss_job(job_id):
    """Restore a dismissed job back to 'new' status."""
    db.undismiss_job(job_id)
    return jsonify({"success": True})


@app.route("/api/jobs/dismissed", methods=["GET"])
def get_dismissed():
    """Return all dismissed (skipped) jobs."""
    jobs = db.get_dismissed_jobs()
    for job in jobs:
        if isinstance(job.get("match_data"), str):
            try:
                job["match_data"] = json.loads(job["match_data"])
            except Exception:
                job["match_data"] = {}
    return jsonify({"jobs": jobs, "count": len(jobs)})


# ─── Stats Endpoints ────────────────────────────────────────────────────────────

@app.route("/api/stats", methods=["GET"])
def get_stats():
    """Get overall statistics."""
    return jsonify(db.get_stats())


@app.route("/api/history", methods=["GET"])
def get_history():
    """Get search run history."""
    limit = int(request.args.get("limit", 10))
    return jsonify(db.get_search_history(limit=limit))


# ─── Settings Endpoint (location, etc.) ─────────────────────────────────────────

@app.route("/api/settings", methods=["GET"])
def get_settings():
    """Return editable search settings."""
    search = _live_config.get("search", {}) if _live_config else {}
    boards_cfg = _live_config.get("job_boards", {}) if _live_config else {}
    boards = {k: v.get("enabled", True) for k, v in boards_cfg.items()}
    # Support both legacy single location and new locations list
    locs = search.get("locations") or [search.get("location", "Boston, MA")]
    default_industries = ["AI / Machine Learning", "Cloud Computing", "Enterprise Tech"]
    industries = search.get("high_priority_industries", default_industries)
    companies_cfg = _live_config.get("companies", []) if _live_config else []
    companies = [{"name": c.get("name", ""), "careers_url": c.get("careers_url", ""),
                  "enabled": c.get("enabled", True)} for c in companies_cfg]
    return jsonify({
        "locations": locs,
        "remote_ok": search.get("remote_ok", True),
        "recent_days": search.get("recent_days", 30),
        "max_jobs_per_source": search.get("max_jobs_per_source", 40),
        "max_total_jobs": search.get("max_total_jobs", 400),
        "min_match_score": search.get("min_match_score", 40),
        "job_boards": boards,
        "high_priority_industries": industries,
        "companies": companies,
    })


@app.route("/api/settings", methods=["POST"])
def save_settings():
    """Update search settings at runtime (persisted to config.yaml)."""
    data = request.get_json() or {}
    remote_ok = bool(data.get("remote_ok", True))
    recent_days = max(1, int(data.get("recent_days", 30)))
    max_jobs_per_source = max(5, int(data.get("max_jobs_per_source", 40)))
    max_total_jobs = max(10, int(data.get("max_total_jobs", 400)))
    min_match_score = max(0, min(100, int(data.get("min_match_score", 40))))

    # Locations: accept list; filter blank entries
    raw_locs = data.get("locations", [])
    locations = [str(l).strip() for l in raw_locs if str(l).strip()]
    if not locations:
        locations = _live_config.get("search", {}).get("locations") or ["Boston, MA"]

    # Job board toggles: dict of {board_name: bool}
    board_updates = data.get("job_boards", {})

    # High priority industries: list of industry strings
    raw_industries = data.get("high_priority_industries")
    if raw_industries is not None:
        high_priority_industries = [str(i).strip() for i in raw_industries if str(i).strip()]
    else:
        high_priority_industries = _live_config.get("search", {}).get("high_priority_industries", []) if _live_config else []

    # Companies: full replacement list [{name, careers_url, enabled, search_params}]
    raw_companies = data.get("companies")
    if raw_companies is not None:
        new_companies = []
        for c in raw_companies:
            name = str(c.get("name", "")).strip()
            url = str(c.get("careers_url", "")).strip()
            if name and url:
                new_companies.append({
                    "name": name, "careers_url": url,
                    "enabled": bool(c.get("enabled", True)),
                    "search_params": str(c.get("search_params", ""))
                })
    else:
        new_companies = None  # no update requested

    if _live_config:
        s = _live_config.setdefault("search", {})
        s["locations"] = locations
        s["remote_ok"] = remote_ok
        s["recent_days"] = recent_days
        s["max_jobs_per_source"] = max_jobs_per_source
        s["max_total_jobs"] = max_total_jobs
        s["min_match_score"] = min_match_score
        s["high_priority_industries"] = high_priority_industries
        for board, enabled in board_updates.items():
            if board in _live_config.get("job_boards", {}):
                _live_config["job_boards"][board]["enabled"] = bool(enabled)
        if new_companies is not None:
            _live_config["companies"] = new_companies

    # Persist to config.yaml
    try:
        import yaml
        config_path = os.path.join(os.path.dirname(__file__), "../config/config.yaml")
        with open(config_path) as f:
            disk_config = yaml.safe_load(f)
        s = disk_config.setdefault("search", {})
        s["locations"] = locations
        s["remote_ok"] = remote_ok
        s["recent_days"] = recent_days
        s["max_jobs_per_source"] = max_jobs_per_source
        s["max_total_jobs"] = max_total_jobs
        s["min_match_score"] = min_match_score
        s["high_priority_industries"] = high_priority_industries
        for board, enabled in board_updates.items():
            if board in disk_config.get("job_boards", {}):
                disk_config["job_boards"][board]["enabled"] = bool(enabled)
        if new_companies is not None:
            disk_config["companies"] = new_companies
        with open(config_path, "w") as f:
            yaml.dump(disk_config, f, default_flow_style=False, allow_unicode=True)
    except Exception as e:
        logger.warning(f"Could not persist settings to config.yaml: {e}")

    return jsonify({"success": True, "locations": locations, "remote_ok": remote_ok,
                    "recent_days": recent_days, "max_jobs_per_source": max_jobs_per_source,
                    "max_total_jobs": max_total_jobs, "min_match_score": min_match_score,
                    "high_priority_industries": high_priority_industries})


# ─── Resume Profiles Endpoint ──────────────────────────────────────────────────

@app.route("/api/profiles", methods=["GET"])
def get_profiles():
    """Return resume analysis and search profiles from latest search_profiles.json."""
    try:
        if os.path.exists(SEARCH_PROFILES_FILE):
            with open(SEARCH_PROFILES_FILE) as f:
                return jsonify(json.load(f))
    except Exception as e:
        logger.warning(f"Could not load search profiles: {e}")
    return jsonify({}), 404


# ─── Custom Roles Endpoints ─────────────────────────────────────────────────────

@app.route("/api/custom-roles", methods=["GET"])
def get_custom_roles():
    """Return the saved list of custom search roles."""
    try:
        if os.path.exists(CUSTOM_ROLES_FILE):
            with open(CUSTOM_ROLES_FILE) as f:
                return jsonify({"roles": json.load(f)})
    except Exception:
        pass
    return jsonify({"roles": []})


@app.route("/api/custom-roles", methods=["POST"])
def set_custom_roles():
    """Save (replace) the list of custom search roles."""
    data = request.get_json() or {}
    roles = [r.strip() for r in data.get("roles", []) if str(r).strip()]
    os.makedirs(os.path.dirname(os.path.abspath(CUSTOM_ROLES_FILE)), exist_ok=True)
    with open(CUSTOM_ROLES_FILE, "w") as f:
        json.dump(roles, f, indent=2)
    return jsonify({"success": True, "roles": roles})


@app.route("/api/reload-profile", methods=["POST"])
def reload_profile():
    """Re-run resume analysis and regenerate search_profiles.json without a full job search."""
    import threading

    def run_reload():
        try:
            config = _live_config or _load_config_fresh()
            sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
            from llm_client import AzureAIFoundryClient
            from resume_reader import load_resume, save_search_profiles
            llm = AzureAIFoundryClient(config)
            resume_data = load_resume(config, llm)
            save_search_profiles(resume_data)
            logger.info("Profile reload complete")
        except Exception as e:
            logger.error(f"Profile reload failed: {e}", exc_info=True)

    threading.Thread(target=run_reload, daemon=True).start()
    return jsonify({"success": True, "message": "Profile reload started — refresh in ~30 seconds"})


# ─── Search Trigger ─────────────────────────────────────────────────────────────

@app.route("/api/search/run", methods=["POST"])
def trigger_search():
    """Manually trigger a job search run."""
    import threading

    def run_search_background():
        try:
            # Use the live config stored at server startup — it already has .env credentials.
            # Fall back to loading config fresh if for some reason it wasn't stored.
            config = _live_config or _load_config_fresh()

            sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
            import main as main_module
            main_module.config_global = config
            main_module.run_job_search(config)
        except Exception as e:
            logger.error(f"Background search failed: {e}", exc_info=True)

    thread = threading.Thread(target=run_search_background, daemon=True)
    thread.start()
    return jsonify({"success": True, "message": "Job search started in background"})


def _load_config_fresh() -> dict:
    """Emergency fallback: load config from disk (may lack .env credentials)."""
    import yaml
    from pathlib import Path
    from dotenv import load_dotenv
    env_path = Path(__file__).parent.parent / ".env"
    load_dotenv(dotenv_path=env_path, override=False)
    config_path = os.path.join(os.path.dirname(__file__), "../config/config.yaml")
    with open(config_path) as f:
        config = yaml.safe_load(f)
    azure = config.setdefault("azure", {})
    for env_key, cfg_key in [
        ("AZURE_ENDPOINT", "endpoint"), ("AZURE_API_KEY", "api_key"),
        ("AZURE_MODEL_NAME", "model_name"), ("AZURE_API_VERSION", "api_version")
    ]:
        val = os.getenv(env_key)
        if val:
            azure[cfg_key] = val
    return config


# ─── Cover Letter ───────────────────────────────────────────────────────────────

@app.route("/api/jobs/<job_id>/cover-letter", methods=["GET"])
def get_cover_letter(job_id):
    """Generate a cover letter outline for a job."""
    job = db.get_job_by_id(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404

    try:
        import yaml

        config_path = os.path.join(os.path.dirname(__file__), "../config/config.yaml")
        with open(config_path) as f:
            config = yaml.safe_load(f)

        from llm_client import AzureAIFoundryClient
        from resume_reader import load_resume

        llm = AzureAIFoundryClient(config)
        resume_data = load_resume(config, llm)
        outline = llm.generate_cover_letter_outline(resume_data, job)
        return jsonify({"outline": outline})
    except Exception as e:
        logger.error(f"Cover letter generation failed: {e}")
        return jsonify({"error": str(e)}), 500


# ─── Static Dashboard ───────────────────────────────────────────────────────────

@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def serve_dashboard(path):
    """Serve the React-like dashboard."""
    dashboard_dir = os.path.join(os.path.dirname(__file__), "../dashboard")
    if path and os.path.exists(os.path.join(dashboard_dir, path)):
        return send_from_directory(dashboard_dir, path)
    return send_from_directory(dashboard_dir, "index.html")


def start_server(host: str = "0.0.0.0", port: int = 5051, debug: bool = False, config: dict = None):
    """Start the Flask development server. Accepts the live config so background searches inherit credentials."""
    global _live_config
    if config:
        _live_config = config
    elif not _live_config:
        # Running standalone (e.g. python scripts/api_server.py) — load config now
        try:
            _live_config = _load_config_fresh()
        except Exception as e:
            logger.warning(f"Could not load config: {e} — some features may be limited")
    logger.info(f"Starting dashboard server at http://localhost:{port}")
    app.run(host=host, port=port, debug=debug, use_reloader=False)


if __name__ == "__main__":
    import argparse
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description="JobRadar Dashboard Server")
    parser.add_argument("--port", type=int, default=None, help="Port to serve on (default: from config or 5051)")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")
    args = parser.parse_args()

    # Load config to get port setting
    try:
        cfg = _load_config_fresh()
        _live_config = cfg
    except Exception as e:
        logger.warning(f"Could not load config: {e}")
        cfg = {}

    port = args.port or cfg.get("output", {}).get("dashboard_port", 5051)
    db.init_db()
    start_server(host=args.host, port=port, debug=False)
