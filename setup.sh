#!/bin/bash
# ============================================================
#  JobRadar - Setup & Scheduler (macOS/Linux)
# ============================================================

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${PYTHON:-python3}"
VENV_DIR="$SCRIPT_DIR/.venv"

echo ""
echo "  ⬡ JobRadar Setup"
echo "  ──────────────────────────────────"

# ── 1. Create virtualenv ─────────────────────────────────────
if [ ! -d "$VENV_DIR" ]; then
    echo "  Creating virtual environment..."
    $PYTHON -m venv "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"
PYTHON="$VENV_DIR/bin/python"

# ── 2. Install dependencies ──────────────────────────────────
echo "  Installing dependencies..."
pip install -q --upgrade pip
pip install -q -r "$SCRIPT_DIR/requirements.txt"

echo "  ✓ Dependencies installed"

# ── 3. Create resumes folder ──────────────────────────────────
mkdir -p "$SCRIPT_DIR/resumes"
mkdir -p "$SCRIPT_DIR/data/results"
mkdir -p "$SCRIPT_DIR/logs"

echo ""
echo "  ─────────────────────────────────────────────"
echo "  IMPORTANT: Add your resume"
echo "  ─────────────────────────────────────────────"
echo "  Copy your resume.docx to:"
echo "  $SCRIPT_DIR/resumes/resume.docx"
echo ""
echo "  Then edit your config:"
echo "  $SCRIPT_DIR/config/config.yaml"
echo "  (Set Azure AI Foundry credentials and location)"
echo "  ─────────────────────────────────────────────"
echo ""

# ── 4. Setup cron job ─────────────────────────────────────────
read -p "  Setup daily cron job at 8 AM? (y/n): " setup_cron

if [[ "$setup_cron" == "y" || "$setup_cron" == "Y" ]]; then
    CRON_CMD="0 8 * * * source $VENV_DIR/bin/activate && $PYTHON $SCRIPT_DIR/main.py --run-once >> $SCRIPT_DIR/logs/cron.log 2>&1"

    # Remove existing cron job for this script
    (crontab -l 2>/dev/null | grep -v "job_search_app/main.py" || true) | crontab -

    # Add new cron job
    (crontab -l 2>/dev/null; echo "$CRON_CMD") | crontab -

    echo "  ✓ Cron job added: daily at 8:00 AM"
    echo "  View cron: crontab -l"
    echo "  Remove cron: crontab -e"
fi

# ── 5. Offer to run now ───────────────────────────────────────
echo ""
read -p "  Run first job search now? (y/n): " run_now

if [[ "$run_now" == "y" || "$run_now" == "Y" ]]; then
    echo "  Starting JobRadar..."
    $PYTHON "$SCRIPT_DIR/main.py"
else
    echo ""
    echo "  Commands:"
    echo "    Run search + start dashboard:  python main.py"
    echo "    Run search once:               python main.py --run-once"
    echo "    Dashboard only:                python main.py --dashboard"
    echo ""
    echo "  Dashboard: http://localhost:5050"
fi
