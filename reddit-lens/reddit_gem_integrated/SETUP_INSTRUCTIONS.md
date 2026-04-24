# Reddit Gem Finder — Integrated Setup Instructions

## What This Is

This folder contains ONE new file: `reddit_gem_integrated.html`

It combines:
- **reddit-lens** — Python backend that fetches Reddit, scores posts, and stores them in SQLite
- **Claude Post Scorer** — the sortable scoring UI with live feed, paste URLs, delta tracking, and CSV export

No files in either original folder are modified. This works alongside them.

---

## What You Need From Each Folder

### From `reddit-lens\` (your existing project — DO NOT EDIT)
You need these files running as-is:
```
reddit-lens\
├── app.py           ← Flask server (the backend)
├── scraper.py       ← Reddit fetching + scoring logic
├── db.py            ← SQLite database
├── config.json      ← Your subreddit list + email settings
├── requirements.txt ← Dependencies
├── .venv\           ← Already-installed Python environment
└── start.bat        ← Already starts the Flask server
```

### From `Claude Post Scorer\` (DO NOT EDIT these either)
You do NOT need to touch or copy anything from here.
The new integrated HTML replaces the role of `reddit_gem_finder.html`.

---

## Setup: Copy ONE File

Copy `reddit_gem_integrated.html` from this folder into your `reddit-lens\` folder:

```
reddit-lens\
├── app.py
├── scraper.py
├── db.py
├── config.json
├── requirements.txt
├── .venv\
├── start.bat
├── templates\
│   └── index.html
└── reddit_gem_integrated.html    ← COPY THIS HERE
```

That's it. One file. Nothing else changes.

---

## How to Run

1. **Double-click `reddit-lens\start.bat`** — this starts the Flask server (same as you always do)

2. **Open your browser** and go to:
   ```
   http://localhost:5001/gem
   ```
   (If port 5001 is taken, reddit-lens auto-picks the next available port — check the terminal window for which port it chose, then go to `http://localhost:XXXX/gem`)

That's it. The page talks to your already-running Flask server.

---

## What Each Tab Does

### ⚡ Live Feed tab
- Enter subreddits (comma-separated) — or leave blank to use ALL subreddits from your `config.json`
- Click **Fetch Now** — triggers reddit-lens's Python scraper (same code as the Fetch button in the original UI)
- Results are scored and sorted by Gem Score
- **Delta tracking**: refresh again and you'll see Δ columns showing score/comment changes since last fetch
- Auto-refresh available (1 min, 2 min, 5 min, 10 min intervals)

### 🔗 Paste URLs tab
- Paste any Reddit URLs (messy text, one per line, whatever)
- The Flask server fetches live data for each post ID via Reddit's `/api/info.json`
- Same scoring applied

### Scoring modes (Priority selector, top right)
| Mode | What it weights |
|------|----------------|
| Balanced | Even mix of all four signals |
| Spicy | Heavy on controversy + vote split |
| Thought-Provoking | Heavy on comment ratio (discussion depth) |
| WTF | Extreme comment ratio (comment explosions) |

### Scoring signals
| Signal | Formula | What it means |
|--------|---------|---------------|
| Controversy | `(1 − upvote_ratio) × 2 × log10(score+1) × 50` | Divisive posts with lots of votes |
| Velocity | `Engagement / log10(age_hours+2) × 5` | How fast engagement is accumulating |
| Comment Ratio | `min(comments / max(score,5) × 30, 100)` | Discussion relative to upvotes |
| Engagement | `log10(score + comments×3 + 1) × 20` | Raw weighted popularity |
| **Gem Score** | Weighted sum of above | The combined rank |

---

## How It Works (Technical)

The integrated HTML talks to the reddit-lens Flask server via its existing API:

| Action | Endpoint used |
|--------|--------------|
| Fetch subreddits | `POST /fetch` |
| Get stored posts | `GET /posts` (new endpoint added to app.py — see note below) |
| Fetch single post by ID | `GET /proxy/https://www.reddit.com/api/info.json?id=...` |

**Important:** The integrated HTML needs one small addition to `app.py` — a `/posts` endpoint that returns stored posts as JSON, and a `/gem` route that serves the HTML file. These are handled by a tiny patch file `app_patch.py` in this folder. See the next section.

---

## Applying the app.py Patch

Instead of editing `app.py` directly, `app_patch.py` monkey-patches it at startup.

**Option A (Recommended): Import the patch**

Add ONE line to the very bottom of `reddit-lens\app.py`:

```python
import app_patch  # noqa — adds /gem and /posts routes
```

**Option B: Run patch directly**

Run `app_patch.py` instead of `app.py`:
```
python app_patch.py
```

The patch file imports everything from `app.py` and adds the two new routes before starting the server. Nothing in `app.py` is altered.

---

## Troubleshooting

**"Cannot connect to server"**
→ Make sure `start.bat` is running. Check which port the terminal shows.

**Posts not loading**
→ Hit Fetch Now first (or use the original reddit-lens UI to fetch). The database needs posts before they appear.

**"0 posts returned"**
→ The database is empty. Click Fetch Now, wait ~10 seconds, try again.

**Port isn't 5001**
→ reddit-lens auto-increments. Update the port in the top of `reddit_gem_integrated.html` (one constant: `const FLASK_PORT = 5001`).
