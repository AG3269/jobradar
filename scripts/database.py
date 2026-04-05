"""
database.py
SQLite persistence layer for job search results and history.
"""

import sqlite3
import json
import logging
import os
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# Resolve DB path relative to this file so it works regardless of CWD
DB_PATH = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "../data/job_search.db"))


def get_connection(db_path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db(db_path: str = DB_PATH):
    """Create database tables if they don't exist."""
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    with get_connection(db_path) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                company TEXT NOT NULL,
                location TEXT,
                description TEXT,
                url TEXT,
                source TEXT,
                posted_date TEXT,
                remote INTEGER DEFAULT 0,
                match_score INTEGER DEFAULT 0,
                match_level TEXT,
                apply_priority TEXT,
                match_data TEXT,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                applied INTEGER DEFAULT 0,
                applied_date TEXT,
                notes TEXT,
                status TEXT DEFAULT 'new'
            );

            CREATE TABLE IF NOT EXISTS search_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_date TEXT NOT NULL,
                total_found INTEGER DEFAULT 0,
                high_priority INTEGER DEFAULT 0,
                medium_priority INTEGER DEFAULT 0,
                low_priority INTEGER DEFAULT 0,
                sources TEXT,
                summary TEXT,
                duration_seconds REAL
            );

            CREATE TABLE IF NOT EXISTS resume_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_date TEXT NOT NULL,
                resume_hash TEXT,
                resume_data TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_jobs_score ON jobs(match_score DESC);
            CREATE INDEX IF NOT EXISTS idx_jobs_priority ON jobs(apply_priority);
            CREATE INDEX IF NOT EXISTS idx_jobs_company ON jobs(company);
            CREATE INDEX IF NOT EXISTS idx_jobs_first_seen ON jobs(first_seen);
        """)
    logger.info(f"Database initialized at {db_path}")


def upsert_jobs(jobs: list[dict], db_path: str = DB_PATH) -> tuple[int, int]:
    """
    Insert new jobs or update existing ones.
    Returns (new_count, updated_count).
    """
    new_count = 0
    updated_count = 0
    now = datetime.now().isoformat()

    with get_connection(db_path) as conn:
        for job in jobs:
            existing = conn.execute(
                "SELECT id, match_score, status FROM jobs WHERE id = ?", (job["id"],)
            ).fetchone()

            match_data_json = json.dumps(job.get("match_data", {}))

            if existing:
                # If the job was dismissed by user, keep it dismissed — don't overwrite
                if (existing["status"] or "new") == "dismissed":
                    conn.execute("UPDATE jobs SET last_seen = ? WHERE id = ?", (now, job["id"]))
                    updated_count += 1
                    continue
                conn.execute("""
                    UPDATE jobs SET
                        title = ?, company = ?, location = ?, description = ?,
                        url = ?, source = ?, remote = ?, match_score = ?,
                        match_level = ?, apply_priority = ?, match_data = ?,
                        last_seen = ?, posted_date = ?
                    WHERE id = ?
                """, (
                    job.get("title", ""),
                    job.get("company", ""),
                    job.get("location", ""),
                    job.get("description", "")[:2000],
                    job.get("url", ""),
                    job.get("source", ""),
                    1 if job.get("remote") else 0,
                    job.get("match_score", 0),
                    job.get("match_level", "unknown"),
                    job.get("apply_priority", "low"),
                    match_data_json,
                    now,
                    job.get("posted_date", now[:10]),
                    job["id"],
                ))
                updated_count += 1
            else:
                conn.execute("""
                    INSERT INTO jobs (
                        id, title, company, location, description, url, source,
                        posted_date, remote, match_score, match_level, apply_priority,
                        match_data, first_seen, last_seen, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new')
                """, (
                    job["id"],
                    job.get("title", ""),
                    job.get("company", ""),
                    job.get("location", ""),
                    job.get("description", "")[:2000],
                    job.get("url", ""),
                    job.get("source", ""),
                    job.get("posted_date", now[:10]),
                    1 if job.get("remote") else 0,
                    job.get("match_score", 0),
                    job.get("match_level", "unknown"),
                    job.get("apply_priority", "low"),
                    match_data_json,
                    now,
                    now,
                ))
                new_count += 1

    return new_count, updated_count


def record_search_run(summary: dict, duration: float, db_path: str = DB_PATH):
    """Record a completed search run."""
    with get_connection(db_path) as conn:
        conn.execute("""
            INSERT INTO search_runs (
                run_date, total_found, high_priority, medium_priority,
                low_priority, sources, summary, duration_seconds
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            datetime.now().isoformat(),
            summary.get("total_jobs_found", 0),
            summary.get("high_priority_count", 0),
            summary.get("medium_priority_count", 0),
            summary.get("low_priority_count", 0),
            json.dumps(summary.get("by_source", {})),
            json.dumps(summary),
            duration,
        ))


def get_jobs(
    db_path: str = DB_PATH,
    min_score: int = 0,
    priority: Optional[str] = None,
    source: Optional[str] = None,
    company: Optional[str] = None,
    status: Optional[str] = None,
    days_back: int = 30,
    limit: int = 500,
    offset: int = 0,
    sort_by: str = "match_score",
) -> list[dict]:
    """Query jobs from database with filtering. Dismissed jobs are excluded by default."""
    conditions = ["match_score >= ?"]
    params = [min_score]

    cutoff = (datetime.now() - timedelta(days=days_back)).isoformat()
    conditions.append("last_seen >= ?")
    params.append(cutoff)

    if status:
        conditions.append("status = ?")
        params.append(status)
    else:
        # Always exclude dismissed unless explicitly requested
        conditions.append("status != 'dismissed'")

    if priority:
        conditions.append("apply_priority = ?")
        params.append(priority)
    if source:
        conditions.append("source = ?")
        params.append(source)
    if company:
        conditions.append("company LIKE ?")
        params.append(f"%{company}%")

    order_map = {
        "match_score": "match_score DESC, apply_priority ASC",
        "date": "first_seen DESC",
        "company": "company ASC",
        "title": "title ASC",
    }
    order = order_map.get(sort_by, "match_score DESC")

    query = f"""
        SELECT * FROM jobs
        WHERE {' AND '.join(conditions)}
        ORDER BY {order}
        LIMIT ? OFFSET ?
    """
    params.extend([limit, offset])

    with get_connection(db_path) as conn:
        rows = conn.execute(query, params).fetchall()
        jobs = []
        for row in rows:
            job = dict(row)
            try:
                job["match_data"] = json.loads(job.get("match_data") or "{}")
            except Exception:
                job["match_data"] = {}
            jobs.append(job)
        return jobs


def get_dismissed_jobs(db_path: str = DB_PATH) -> list[dict]:
    """Return all jobs the user has dismissed (the skip list)."""
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE status = 'dismissed' ORDER BY last_seen DESC"
        ).fetchall()
        jobs = []
        for row in rows:
            job = dict(row)
            try:
                job["match_data"] = json.loads(job.get("match_data") or "{}")
            except Exception:
                job["match_data"] = {}
            jobs.append(job)
        return jobs


def undismiss_job(job_id: str, db_path: str = DB_PATH):
    """Restore a dismissed job back to 'new' status."""
    with get_connection(db_path) as conn:
        conn.execute("UPDATE jobs SET status = 'new' WHERE id = ?", (job_id,))


def get_job_by_id(job_id: str, db_path: str = DB_PATH) -> Optional[dict]:
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row:
            job = dict(row)
            try:
                job["match_data"] = json.loads(job.get("match_data") or "{}")
            except Exception:
                job["match_data"] = {}
            return job
    return None


def update_job_status(
    job_id: str,
    status: str,
    applied: bool = False,
    notes: str = "",
    db_path: str = DB_PATH,
):
    """Update application status for a job."""
    with get_connection(db_path) as conn:
        conn.execute("""
            UPDATE jobs SET
                status = ?,
                applied = ?,
                applied_date = ?,
                notes = ?
            WHERE id = ?
        """, (
            status,
            1 if applied else 0,
            datetime.now().isoformat() if applied else None,
            notes,
            job_id,
        ))


def get_search_history(limit: int = 10, db_path: str = DB_PATH) -> list[dict]:
    """Get recent search run history."""
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM search_runs ORDER BY run_date DESC LIMIT ?", (limit,)
        ).fetchall()
        runs = []
        for row in rows:
            run = dict(row)
            try:
                run["summary"] = json.loads(run.get("summary") or "{}")
                run["sources"] = json.loads(run.get("sources") or "{}")
            except Exception:
                pass
            runs.append(run)
        return runs


def get_stats(db_path: str = DB_PATH) -> dict:
    """Get overall statistics. Dismissed (skipped) jobs are excluded from all counts."""
    with get_connection(db_path) as conn:
        total = conn.execute("SELECT COUNT(*) FROM jobs WHERE status != 'dismissed'").fetchone()[0]
        high = conn.execute("SELECT COUNT(*) FROM jobs WHERE apply_priority='high' AND status != 'dismissed'").fetchone()[0]
        medium = conn.execute("SELECT COUNT(*) FROM jobs WHERE apply_priority='medium' AND status != 'dismissed'").fetchone()[0]
        applied = conn.execute("SELECT COUNT(*) FROM jobs WHERE applied=1 AND status != 'dismissed'").fetchone()[0]
        new_today = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE date(first_seen)=date('now') AND status != 'dismissed'"
        ).fetchone()[0]
        avg_score = conn.execute("SELECT AVG(match_score) FROM jobs WHERE match_score > 0 AND status != 'dismissed'").fetchone()[0]
        sources = conn.execute(
            "SELECT source, COUNT(*) as count FROM jobs WHERE status != 'dismissed' GROUP BY source ORDER BY count DESC"
        ).fetchall()
        dismissed_count = conn.execute("SELECT COUNT(*) FROM jobs WHERE status = 'dismissed'").fetchone()[0]

        return {
            "total_jobs": total,
            "high_priority": high,
            "medium_priority": medium,
            "applied_count": applied,
            "new_today": new_today,
            "avg_score": round(avg_score or 0, 1),
            "by_source": {row["source"]: row["count"] for row in sources},
            "dismissed_count": dismissed_count,
        }


def cleanup_old_jobs(days_to_keep: int = 30, db_path: str = DB_PATH) -> int:
    """Remove jobs not seen recently, except applied and dismissed ones.
    Dismissed jobs are kept indefinitely so the skip list persists across searches."""
    cutoff = (datetime.now() - timedelta(days=days_to_keep)).isoformat()
    with get_connection(db_path) as conn:
        result = conn.execute(
            "DELETE FROM jobs WHERE last_seen < ? AND applied = 0 AND status != 'dismissed'", (cutoff,)
        )
        deleted = result.rowcount
    logger.info(f"Cleaned up {deleted} old jobs (older than {days_to_keep} days)")
    return deleted
