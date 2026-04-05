"""
job_matcher.py
Scores and ranks job postings against a resume using Azure AI Foundry.
"""

import logging
import json
import time
from datetime import datetime
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)


def score_jobs_batch(
    jobs: list[dict],
    resume_data: dict,
    llm_client,
    min_score: int = 40,
    max_workers: int = 3,
) -> list[dict]:
    """
    Score all jobs against resume using LLM.
    Uses thread pool for parallel scoring with rate limit consideration.
    """
    if not llm_client:
        logger.warning("No LLM client - using keyword matching only")
        return [_score_by_keywords(job, resume_data) for job in jobs]

    logger.info(f"Scoring {len(jobs)} jobs against resume...")
    scored_jobs = []

    def score_single(job: dict) -> dict:
        try:
            match_data = llm_client.score_job_match(resume_data, job)
            job["match_data"] = match_data
            job["match_score"] = match_data.get("score", 0)
            job["match_level"] = match_data.get("match_level", "unknown")
            job["apply_priority"] = match_data.get("apply_priority", "low")
        except Exception as e:
            logger.debug(f"Scoring failed for {job.get('title')}: {e}")
            # Fall back to keyword matching
            fallback = _score_by_keywords(job, resume_data)
            job.update(fallback)
        return job

    # Use thread pool but respect rate limits
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(score_single, job): job for job in jobs}
        completed = 0
        for future in as_completed(futures):
            try:
                scored_job = future.result(timeout=30)
                if scored_job.get("match_score", 0) >= min_score:
                    scored_jobs.append(scored_job)
                completed += 1
                if completed % 10 == 0:
                    logger.info(f"Scored {completed}/{len(jobs)} jobs...")
                # Small delay to avoid hammering the API
                time.sleep(0.2)
            except Exception as e:
                logger.debug(f"Job scoring future failed: {e}")

    logger.info(
        f"Scoring complete: {len(scored_jobs)}/{len(jobs)} jobs above "
        f"minimum score of {min_score}"
    )
    return scored_jobs


def _score_by_keywords(job: dict, resume_data: dict) -> dict:
    """
    Simple keyword-based scoring fallback (no LLM required).
    Scores 0-100 based on keyword overlap.
    """
    resume_skills = []
    skills_data = resume_data.get("skills", {})
    if isinstance(skills_data, dict):
        resume_skills = skills_data.get("technical", []) + skills_data.get("soft", [])
    elif isinstance(skills_data, list):
        resume_skills = skills_data

    search_keywords = resume_data.get("search_keywords", [])
    target_roles = resume_data.get("target_roles", [])

    all_resume_terms = [t.lower() for t in resume_skills + search_keywords + target_roles]

    job_text = (
        f"{job.get('title', '')} {job.get('description', '')} {job.get('company', '')}"
    ).lower()

    # Count matches
    matches = [term for term in all_resume_terms if term in job_text]
    match_count = len(set(matches))

    # Score based on matches (max ~80 for keyword match)
    base_score = min(80, match_count * 8)

    # Bonus for title match
    title_lower = job.get("title", "").lower()
    if any(role.lower() in title_lower for role in target_roles):
        base_score = min(100, base_score + 15)

    # Bonus for remote if candidate likes remote
    if job.get("remote") and resume_data.get("remote_preferred"):
        base_score = min(100, base_score + 5)

    score = int(base_score)

    if score >= 75:
        match_level = "excellent"
        priority = "high"
    elif score >= 60:
        match_level = "good"
        priority = "medium"
    elif score >= 40:
        match_level = "fair"
        priority = "low"
    else:
        match_level = "poor"
        priority = "skip"

    job["match_score"] = score
    job["match_level"] = match_level
    job["apply_priority"] = priority
    job["match_data"] = {
        "score": score,
        "match_level": match_level,
        "matching_skills": list(set(matches))[:8],
        "missing_skills": [],
        "strengths": [f"Keyword match: {match_count} terms"],
        "concerns": ["Scored by keyword matching (LLM unavailable)"],
        "recommendation": "Review manually",
        "apply_priority": priority,
    }
    return job


def rank_jobs(scored_jobs: list[dict]) -> list[dict]:
    """
    Sort jobs by profile tier first, then apply_priority, then score.
    Tier order: primary (non-telecom) > adjacent (pivot) > telecom (fallback) > other
    Within tier: high > medium > low > skip
    """
    tier_order     = {"primary": 0, "adjacent": 1, "telecom": 2, "other": 3}
    priority_order = {"high": 0, "medium": 1, "low": 2, "skip": 3, "unknown": 4}

    def sort_key(job):
        tier     = job.get("profile_category", "other")
        priority = job.get("apply_priority", "unknown")
        score    = job.get("match_score", 0)
        return (tier_order.get(tier, 3), priority_order.get(priority, 4), -score)

    return sorted(scored_jobs, key=sort_key)


def generate_daily_summary(jobs: list[dict], resume_data: dict) -> dict:
    """Generate a summary of the daily job search results."""
    total = len(jobs)
    by_priority = {
        "high": [j for j in jobs if j.get("apply_priority") == "high"],
        "medium": [j for j in jobs if j.get("apply_priority") == "medium"],
        "low": [j for j in jobs if j.get("apply_priority") == "low"],
    }
    by_source = {}
    for job in jobs:
        source = job.get("source", "Unknown")
        by_source[source] = by_source.get(source, 0) + 1

    top_companies = {}
    for job in jobs:
        company = job.get("company", "Unknown")
        top_companies[company] = top_companies.get(company, 0) + 1

    # ── Profile category breakdown ─────────────────────────────────
    primary_jobs  = [j for j in jobs if j.get("profile_category") == "primary"]
    adjacent_jobs = [j for j in jobs if j.get("profile_category") == "adjacent"]
    telecom_jobs  = [j for j in jobs if j.get("profile_category") == "telecom"]

    # Count per matched profile
    profile_counts: dict = {}
    for job in jobs:
        profile = job.get("matched_profile", "")
        category = job.get("profile_category", "other")
        if profile:
            key = f"[{category}] {profile}"
            profile_counts[key] = profile_counts.get(key, 0) + 1

    # Surface the actual profiles so dashboard/logs can display them
    job_profiles = resume_data.get("job_profiles", {})

    return {
        "run_date": datetime.now().isoformat(),
        "candidate_name": resume_data.get("name", "Candidate"),
        "total_jobs_found": total,
        "high_priority_count": len(by_priority["high"]),
        "medium_priority_count": len(by_priority["medium"]),
        "low_priority_count": len(by_priority["low"]),
        "primary_profile_jobs": len(primary_jobs),
        "adjacent_profile_jobs": len(adjacent_jobs),
        "telecom_profile_jobs": len(telecom_jobs),
        "jobs_by_profile": dict(sorted(profile_counts.items(), key=lambda x: -x[1])),
        "primary_profiles":  [p.get("title") for p in job_profiles.get("primary_profiles", [])],
        "adjacent_profiles": [p.get("title") for p in job_profiles.get("adjacent_profiles", [])],
        "telecom_profiles":  [p.get("title") for p in job_profiles.get("telecom_profiles", [])],
        "by_source": by_source,
        "top_companies": dict(
            sorted(top_companies.items(), key=lambda x: -x[1])[:10]
        ),
        "top_5_jobs": [
            {
                "title": j.get("title"),
                "company": j.get("company"),
                "score": j.get("match_score"),
                "profile_category": j.get("profile_category", "other"),
                "matched_profile": j.get("matched_profile", ""),
                "url": j.get("url"),
            }
            for j in jobs[:5]
        ],
    }
