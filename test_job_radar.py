"""
test_job_radar.py
Tests for all modules in the JobRadar app.
Runs without network or Azure credentials.
"""

import sys
import os
import json
import tempfile
import shutil
import sqlite3
import unittest
from unittest.mock import MagicMock, patch, mock_open
from datetime import datetime

# Add scripts to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "scripts"))
sys.path.insert(0, os.path.dirname(__file__))

# ─────────────────────────────────────────────────────────────
PASS = "\033[92m✓\033[0m"
FAIL = "\033[91m✗\033[0m"
SKIP = "\033[93m~\033[0m"
BOLD = "\033[1m"
RESET = "\033[0m"
DIM  = "\033[2m"

results = {"pass": 0, "fail": 0, "skip": 0}


def test(name, fn):
    try:
        fn()
        print(f"  {PASS} {name}")
        results["pass"] += 1
    except Exception as e:
        print(f"  {FAIL} {name}")
        print(f"       {DIM}{type(e).__name__}: {e}{RESET}")
        results["fail"] += 1


def section(title):
    print(f"\n{BOLD}-- {title} {'-' * (50 - len(title))}{RESET}")


# ═════════════════════════════════════════════════════════════
# 1. CONFIG LOADING
# ═════════════════════════════════════════════════════════════
section("Config")

import yaml

def test_config_loads():
    with open("config/config.yaml") as f:
        cfg = yaml.safe_load(f)
    assert "azure" in cfg
    assert "resume" in cfg
    assert "search" in cfg
    assert "companies" in cfg
    assert "scheduler" in cfg

test("config.yaml loads and has required sections", test_config_loads)

def test_config_companies():
    with open("config/config.yaml") as f:
        cfg = yaml.safe_load(f)
    companies = cfg["companies"]
    assert isinstance(companies, list)
    assert len(companies) > 0
    for c in companies:
        assert "name" in c
        assert "careers_url" in c

test("companies list is valid", test_config_companies)

def test_config_azure_keys():
    with open("config/config.yaml") as f:
        cfg = yaml.safe_load(f)
    az = cfg["azure"]
    assert "endpoint" in az
    assert "api_key" in az
    assert "model_name" in az

test("azure config has required keys", test_config_azure_keys)

def test_config_scheduler():
    with open("config/config.yaml") as f:
        cfg = yaml.safe_load(f)
    sched = cfg["scheduler"]
    run_time = sched["run_time"]
    assert ":" in run_time
    h, m = run_time.split(":")
    assert 0 <= int(h) <= 23
    assert 0 <= int(m) <= 59

test("scheduler run_time is valid HH:MM", test_config_scheduler)


# ═════════════════════════════════════════════════════════════
# 2. RESUME READER
# ═════════════════════════════════════════════════════════════
section("Resume Reader")

from resume_reader import (
    parse_resume_locally,
    _create_placeholder_resume,
    find_resume,
    extract_text_from_docx,
)

SAMPLE_RESUME = """
Jane Smith
jane.smith@email.com | (555) 123-4567 | linkedin.com/in/janesmith | github.com/janesmith
Boston, MA

EXPERIENCE
Senior Software Engineer — Acme Corp (2020–Present)
  • Built scalable Python microservices serving 10M+ requests/day
  • Led team of 5 engineers, drove 40% latency reduction
  • Technologies: Python, FastAPI, AWS, Docker, Kubernetes, PostgreSQL

Software Engineer — StartupXYZ (2018–2020)
  • Developed React frontend and Node.js backend for SaaS product
  • Implemented CI/CD pipeline with GitHub Actions

EDUCATION
B.S. Computer Science — MIT, 2018

SKILLS
Python, JavaScript, TypeScript, React, Node.js, FastAPI, Django
AWS, GCP, Docker, Kubernetes, Terraform, PostgreSQL, Redis
Machine Learning, TensorFlow, Data Science
"""

def test_local_parse_name():
    data = parse_resume_locally(SAMPLE_RESUME)
    assert data["name"] == "Jane Smith"

test("extracts candidate name", test_local_parse_name)

def test_local_parse_email():
    data = parse_resume_locally(SAMPLE_RESUME)
    assert data["email"] == "jane.smith@email.com"

test("extracts email address", test_local_parse_email)

def test_local_parse_phone():
    data = parse_resume_locally(SAMPLE_RESUME)
    assert "555" in data["phone"] or "5551234567" in data["phone"].replace("-","").replace("(","").replace(")","").replace(" ","")

test("extracts phone number", test_local_parse_phone)

def test_local_parse_linkedin():
    data = parse_resume_locally(SAMPLE_RESUME)
    assert "linkedin.com/in/janesmith" in data["linkedin"]

test("extracts LinkedIn URL", test_local_parse_linkedin)

def test_local_parse_github():
    data = parse_resume_locally(SAMPLE_RESUME)
    assert "github.com/janesmith" in data["github"]

test("extracts GitHub URL", test_local_parse_github)

def test_local_parse_skills():
    data = parse_resume_locally(SAMPLE_RESUME)
    skills = data["skills"]
    assert len(skills) > 5
    # Check some expected skills are found
    skill_lower = [s.lower() for s in skills]
    assert "python" in skill_lower
    assert "react" in skill_lower or "docker" in skill_lower

test("extracts technical skills", test_local_parse_skills)

def test_local_parse_years():
    data = parse_resume_locally(SAMPLE_RESUME)
    # Should detect years from 2018 onward
    assert data["years_experience"] > 0
    assert data["years_experience"] < 50

test("estimates years of experience from dates", test_local_parse_years)

def test_placeholder_resume():
    p = _create_placeholder_resume()
    required_keys = ["name", "target_roles", "skills", "search_keywords", "seniority_level"]
    for key in required_keys:
        assert key in p, f"Missing key: {key}"

test("placeholder resume has all required keys", test_placeholder_resume)

def test_find_resume_missing():
    with tempfile.TemporaryDirectory() as tmpdir:
        result = find_resume(tmpdir, "resume.docx")
        assert result is None

test("find_resume returns None when folder is empty", test_find_resume_missing)

def test_find_resume_auto_detect():
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a fake .docx file
        fake_path = os.path.join(tmpdir, "my_resume.docx")
        open(fake_path, "w").close()
        # Should not find because it's empty/invalid, but test path detection
        result = find_resume(tmpdir, "resume.docx")
        assert result == fake_path  # auto-detected

test("find_resume auto-detects .docx when exact name not found", test_find_resume_auto_detect)

def test_raw_text_preserved():
    data = parse_resume_locally(SAMPLE_RESUME)
    assert data["raw_text"] == SAMPLE_RESUME

test("raw_text preserved in parsed data", test_raw_text_preserved)


# ═════════════════════════════════════════════════════════════
# 3. JOB SCRAPER UTILITIES
# ═════════════════════════════════════════════════════════════
section("Job Scraper")

from job_scraper import (
    clean_text,
    deduplicate_jobs,
    _make_job,
    fetch_job_description,
)

def test_clean_text_whitespace():
    assert clean_text("  hello   world  ") == "hello world"
    assert clean_text("line1\n\n  line2  ") == "line1 line2"

test("clean_text strips extra whitespace", test_clean_text_whitespace)

def test_make_job_structure():
    job = _make_job("Senior Engineer", "TechCorp", "Boston, MA", "https://example.com", "TestSource")
    required = ["id", "title", "company", "location", "url", "source", "posted_date", "remote"]
    for key in required:
        assert key in job, f"Missing key: {key}"
    assert job["title"] == "Senior Engineer"
    assert job["company"] == "TechCorp"
    assert job["source"] == "TestSource"

test("_make_job creates valid job dict with all fields", test_make_job_structure)

def test_make_job_remote_detection():
    remote_job = _make_job("Engineer (Remote)", "Co", "Remote", "http://x.com", "S")
    onsite_job = _make_job("Engineer", "Co", "Boston, MA", "http://x.com", "S")
    assert remote_job["remote"] is True
    assert onsite_job["remote"] is False

test("_make_job detects remote from title/location", test_make_job_remote_detection)

def test_deduplicate_exact():
    jobs = [
        _make_job("Engineer", "ACME", "NYC", "http://a.com", "S1"),
        _make_job("Engineer", "ACME", "NYC", "http://b.com", "S2"),  # same title+company
        _make_job("Scientist", "ACME", "NYC", "http://c.com", "S3"),
    ]
    unique = deduplicate_jobs(jobs)
    assert len(unique) == 2

test("deduplicate_jobs removes exact title+company duplicates", test_deduplicate_exact)

def test_deduplicate_case_insensitive():
    jobs = [
        _make_job("Software Engineer", "Google", "NYC", "http://a.com", "S"),
        _make_job("software engineer", "GOOGLE", "SF",  "http://b.com", "S"),
    ]
    unique = deduplicate_jobs(jobs)
    assert len(unique) == 1

test("deduplicate_jobs is case-insensitive", test_deduplicate_case_insensitive)

def test_deduplicate_preserves_unique():
    jobs = [_make_job(f"Role {i}", f"Company {i}", "NYC", f"http://{i}.com", "S") for i in range(5)]
    unique = deduplicate_jobs(jobs)
    assert len(unique) == 5

test("deduplicate_jobs preserves all unique jobs", test_deduplicate_preserves_unique)

def test_fetch_description_no_url():
    result = fetch_job_description("")
    assert result == ""

test("fetch_job_description returns empty string for empty URL", test_fetch_description_no_url)


# ═════════════════════════════════════════════════════════════
# 4. JOB MATCHER
# ═════════════════════════════════════════════════════════════
section("Job Matcher")

from job_matcher import (
    _score_by_keywords,
    rank_jobs,
    generate_daily_summary,
)

SAMPLE_RESUME_DATA = {
    "name": "Jane Smith",
    "current_title": "Senior Software Engineer",
    "target_roles": ["Software Engineer", "Backend Engineer", "Python Developer"],
    "seniority_level": "senior",
    "years_experience": 6,
    "skills": {"technical": ["Python", "FastAPI", "AWS", "Docker", "PostgreSQL", "React"], "soft": []},
    "search_keywords": ["python", "backend", "aws", "docker", "software engineer"],
    "industries": ["SaaS", "Technology"],
    "key_achievements": ["Led team of 5 engineers"],
}

def test_keyword_score_high_match():
    job = {
        "id": "j1", "title": "Python Backend Software Engineer",
        "company": "TechCo", "description": "Looking for Python developer with AWS and Docker experience. FastAPI knowledge preferred.",
        "location": "Boston", "remote": False, "source": "Test",
    }
    result = _score_by_keywords(job, SAMPLE_RESUME_DATA)
    assert result["match_score"] >= 50, f"Expected high score, got {result['match_score']}"

test("keyword scorer gives high score for strong match", test_keyword_score_high_match)

def test_keyword_score_low_match():
    job = {
        "id": "j2", "title": "Nurse Practitioner",
        "company": "Hospital", "description": "Medical experience required. Patient care.",
        "location": "Boston", "remote": False, "source": "Test",
    }
    result = _score_by_keywords(job, SAMPLE_RESUME_DATA)
    assert result["match_score"] < 40, f"Expected low score, got {result['match_score']}"

test("keyword scorer gives low score for unrelated job", test_keyword_score_low_match)

def test_keyword_score_fields():
    job = _make_job("Python Dev", "Co", "NYC", "http://x.com", "S")
    result = _score_by_keywords(job, SAMPLE_RESUME_DATA)
    assert "match_score" in result
    assert "match_level" in result
    assert "apply_priority" in result
    assert "match_data" in result
    assert 0 <= result["match_score"] <= 100

test("keyword scorer returns all required fields with valid score range", test_keyword_score_fields)

def test_keyword_score_match_levels():
    # Construct jobs with varying match quality
    high_job = {"id": "h", "title": "Senior Python Software Engineer", "company": "Co",
                "description": "Python FastAPI AWS Docker PostgreSQL Backend", "location": "Boston", "remote": False, "source": "T"}
    low_job  = {"id": "l", "title": "Accountant", "company": "Bank",
                "description": "Excel spreadsheets finance", "location": "NYC", "remote": False, "source": "T"}
    high = _score_by_keywords(high_job, SAMPLE_RESUME_DATA)
    low  = _score_by_keywords(low_job,  SAMPLE_RESUME_DATA)
    assert high["match_score"] > low["match_score"]

test("keyword scorer: high-match job scores higher than unrelated job", test_keyword_score_match_levels)

def test_rank_jobs_order():
    jobs = [
        {"id": "1", "match_score": 55, "apply_priority": "medium"},
        {"id": "2", "match_score": 90, "apply_priority": "high"},
        {"id": "3", "match_score": 30, "apply_priority": "low"},
        {"id": "4", "match_score": 85, "apply_priority": "high"},
    ]
    ranked = rank_jobs(jobs)
    # High priority should come first
    assert ranked[0]["apply_priority"] == "high"
    assert ranked[1]["apply_priority"] == "high"
    # Within high, higher score first
    assert ranked[0]["match_score"] >= ranked[1]["match_score"]

test("rank_jobs: high priority first, then sorted by score descending", test_rank_jobs_order)

def test_rank_jobs_all_priorities():
    jobs = [
        {"id": "a", "match_score": 70, "apply_priority": "medium"},
        {"id": "b", "match_score": 80, "apply_priority": "low"},
        {"id": "c", "match_score": 95, "apply_priority": "high"},
        {"id": "d", "match_score": 10, "apply_priority": "skip"},
    ]
    ranked = rank_jobs(jobs)
    priorities = [j["apply_priority"] for j in ranked]
    assert priorities == ["high", "medium", "low", "skip"]

test("rank_jobs orders: high > medium > low > skip", test_rank_jobs_all_priorities)

def test_generate_summary():
    jobs = []
    for i, priority in enumerate(["high", "high", "medium", "low", "low", "low"]):
        jobs.append({
            "id": str(i),
            "title": f"Role {i}",
            "company": f"Company {i}",
            "match_score": 90 - i * 5,
            "apply_priority": priority,
            "source": "Indeed" if i % 2 == 0 else "LinkedIn",
            "url": f"http://job{i}.com",
        })
    summary = generate_daily_summary(jobs, SAMPLE_RESUME_DATA)
    assert summary["total_jobs_found"] == 6
    assert summary["high_priority_count"] == 2
    assert summary["medium_priority_count"] == 1
    assert summary["low_priority_count"] == 3
    assert "by_source" in summary
    assert "top_5_jobs" in summary
    assert len(summary["top_5_jobs"]) <= 5

test("generate_daily_summary counts priorities and sources correctly", test_generate_summary)


# ═════════════════════════════════════════════════════════════
# 5. DATABASE
# ═════════════════════════════════════════════════════════════
section("Database")

import database as db

TMP_DB = None

def setup_tmp_db():
    global TMP_DB
    tmp = tempfile.mktemp(suffix=".db")
    TMP_DB = tmp
    db.init_db(tmp)
    return tmp

def test_db_init():
    tmp = setup_tmp_db()
    conn = sqlite3.connect(tmp)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    conn.close()
    assert "jobs" in tables
    assert "search_runs" in tables
    assert "resume_snapshots" in tables

test("init_db creates all required tables", test_db_init)

SAMPLE_JOBS = [
    {
        "id": "test_001",
        "title": "Senior Python Engineer",
        "company": "TechCorp",
        "location": "Boston, MA",
        "description": "Python microservices role",
        "url": "https://techcorp.com/jobs/001",
        "source": "LinkedIn",
        "posted_date": "2025-03-01",
        "remote": False,
        "match_score": 85,
        "match_level": "excellent",
        "apply_priority": "high",
        "match_data": {"score": 85, "matching_skills": ["Python", "AWS"]},
    },
    {
        "id": "test_002",
        "title": "React Frontend Developer",
        "company": "StartupABC",
        "location": "Remote",
        "description": "React and TypeScript",
        "url": "https://startup.com/jobs/002",
        "source": "Indeed",
        "posted_date": "2025-03-01",
        "remote": True,
        "match_score": 62,
        "match_level": "good",
        "apply_priority": "medium",
        "match_data": {"score": 62, "matching_skills": ["React"]},
    },
]

def test_upsert_new_jobs():
    tmp = setup_tmp_db()
    new_count, updated_count = db.upsert_jobs(SAMPLE_JOBS, tmp)
    assert new_count == 2
    assert updated_count == 0

test("upsert_jobs inserts new jobs correctly", test_upsert_new_jobs)

def test_upsert_updates_existing():
    tmp = setup_tmp_db()
    db.upsert_jobs(SAMPLE_JOBS, tmp)
    modified = [{**SAMPLE_JOBS[0], "match_score": 90}]
    new_count, updated_count = db.upsert_jobs(modified, tmp)
    assert new_count == 0
    assert updated_count == 1
    jobs = db.get_jobs(db_path=tmp)
    job = next(j for j in jobs if j["id"] == "test_001")
    assert job["match_score"] == 90

test("upsert_jobs updates score on second insert", test_upsert_updates_existing)

def test_get_jobs_all():
    tmp = setup_tmp_db()
    db.upsert_jobs(SAMPLE_JOBS, tmp)
    jobs = db.get_jobs(db_path=tmp)
    assert len(jobs) == 2

test("get_jobs returns all jobs", test_get_jobs_all)

def test_get_jobs_min_score():
    tmp = setup_tmp_db()
    db.upsert_jobs(SAMPLE_JOBS, tmp)
    jobs = db.get_jobs(min_score=70, db_path=tmp)
    assert len(jobs) == 1
    assert jobs[0]["id"] == "test_001"

test("get_jobs filters by min_score", test_get_jobs_min_score)

def test_get_jobs_by_source():
    tmp = setup_tmp_db()
    db.upsert_jobs(SAMPLE_JOBS, tmp)
    jobs = db.get_jobs(source="Indeed", db_path=tmp)
    assert len(jobs) == 1
    assert jobs[0]["source"] == "Indeed"

test("get_jobs filters by source", test_get_jobs_by_source)

def test_get_job_by_id():
    tmp = setup_tmp_db()
    db.upsert_jobs(SAMPLE_JOBS, tmp)
    job = db.get_job_by_id("test_001", tmp)
    assert job is not None
    assert job["title"] == "Senior Python Engineer"
    assert job["company"] == "TechCorp"

test("get_job_by_id retrieves correct job", test_get_job_by_id)

def test_get_job_by_id_missing():
    tmp = setup_tmp_db()
    job = db.get_job_by_id("nonexistent_id", tmp)
    assert job is None

test("get_job_by_id returns None for missing job", test_get_job_by_id_missing)

def test_update_job_status():
    tmp = setup_tmp_db()
    db.upsert_jobs(SAMPLE_JOBS, tmp)
    db.update_job_status("test_001", status="applied", applied=True, notes="Great company", db_path=tmp)
    job = db.get_job_by_id("test_001", tmp)
    assert job["status"] == "applied"
    assert job["applied"] == 1
    assert job["notes"] == "Great company"

test("update_job_status marks job as applied with notes", test_update_job_status)

def test_match_data_roundtrip():
    tmp = setup_tmp_db()
    db.upsert_jobs(SAMPLE_JOBS, tmp)
    job = db.get_job_by_id("test_001", tmp)
    assert isinstance(job["match_data"], dict)
    assert job["match_data"]["score"] == 85
    assert "Python" in job["match_data"]["matching_skills"]

test("match_data JSON serializes and deserializes correctly", test_match_data_roundtrip)

def test_record_search_run():
    tmp = setup_tmp_db()
    summary = {
        "total_jobs_found": 42,
        "high_priority_count": 5,
        "medium_priority_count": 15,
        "low_priority_count": 22,
        "by_source": {"Indeed": 20, "LinkedIn": 22},
        "top_5_jobs": [],
    }
    db.record_search_run(summary, duration=12.5, db_path=tmp)
    history = db.get_search_history(limit=5, db_path=tmp)
    assert len(history) == 1
    assert history[0]["total_found"] == 42
    assert history[0]["high_priority"] == 5

test("record_search_run saves run history correctly", test_record_search_run)

def test_get_stats():
    tmp = setup_tmp_db()
    db.upsert_jobs(SAMPLE_JOBS, tmp)
    db.update_job_status("test_001", status="applied", applied=True, db_path=tmp)
    db.update_job_status("test_002", status="dismissed", db_path=tmp)
    stats = db.get_stats(db_path=tmp)
    # Dismissed jobs are excluded from all counts
    assert stats["total_jobs"] == 1          # only non-dismissed
    assert stats["applied_count"] == 1
    assert stats["high_priority"] == 1
    assert stats["medium_priority"] == 0     # test_002 is dismissed
    assert stats["dismissed_count"] == 1
    assert "by_source" in stats

test("get_stats returns accurate counts (dismissed excluded)", test_get_stats)

def test_cleanup_old_jobs():
    tmp = setup_tmp_db()
    db.upsert_jobs(SAMPLE_JOBS, tmp)
    # Manually age one job
    conn = sqlite3.connect(tmp)
    conn.execute("UPDATE jobs SET last_seen='2020-01-01' WHERE id='test_002'")
    conn.commit()
    conn.close()
    deleted = db.cleanup_old_jobs(days_to_keep=30, db_path=tmp)
    assert deleted == 1
    remaining = db.get_jobs(days_back=365*10, db_path=tmp)
    assert all(j["id"] != "test_002" for j in remaining)

test("cleanup_old_jobs removes stale jobs (preserves applied)", test_cleanup_old_jobs)

def test_cleanup_preserves_applied():
    tmp = setup_tmp_db()
    db.upsert_jobs(SAMPLE_JOBS, tmp)
    db.update_job_status("test_002", status="applied", applied=True, db_path=tmp)
    conn = sqlite3.connect(tmp)
    conn.execute("UPDATE jobs SET last_seen='2020-01-01'")  # age both
    conn.commit()
    conn.close()
    deleted = db.cleanup_old_jobs(days_to_keep=30, db_path=tmp)
    assert deleted == 1  # only non-applied one deleted
    remaining_ids = [j["id"] for j in db.get_jobs(days_back=365*10, db_path=tmp)]
    assert "test_002" in remaining_ids  # applied job preserved

test("cleanup_old_jobs preserves applied jobs", test_cleanup_preserves_applied)

def test_cleanup_preserves_dismissed():
    tmp = setup_tmp_db()
    db.upsert_jobs(SAMPLE_JOBS, tmp)
    db.update_job_status("test_002", status="dismissed", db_path=tmp)
    conn = sqlite3.connect(tmp)
    conn.execute("UPDATE jobs SET last_seen='2020-01-01'")
    conn.commit()
    conn.close()
    deleted = db.cleanup_old_jobs(days_to_keep=30, db_path=tmp)
    assert deleted == 1  # only non-dismissed one deleted
    # Dismissed job must still be in the DB even though it's old
    conn2 = sqlite3.connect(tmp)
    row = conn2.execute("SELECT id FROM jobs WHERE id='test_002'").fetchone()
    conn2.close()
    assert row is not None

test("cleanup_old_jobs preserves dismissed (skip list) jobs", test_cleanup_preserves_dismissed)


# ═════════════════════════════════════════════════════════════
# 6. LLM CLIENT (mocked)
# ═════════════════════════════════════════════════════════════
section("LLM Client (mocked)")

from llm_client import AzureAIFoundryClient

def make_mock_llm(side_effect=None):
    """
    Create a mocked AzureAIFoundryClient by bypassing _init_client
    and injecting a mock _client directly.
    """
    mock_response = MagicMock()
    mock_response.choices[0].message.content = json.dumps({
        "score": 78,
        "match_level": "good",
        "matching_skills": ["Python", "AWS", "Docker"],
        "missing_skills": ["Kubernetes"],
        "strengths": ["Strong Python background"],
        "concerns": ["Limited K8s experience"],
        "recommendation": "Strong candidate, apply confidently",
        "apply_priority": "high",
    })

    config = {
        "azure": {
            "endpoint": "https://test.ai.azure.com/models",
            "api_key": "test-key-123",
            "model_name": "gpt-4o",
        }
    }

    # Patch _init_client so no real network call is made
    with patch.object(AzureAIFoundryClient, "_init_client", lambda self: None):
        client = AzureAIFoundryClient(config)

    mock_inner = MagicMock()
    if side_effect:
        mock_inner.complete.side_effect = side_effect
    else:
        mock_inner.complete.return_value = mock_response
    client._client = mock_inner

    return client, mock_response


def test_llm_score_job_returns_dict():
    client, _ = make_mock_llm()
    job = {"title": "Python Engineer", "company": "TechCo", "description": "Python AWS Docker"}
    result = client.score_job_match(SAMPLE_RESUME_DATA, job)
    assert isinstance(result, dict)
    assert "score" in result
    assert "match_level" in result
    assert "apply_priority" in result

test("score_job_match returns dict with required keys", test_llm_score_job_returns_dict)

def test_llm_score_range():
    client, _ = make_mock_llm()
    job = {"title": "Engineer", "company": "Co", "description": "test"}
    result = client.score_job_match(SAMPLE_RESUME_DATA, job)
    assert 0 <= result["score"] <= 100

test("score_job_match score is in 0-100 range", test_llm_score_range)

def test_llm_score_failure_graceful():
    """When LLM fails, should return a default dict (not raise)."""
    client, _ = make_mock_llm(side_effect=Exception("Network error"))
    job = {"title": "Engineer", "company": "Co", "description": "test"}
    result = client.score_job_match(SAMPLE_RESUME_DATA, job)
    assert result["score"] == 0
    assert "LLM scoring failed" in str(result.get("concerns", []))

test("score_job_match fails gracefully on LLM error", test_llm_score_failure_graceful)

def test_llm_complete_json_strips_fences():
    """complete_json should handle markdown-wrapped JSON."""
    mock_response = MagicMock()
    mock_response.choices[0].message.content = '```json\n{"key": "value"}\n```'

    with patch.object(AzureAIFoundryClient, "_init_client", lambda self: None):
        config = {"azure": {"endpoint": "https://x.com", "api_key": "k", "model_name": "m"}}
        client = AzureAIFoundryClient(config)

    # Patch the `complete` instance method directly to avoid Azure import
    client.complete = MagicMock(return_value=mock_response)

    result = client.complete_json("test prompt")
    assert result == {"key": "value"}

test("complete_json strips markdown code fences from response", test_llm_complete_json_strips_fences)


# ═════════════════════════════════════════════════════════════
# 7. INTEGRATION: Full Pipeline (no network)
# ═════════════════════════════════════════════════════════════
section("Integration Pipeline")

def test_pipeline_no_llm():
    """Run the full score+rank pipeline using keyword matching only."""
    from job_matcher import score_jobs_batch, rank_jobs, generate_daily_summary

    raw_jobs = [
        _make_job("Python Backend Engineer", "TechCorp", "Boston", "http://a.com", "Indeed"),
        _make_job("Senior Data Scientist", "DataCo", "Remote", "http://b.com", "LinkedIn"),
        _make_job("Accountant", "FinanceCo", "NYC", "http://c.com", "Indeed"),
        _make_job("React Frontend Dev", "WebCo", "Boston", "http://d.com", "Built In"),
        _make_job("DevOps Engineer AWS", "CloudCo", "Remote", "http://e.com", "LinkedIn"),
    ]

    scored = score_jobs_batch(raw_jobs, SAMPLE_RESUME_DATA, llm_client=None, min_score=0)
    ranked = rank_jobs(scored)
    summary = generate_daily_summary(ranked, SAMPLE_RESUME_DATA)

    assert len(scored) == 5
    assert len(ranked) == 5
    assert summary["total_jobs_found"] == 5
    # Python backend engineer should score higher than accountant
    python_job = next(j for j in ranked if "Python" in j["title"])
    accountant = next(j for j in ranked if "Accountant" in j["title"])
    assert python_job["match_score"] >= accountant["match_score"]

test("full pipeline (no LLM): score+rank+summarize 5 jobs", test_pipeline_no_llm)

def test_pipeline_db_roundtrip():
    """Score jobs and persist them to DB, then retrieve."""
    from job_matcher import score_jobs_batch, rank_jobs

    tmp = tempfile.mktemp(suffix=".db")
    db.init_db(tmp)

    raw_jobs = [_make_job(f"Role {i}", f"Co{i}", "NYC", f"http://{i}.com", "Test") for i in range(5)]
    scored = score_jobs_batch(raw_jobs, SAMPLE_RESUME_DATA, llm_client=None, min_score=0)
    ranked = rank_jobs(scored)

    new_count, _ = db.upsert_jobs(ranked, tmp)
    assert new_count == 5

    retrieved = db.get_jobs(db_path=tmp)
    assert len(retrieved) == 5
    # Should be sorted by score desc
    scores = [j["match_score"] for j in retrieved]
    assert scores == sorted(scores, reverse=True)

test("full pipeline: score → upsert → retrieve maintains sort order", test_pipeline_db_roundtrip)

def test_empty_jobs_graceful():
    """Pipeline handles empty job list without errors."""
    from job_matcher import score_jobs_batch, rank_jobs, generate_daily_summary
    scored = score_jobs_batch([], SAMPLE_RESUME_DATA, llm_client=None, min_score=0)
    ranked = rank_jobs(scored)
    summary = generate_daily_summary(ranked, SAMPLE_RESUME_DATA)
    assert summary["total_jobs_found"] == 0
    assert summary["high_priority_count"] == 0

test("pipeline handles empty job list gracefully", test_empty_jobs_graceful)

def test_malformed_resume_data():
    """Scorer handles missing/malformed resume data without crashing."""
    from job_matcher import _score_by_keywords
    bad_resume = {}
    job = _make_job("Engineer", "Co", "NYC", "http://x.com", "S")
    result = _score_by_keywords(job, bad_resume)
    assert "match_score" in result
    assert 0 <= result["match_score"] <= 100

test("scorer handles missing resume data gracefully", test_malformed_resume_data)


# ═════════════════════════════════════════════════════════════
# 8. EDGE CASES & SECURITY
# ═════════════════════════════════════════════════════════════
section("Edge Cases")

def test_job_id_uniqueness():
    """Job IDs should be deterministic and unique per title+company."""
    j1 = _make_job("Engineer", "ACME", "NYC", "http://a.com", "S")
    j2 = _make_job("Engineer", "ACME", "SF",  "http://b.com", "S")  # same name, different location
    j3 = _make_job("Manager", "ACME", "NYC", "http://c.com", "S")   # different title
    assert j1["id"] == j2["id"]  # same title+company → same id (dedup key)
    assert j1["id"] != j3["id"]

test("job IDs are deterministic based on title+company", test_job_id_uniqueness)

def test_long_description_truncated():
    """Very long descriptions should be handled without error."""
    tmp = setup_tmp_db()
    long_desc = "x" * 10_000
    jobs = [{**SAMPLE_JOBS[0], "description": long_desc}]
    new_count, _ = db.upsert_jobs(jobs, tmp)
    assert new_count == 1
    job = db.get_job_by_id(SAMPLE_JOBS[0]["id"], tmp)
    assert len(job["description"]) <= 2001  # truncated in upsert

test("long job descriptions are truncated safely in DB", test_long_description_truncated)

def test_special_chars_in_jobs():
    """Jobs with special characters shouldn't crash anything."""
    tmp = setup_tmp_db()
    jobs = [{
        **SAMPLE_JOBS[0],
        "title": "Engineer & Developer (Senior) — Full-Stack",
        "company": "Müller & Co. <GmbH>",
        "description": 'Great role! "Apply now" & earn $100k+',
    }]
    new_count, _ = db.upsert_jobs(jobs, tmp)
    assert new_count == 1
    job = db.get_job_by_id(SAMPLE_JOBS[0]["id"], tmp)
    assert "Müller" in job["company"] or "GmbH" in job["company"]

test("special characters in job data handled safely", test_special_chars_in_jobs)

def test_resume_with_no_skills():
    """Resume with no recognizable skills should still parse without error."""
    minimal = "John Doe\njohn@email.com\nI work hard and am a team player."
    data = parse_resume_locally(minimal)
    assert data["name"] == "John Doe"
    assert data["email"] == "john@email.com"
    assert isinstance(data["skills"], list)

test("resume with no recognizable skills parses without error", test_resume_with_no_skills)


# ═════════════════════════════════════════════════════════════
# RESULTS SUMMARY
# ═════════════════════════════════════════════════════════════
total = results["pass"] + results["fail"] + results["skip"]
passed = results["pass"]
failed = results["fail"]

print(f"\n{'═'*55}")
print(f"{BOLD}  Results: {passed}/{total} tests passed{RESET}")
if failed:
    print(f"  {FAIL} {failed} test(s) FAILED")
else:
    print(f"  {PASS} All tests passed!")
print(f"{'═'*55}\n")

sys.exit(0 if failed == 0 else 1)
