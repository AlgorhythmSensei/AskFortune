# AskFortune

## Release Notes (2026-05-23)

AskFortune is a desktop companion app for Powerball, Oz Lotto, and Saturday Lotto. It retrieves official historical draw data, analyzes number‑frequency trends, and presents structured suggested lines with clean, readable visual formatting.

Important: AskFortune is created purely for entertainment and statistical curiosity. Lottery outcomes are entirely random, and no tool can improve your odds. This is not a recommendation to gamble. Only participate in lotteries if it is legal in your area and you can do so responsibly. If gambling causes stress or financial pressure, seek support and consider stepping away entirely.

Disclaimer: Any decisions you make based on AskFortune are entirely your own. I take no responsibility for financial outcomes, losses, or any consequences related to lottery participation.

- Added game-aware suggestion formatting:
	- Powerball outputs `N1..N7 + PB`
	- Oz Lotto outputs 7 main numbers
	- Saturday Lotto outputs 6 main numbers
- Added colorized result display in the desktop UI:
	- normal numbers in green
	- special numbers (for example PB) in red
- Hardened model handling with fallback support:
	- `llama-3.3-70b-versatile`
	- fallback to `llama-3.1-8b-instant`
- Improved environment key loading for packaged and local runs:
	- prefers user-local env paths (`~/.askfortune/.env`, `~/.askfortune/.env.local`)
	- skips placeholder keys and continues search
- Added repository secret-safety hygiene:
	- `.gitignore` includes local env files
	- `.env.example` added for template-based setup
- Added output-buffer trimming in UI to reduce long-session memory growth risk.
- Completed syntax, static security, dependency vulnerability, and secret-leak verification checks.

AskFortune is a desktop lottery helper app built with Tkinter and Groq.
It downloads official draw history, analyzes hot/cold trends, and generates suggested lines for:

- Powerball
- Oz Lotto
- Saturday Lotto

## Core Features

- Tkinter desktop UI with game selector and one-click fetch/predict flow
- Official CSV data ingestion from Lotterywest game endpoints
- Statistical summary using recent historical draws (currently 17)
- Colorized output in the results panel:
	- normal numbers in green
	- special numbers (for example Powerball) in red
- Structured Groq JSON output with validation before display
- Game-specific line formatting:
	- Powerball: 7 main numbers + PB
	- Oz Lotto: 7 main numbers
	- Saturday Lotto: 6 main numbers

## Files

- app.py: Main desktop application
- scraper.py: CSV/HTML ingestion utility (script mode)
- generator.py: Analysis + LLM pipeline (script mode)
- main.py: Script orchestrator (non-GUI flow)
- requirements.txt: Python dependencies
- .env: Environment variables (API key)

## Environment Setup

The app has been validated with a Tk-enabled Python environment named `.venv-tk`.

1. Create the Tk-enabled venv (if needed):

```bash
/usr/local/bin/python3.13 -m venv .venv-tk
```

2. Install dependencies:

```bash
.venv-tk/bin/pip install -r requirements.txt
.venv-tk/bin/pip install python-dotenv
```

3. Configure your API key locally (do not commit secrets). Use one of:

- `~/.askfortune/.env` (recommended for app bundle and local runs)
- `.env.local` in project root (gitignored)

You can copy from `.env.example` and edit:

```bash
cp .env.example .env.local
```

Then set your key:

```env
GROQ_API_KEY=your_groq_api_key_here
```

Optional model override:

```env
GROQ_MODEL=llama-3.3-70b-versatile
```

## LLM Model Selection

By default, AskFortune uses this Groq fallback order:

1. `llama-3.3-70b-versatile`
2. `llama-3.1-8b-instant`

You can force a specific model by setting `GROQ_MODEL` in your local env file.

## Run Desktop App

```bash
cd /Users/mikko/Documents/VSCodeIDE/AskFortune
.venv-tk/bin/python app.py
```

## Build macOS App Bundle

1. Build:

```bash
cd /Users/mikko/Documents/VSCodeIDE/AskFortune
.venv-tk/bin/pip install pyinstaller
.venv-tk/bin/pyinstaller --noconfirm --clean --windowed --name AskFortune app.py
```

2. Install to Applications:

```bash
mkdir -p ~/Applications
ditto dist/AskFortune.app ~/Applications/AskFortune.app
open ~/Applications/AskFortune.app
```

## Troubleshooting

- If the app says `GROQ_API_KEY is missing or still a placeholder`, ensure your key is set in `~/.askfortune/.env` or `.env.local`.
- If macOS blocks first launch of the built app, clear quarantine:

```bash
xattr -dr com.apple.quarantine ~/Applications/AskFortune.app
```

- If you launch from terminal, use `.venv-tk/bin/python app.py` (other interpreters on this machine may not have working Tk support).

## Release Verification

The following checks were run before release and all passed.

### One-Command Check (Recommended)

```bash
./security_check.sh
```

This script runs syntax checks, Bandit on source files only, pip-audit, and a secret exposure scan.

Why source files only for Bandit:

- Scanning virtualenv or bundled dependency directories creates noisy third-party findings that do not represent app-source risk.

### Syntax and Diagnostics

```bash
.venv-tk/bin/python -m compileall -q .
.venv-tk/bin/python -m py_compile app.py scraper.py generator.py main.py
```

Result:

- No syntax errors.
- No VS Code diagnostics errors across project files.

### Static Security Scan (Bandit)

```bash
.venv-tk/bin/bandit app.py scraper.py generator.py main.py
```

Result:

- No issues identified.
- Severity summary: low=0, medium=0, high=0.

### Dependency Vulnerability Audit

```bash
.venv-tk/bin/pip-audit
```

Result:

- No known vulnerabilities found.

### Secret Exposure Scan

```bash
grep -RIn "gsk_\|GROQ_API_KEY=.*gsk_" . --exclude-dir=.venv --exclude-dir=.venv-tk --exclude-dir=build --exclude-dir=dist
```

Result:

- No hardcoded Groq API keys found in project files.
- Expected informational matches may appear in `README.md` and `security_check.sh` because they contain the grep pattern itself.

### Memory Safety Guard

To reduce long-session memory growth in the UI, the output panel buffer in app.py is bounded with a line cap (`MAX_OUTPUT_LINES`) and older lines are trimmed automatically.
