#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PY="$ROOT_DIR/.venv-tk/bin/python"
VENV_BANDIT="$ROOT_DIR/.venv-tk/bin/bandit"
VENV_PIP_AUDIT="$ROOT_DIR/.venv-tk/bin/pip-audit"

if [[ ! -x "$VENV_PY" ]]; then
  echo "Error: Tk virtualenv Python not found at: $VENV_PY" >&2
  echo "Create it first (example): /usr/local/bin/python3.13 -m venv .venv-tk" >&2
  exit 1
fi

if [[ ! -x "$VENV_BANDIT" ]]; then
  echo "Error: bandit not found at: $VENV_BANDIT" >&2
  echo "Install dependencies with: .venv-tk/bin/pip install -r requirements.txt" >&2
  exit 1
fi

if [[ ! -x "$VENV_PIP_AUDIT" ]]; then
  echo "Error: pip-audit not found at: $VENV_PIP_AUDIT" >&2
  echo "Install with: .venv-tk/bin/pip install pip-audit" >&2
  exit 1
fi

cd "$ROOT_DIR"

echo "==> Syntax checks"
"$VENV_PY" -m compileall -q .
"$VENV_PY" -m py_compile app.py scraper.py generator.py main.py

echo

echo "==> Static security scan (source files only)"
"$VENV_BANDIT" app.py scraper.py generator.py main.py

echo

echo "==> Dependency vulnerability audit"
"$VENV_PIP_AUDIT"

echo

echo "==> Secret exposure scan"
grep -RIn "gsk_\|GROQ_API_KEY=.*gsk_" . \
  --exclude-dir=.venv \
  --exclude-dir=.venv-tk \
  --exclude-dir=build \
  --exclude-dir=dist || true

echo

echo "All checks completed."
