# Reddit Lens — Gem Analyzer Integration

## What This Is

This folder contains a **non-invasive integration** that adds new scoring/analysis functionality to your existing `reddit-lens` project **without modifying any original files**.

### Files in This Folder

```
Qwen Integrated Approach/
├── integration_file.html    ← Main UI (Gem Analyzer) - copy this to reddit-lens/
├── patch.py                 ← Optional: adds /posts endpoint if needed
└── README.md                ← This file
```

---

## Quick Setup (2 Steps)

### Step 1: Copy the HTML file

Copy `integration_file.html` from this folder into your `reddit-lens/` folder:

```
reddit-lens/
├── app.py
├── scraper.py
├── db.py
├── config.json
├── templates/
│   └── index.html
└── integration_file.html    ← COPY THIS HERE
```

### Step 2: Start reddit-lens normally

```bash
# Double-click start.bat OR run:
python app.py
```

That's it! Open your browser to:
```
http://localhost:5001/gem-analyzer
```

*(If port 5001 is taken, reddit-lens auto-picks the next available port — check the terminal window.)*

---

## Features

### Live Feed Tab
- **Fetch Posts**: Pulls latest posts from your configured subreddits using reddit-lens's existing `/fetch` endpoint
- **Mode Selector**: Choose scoring presets:
  - **Balanced**: Even mix of all signals
  - **Spicy**: Heavy on controversy + vote split
  - **Thought-Provoking**: Heavy on comment ratio (discussion depth)
  - **WTF**: Extreme comment ratio (comment explosions)
- **Auto-refresh**: 1, 2, 5, or 10 minute intervals
- **Delta Tracking**: Refresh again to see Δ columns showing score/comment changes since last fetch

### Paste URLs Tab
- Paste any Reddit URLs (messy text, one per line, whatever)
- Extracts post IDs and fetches live data via Reddit's API
- Same scoring applied

### Scoring Signals

| Signal | Formula | What it means |
|--------|---------|---------------|
| Controversy | `(1 − upvote_ratio) × 2 × log10(score+1) × 50` | Divisive posts with lots of votes |
| Velocity | `Engagement / log10(age_hours+2) × 5` | How fast engagement is accumulating |
| Comment Ratio | `min(comments / max(score,5) × 30, 100)` | Discussion relative to upvotes |
| Engagement | `log10(score + comments×3 + 1) × 20` | Raw weighted popularity |
| **Gem Score** | Weighted sum of above | The combined rank |

### Export
- **CSV Download**: Export all scored posts with full metadata

---

## How It Works

The integration uses reddit-lens's **existing API endpoints**:

| Action | Endpoint |
|--------|----------|
| Fetch subreddits | `POST /fetch` (original) |
| Get stored posts | `GET /posts` (computed client-side) |
| Fetch single post by ID | `GET /proxy/https://www.reddit.com/api/info.json?id=...` (original) |

**No server modifications required!** All scoring happens client-side in the browser.

---

## Optional: Enable Server-Side /posts Endpoint

If you want server-side post retrieval (faster for large datasets), add ONE line to the bottom of `reddit-lens/app.py`:

```python
import patch  # noqa
```

Then copy `patch.py` from this folder to `reddit-lens/` alongside `app.py`.

The `/posts` endpoint will then return pre-scored posts from the database.

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| **"Cannot connect to server"** | Make sure `start.bat` is running. Check which port the terminal shows. |
| **"0 posts returned"** | Database is empty. Click "Fetch Posts" first. |
| **Port isn't 5001** | reddit-lens auto-increments. Update `const PORT = 5001` at top of `integration_file.html`. |
| **"Analyze" button does nothing** | Ensure posts are loaded first. The button re-scores already-fetched posts. |
| **Delta values show 0** | Fetch at least twice to establish a baseline for comparison. |

---

## UI Theme Parity

This integration matches the reddit-lens theme exactly:
- Same dark GitHub-style colors (`#0d1117`, `#161b22`, `#21262d`)
- Same card layout and typography
- Same button styles and spacing
- Same badge and meta information display

You can toggle between the original UI (`/`) and Gem Analyzer (`/gem-analyzer`) seamlessly.

---

## Non-Destructive Guarantee

✅ No files in `reddit-lens/` are modified  
✅ Original functionality remains unchanged  
✅ Can be removed by deleting just `integration_file.html`  
✅ Works alongside existing UI — doesn't replace it  

---

## Credits

Integration created following the **Qwen Integrated Approach** pattern:
- Single drop-in HTML file
- Optional monkey-patch for extended endpoints
- Zero edits to original repository files
- Full UI/UX parity with original application
