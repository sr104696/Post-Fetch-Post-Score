# Reddit Lens (Flask)

Lightweight Flask service that fetches subreddit posts, stores them in SQLite, and exposes routes for fetching, exporting, and diagnostics (including `/api/bug-report`).

## Setup
1. Install Python 3.11+.
2. Install deps: `pip install -r requirements.txt`
3. Install Playwright Chromium: `python -m playwright install chromium`
4. Configure: copy `config.example.json` to `config.json` and adjust subreddits/email if needed (email export is disabled by default).

## Run
```
python app.py
```
Open the printed URL (defaults to http://127.0.0.1:5001). `/api/bug-report` downloads a markdown diagnostics report.

## Notes
- Uses SQLite at `reddit_lens.db` (created on first run).
- Screenshot/export features require Playwright Chromium.
- Logs write to `error.log` as configured.
