# ⬡ JobRadar — AI-Powered Job Search Automation

Automatically searches for jobs across multiple cities and job boards, matches them to your resume using **Azure AI Foundry**, and presents a prioritized list in a sleek web dashboard.

---

## ⚡ Quickstart (5 minutes)

**Prerequisites:** Python 3.10+, an Azure OpenAI deployment, your resume as a `.docx` file.

```powershell
# 1. Install dependencies
cd job_search_app
pip install -r requirements.txt

# 2. Add your Azure credentials
copy .env.example .env
# → Edit .env and fill in AZURE_ENDPOINT, AZURE_API_KEY, AZURE_MODEL_NAME

# 3. Drop your resume in the resumes folder
#    Any .docx file is auto-detected, e.g.:
#    resumes/resume_2026.docx

# 4. (Optional) Set your target locations & roles in config/config.yaml
#    — or skip this and use the ⚙ gear icon in the dashboard later

# 5. Run a search and open the dashboard
python main.py --run-once   # search once (takes ~2-5 min)
python main.py --dashboard  # then open http://localhost:5051
```

> **Tip:** `python main.py` (no flags) runs the search *and* starts the daily scheduler + dashboard in one command.

| Common commands | What it does |
|-----------------|--------------|
| `python main.py` | Full run: search + schedule + dashboard |
| `python main.py --run-once` | One-off search, no scheduler |
| `python main.py --dashboard` | Dashboard only — no search required |
| `python scripts/api_server.py` | Standalone API server (same dashboard, no `main.py`) |

Once the dashboard is open, click **⚙** (top-right) to change locations, job boards, and search limits — no config file editing needed.

---

## Features

- **📄 Resume Analysis** — Reads your `.docx` resume; Azure AI extracts skills, work history, functional title, and builds tiered job profiles
- **🎯 Custom Search Roles** — Define your own target roles (e.g. "Principal Product Manager") that take priority over resume-derived profiles
- **📍 Multi-Location Search** — Search across any number of cities simultaneously
- **🔍 Job Boards** — LinkedIn, Built In, The Muse, Remotive, Indeed (where available)
- **🏢 Company Career Pages** — Search and add from a catalogue of 60+ supported companies (Greenhouse boards, Lever boards, Amazon public API); managed entirely from the ⚙ gear drawer — no config file editing needed
- **🤖 AI Matching** — Azure AI Foundry scores every job 0–100% against your resume
- **📊 Dashboard** — Web UI at `http://localhost:5051` with filters, priority views, per-job match analysis, cover letter generation
- **⚙ Settings Drawer** — Manage locations, industry focus, job board toggles, search limits, and custom roles from the gear icon — no config file editing needed
- **🏢 Industry Focus** — Configure high-priority industries (AI/ML, FinTech, etc.) to drive P1/P3 profile labeling on the Profiles page
- **✕ Skip List** — Dismiss jobs so they never reappear; restore them anytime; skip list counts are excluded from all stats
- **📅 Recency Filter** — Filter results by Today / 7 days / 30 days / All
- **◎ Resume Profiles Page** — Full view of AI-generated candidate profile and all identified job profiles
- **↻ Reload Profile** — Re-analyze your resume without running a full search
- **⏱ Daily Scheduler** — Runs automatically every morning (APScheduler / cron / Windows Task Scheduler)
- **✉ Cover Letter AI** — One-click cover letter outline per job
- **💾 SQLite Persistence** — Tracks all jobs, marks applied, stores search run history; dismissed jobs persist across cleanup cycles

---

## Quick Start

### 1. Clone / unzip and install dependencies

```bash
cd job_search_app
pip install -r requirements.txt
```

Or use the bundled setup script:
```bash
# macOS / Linux
chmod +x setup.sh && ./setup.sh

# Windows (run as Administrator)
setup_scheduler_windows.bat
```

### 2. Add your resume

Place your resume DOCX file in the `resumes/` folder:
```
resumes/resume_2026.docx    ← example name, any .docx works
```

> The `resumes/` folder lives **one level above** `job_search_app/` by default (`../resumes`).  
> Change `resume.folder` in `config/config.yaml` if you use a different path.

### 3. Configure Azure AI Foundry

Create a `.env` file in `job_search_app/` (never commit this file):
```env
AZURE_ENDPOINT=https://your-project.openai.azure.com
AZURE_API_KEY=your_api_key_here
AZURE_MODEL_NAME=gpt-4o
AZURE_API_VERSION=2025-04-01-preview
```

**Where to find these values:**
1. Go to [Azure AI Foundry](https://ai.azure.com)
2. Open your project → **Settings → Project details**
3. Copy the **API endpoint** and **API key**
4. Deploy a model under **Deployments** and copy the deployment name

### 4. Configure your search (optional — or use the dashboard ⚙ Settings)

Edit `config/config.yaml`, or open the dashboard and click the **⚙** gear icon in the top-right:
```yaml
search:
  locations:
    - Boston, MA
    - San Francisco, CA
    - Seattle, WA
    - Redmond, WA
    - San Diego, CA
  high_priority_industries:      # drives P1/P3 labeling on the Profiles page
    - AI / Machine Learning
    - Cloud Computing
    - Enterprise Tech
    - Cybersecurity
    - FinTech
  remote_ok: true
  recent_days: 7          # default recency filter shown in dashboard
  max_jobs_per_source: 40
  max_total_jobs: 400
  min_match_score: 40     # jobs below this score are dropped

resume:
  filename: resume.docx   # blank = auto-detect newest .docx
  folder: ../resumes
```

### 5. (Optional) Add custom search roles

Create or edit `data/custom_roles.json`:
```json
["Principal Product Manager", "AI Infrastructure PM", "Director Product Management"]
```
Custom roles are searched **first** with full quota and are not limited by what the LLM extracts from your resume. They appear as Priority 1 profiles in the dashboard.

### 6. Run

```bash
# Run search once, then start the daily scheduler + dashboard
python main.py

# Run search only (no scheduler, no dashboard)
python main.py --run-once

# Dashboard only — no search required
python main.py --dashboard
```

Or run the API server directly (fully standalone dashboard):
```bash
python scripts/api_server.py
python scripts/api_server.py --port 8080   # custom port
```

Open **http://localhost:5051** in your browser.

---

## Dashboard Reference

### Sidebar navigation

| View | Description |
|------|-------------|
| All Jobs | Every scored job (dismissed excluded from counts) |
| High Priority | Score >= 75 |
| Medium Priority | Score 60–74 |
| New Today | Jobs first seen today |
| Applied | Jobs marked as applied |
| Resume Profiles | AI-generated candidate profile and tiered job profiles |
| Skip List | Dismissed jobs — restore or leave skipped |
| Indeed / LinkedIn / Company Sites | Filter by source |

### Filter chips

| Chip | Effect |
|------|--------|
| All Scores / 80%+ / 60%+ / 40%+ | Minimum match score |
| 🌐 Remote only | Show only remote-flagged jobs |
| 📅 Today / 7d / 30d / All | Recency window (saved to config) |
| Search box | Live title/company text filter |

### Job actions

| Button | Action |
|--------|--------|
| ↗ View Job | Open original posting in new tab |
| ✓ Mark Applied | Record that you applied |
| ✉ Cover Letter | Generate an AI cover letter outline |
| ✕ Skip | Dismiss permanently (moves to Skip List) |

### ⚙ Settings Drawer

Click the **⚙** gear icon in the top-right of the topbar to open a slide-in settings panel:

| Section | What you can change |
|---------|---------------------|
| 📍 Search Locations | Add / remove cities; toggle remote jobs |
| 🏢 Company Career Pages | Search and add from 60+ supported companies (Greenhouse / Lever / API); toggle each on/off |
| 🏢 Industry Focus | Add / remove high-priority industries — controls P1/P3 section labels on Profiles page |
| 🔍 Job Boards | Enable or disable each board individually |
| ⚡ Search Limits | Max per source, max total, min match score %, recent days |
| ✦ Custom Roles | Add / remove custom target roles |

Click **✓ Save All Settings** — changes are persisted to `config/config.yaml` and take effect on the next search run.

### Resume Profiles page (`/profiles.html`)

- Shows the AI-generated **candidate card**: name, functional title, years of experience, summary, skills
- **🏢 Industry Focus panel** — Editable list of high-priority industries at the top of the page; click **✓ Save Focus** to persist to `config.yaml` without opening the settings drawer
- **Priority 1** — Best-fit roles matching your configured industry focus (highest fit scores; label updates dynamically)
- **Priority 2** — Adjacent pivot roles with skill gaps and upskilling suggestions
- **Priority 3** — Fallback roles outside your industry focus (label dynamically reflects which industries are deprioritized)
- Click **↻ Reload Profile** to re-run resume analysis without a full search (~30 seconds)

---

## How Matching Works

1. **Resume Parsing** — Azure AI reads your DOCX in document order (paragraphs and tables interleaved). Heading styles are preserved as section markers for better context.
2. **Profile Tiers** — Three priority tiers: *Primary* (highest priority), *Adjacent pivots* (medium), *Domain fallback* (lowest).
3. **Industry Focus** — `search.high_priority_industries` in `config.yaml` (or the ⚙ gear / Profiles page panel) controls which industries are treated as Priority 1 targets. Profiles outside the focus land in Priority 3. Section labels on the Profiles page update dynamically to reflect your current focus.
4. **Custom Roles Override** — Roles in `data/custom_roles.json` are injected as Priority 1 profiles with `fit_score: 99` and searched first, before all resume-derived profiles.
5. **Multi-Location Scraping** — Each profile is searched across every configured location on every enabled job board.
6. **AI Scoring** — Each job is scored 0–100% against your full resume profile. Factors: skill overlap, title match, seniority alignment, industry fit.
7. **Priority Assignment** — High (>= 75), Medium (60–74), Low (40–59). Jobs below `min_match_score` are dropped entirely.
8. **Skip Preservation** — Dismissed jobs are stored with `status = dismissed` and excluded from all queries, stats, and cleanup operations. The skip list persists indefinitely.

---

## Running the Dashboard Independently

The dashboard can be started without running a job search, from any working directory:

```powershell
# Option A — via main.py
python C:\path\to\job_search_app\main.py --dashboard

# Option B — standalone API server (no main.py needed)
python C:\path\to\job_search_app\scripts\api_server.py
```

If the port is already in use, a warning is logged and the existing server continues — no crash.

---

## Directory Structure

```
job_search_app/
├── main.py                      # Entry point, scheduler, orchestration
├── requirements.txt
├── .env                         # Azure credentials (never commit)
├── setup.sh                     # macOS/Linux setup
├── setup_scheduler_windows.bat  # Windows Task Scheduler setup
├── config/
│   └── config.yaml              # All configuration
├── resumes/                     # PUT YOUR RESUME HERE (auto-detected)
│   └── resume_2026.docx
├── scripts/
│   ├── resume_reader.py         # DOCX extraction + LLM resume analysis
│   ├── llm_client.py            # Azure AI Foundry wrapper
│   ├── job_scraper.py           # Web scraping (boards + company pages)
│   ├── job_matcher.py           # AI scoring + ranking
│   ├── database.py              # SQLite persistence layer
│   └── api_server.py            # Flask REST API + standalone server
├── dashboard/
│   ├── index.html               # Main dashboard UI
│   └── profiles.html            # Resume profiles & analysis page
├── data/
│   ├── custom_roles.json        # Your custom target roles (edit this)
│   ├── search_profiles.json     # Generated resume profile (auto-updated)
│   ├── job_search.db            # SQLite database (auto-created)
│   └── results/                 # JSON result archives per run
└── logs/
    └── job_search.log           # Application logs
```

> **Note:** `resumes/` is at `../resumes` relative to `job_search_app/` by default.

---

## Scheduler Options

### Built-in APScheduler (default)

Runs automatically when you start `python main.py`. Configure in `config.yaml`:
```yaml
scheduler:
  enabled: true
  run_time: "08:00"
  timezone: "America/New_York"
```

### Cron (macOS / Linux)
```bash
# Run at 8 AM daily
0 8 * * * /path/to/venv/bin/python /path/to/job_search_app/main.py --run-once
```

### Windows Task Scheduler
Run `setup_scheduler_windows.bat` as Administrator, or manually:
```
schtasks /create /tn "JobRadarSearch" ^
  /tr "C:\path\to\venv\Scripts\python.exe C:\path\to\job_search_app\main.py --run-once" ^
  /sc DAILY /st 08:00
```

---

## Adding Target Companies

The easiest way is via the **⚙ gear drawer** in the dashboard:

1. Click **⚙** (top-right) to open Settings.
2. Under **🏢 Company Career Pages**, type in the search box — results are filtered from a catalogue of 60+ pre-configured companies (Greenhouse, Lever, Amazon API).
3. Click a company to add it; toggle the checkbox to enable/disable scraping.
4. Click **✓ Save All Settings** — changes persist to `config/config.yaml` immediately.

If the company you want isn't in the catalogue, expand the **Add manually** section in the drawer and paste any careers URL directly.

You can also edit `config/config.yaml` directly:
```yaml
companies:
  - name: "Stripe"
    careers_url: "https://stripe.com/jobs/listing"
    enabled: true
  - name: "Figma"
    careers_url: "https://www.figma.com/careers/"
    enabled: true
```

Set `enabled: false` to temporarily pause scraping a company without removing it.

> **Scraper support notes:**
> - **Greenhouse boards** (`boards.greenhouse.io/…`) and **Lever boards** (`jobs.lever.co/…`) are fully supported, including embedded ATS detection.
> - **Amazon** uses a public JSON API and works reliably.
> - JS-rendered pages (Google, Microsoft, Netflix, etc.) have limited or no server-side API and may return zero results.

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/jobs` | List jobs (`min_score`, `priority`, `source`, `days_back`, `sort_by`, `limit`) |
| GET | `/api/jobs/<id>` | Single job detail |
| PATCH | `/api/jobs/<id>/status` | Update status / mark applied |
| POST | `/api/jobs/<id>/undismiss` | Restore a dismissed job |
| GET | `/api/jobs/dismissed` | List all dismissed (skipped) jobs |
| GET | `/api/jobs/<id>/cover-letter` | Generate AI cover letter outline |
| GET | `/api/stats` | Summary counts (dismissed excluded from all totals) |
| GET | `/api/history` | Recent search run history |
| GET | `/api/profiles` | Resume analysis & profile JSON |
| POST | `/api/reload-profile` | Re-run resume analysis in background |
| GET | `/api/settings` | Get all settings (locations, industries, boards, limits, companies) |
| POST | `/api/settings` | Save settings incl. `high_priority_industries` and `companies` array (persisted to config.yaml) |
| GET | `/api/custom-roles` | Get custom search roles |
| POST | `/api/custom-roles` | Save custom search roles |
| POST | `/api/search/run` | Trigger a background job search |

---

## Azure AI Models

Tested with:
- `gpt-4o` — Best quality; recommended
- `gpt-4o-mini` — Faster and cheaper
- `gpt-5.2-chat-2` — Azure AI Foundry preview
- Any other model deployed in your Azure AI Foundry project

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| "No resume found" | Check `resume.folder` in config.yaml — default is `../resumes` (sibling of `job_search_app/`) |
| Dashboard shows old/wrong title | Add your target role to `data/custom_roles.json` or click ↻ Reload Profile |
| "Azure client failed" | Verify endpoint, API key, and model deployment name in `.env` |
| Dashboard blank / no jobs | Run a search first: `python main.py --run-once` |
| No jobs from Indeed | Indeed blocks scraping (HTTP 401); LinkedIn and Built In are most reliable |
| `--dashboard` crashes from wrong directory | Fixed — paths are resolved relative to main.py, not CWD |
| Port 5051 already in use | A warning is logged and the existing server continues; no crash |
| Very low match scores | Ensure resume has clear role titles, bullet achievements, and a skills section |
| `max_total_jobs` hit | Raise `search.max_total_jobs` in config.yaml or via ⚙ Settings |
| Skipped jobs counted in stats | Fixed — all stats exclude dismissed jobs |

---

## Privacy

- All data stays **local** — SQLite database at `data/job_search.db`
- Resume text is sent to your **own** Azure AI Foundry endpoint for analysis
- Job descriptions are sent to Azure AI for scoring
- No analytics, no telemetry, no third-party data sharing
