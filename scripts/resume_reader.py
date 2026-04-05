"""
resume_reader.py
Reads a DOCX resume and extracts structured information using Azure AI Foundry.
"""

import os
import re
import glob
import logging
from pathlib import Path
from typing import Optional
from docx import Document

logger = logging.getLogger(__name__)


def find_resume(folder: str, filename: str) -> Optional[str]:
    """Find resume DOCX file in the specified folder."""
    # Try exact filename first
    exact_path = os.path.join(folder, filename)
    if os.path.exists(exact_path):
        return exact_path

    # Auto-detect any DOCX file in folder
    docx_files = glob.glob(os.path.join(folder, "*.docx"))
    if not docx_files:
        logger.error(f"No .docx files found in {folder}")
        return None

    # Use most recently modified file
    docx_files.sort(key=os.path.getmtime, reverse=True)
    logger.info(f"Auto-detected resume: {docx_files[0]}")
    return docx_files[0]


def extract_text_from_docx(filepath: str) -> str:
    """Extract full text from a DOCX file, preserving document order and section structure."""
    try:
        from docx.text.paragraph import Paragraph as _Para
        from docx.table import Table as _Table
        doc = Document(filepath)
        full_text = []
        seen: set = set()

        def _add(text: str):
            if text and text not in seen:
                seen.add(text)
                full_text.append(text)

        # Iterate body elements in document order so tables stay in context
        for node in doc.element.body:
            tag = node.tag.split('}')[-1] if '}' in node.tag else node.tag
            if tag == 'p':
                para = _Para(node, doc)
                text = para.text.strip()
                if not text:
                    continue
                style_name = para.style.name if para.style else ""
                if any(style_name.startswith(h) for h in ("Heading", "Title", "Subtitle")):
                    # Mark section headers clearly so LLM can distinguish resume sections
                    marker = f"\n=== {text} ==="
                    if text not in seen:
                        seen.add(text)
                        full_text.append(marker)
                else:
                    _add(text)
            elif tag == 'tbl':
                table = _Table(node, doc)
                for row in table.rows:
                    row_parts = []
                    for cell in row.cells:
                        cell_text = cell.text.strip()
                        if cell_text:
                            row_parts.append(cell_text)
                    if row_parts:
                        combined = " | ".join(dict.fromkeys(row_parts))  # dedup cells
                        if combined not in seen:
                            seen.add(combined)
                            full_text.append(combined)

        return "\n".join(full_text)
    except Exception as e:
        logger.error(f"Failed to read DOCX file {filepath}: {e}")
        raise


def parse_resume_locally(text: str) -> dict:
    """
    Basic local parsing of resume text to extract key info.
    Used as fallback or to supplement LLM parsing.
    """
    resume_data = {
        "raw_text": text,
        "name": "",
        "email": "",
        "phone": "",
        "linkedin": "",
        "github": "",
        "location": "",
        "skills": [],
        "job_titles": [],
        "companies": [],
        "education": [],
        "years_experience": 0,
        "summary": "",
    }

    lines = text.split("\n")

    # Extract email
    email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
    emails = re.findall(email_pattern, text)
    if emails:
        resume_data["email"] = emails[0]

    # Extract phone
    phone_pattern = r'(\+?1?\s?)?(\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4})'
    phones = re.findall(phone_pattern, text)
    if phones:
        resume_data["phone"] = "".join(phones[0]).strip()

    # Extract LinkedIn
    linkedin_pattern = r'linkedin\.com/in/[\w-]+'
    linkedin = re.findall(linkedin_pattern, text, re.IGNORECASE)
    if linkedin:
        resume_data["linkedin"] = "https://" + linkedin[0]

    # Extract GitHub
    github_pattern = r'github\.com/[\w-]+'
    github = re.findall(github_pattern, text, re.IGNORECASE)
    if github:
        resume_data["github"] = "https://" + github[0]

    # Extract name (usually first non-empty line)
    for line in lines[:5]:
        if line and not any(c in line for c in ['@', 'http', 'linkedin', 'github']):
            if len(line.split()) <= 4 and not any(char.isdigit() for char in line):
                resume_data["name"] = line.strip()
                break

    # Common tech skills — use word-boundary matching to avoid false positives
    # (e.g. "r" must not match inside "developer", "Go" must not match inside "going")
    tech_skills = [
        "Python", "JavaScript", "TypeScript", "Java", "C++", "C#", "Go", "Rust", "Ruby",
        "PHP", "Swift", "Kotlin", "Scala", "MATLAB",
        "React", "Angular", "Vue", "Node.js", "Django", "Flask", "FastAPI", "Spring",
        "Express", "Next.js", "GraphQL", "REST", "gRPC",
        "AWS", "Azure", "GCP", "Docker", "Kubernetes", "Terraform", "Ansible",
        "CI/CD", "Jenkins", "GitHub Actions", "GitLab CI",
        "SQL", "MySQL", "PostgreSQL", "MongoDB", "Redis", "Elasticsearch",
        "Machine Learning", "Deep Learning", "TensorFlow", "PyTorch", "scikit-learn",
        "LLM", "NLP", "Computer Vision", "Data Science", "Data Engineering",
        "Spark", "Kafka", "Airflow", "dbt", "Databricks", "Snowflake",
        "Git", "Linux", "Agile", "Scrum", "Microservices", "DevOps",
        "HTML", "CSS", "Tailwind", "Bootstrap",
        "Product Management", "Project Management",
    ]
    # Ambiguous short names that need extra-strict whole-word matching
    short_skills = ["R", "Go", "C"]

    found_skills = []
    for skill in tech_skills:
        # Escape special regex chars (e.g. C++, C#)
        pattern = r'\b' + re.escape(skill) + r'\b'
        if re.search(pattern, text, re.IGNORECASE):
            found_skills.append(skill)
    for skill in short_skills:
        # Require the skill to stand alone (surrounded by spaces or punctuation)
        pattern = r'(?<![\w])' + re.escape(skill) + r'(?![\w])'
        if re.search(pattern, text):
            found_skills.append(skill)
    resume_data["skills"] = found_skills

    # Extract job titles from lines that look like role titles
    # (short lines with title-case words, no dates, no digits, not contact info)
    title_indicators = [
        "engineer", "developer", "analyst", "manager", "director", "architect",
        "scientist", "lead", "head", "principal", "staff", "vp", "cto", "ceo",
        "consultant", "specialist", "coordinator", "designer", "devops", "sre",
        "product", "program", "project", "data", "software", "backend", "frontend",
        "full stack", "fullstack", "cloud", "security", "ml", "ai",
    ]
    extracted_titles = []
    for line in lines:
        cleaned = line.strip()
        # Skip lines with excessive whitespace/tabs (common in docx table cells)
        if '\t' in cleaned or '  ' in cleaned:
            continue
        if (
            3 <= len(cleaned.split()) <= 7
            and not any(c in cleaned for c in ['@', 'http', '|', '\u2022', '+'])
            and not re.search(r'\d{4}', cleaned)   # no years
            and any(ind in cleaned.lower() for ind in title_indicators)
        ):
            extracted_titles.append(cleaned)
    resume_data["job_titles"] = extracted_titles[:10]

    # Estimate years of experience from dates
    year_pattern = r'\b(?:19|20)\d{2}\b'
    years_found = [int(y) for y in re.findall(year_pattern, text)]
    if years_found:
        import datetime
        current_year = datetime.datetime.now().year
        oldest_year = min(years_found)
        if oldest_year > 1990:
            resume_data["years_experience"] = current_year - oldest_year

    return resume_data


def analyze_resume_with_llm(resume_text: str, llm_client) -> dict:
    """
    Use Azure AI Foundry to deeply analyze the resume and extract structured data.
    """
    prompt = f"""Analyze this resume and extract structured information. Return ONLY valid JSON.

Resume:
{resume_text[:8000]}

Return a JSON object with these exact fields:
{{
  "name": "Full Name",
  "current_title": "Most recent job title",
  "target_roles": ["list of 5-8 job titles this person should target"],
  "seniority_level": "entry/mid/senior/lead/principal/staff/director/vp",
  "years_experience": number,
  "skills": {{
    "technical": ["list of technical skills"],
    "soft": ["list of soft skills"],
    "domains": ["industry domains e.g. fintech, healthcare, saas"]
  }},
  "education": [
    {{"degree": "degree name", "field": "field of study", "school": "school name"}}
  ],
  "industries": ["industries worked in"],
  "key_achievements": ["3-5 notable achievements from resume"],
  "search_keywords": ["15-20 keywords to use in job searches"],
  "preferred_company_types": ["startup/enterprise/mid-size/public/private/etc"],
  "summary": "2-3 sentence professional summary of this candidate",
  "work_history": [
    {{"title": "Job Title", "company": "Company Name", "duration": "e.g. 2 years"}}
  ]
}}"""

    try:
        response = llm_client.complete(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=2000,
        )

        content = response.choices[0].message.content.strip()

        # Clean up JSON if wrapped in markdown
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()

        import json
        parsed = json.loads(content)
        parsed["raw_text"] = resume_text
        return parsed

    except Exception as e:
        logger.error(f"LLM resume analysis failed: {e}")
        # Fall back to local parsing
        local_data = parse_resume_locally(resume_text)
        local_data["current_title"] = ""
        local_data["seniority_level"] = "mid"
        local_data["key_achievements"] = []
        local_data["preferred_company_types"] = ["any"]
        local_data["industries"] = []
        # Use extracted job titles as search keywords, not raw skills
        extracted_titles = local_data.get("job_titles", [])
        if extracted_titles:
            local_data["target_roles"] = extracted_titles[:8]
            local_data["search_keywords"] = extracted_titles[:8]
        else:
            local_data["target_roles"] = ["Software Engineer", "Software Developer"]
            local_data["search_keywords"] = ["Software Engineer", "Software Developer"]
        return local_data


def identify_job_profiles_with_llm(resume_data: dict, llm_client) -> dict:
    """
    Ask the LLM to produce THREE priority tiers of job profiles:

    1. primary_profiles   — roles OUTSIDE telecom that best leverage transferable skills
                            (leadership, enterprise sales, solutions architecture, etc.).
                            HIGH search priority.
    2. adjacent_profiles  — cross-industry PIVOT roles requiring upskilling.
                            MEDIUM search priority.  Includes skill_gaps.
    3. telecom_profiles   — telecom / 5G / carrier-specific roles where the background
                            is a direct match but telecom is NOT the target industry.
                            LOWEST search priority.

    Returns:
        {
          "primary_profiles":  [ {title, search_titles, seniority, keywords, rationale, fit_score} ],
          "adjacent_profiles": [ {title, search_titles, seniority, keywords, rationale, skill_gaps, fit_score} ],
          "telecom_profiles":  [ {title, search_titles, seniority, keywords, rationale, fit_score} ],
        }
    """
    import json

    skills_raw = resume_data.get("skills", {})
    if isinstance(skills_raw, dict):
        tech_skills = skills_raw.get("technical", [])[:20]
        domains     = skills_raw.get("domains", [])
    else:
        tech_skills = skills_raw[:20]
        domains     = []

    work_history = resume_data.get("work_history", [])
    work_history_str = "; ".join(
        f"{w.get('title','?')} at {w.get('company','?')} ({w.get('duration','')})"
        for w in work_history
    ) if work_history else "(not extracted)"
    local_job_titles = resume_data.get("job_titles", [])
    job_titles_str = ", ".join(local_job_titles) if local_job_titles else "(none extracted)"

    prompt = f"""You are a senior career advisor helping a candidate PIVOT AWAY from Telecom.

The candidate has strong telecom/5G experience but wants to BROADEN their search beyond telecom.
Your job is to identify THREE tiers of job profiles:
IMPORTANT: Also scan the candidate's full work history below — if they have held product management,
program management, technical account, or similar roles in ANY industry, those must be reflected
in primary_profiles (not just telecom roles).

TIER 1 – PRIMARY (high priority, NON-telecom industries):
  Roles in industries like enterprise SaaS, cloud, cybersecurity, AI/ML, professional services,
  healthcare tech, fintech, or general enterprise technology — where the candidate's transferable
  skills (leadership, solutions architecture, enterprise account management, service delivery,
  RFP/deal strategy, cross-functional team leadership) are a strong fit.
  Do NOT put telecom-specific roles here.
  fit_score: 70-95.

TIER 2 – ADJACENT (medium priority, stretch pivots):
  Roles that require meaningful upskilling or a career pivot but are achievable with 6-18 months
  of targeted effort. Could be in any industry. Include honest skill_gaps.
  fit_score: 50-69.

TIER 3 – TELECOM (lowest priority, apply only as fallback):
  Roles specifically in telecom, 5G, carrier, or networking vendor space. These are easy wins
  but NOT the primary goal. The candidate should only apply to these if tiers 1-2 produce nothing.
  fit_score: 75-95 (technically strong match, but lower search priority by design).

Candidate profile:
- Name: {resume_data.get('name', 'Candidate')}
- Current title: {resume_data.get('current_title', 'Unknown')}
- Seniority: {resume_data.get('seniority_level', 'mid')}
- Years of experience: {resume_data.get('years_experience', 0)}
- Technical skills: {', '.join(tech_skills)}
- Domains / industries: {', '.join(domains + resume_data.get('industries', []))}
- Education: {json.dumps(resume_data.get('education', []))}
- Key achievements: {'; '.join(resume_data.get('key_achievements', [])[:5])}
- Professional summary: {resume_data.get('summary', '')}
- Full work history: {work_history_str}
- Extracted job title lines: {job_titles_str}

Return ONLY valid JSON with this exact structure:
{{
  "primary_profiles": [
    {{
      "title": "Non-telecom role title",
      "industry": "Target industry (e.g. Enterprise SaaS, Cybersecurity, HealthTech)",
      "search_titles": ["Exact title variant 1", "Exact title variant 2", "Exact title variant 3"],
      "seniority": "senior",
      "keywords": ["keyword1", "keyword2", "keyword3"],
      "rationale": "One sentence: which transferable skills make this a strong fit",
      "transferable_skills": ["Skill A", "Skill B"],
      "fit_score": 85
    }}
  ],
  "adjacent_profiles": [
    {{
      "title": "Pivot role title",
      "industry": "Target industry",
      "search_titles": ["Exact title variant 1", "Exact title variant 2"],
      "seniority": "senior",
      "keywords": ["keyword1", "keyword2"],
      "rationale": "One sentence: why this is achievable with upskilling",
      "skill_gaps": ["Gap 1", "Gap 2"],
      "upskilling_path": "Brief: what cert/experience would close the gap",
      "fit_score": 60
    }}
  ],
  "telecom_profiles": [
    {{
      "title": "Telecom-specific role",
      "industry": "Telecom / Networking",
      "search_titles": ["Exact title variant 1", "Exact title variant 2"],
      "seniority": "senior",
      "keywords": ["keyword1", "keyword2"],
      "rationale": "One sentence: direct match but lowest priority",
      "fit_score": 90
    }}
  ]
}}

Rules:
- primary_profiles: 3-5 roles, ALL in non-telecom industries, ordered by fit_score desc.
- adjacent_profiles: 3-4 roles, any industry, with honest skill_gaps and upskilling_path.
- telecom_profiles: 2-3 roles max — the easy fallback options only.
- search_titles must be exact job-board-style titles (what you'd type into LinkedIn search).
- Be specific (e.g. "Senior Enterprise Account Executive - SaaS" not "Account Executive")."""

    try:
        result = llm_client.complete_json(prompt, max_tokens=2000)
        logger.info(
            f"Job profile identification complete: "
            f"{len(result.get('primary_profiles', []))} primary (non-telecom), "
            f"{len(result.get('adjacent_profiles', []))} adjacent (pivot), "
            f"{len(result.get('telecom_profiles', []))} telecom (lowest priority)"
        )
        return result
    except Exception as e:
        logger.warning(f"Job profile identification failed: {e}")
        return {"primary_profiles": [], "adjacent_profiles": [], "telecom_profiles": []}


def load_resume(config: dict, llm_client=None) -> dict:
    """
    Main entry point: load and parse resume from config.
    Returns structured resume data dict.
    """
    folder = config["resume"]["folder"]
    filename = config["resume"]["filename"]

    # Resolve relative paths against the config file location (app root),
    # not the process CWD (which may differ when run from dashboard thread).
    if not os.path.isabs(folder):
        config_dir = config.get("_config_dir", os.getcwd())
        folder = os.path.normpath(os.path.join(config_dir, folder))

    # Create folder if it doesn't exist
    os.makedirs(folder, exist_ok=True)

    resume_path = find_resume(folder, filename)
    if not resume_path:
        logger.warning(f"No resume found in {folder}. Creating placeholder.")
        return _create_placeholder_resume()

    logger.info(f"Loading resume from: {resume_path}")
    resume_text = extract_text_from_docx(resume_path)

    if not resume_text.strip():
        logger.error("Resume appears to be empty")
        return _create_placeholder_resume()

    logger.info(f"Resume text extracted: {len(resume_text)} characters")

    if llm_client:
        logger.info("Analyzing resume with Azure AI Foundry...")
        resume_data = analyze_resume_with_llm(resume_text, llm_client)

        # ── Identify primary and adjacent job profiles ──────────────
        logger.info("Identifying primary and adjacent job profiles with LLM...")
        job_profiles = identify_job_profiles_with_llm(resume_data, llm_client)
        resume_data["job_profiles"] = job_profiles

        # Flatten profile search_titles into target_roles.
        # Order: primary (non-telecom) first, then adjacent pivots, telecom last.
        all_search_titles = []
        for p in job_profiles.get("primary_profiles", []):
            all_search_titles.extend(p.get("search_titles", [p.get("title", "")]))
        for p in job_profiles.get("adjacent_profiles", []):
            all_search_titles.extend(p.get("search_titles", [p.get("title", "")]))
        for p in job_profiles.get("telecom_profiles", []):
            all_search_titles.extend(p.get("search_titles", [p.get("title", "")]))
        if all_search_titles:
            seen = set()
            resume_data["target_roles"] = [
                t for t in all_search_titles if not (t in seen or seen.add(t))
            ]
    else:
        logger.info("Parsing resume locally (no LLM client)")
        resume_data = parse_resume_locally(resume_text)
        resume_data["current_title"] = ""
        resume_data["seniority_level"] = "mid"
        resume_data["job_profiles"] = {}

        # Use extracted job titles as search keywords (not raw skills).
        # Job titles produce meaningful job-board results; skills alone do not.
        extracted_titles = resume_data.get("job_titles", [])
        if extracted_titles:
            resume_data["target_roles"] = extracted_titles[:8]
            resume_data["search_keywords"] = extracted_titles[:8]
            logger.info(
                f"Local fallback: using {len(extracted_titles)} extracted job titles "
                f"as search keywords: {extracted_titles[:4]}"
            )
        else:
            # Last resort: generic engineering roles
            resume_data["target_roles"] = ["Software Engineer", "Software Developer"]
            resume_data["search_keywords"] = ["Software Engineer", "Software Developer"]
            logger.warning("Could not extract job titles from resume; using generic fallback keywords")

    return resume_data


def save_search_profiles(resume_data: dict, output_dir: str = "./data") -> str:
    """
    Write the identified search profiles (primary + adjacent) and resume
    summary to a human-readable JSON file in the output directory.
    Returns the path to the saved file.
    """
    import json
    from datetime import datetime

    os.makedirs(output_dir, exist_ok=True)
    filepath = os.path.join(output_dir, "search_profiles.json")

    skills_raw = resume_data.get("skills", {})
    if isinstance(skills_raw, dict):
        tech_skills = skills_raw.get("technical", [])
    else:
        tech_skills = skills_raw

    job_profiles = resume_data.get("job_profiles", {})
    primary  = job_profiles.get("primary_profiles", [])
    adjacent = job_profiles.get("adjacent_profiles", [])
    telecom  = job_profiles.get("telecom_profiles", [])

    document = {
        "generated_at": datetime.now().isoformat(),
        "candidate": {
            "name": resume_data.get("name", ""),
            "current_title": resume_data.get("current_title", ""),
            "seniority_level": resume_data.get("seniority_level", ""),
            "years_experience": resume_data.get("years_experience", 0),
            "summary": resume_data.get("summary", ""),
            "technical_skills": tech_skills,
        },
        "search_strategy": (
            "Priority 1 (HIGH): Non-telecom roles leveraging transferable skills. "
            "Priority 2 (MEDIUM): Cross-industry pivot roles requiring upskilling. "
            "Priority 3 (LOW): Telecom/5G roles — fallback only."
        ),
        "search_profiles": {
            "priority_1_primary_non_telecom": primary,
            "priority_2_adjacent_pivots": adjacent,
            "priority_3_telecom_fallback": telecom,
        },
        "target_roles": resume_data.get("target_roles", []),
        "search_keywords": resume_data.get("search_keywords", []),
    }

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(document, f, indent=2, ensure_ascii=False)

    logger.info(f"Search profiles saved to {filepath}")
    return filepath


def _create_placeholder_resume() -> dict:
    """Return placeholder data when no resume is found."""
    return {
        "name": "Candidate",
        "current_title": "Software Engineer",
        "target_roles": ["Software Engineer", "Backend Engineer", "Full Stack Engineer"],
        "seniority_level": "mid",
        "years_experience": 3,
        "skills": {"technical": ["Python", "JavaScript"], "soft": [], "domains": []},
        "education": [],
        "industries": ["Technology"],
        "key_achievements": [],
        "search_keywords": ["software engineer", "developer", "python", "javascript"],
        "preferred_company_types": ["any"],
        "summary": "Software engineer seeking new opportunities.",
        "raw_text": "",
    }
