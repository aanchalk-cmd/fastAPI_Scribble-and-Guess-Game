#!/usr/bin/env bash
# Deploy the Scribble app on the self-hosted runner (called by .github/workflows/deploy.yml).
#
#   1. update the checkout to origin/main
#   2. install requirements
#   3. preflight: required data present + the app imports (before touching the live service)
#   4. restart the systemd service
#   5. wait until the service is active AND the HTTP endpoint answers, or fail with diagnostics
#
# Environment overrides (defaults match the production runner):
#   PROJECT_DIR      checkout to deploy          (/home/hardik/projects/fastAPI_Scribble-and-Guess-Game)
#   SERVICE_NAME     systemd unit                (scribble.service)
#   HEALTH_URL       URL that must return 2xx    (http://127.0.0.1:8000/)
#   STARTUP_TIMEOUT  seconds to wait for health  (60)
#   SKIP_UPDATE=1    verify + restart the current checkout without git fetch/reset or pip install
set -Eeuo pipefail

PROJECT_DIR="${PROJECT_DIR:-/home/hardik/projects/fastAPI_Scribble-and-Guess-Game}"
SERVICE_NAME="${SERVICE_NAME:-scribble.service}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8000/}"
STARTUP_TIMEOUT="${STARTUP_TIMEOUT:-60}"
SKIP_UPDATE="${SKIP_UPDATE:-0}"

RESTARTED_AT=""

section() { echo; echo "==> $*"; }

# Service state and its log since this deploy's restart. Only the app's own output is
# shown (journal), never the environment, so secrets from .env are not printed.
dump_service_diagnostics() {
    echo "---------------- systemctl status $SERVICE_NAME ----------------"
    sudo -n systemctl status "$SERVICE_NAME" --no-pager --lines=0 2>&1 || true
    echo "---------------- journal for $SERVICE_NAME ----------------"
    if [[ -n "$RESTARTED_AT" ]]; then
        sudo -n journalctl -u "$SERVICE_NAME" --since "$RESTARTED_AT" --no-pager -n 120 2>&1 \
            || echo "(could not read the journal; run: journalctl -u $SERVICE_NAME -n 200)"
    else
        sudo -n journalctl -u "$SERVICE_NAME" --no-pager -n 60 2>&1 \
            || echo "(could not read the journal; run: journalctl -u $SERVICE_NAME -n 200)"
    fi
    echo "-----------------------------------------------------------------"
}

# Any unexpected failure: say exactly which command failed, where, and with what code.
on_error() {
    local code=$? line=$1 cmd=$2
    echo
    echo "DEPLOY FAILED: command exited with code $code at scripts/deploy.sh:$line"
    echo "  command: $cmd"
    if [[ -n "$RESTARTED_AT" ]]; then
        dump_service_diagnostics
    fi
    exit "$code"
}
trap 'on_error "$LINENO" "$BASH_COMMAND"' ERR

fail() {
    trap - ERR
    echo
    echo "DEPLOY FAILED: $*"
    if [[ -n "$RESTARTED_AT" ]]; then
        dump_service_diagnostics
    fi
    exit 1
}

echo "======================================"
echo "Starting Scribble deployment"
echo "  project: $PROJECT_DIR"
echo "  service: $SERVICE_NAME"
echo "  health:  $HEALTH_URL (timeout ${STARTUP_TIMEOUT}s)"
echo "======================================"

cd "$PROJECT_DIR"

if [[ "$SKIP_UPDATE" != "1" ]]; then
    section "Fetching latest code"
    git fetch origin main
    git reset --hard origin/main
    echo "Deploying commit $(git rev-parse --short HEAD): $(git log -1 --format=%s)"

    section "Installing dependencies"
    # shellcheck disable=SC1091
    source .venv/bin/activate
    python -m pip install -r requirements.txt
else
    section "SKIP_UPDATE=1: verifying the current checkout $(git rev-parse --short HEAD)"
    # shellcheck disable=SC1091
    source .venv/bin/activate
fi

# ---- Preflight: catch a broken build before restarting the live service ----
section "Preflight checks"
WORDS_FILE="app/data/words.json"
if [[ ! -s "$WORDS_FILE" ]]; then
    fail "required data file $WORDS_FILE is missing or empty in the deployed checkout.
  The app loads it at import time and cannot start without it.
  The running service was NOT restarted."
fi
echo "ok: $WORDS_FILE present"

# Importing main runs module-level setup (word list, DB engine config) exactly as the
# service will; a failure here prints the real traceback.
if ! import_output="$(python -c "import main" 2>&1)"; then
    echo "$import_output" | tail -n 25
    fail "the application failed to import (traceback above).
  The running service was NOT restarted."
fi
echo "ok: application imports"

# ---- Restart and wait for health ----
section "Restarting $SERVICE_NAME"
RESTARTED_AT="$(date '+%Y-%m-%d %H:%M:%S')"
sudo -n systemctl restart "$SERVICE_NAME"

section "Waiting for $SERVICE_NAME to become healthy"
deadline=$(( SECONDS + STARTUP_TIMEOUT ))
last_http=""
while :; do
    state="$(sudo -n systemctl is-active "$SERVICE_NAME" 2>/dev/null || true)"
    if [[ "$state" == "failed" ]]; then
        fail "$SERVICE_NAME entered state 'failed' after restart (the app process exited)."
    fi
    if [[ "$state" == "active" ]]; then
        last_http="$(curl --silent --show-error --output /dev/null --max-time 5 \
            --write-out '%{http_code}' "$HEALTH_URL" 2>&1 || true)"
        if [[ "$last_http" =~ ^2[0-9][0-9]$ ]]; then
            echo "ok: service active, $HEALTH_URL returned HTTP $last_http"
            break
        fi
    fi
    if (( SECONDS >= deadline )); then
        fail "$SERVICE_NAME not healthy after ${STARTUP_TIMEOUT}s (state: ${state:-unknown}, last HTTP check: ${last_http:-not attempted})."
    fi
    sleep 2
done

echo "======================================"
echo "Deployment successful! ($(git rev-parse --short HEAD))"
echo "======================================"
