"""
job_scraper.py
Scrapes jobs from company career pages and public job boards.
Uses requests + BeautifulSoup for static pages.
"""

import re
import time
import logging
import requests
from datetime import datetime, timedelta
from typing import Optional
from bs4 import BeautifulSoup
from urllib.parse import urljoin, quote_plus, urlparse
import json

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

REQUEST_DELAY = 1.5  # seconds between requests


def make_request(url: str, params: dict = None, timeout: int = 15) -> Optional[requests.Response]:
    """Make a polite HTTP request with error handling."""
    try:
        time.sleep(REQUEST_DELAY)
        resp = requests.get(url, headers=HEADERS, params=params, timeout=timeout)
        resp.raise_for_status()
        return resp
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 429:
            logger.warning(f"Rate limited on {url}. Waiting 30s...")
            time.sleep(30)
        else:
            logger.warning(f"HTTP {e.response.status_code} for {url}")
    except requests.exceptions.Timeout:
        logger.warning(f"Timeout fetching {url}")
    except requests.exceptions.ConnectionError:
        logger.warning(f"Connection error for {url}")
    except Exception as e:
        logger.warning(f"Request failed for {url}: {e}")
    return None


def clean_text(text: str) -> str:
    """Clean whitespace from scraped text."""
    return re.sub(r'\s+', ' ', text).strip()


# Words to ignore when doing flexible title bigram matching
_TITLE_STOPWORDS = frozenset({
    "senior", "principal", "junior", "lead", "chief", "director", "head",
    "vp", "svp", "evp", "of", "the", "a", "an", "and", "in", "at", "for",
    "b2b", "saas", "cloud", "enterprise", "global", "platform", "systems",
})

# High-value 2-gram role phrases used by _core_role_query
_ROLE_NGRAMS = [
    "solutions architect", "customer success", "technical program manager",
    "program manager", "product manager", "professional services",
    "sales engineer", "revenue operations", "cloud operations",
    "strategy operations", "network architect", "5g",
]


def _flexible_title_match(title: str, profile_keywords: list[str]) -> bool:
    """
    Return True if a job title plausibly matches any profile keyword.
    Handles variations like "Director, Customer Success" matching
    "Director of Customer Success - Enterprise SaaS" via bigram overlap.
    """
    title_norm = re.sub(r'[-–,/]', ' ', title.lower())

    for kw in profile_keywords:
        kw_norm = re.sub(r'[-–,/]', ' ', kw.lower())

        # 1. Direct substring match
        if kw_norm in title_norm or title_norm in kw_norm:
            return True

        # 2. Bigram match: every 2-word chunk from the keyword vs the title
        words = [w for w in kw_norm.split() if w not in _TITLE_STOPWORDS and len(w) > 2]
        for i in range(len(words) - 1):
            bigram = f"{words[i]} {words[i + 1]}"
            if bigram in title_norm:
                return True

        # 3. Single distinctive word match (>6 chars avoids false positives)
        for w in words:
            if len(w) > 6 and w in title_norm:
                return True

    return False


def _core_role_query(keywords: list[str]) -> str:
    """
    Extract a short 1-3 word search query from profile title keywords.
    Used for Remotive's search parameter.
    """
    combined = " ".join(keywords).lower()
    for ng in _ROLE_NGRAMS:
        if ng in combined:
            return ng
    # Fallback: first keyword, first 3 words
    if keywords:
        return " ".join(keywords[0].split()[:3])
    return ""


# ─── Job Board Scrapers ────────────────────────────────────────────────────────

def scrape_indeed(keywords: list[str], location: str, max_jobs: int = 25) -> list[dict]:
    """Scrape Indeed job listings."""
    jobs = []
    query = " ".join(keywords[:5])

    url = "https://www.indeed.com/jobs"
    params = {
        "q": query,
        "l": location,
        "sort": "date",
        "fromage": "7",  # Jobs from last 7 days
    }

    logger.info(f"Searching Indeed: '{query}' in '{location}'")
    resp = make_request(url, params=params)
    if not resp:
        return jobs

    soup = BeautifulSoup(resp.text, "lxml")
    job_cards = soup.find_all("div", class_=re.compile(r"job_seen_beacon|resultContent"))

    for card in job_cards[:max_jobs]:
        try:
            title_el = card.find("h2", class_=re.compile(r"jobTitle"))
            company_el = card.find(attrs={"data-testid": "company-name"}) or \
                         card.find("span", class_=re.compile(r"companyName"))
            location_el = card.find(attrs={"data-testid": "text-location"}) or \
                          card.find("div", class_=re.compile(r"companyLocation"))
            desc_el = card.find("div", class_=re.compile(r"job-snippet|snippet"))

            title = clean_text(title_el.get_text()) if title_el else "Unknown Title"
            company = clean_text(company_el.get_text()) if company_el else "Unknown Company"
            loc = clean_text(location_el.get_text()) if location_el else location
            desc = clean_text(desc_el.get_text()) if desc_el else ""

            # Get job URL
            link_el = card.find("a", href=re.compile(r"/rc/clk|/pagead/clk"))
            job_url = ""
            if link_el:
                href = link_el.get("href", "")
                if href.startswith("/"):
                    job_url = "https://www.indeed.com" + href
                else:
                    job_url = href

            jobs.append({
                "id": f"indeed_{hash(title + company)}",
                "title": title,
                "company": company,
                "location": loc,
                "description": desc,
                "url": job_url,
                "source": "Indeed",
                "posted_date": datetime.now().strftime("%Y-%m-%d"),
                "remote": "remote" in loc.lower() or "remote" in title.lower(),
            })
        except Exception as e:
            logger.debug(f"Failed to parse Indeed card: {e}")
            continue

    logger.info(f"Indeed: found {len(jobs)} jobs")
    return jobs


def scrape_linkedin(keywords: list[str], location: str, max_jobs: int = 25) -> list[dict]:
    """Scrape LinkedIn job listings via guest jobs API (no login required)."""
    jobs = []
    query = " ".join(keywords[:4])

    # The jobs-guest API returns parseable HTML fragments without requiring a browser
    url = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
    params = {
        "keywords": query,
        "location": location,
        "sortBy": "DD",  # Date posted
        "f_TPR": "r604800",  # Last 7 days
        "start": 0,
    }

    logger.info(f"Searching LinkedIn: '{query}' in '{location}'")
    resp = make_request(url, params=params)
    if not resp:
        return jobs

    soup = BeautifulSoup(resp.text, "lxml")
    job_cards = soup.find_all(
        "div",
        class_=re.compile(r"base-card|result-card|job-search-card")
    )

    for card in job_cards[:max_jobs]:
        try:
            title_el = card.find("h3", class_=re.compile(r"base-search-card__title"))
            company_el = card.find("h4", class_=re.compile(r"base-search-card__subtitle"))
            location_el = card.find("span", class_=re.compile(r"job-search-card__location"))
            link_el = card.find("a", class_=re.compile(r"base-card__full-link"))

            title = clean_text(title_el.get_text()) if title_el else "Unknown Title"
            company = clean_text(company_el.get_text()) if company_el else "Unknown Company"
            loc = clean_text(location_el.get_text()) if location_el else location
            job_url = link_el.get("href", "") if link_el else ""

            jobs.append({
                "id": f"linkedin_{hash(title + company)}",
                "title": title,
                "company": company,
                "location": loc,
                "description": f"LinkedIn job: {title} at {company}",
                "url": job_url,
                "source": "LinkedIn",
                "posted_date": datetime.now().strftime("%Y-%m-%d"),
                "remote": "remote" in loc.lower() or "remote" in title.lower(),
            })
        except Exception as e:
            logger.debug(f"Failed to parse LinkedIn card: {e}")
            continue

    logger.info(f"LinkedIn: found {len(jobs)} jobs")
    return jobs


def scrape_themuse(keywords: list[str], location: str, max_jobs: int = 20) -> list[dict]:
    """Scrape The Muse public API (free, no auth required)."""
    import html as html_module
    jobs = []
    keyword_lower = [k.lower() for k in keywords]

    # Select Muse categories based on keyword content
    combined_kw = " ".join(keyword_lower)
    categories = set()
    if any(w in combined_kw for w in ["architect", "engineer", "technical", "cloud", "program manager", "tpm"]):
        categories.add("Engineering")
    if any(w in combined_kw for w in ["product manager", "program manager", "tpm", "revenue", "operations"]):
        categories.add("Product & Management")
    if any(w in combined_kw for w in ["sales", "customer success", "revenue oper"]):
        categories.add("Sales & Business Development")
        categories.add("Customer Service")
    if any(w in combined_kw for w in ["professional services", "consulting", "strategy", "director"]):
        categories.add("Management & Operations")
    if not categories:
        categories.add("Engineering")

    url = "https://www.themuse.com/api/public/jobs"
    seen_ids = set()

    for category in list(categories)[:4]:
        if len(jobs) >= max_jobs:
            break
        params = {
            "category": category,
            "level": ["Senior Level", "Management, Director, and Executive"],
            "page": 0,
            "descending": "true",
        }
        logger.info(f"Searching The Muse: category='{category}'")
        try:
            resp = make_request(url, params=params)
            if not resp:
                continue
            data = resp.json()
            results = data.get("results", [])

            for result in results:
                if len(jobs) >= max_jobs:
                    break
                job_id = result.get("id")
                if job_id in seen_ids:
                    continue
                seen_ids.add(job_id)

                title = result.get("name", "")
                company = result.get("company", {}).get("name", "")
                locations = [loc.get("name", "") for loc in result.get("locations", [])]
                location_str = locations[0] if locations else "Remote"
                job_url = result.get("refs", {}).get("landing_page", "")
                pub_date = result.get("publication_date", "")[:10]

                # Flexible title match against profile keywords (handles variants)
                if not _flexible_title_match(title, keywords):
                    continue

                # Accept US, Boston, MA, or remote locations
                loc_lower = location_str.lower()
                if not any(loc in loc_lower for loc in [
                    "boston", "massachusetts", ", ma", "remote",
                    "united states", "nationwide", "anywhere", "us", " ma "
                ]):
                    continue

                # Strip HTML from description
                raw_contents = html_module.unescape(result.get("contents", ""))
                desc = BeautifulSoup(raw_contents, "lxml").get_text()[:1000] if raw_contents else ""

                jobs.append({
                    "id": f"muse_{job_id}",
                    "title": title,
                    "company": company,
                    "location": location_str,
                    "description": desc,
                    "url": job_url,
                    "source": "The Muse",
                    "posted_date": pub_date,
                    "remote": "remote" in loc_lower or "anywhere" in loc_lower,
                })
        except Exception as e:
            logger.warning(f"The Muse scrape failed for category '{category}': {e}")

    logger.info(f"The Muse: found {len(jobs)} jobs")
    return jobs[:max_jobs]


def scrape_remotive(keywords: list[str], location: str, max_jobs: int = 20) -> list[dict]:
    """Scrape Remotive public API (free, no auth required, remote jobs only)."""
    jobs = []

    # Extract a short, focused search query (Remotive doesn't handle long phrases well)
    query = _core_role_query(keywords)
    if not query:
        return jobs

    url = "https://remotive.com/api/remote-jobs"
    params = {"search": query, "limit": max_jobs * 3}

    logger.info(f"Searching Remotive: '{query}'")
    try:
        resp = make_request(url, params=params)
        if not resp:
            return jobs

        data = resp.json()
        results = data.get("jobs", [])

        for result in results[:max_jobs * 2]:
            if len(jobs) >= max_jobs:
                break
            title = result.get("title", "")
            company = result.get("company_name", "")
            job_url = result.get("url", "")
            pub_date = result.get("publication_date", "")[:10]

            # Flexible title match against profile keywords
            if not _flexible_title_match(title, keywords):
                continue

            desc_html = result.get("description", "")
            desc = BeautifulSoup(desc_html, "lxml").get_text()[:1000] if desc_html else ""

            jobs.append({
                "id": f"remotive_{result.get('id', abs(hash(title + company)))}",
                "title": title,
                "company": company,
                "location": "Remote",
                "description": desc,
                "url": job_url,
                "source": "Remotive",
                "posted_date": pub_date,
                "remote": True,
            })
    except Exception as e:
        logger.warning(f"Remotive scrape failed: {e}")

    logger.info(f"Remotive: found {len(jobs)} jobs")
    return jobs[:max_jobs]


def scrape_builtin(keywords: list[str], location: str, max_jobs: int = 20) -> list[dict]:
    """Scrape Built In tech job board."""
    jobs = []

    # Built In has city-specific URLs
    city_map = {
        "boston": "https://builtin.com/jobs/boston",
        "new york": "https://builtin.com/jobs/nyc",
        "san francisco": "https://builtin.com/jobs/san-francisco",
        "chicago": "https://builtin.com/jobs/chicago",
        "seattle": "https://builtin.com/jobs/seattle",
        "austin": "https://builtin.com/jobs/austin",
        "denver": "https://builtin.com/jobs/colorado",
        "remote": "https://builtin.com/jobs/remote",
    }

    location_lower = location.lower()
    base_url = next(
        (url for city, url in city_map.items() if city in location_lower),
        "https://builtin.com/jobs"
    )

    query = keywords[0] if keywords else "software engineer"
    url = f"{base_url}?keyword={quote_plus(query)}"

    logger.info(f"Searching Built In: '{query}'")
    resp = make_request(url)
    if not resp:
        return jobs

    soup = BeautifulSoup(resp.text, "lxml")
    job_cards = soup.find_all("div", attrs={"data-id": True}) or \
                soup.find_all("article", class_=re.compile(r"job"))

    for card in job_cards[:max_jobs]:
        try:
            title_el = card.find(["h2", "h3"], class_=re.compile(r"job|title"))
            company_el = card.find("span", class_=re.compile(r"company"))
            loc_el = card.find("span", class_=re.compile(r"location"))
            link_el = card.find("a", href=True)

            title = clean_text(title_el.get_text()) if title_el else "Tech Role"
            company = clean_text(company_el.get_text()) if company_el else "Company"
            loc = clean_text(loc_el.get_text()) if loc_el else location
            href = link_el.get("href", "") if link_el else ""
            job_url = urljoin("https://builtin.com", href) if href.startswith("/") else href

            if title and company:
                jobs.append({
                    "id": f"builtin_{hash(title + company)}",
                    "title": title,
                    "company": company,
                    "location": loc,
                    "description": f"Built In listing: {title} at {company}",
                    "url": job_url,
                    "source": "Built In",
                    "posted_date": datetime.now().strftime("%Y-%m-%d"),
                    "remote": "remote" in loc.lower() or "remote" in title.lower(),
                })
        except Exception as e:
            logger.debug(f"Built In parse error: {e}")

    logger.info(f"Built In: found {len(jobs)} jobs")
    return jobs


def _kw_filter(jobs: list, keywords: list) -> list:
    """Keep only jobs whose title contains at least one keyword (case-insensitive)."""
    if not keywords or not jobs:
        return jobs
    kw_lower = [k.lower() for k in keywords]
    filtered = [j for j in jobs if any(kw in j["title"].lower() for kw in kw_lower)]
    return filtered if filtered else jobs  # if nothing passes, return all


def _parse_greenhouse_soup(soup, company_name: str, base_url: str) -> list:
    """Parse Greenhouse job board HTML — supports both classic (div.opening) and new (tr.job-post) layouts."""
    jobs = []
    # New layout (2024+): tr.job-post rows
    rows = soup.find_all("tr", class_="job-post")
    if rows:
        for row in rows:
            a = row.find("a", href=True)
            if not a:
                continue
            title_p = row.find("p", class_=lambda c: c and "body--medium" in c)
            loc_p = row.find("p", class_=lambda c: c and "body--metadata" in c)
            title = clean_text(title_p.get_text()) if title_p else clean_text(a.get_text())
            loc = clean_text(loc_p.get_text()) if loc_p else "See listing"
            href = a.get("href", "")
            jobs.append(_make_job(title, company_name, loc, href or urljoin(base_url, href), company_name))
    else:
        # Classic layout: div.opening
        for section in soup.find_all("div", class_="opening"):
            title_el = section.find("a")
            if title_el:
                title = clean_text(title_el.get_text())
                href = title_el.get("href", "")
                loc_el = section.find("span", class_="location")
                loc = clean_text(loc_el.get_text()) if loc_el else "See listing"
                jobs.append(_make_job(title, company_name, loc, urljoin(base_url, href), company_name))
    return jobs


def _parse_lever_soup(soup, company_name: str, base_url: str) -> list:
    jobs = []
    for posting in soup.find_all("div", class_=re.compile(r"posting")):
        title_el = posting.find("h5") or posting.find("a", class_=re.compile(r"posting-name"))
        if title_el:
            title = clean_text(title_el.get_text())
            link_el = posting.find("a", href=re.compile(r"/apply|lever\.co"))
            href = link_el.get("href", "") if link_el else ""
            loc_el = posting.find("span", class_=re.compile(r"location|workplace"))
            loc = clean_text(loc_el.get_text()) if loc_el else "See listing"
            jobs.append(_make_job(title, company_name, loc, href or base_url, company_name))
    return jobs


def _scrape_microsoft_jobs(company_name: str, keywords: list, max_jobs: int) -> list:
    """Scrape Microsoft via their public JSON search API."""
    jobs, seen = [], set()
    for kw in (keywords or ["product manager"])[:4]:
        try:
            resp = make_request(
                "https://jobs.microsoft.com/api/jobs/search",
                params={"l": "en_us", "keywords": kw, "pg": 1, "pgSz": 20},
            )
            if not resp:
                continue
            for job in resp.json().get("operationResult", {}).get("result", {}).get("jobs", []):
                title = job.get("title", "")
                jid = job.get("jobId", "")
                if not title or jid in seen:
                    continue
                seen.add(jid)
                props = job.get("properties", {})
                loc = props.get("primaryLocation", "")
                link = props.get("applyLink", f"https://jobs.microsoft.com/en-us/job/{jid}")
                jobs.append(_make_job(title, company_name, loc, link, company_name))
                if len(jobs) >= max_jobs:
                    return jobs
        except Exception as e:
            logger.warning(f"Microsoft API error for '{kw}': {e}")
    return jobs[:max_jobs]


def _scrape_amazon_jobs(company_name: str, keywords: list, max_jobs: int) -> list:
    """Scrape Amazon via their public JSON search API."""
    jobs, seen = [], set()
    for kw in (keywords or ["product manager"])[:4]:
        try:
            resp = make_request(
                "https://www.amazon.jobs/en/search.json",
                params={"base_query": kw, "loc_query": "", "country": "USA", "result_limit": 20},
            )
            if not resp:
                continue
            for job in resp.json().get("jobs", []):
                title = job.get("title", "")
                jid = str(job.get("id", ""))
                if not title or jid in seen:
                    continue
                seen.add(jid)
                loc = job.get("location", "")
                path = job.get("job_path", "")
                # job_path links to the listing page; url_next_step is the apply URL
                link = f"https://www.amazon.jobs{path}" if path else job.get("url_next_step", "https://www.amazon.jobs")
                jobs.append(_make_job(title, company_name, loc, link, company_name))
                if len(jobs) >= max_jobs:
                    return jobs
        except Exception as e:
            logger.warning(f"Amazon API error for '{kw}': {e}")
    return jobs[:max_jobs]


def _scrape_netflix_jobs(company_name: str, keywords: list, max_jobs: int) -> list:
    """Netflix now uses JS-rendered iCIMS at explore.jobs.netflix.net — fall through to generic scraper."""
    logger.info(f"{company_name}: career site is JS-rendered; generic HTML fallback will be used")
    return []  # signals caller to fall through to generic HTML scrape


def _scrape_google_jobs(company_name: str, keywords: list, max_jobs: int) -> list:
    """Scrape Google via their careers search API."""
    jobs, seen = [], set()
    for kw in (keywords or ["product manager"])[:4]:
        try:
            resp = make_request(
                "https://careers.google.com/api/v3/search/",
                params={"query": kw, "language": "en_US", "pageSize": 20},
            )
            if not resp:
                continue
            for job in resp.json().get("jobs", []):
                title = job.get("title", "")
                jid = job.get("jobId", "")
                if not title or jid in seen:
                    continue
                seen.add(jid)
                locs = job.get("locations", [])
                loc = locs[0] if locs else ""
                link = job.get("applyUrl", f"https://careers.google.com/jobs/results/{jid}")
                jobs.append(_make_job(title, company_name, loc, link, company_name))
                if len(jobs) >= max_jobs:
                    return jobs
        except Exception as e:
            logger.warning(f"Google careers API error for '{kw}': {e}")
    return jobs[:max_jobs]


def scrape_company_careers(company: dict, keywords: list[str], max_jobs: int = 15) -> list[dict]:
    """
    Scrape a company's career page.
    Priority:
      1. Known JSON APIs (Microsoft, Amazon, Netflix, Google)
      2. Direct Greenhouse / Lever boards
      3. Embedded Greenhouse / Lever board detection in page HTML
      4. Generic HTML link fallback
    """
    company_name = company["name"]
    url = company["careers_url"]
    domain = urlparse(url).netloc.lower()
    jobs = []

    logger.info(f"Scraping {company_name} careers: {url}")

    # ── 1. Known JSON API routes ───────────────────────
    if "jobs.microsoft.com" in url:
        jobs = _scrape_microsoft_jobs(company_name, keywords, max_jobs)
    elif "amazon.jobs" in url:
        jobs = _scrape_amazon_jobs(company_name, keywords, max_jobs)
    elif "jobs.netflix.com" in url or "explore.jobs.netflix.net" in url:
        jobs = _scrape_netflix_jobs(company_name, keywords, max_jobs)  # no-op; falls to HTML
    elif "careers.google.com" in url:
        jobs = _scrape_google_jobs(company_name, keywords, max_jobs)
    else:
        # ── 2. Direct Greenhouse / Lever board ────────
        resp = make_request(url)
        if not resp:
            logger.warning(f"{company_name}: no response from {url}")
            return []
        soup = BeautifulSoup(resp.text, "lxml")

        if "greenhouse" in domain or "boards.greenhouse.io" in url:
            jobs = _parse_greenhouse_soup(soup, company_name, url)

        elif "lever.co" in domain or "jobs.lever.co" in url:
            jobs = _parse_lever_soup(soup, company_name, url)

        else:
            # ── 3. Embedded ATS detection ──────────────
            gh_match = re.search(r'boards\.greenhouse\.io/([A-Za-z0-9_-]+)', resp.text)
            lever_match = re.search(r'jobs\.lever\.co/([A-Za-z0-9_-]+)', resp.text)

            if gh_match:
                gh_url = f"https://boards.greenhouse.io/{gh_match.group(1)}"
                logger.info(f"{company_name}: embedded Greenhouse board → {gh_url}")
                gh_resp = make_request(gh_url)
                if gh_resp:
                    jobs = _parse_greenhouse_soup(
                        BeautifulSoup(gh_resp.text, "lxml"), company_name, gh_url
                    )

            elif lever_match:
                lever_url = f"https://jobs.lever.co/{lever_match.group(1)}"
                logger.info(f"{company_name}: embedded Lever board → {lever_url}")
                lv_resp = make_request(lever_url)
                if lv_resp:
                    jobs = _parse_lever_soup(
                        BeautifulSoup(lv_resp.text, "lxml"), company_name, lever_url
                    )

            if not jobs:
                # ── 4. Generic HTML link fallback ──────
                seen_urls: set = set()
                skip_phrases = {"view all", "see all", "load more", "next", "previous", "back",
                                "privacy", "contact", "about", "home", "team"}
                for link in soup.find_all("a", href=re.compile(
                    r"/jobs?/|/careers?/|/positions?/|/openings?/|/roles?/",
                    re.IGNORECASE
                ))[:max_jobs * 3]:
                    title = clean_text(link.get_text())
                    href = link.get("href", "")
                    full_url = urljoin(url, href)
                    if (
                        title and 6 < len(title) < 100
                        and full_url not in seen_urls
                        and not any(p in title.lower() for p in skip_phrases)
                        and (not keywords or any(kw.lower() in title.lower() for kw in keywords))
                    ):
                        seen_urls.add(full_url)
                        jobs.append(_make_job(title, company_name, "See listing", full_url, company_name))

    jobs = _kw_filter(jobs, keywords)
    logger.info(f"{company_name}: found {len(jobs[:max_jobs])} relevant jobs")
    return jobs[:max_jobs]


def _make_job(title: str, company: str, location: str, url: str, source: str) -> dict:
    """Helper to create a job dict."""
    return {
        "id": f"{source.lower().replace(' ', '_')}_{abs(hash(title + company))}",
        "title": title,
        "company": company,
        "location": location,
        "description": f"{title} at {company}",
        "url": url,
        "source": source,
        "posted_date": datetime.now().strftime("%Y-%m-%d"),
        "remote": "remote" in location.lower() or "remote" in title.lower(),
    }


def fetch_job_description(job_url: str) -> str:
    """Fetch full job description from a job URL."""
    if not job_url:
        return ""

    resp = make_request(job_url, timeout=10)
    if not resp:
        return ""

    soup = BeautifulSoup(resp.text, "lxml")

    # Remove navigation, headers, footers
    for tag in soup(["nav", "header", "footer", "script", "style", "aside"]):
        tag.decompose()

    # Try common job description containers
    for selector in [
        ("div", re.compile(r"job.?description|jobDescription|job-details|posting")),
        ("section", re.compile(r"description|content")),
        ("article", None),
    ]:
        tag, class_re = selector
        if class_re:
            el = soup.find(tag, class_=class_re)
        else:
            el = soup.find(tag)
        if el:
            return clean_text(el.get_text())[:3000]

    # Fall back to main content
    main = soup.find("main") or soup.find("body")
    if main:
        return clean_text(main.get_text())[:3000]

    return ""


def deduplicate_jobs(jobs: list[dict]) -> list[dict]:
    """Remove duplicate jobs based on title + company similarity."""
    seen = set()
    unique_jobs = []

    for job in jobs:
        key = f"{job['title'].lower().strip()}_{job['company'].lower().strip()}"
        # Normalize key
        key = re.sub(r'\s+', ' ', key)
        if key not in seen:
            seen.add(key)
            unique_jobs.append(job)

    return unique_jobs


def _tag_job_with_profile(
    job: dict,
    primary_profiles: list,
    adjacent_profiles: list,
    telecom_profiles: list,
) -> dict:
    """
    Tag a job with profile_category ('primary', 'adjacent', 'telecom', or 'other')
    and matched_profile. Primary beats adjacent beats telecom.
    """
    title_lower = job.get("title", "").lower()
    for profile in primary_profiles:
        for st in profile.get("search_titles", [profile.get("title", "")]):
            if st.lower() in title_lower or title_lower in st.lower():
                job["profile_category"] = "primary"
                job["matched_profile"] = profile.get("title", "")
                return job
    for profile in adjacent_profiles:
        for st in profile.get("search_titles", [profile.get("title", "")]):
            if st.lower() in title_lower or title_lower in st.lower():
                job["profile_category"] = "adjacent"
                job["matched_profile"] = profile.get("title", "")
                return job
    for profile in telecom_profiles:
        for st in profile.get("search_titles", [profile.get("title", "")]):
            if st.lower() in title_lower or title_lower in st.lower():
                job["profile_category"] = "telecom"
                job["matched_profile"] = profile.get("title", "")
                return job
    job["profile_category"] = "other"
    job["matched_profile"] = ""
    return job


def search_all_sources(config: dict, resume_data: dict) -> list[dict]:
    """
    Main job search orchestrator.
    Searches all enabled sources and returns deduplicated job list.
    """
    all_jobs = []
    search_config = config.get("search", {})
    locations = search_config.get("locations") or [search_config.get("location", "Remote")]
    max_per_source = search_config.get("max_jobs_per_source", 25)

    # ── Build keyword lists from LLM-identified profiles ──────────────────────
    job_profiles      = resume_data.get("job_profiles", {})
    primary_profiles  = job_profiles.get("primary_profiles", [])   # non-telecom, high priority
    adjacent_profiles = job_profiles.get("adjacent_profiles", [])  # pivots, medium priority
    telecom_profiles  = job_profiles.get("telecom_profiles", [])   # telecom, lowest priority

    primary_titles  = []
    for p in primary_profiles:
        primary_titles.extend(p.get("search_titles", [p.get("title", "")]))

    adjacent_titles = []
    for p in adjacent_profiles:
        adjacent_titles.extend(p.get("search_titles", [p.get("title", "")]))

    telecom_titles  = []
    for p in telecom_profiles:
        telecom_titles.extend(p.get("search_titles", [p.get("title", "")]))

    # Profile-specific keywords
    profile_keywords = []
    for p in primary_profiles + adjacent_profiles:
        profile_keywords.extend(p.get("keywords", []))

    # Fall back to legacy fields if no profiles were identified
    if primary_titles:
        generic_kw = resume_data.get("search_keywords", [])
        keywords = list(dict.fromkeys(
            primary_titles + adjacent_titles + telecom_titles + generic_kw
        ))
        logger.info(
            f"Profile-based search: {len(primary_titles)} primary (non-telecom), "
            f"{len(adjacent_titles)} adjacent (pivot), "
            f"{len(telecom_titles)} telecom (fallback)"
        )
    else:
        keywords = resume_data.get("search_keywords", [])
        if not keywords:
            keywords = resume_data.get("target_roles", ["software engineer"])

    logger.info(f"Starting job search | Top keywords: {keywords[:5]} | Locations: {locations}")

    # ── 1. Manual Company List ─────────────────────────────────────────────────
    companies = config.get("companies", [])
    enabled_companies = [c for c in companies if c.get("enabled", True)]
    logger.info(f"Scraping {len(enabled_companies)} company career pages...")

    # Use non-telecom + adjacent keywords for company pages; skip telecom-only terms
    company_keywords = list(dict.fromkeys(
        (primary_titles + adjacent_titles + profile_keywords)[:20] or keywords[:20]
    ))

    for company in enabled_companies:
        try:
            company_jobs = scrape_company_careers(company, company_keywords, max_per_source)
            for j in company_jobs:
                _tag_job_with_profile(j, primary_profiles, adjacent_profiles, telecom_profiles)
            all_jobs.extend(company_jobs)
        except Exception as e:
            logger.warning(f"Failed to scrape {company['name']}: {e}")
        time.sleep(0.5)

    # ── 2. Job Board searches (per-profile per tier) ───────────────────────────
    boards_config = config.get("job_boards", {})

    # Split primary_profiles into custom (user-defined) and resume-derived
    custom_profiles  = [p for p in primary_profiles if p.get("is_custom")]
    resume_profiles  = [p for p in primary_profiles if not p.get("is_custom")]

    # Build search runs: ALL custom roles first (full slot each), then resume profiles
    board_search_runs = []

    for p in custom_profiles:           # every custom role gets its own pass
        board_search_runs.append({
            "keywords": p.get("search_titles", [p.get("title", "")])[:5]
                        + p.get("keywords", [])[:3],
            "category": "primary",
            "profile": p,
            "is_custom": True,
        })

    for p in resume_profiles[:4]:       # up to 4 resume-derived primary passes
        board_search_runs.append({
            "keywords": p.get("search_titles", [p.get("title", "")])[:3]
                        + p.get("keywords", [])[:2],
            "category": "primary",
            "profile": p,
        })

    if adjacent_titles:
        board_search_runs.append({
            "keywords": list(dict.fromkeys(adjacent_titles))[:4]
                        + list(dict.fromkeys(profile_keywords))[:2],
            "category": "adjacent",
            "profile": None,
        })
    # Telecom: one combined pass, capped to 50% of normal job allocation
    if telecom_titles:
        board_search_runs.append({
            "keywords": list(dict.fromkeys(telecom_titles))[:3],
            "category": "telecom",
            "profile": None,
            "cap_multiplier": 0.5,   # fetch fewer telecom jobs
        })
    if not board_search_runs:
        board_search_runs.append({"keywords": keywords[:6], "category": "primary", "profile": None})

    # Custom roles get a guaranteed full-size slot; everything else shares the remainder
    n_custom = len(custom_profiles)
    n_resume = max(len(board_search_runs) - n_custom, 1)
    jobs_per_custom = max_per_source                          # full quota per custom role
    jobs_per_resume = max(5, max_per_source // n_resume)     # shared quota for resume runs

    logger.info(
        f"Search runs: {n_custom} custom (primary) + {len(board_search_runs)-n_custom} resume/adjacent/telecom"
    )

    for run in board_search_runs:
        run_keywords = run["keywords"]
        run_category = run["category"]
        run_profile  = run["profile"]
        cap = run.get("cap_multiplier", 1.0)
        base_limit = jobs_per_custom if run.get("is_custom") else jobs_per_resume
        run_limit = max(3, int(base_limit * cap))

        for location in locations:
            if boards_config.get("indeed", {}).get("enabled", True):
                try:
                    batch = scrape_indeed(run_keywords, location, run_limit)
                    for j in batch:
                        if run_profile:
                            j["profile_category"] = run_category
                            j["matched_profile"]  = run_profile.get("title", "")
                        else:
                            _tag_job_with_profile(j, primary_profiles, adjacent_profiles, telecom_profiles)
                            if j["profile_category"] == "other":
                                j["profile_category"] = run_category
                    all_jobs.extend(batch)
                except Exception as e:
                    logger.warning(f"Indeed search failed: {e}")

            if boards_config.get("linkedin", {}).get("enabled", True):
                try:
                    batch = scrape_linkedin(run_keywords, location, run_limit)
                    for j in batch:
                        if run_profile:
                            j["profile_category"] = run_category
                            j["matched_profile"]  = run_profile.get("title", "")
                        else:
                            _tag_job_with_profile(j, primary_profiles, adjacent_profiles, telecom_profiles)
                            if j["profile_category"] == "other":
                                j["profile_category"] = run_category
                    all_jobs.extend(batch)
                except Exception as e:
                    logger.warning(f"LinkedIn search failed: {e}")

            if boards_config.get("builtin", {}).get("enabled", True):
                try:
                    batch = scrape_builtin(run_keywords, location, run_limit)
                    for j in batch:
                        if run_profile:
                            j["profile_category"] = run_category
                            j["matched_profile"]  = run_profile.get("title", "")
                        else:
                            _tag_job_with_profile(j, primary_profiles, adjacent_profiles, telecom_profiles)
                            if j["profile_category"] == "other":
                                j["profile_category"] = run_category
                    all_jobs.extend(batch)
                except Exception as e:
                    logger.warning(f"Built In search failed: {e}")

            if boards_config.get("themuse", {}).get("enabled", True):
                try:
                    batch = scrape_themuse(run_keywords, location, run_limit)
                    for j in batch:
                        if run_profile:
                            j["profile_category"] = run_category
                            j["matched_profile"]  = run_profile.get("title", "")
                        else:
                            _tag_job_with_profile(j, primary_profiles, adjacent_profiles, telecom_profiles)
                            if j["profile_category"] == "other":
                                j["profile_category"] = run_category
                    all_jobs.extend(batch)
                except Exception as e:
                    logger.warning(f"The Muse search failed: {e}")

            if boards_config.get("remotive", {}).get("enabled", True):
                try:
                    batch = scrape_remotive(run_keywords, location, run_limit)
                    for j in batch:
                        if run_profile:
                            j["profile_category"] = run_category
                            j["matched_profile"]  = run_profile.get("title", "")
                        else:
                            _tag_job_with_profile(j, primary_profiles, adjacent_profiles, telecom_profiles)
                            if j["profile_category"] == "other":
                                j["profile_category"] = run_category
                    all_jobs.extend(batch)
                except Exception as e:
                    logger.warning(f"Remotive search failed: {e}")

    # ── 3. Deduplicate ─────────────────────────────────────────────────────────
    unique_jobs = deduplicate_jobs(all_jobs)
    max_total = search_config.get("max_total_jobs", 200)

    primary_count  = sum(1 for j in unique_jobs if j.get("profile_category") == "primary")
    adjacent_count = sum(1 for j in unique_jobs if j.get("profile_category") == "adjacent")
    telecom_count  = sum(1 for j in unique_jobs if j.get("profile_category") == "telecom")
    logger.info(
        f"Found {len(all_jobs)} total jobs → {len(unique_jobs)} after dedup "
        f"(non-telecom primary: {primary_count}, adjacent pivot: {adjacent_count}, "
        f"telecom fallback: {telecom_count}, capped at {max_total})"
    )
    return unique_jobs[:max_total]
