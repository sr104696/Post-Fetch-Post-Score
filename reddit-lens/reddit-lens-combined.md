# Reddit Lens — Combined Reference

## README

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

---

## `requirements.txt`

```text
flask==3.1.0
requests==2.32.3
playwright==1.58.0
```

---

## `.gitignore`

```text
.venv/
__pycache__/
*.pyc
.idea/
.junie/
.pytest_cache/
config.json
*.log
*.db
```

---

## `start.bat`

```bat
@echo off
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if %errorlevel% neq 0 (
    echo Python not found. Install from https://www.python.org/downloads/
    pause & exit /b 1
)

if not exist ".venv\" (
    echo First-time setup — installing packages...
    python -m venv .venv
    call .venv\Scripts\activate.bat
    pip install -q -r requirements.txt
)

:: Launch with pythonw so no console window appears
start "" ".venv\Scripts\pythonw.exe" app.py
```

---

## `config.example.json`

```json
{
  "reddit_sources": [
    {
      "subreddit": "news",
      "sort": "hot",
      "min_score": 50,
      "nsfw": false,
      "flair_filter": [],
      "keyword_blocklist": [],
      "keyword_allowlist": [],
      "last_fetched": 1776905957.8901956
    }
  ],
  "limit_per_sub": 25,
  "fetch_cooldown_seconds": 0,
  "db_path": "reddit_lens.db",
  "error_log": "error.log",
  "server_host": "127.0.0.1",
  "server_port_start": 5001,
  "server_port_tries": 20,
  "max_display": 200,
  "max_screenshots": 50,
  "email_export": {
    "enabled": true,
    "smtp_server": "smtp.gmail.com",
    "smtp_port": 587,
    "smtp_username": "your-email@gmail.com",
    "smtp_password": "",
    "from_email": "your-email@gmail.com",
    "to_email": "your-email@gmail.com",
    "smtp_use_tls": true
  }
}
```

---

## `db.py`

```python
"""db.py — SQLite deduplication for reddit-lens."""

import logging
import sqlite3
import time
from typing import Dict, List, Tuple

log = logging.getLogger(__name__)

# SQLite retry counter for diagnostics
_sqlite_retry_counter = {"count": 0}


def _with_retry(fn, *args, **kwargs):
    """Retry on 'database is locked' with 50/200/500ms backoff."""
    delays = [0.05, 0.2, 0.5]
    last_exc = None
    for delay in delays + [None]:
        try:
            return fn(*args, **kwargs)
        except sqlite3.OperationalError as e:
            if "locked" not in str(e).lower():
                raise
            last_exc = e
            _sqlite_retry_counter["count"] += 1
            if delay is None:
                raise
            time.sleep(delay)


def get_sqlite_retry_count() -> int:
    """Return the number of SQLite retries since startup."""
    return _sqlite_retry_counter["count"]


def _open_db(db_path: str):
    """Open a connection with pragmas set."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(db_path: str) -> None:
    with _open_db(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS seen_posts (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                url               TEXT    NOT NULL UNIQUE,
                title             TEXT,
                subreddit         TEXT    DEFAULT '',
                score             INTEGER DEFAULT 0,
                controversy_score REAL    DEFAULT 0,
                velocity          REAL    DEFAULT 0,
                permalink         TEXT    DEFAULT '',
                created_utc       REAL    DEFAULT 0,
                over_18           INTEGER DEFAULT 0,
                flair             TEXT    DEFAULT '',
                preview           TEXT    DEFAULT '',
                is_crosspost      INTEGER DEFAULT 0,
                upvote_ratio      REAL    DEFAULT 1.0,
                num_comments      INTEGER DEFAULT 0,
                first_seen        DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cols = [c[1] for c in conn.execute("PRAGMA table_info(seen_posts)").fetchall()]
        _MIGRATIONS = [
            ("permalink", "TEXT DEFAULT ''"),
            ("created_utc", "REAL DEFAULT 0"),
            ("over_18", "INTEGER DEFAULT 0"),
            ("flair", "TEXT DEFAULT ''"),
            ("preview", "TEXT DEFAULT ''"),
            ("is_crosspost", "INTEGER DEFAULT 0"),
            ("upvote_ratio", "REAL DEFAULT 1.0"),
            ("num_comments", "INTEGER DEFAULT 0"),
        ]
        for col_name, col_type in _MIGRATIONS:
            if col_name not in cols:
                _with_retry(
                    lambda c=conn, n=col_name, t=col_type:
                        c.execute(f"ALTER TABLE seen_posts ADD COLUMN {n} {t}")
                )
        conn.commit()
    log.info("DB ready: %s", db_path)


def _exec_insert(conn, sql: str, params: tuple):
    """Helper to execute INSERT with retry."""
    return _with_retry(lambda: conn.execute(sql, params))


def insert_new_posts(
    db_path: str,
    items: List[Dict],
) -> Tuple[List[Dict], int]:
    """Insert posts with default-arg capture to avoid closure issues."""
    new_items: List[Dict] = []
    skipped = 0
    sql = """INSERT INTO seen_posts
       (url, title, subreddit, score, controversy_score, velocity,
        permalink, created_utc, over_18, flair, preview, is_crosspost,
        upvote_ratio, num_comments)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
       ON CONFLICT(url) DO UPDATE SET
        score=excluded.score,
        controversy_score=excluded.controversy_score,
        velocity=excluded.velocity,
        permalink=excluded.permalink,
        created_utc=excluded.created_utc,
        over_18=excluded.over_18,
        flair=excluded.flair,
        preview=excluded.preview,
        is_crosspost=excluded.is_crosspost,
        upvote_ratio=excluded.upvote_ratio,
        num_comments=excluded.num_comments
    """
    with _open_db(db_path) as conn:
        for item in items:
            url = (item.get("url") or "").strip()
            if not url or not url.startswith("http"):
                skipped += 1
                continue
            try:
                # Default-arg capture to avoid lambda closure issues
                _exec_insert(
                    conn, sql,
                    (
                        url,
                        item.get("title", ""),
                        item.get("subreddit", ""),
                        item.get("score", 0),
                        item.get("controversy_score", 0.0),
                        item.get("velocity", 0.0),
                        item.get("permalink", ""),
                        item.get("created_utc", 0.0),
                        1 if item.get("over_18") else 0,
                        item.get("flair", ""),
                        item.get("preview", ""),
                        1 if item.get("is_crosspost") else 0,
                        item.get("upvote_ratio", 1.0),
                        item.get("num_comments", 0),
                    )
                )
                new_items.append(item)
            except Exception as e:
                log.error("DB error: %s", e)
                skipped += 1
        conn.commit()
    log.info("insert_new_posts: %d new, %d skipped", len(new_items), skipped)
    return new_items, skipped


def get_recent_posts(db_path: str, limit: int = 200) -> List[Dict]:
    with _open_db(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM seen_posts ORDER BY first_seen DESC LIMIT ?",
            (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_stats(db_path: str) -> Dict:
    with _open_db(db_path) as conn:
        total = conn.execute("SELECT COUNT(*) FROM seen_posts").fetchone()[0]
        rows = conn.execute(
            "SELECT subreddit, COUNT(*) FROM seen_posts "
            "WHERE subreddit != '' GROUP BY subreddit ORDER BY 2 DESC LIMIT 20"
        ).fetchall()
    return {
        "total": total,
        "by_subreddit": [{"subreddit": r[0], "count": r[1]} for r in rows],
    }


def get_posts_for_export(db_path: str, limit: int = 200) -> List[Dict]:
    """Fetch posts for email export, formatted for readability."""
    with _open_db(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT title, url, subreddit, score, num_comments, created_utc, permalink
               FROM seen_posts ORDER BY first_seen DESC LIMIT ?""",
            (limit,)
        ).fetchall()
    return [dict(r) for r in rows]
```

---

## `scraper.py`

```python
"""scraper.py — Reddit fetcher with scoring and filtering for reddit-lens."""

import logging
import math
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional

import requests

log = logging.getLogger(__name__)

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
]


def _ua() -> str:
    return random.choice(_USER_AGENTS)


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def controversy_score(score: int, upvote_ratio: float) -> float:
    """
    Reconstruct Reddit-style controversy from score + upvote_ratio.
    High score + near-50% ratio = maximally controversial.
    Returns 0 for posts with no meaningful vote split.
    """
    if upvote_ratio >= 1.0 or upvote_ratio <= 0.0 or score <= 0:
        return 0.0
    # Infer ups/downs: score = ups - downs, ratio = ups/(ups+downs)
    # Solving: ups = ratio*(ups+downs), score = ups-downs
    # => ups+downs = score / (2*ratio - 1)  [when ratio != 0.5]
    if abs(upvote_ratio - 0.5) < 0.001:
        total = score * 2  # can't solve exactly at 50%, approximate
    else:
        total = score / (2 * upvote_ratio - 1)
    ups = round(total * upvote_ratio)
    downs = round(total * (1 - upvote_ratio))
    if ups <= 0 or downs <= 0:
        return 0.0
    magnitude = ups + downs
    balance = min(ups, downs) / max(ups, downs)
    return magnitude ** balance


def velocity_score(num_comments: int, created_utc: float) -> float:
    """Comments per hour since post creation. Capped age at 0.25h to avoid div/0 on brand-new posts."""
    age_hours = max((time.time() - created_utc) / 3600, 0.25)
    return num_comments / age_hours


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------

def passes_filters(post: Dict, source_cfg: Dict) -> bool:
    """Return True if post passes all configured filters for its source."""
    # Score threshold
    if post.get("score", 0) < source_cfg.get("min_score", 0):
        return False

    # NSFW
    if not source_cfg.get("nsfw", False) and post.get("over_18", False):
        return False

    # Flair filter (allowlist — if set, post must match one)
    flair_filter = source_cfg.get("flair_filter", [])
    if flair_filter:
        post_flair = (post.get("flair") or "").lower()
        if not any(f.lower() in post_flair for f in flair_filter):
            return False

    # Keyword filters applied to title + flair
    text = f"{post.get('title', '')} {post.get('flair', '')}".lower()

    blocklist = source_cfg.get("keyword_blocklist", [])
    if blocklist and any(kw.lower() in text for kw in blocklist):
        return False

    allowlist = source_cfg.get("keyword_allowlist", [])
    if allowlist and not any(kw.lower() in text for kw in allowlist):
        return False

    return True


# ---------------------------------------------------------------------------
# Fetcher
# ---------------------------------------------------------------------------

def fetch_subreddits_batch(sources_cfg: List[Dict], limit: int = 25) -> List[Dict]:
    """
    Fetch posts from multiple subreddits in one request using the r/sub1+sub2/sort.json endpoint.
    """
    if not sources_cfg:
        return []
    
    # We assume all sources in a batch have the same sort
    sort = sources_cfg[0].get("sort", "hot").strip()
    subs = [s.get("subreddit", "").strip() for s in sources_cfg]
    subs = [s for s in subs if s]
    if not subs:
        return []
    
    sub_query = "+".join(subs)
    
    # Per-task jitter
    time.sleep(random.uniform(0.1, 0.8))

    url = f"https://www.reddit.com/r/{sub_query}/{sort}.json?limit={limit * len(subs)}&raw_json=1"
    
    data = None
    for attempt in range(3):
        try:
            resp = requests.get(
                url,
                headers={"User-Agent": _ua(), "Accept": "application/json"},
                timeout=20,
            )
            if resp.status_code in (429, 500, 502, 503, 504):
                sleep_time = (2 ** attempt) + random.uniform(0.5, 1.5)
                log.warning("Reddit %d on batch %s (attempt %d), retrying in %.1fs", 
                          resp.status_code, subs[:2], attempt + 1, sleep_time)
                time.sleep(sleep_time)
                continue
            resp.raise_for_status()
            data = resp.json()
            break
        except Exception as exc:
            log.error("Error fetching batch %s: %s", subs[:2], exc)
            if attempt == 2: return []
            time.sleep(1)

    if not data:
        return []

    results: List[Dict] = []
    children = data.get("data", {}).get("children", [])
    
    # Create a lookup for source configs by subreddit name
    cfg_map = {s.get("subreddit", "").lower(): s for s in sources_cfg}

    for child in children:
        d = child.get("data", {})
        title = (d.get("title") or "").strip()
        url   = (d.get("url") or "").strip()
        sub   = d.get("subreddit", "").lower()
        
        if not title or not url or url.startswith("/r/"):
            continue

        post = {
            "title":       title,
            "url":         url,
            "subreddit":   d.get("subreddit", ""),
            "sort":        sort,
            "score":       d.get("score", 0),
            "upvote_ratio": d.get("upvote_ratio", 1.0),
            "num_comments": d.get("num_comments", 0),
            "created_utc": d.get("created_utc", time.time()),
            "over_18":     d.get("over_18", False),
            "flair":       d.get("link_flair_text") or "",
            "author":      d.get("author", ""),
            "preview":     (d.get("selftext") or "")[:200].strip(),
            "is_crosspost": bool(d.get("crosspost_parent")),
            "permalink":   f"https://reddit.com{d.get('permalink', '')}",
        }

        # Apply specific filters for the subreddit this post came from
        source_cfg = cfg_map.get(sub)
        if source_cfg and not passes_filters(post, source_cfg):
            continue

        post["controversy_score"] = controversy_score(post["score"], post["upvote_ratio"])
        post["velocity"]          = velocity_score(post["num_comments"], post["created_utc"])

        results.append(post)

    log.info("Batch [%s...] (%s) → %d posts", subs[0], sort, len(results))
    return results


def suggest_subreddits(query: str) -> List[str]:
    """Fetch similar/related subreddits using Reddit search."""
    try:
        # Using subreddits/search for better relevance to the query name
        url = f"https://www.reddit.com/subreddits/search.json?q={query}&limit=25"
        resp = requests.get(url, headers={"User-Agent": _ua()}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        subs = []
        for child in data.get("data", {}).get("children", []):
            sub_data = child.get("data", {})
            sub_name = sub_data.get("display_name")
            # Filter out NSFW if query wasn't obviously NSFW or just as a safety
            if sub_name and not sub_data.get("over18", False):
                subs.append(sub_name)
        
        # Deduplicate and return
        return list(dict.fromkeys(subs))
    except Exception as e:
        log.error(f"Error suggesting subreddits for {query}: {e}")
        return []


def suggest_by_overlap(subreddit: str) -> List[str]:
    """Fetch user-overlap similar subreddits from subredditstats.com."""
    import re
    try:
        url = f"https://subredditstats.com/api/user-subreddit-overlaps?subreddit={subreddit}"
        resp = requests.get(url, headers={"User-Agent": _ua()}, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        subs = []
        if isinstance(data, list):
            for item in data:
                name = item.get("subreddit") or item.get("name") or item.get("displayName")
                if name:
                    subs.append(name)
        elif isinstance(data, dict):
            for item in data.get("overlaps", data.get("subreddits", [])):
                name = item.get("subreddit") or item.get("name")
                if name:
                    subs.append(name)
        return list(dict.fromkeys(subs))[:25]
    except Exception as e:
        log.error(f"Error fetching overlap for {subreddit}: {e}")
        return []


def suggest_by_semantic(subreddit: str) -> List[str]:
    """Find semantically similar subreddits via Reddit search with alternate query."""
    try:
        query = f"like {subreddit}"
        url = f"https://www.reddit.com/subreddits/search.json?q={requests.utils.quote(query)}&limit=25"
        resp = requests.get(url, headers={"User-Agent": _ua()}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        subs = []
        for child in data.get("data", {}).get("children", []):
            sub_data = child.get("data", {})
            name = sub_data.get("display_name")
            if name and not sub_data.get("over18", False):
                subs.append(name)
        return list(dict.fromkeys(subs))
    except Exception as e:
        log.error(f"Error fetching semantic suggestions for {subreddit}: {e}")
        return []


def suggest_by_graph(subreddit: str) -> List[str]:
    """Extract related subreddits from the subreddit's own sidebar/description links."""
    import re
    try:
        about_url = f"https://www.reddit.com/r/{subreddit}/about.json"
        resp = requests.get(about_url, headers={"User-Agent": _ua()}, timeout=10)
        resp.raise_for_status()
        about = resp.json().get("data", {})
        text = " ".join(filter(None, [
            about.get("description", ""),
            about.get("public_description", ""),
            about.get("submit_text", ""),
        ]))
        mentions = re.findall(r'(?:^|[^/])r/([A-Za-z0-9_]{2,21})', text)
        # Dedupe, filter out the source subreddit itself
        seen, results = set(), []
        for m in mentions:
            key = m.lower()
            if key != subreddit.lower() and key not in seen:
                seen.add(key)
                results.append(m)
        if results:
            return results[:25]
        # Fallback: keyword search using public description
        desc = (about.get("public_description", "") or "")[:80].strip() or subreddit
        url = f"https://www.reddit.com/subreddits/search.json?q={requests.utils.quote(desc)}&limit=25"
        resp2 = requests.get(url, headers={"User-Agent": _ua()}, timeout=10)
        resp2.raise_for_status()
        data = resp2.json()
        subs = []
        for child in data.get("data", {}).get("children", []):
            sub_data = child.get("data", {})
            name = sub_data.get("display_name")
            if name and not sub_data.get("over18", False):
                subs.append(name)
        return list(dict.fromkeys(subs))[:25]
    except Exception as e:
        log.error(f"Error fetching graph suggestions for {subreddit}: {e}")
        return []


def suggest_by_xposts(subreddit: str) -> List[str]:
    """Find related subreddits via cross-post analysis of the subreddit's hot posts."""
    try:
        url = f"https://www.reddit.com/r/{subreddit}/hot.json?limit=50"
        resp = requests.get(url, headers={"User-Agent": _ua()}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        xpost_subs = []
        for child in data.get("data", {}).get("children", []):
            post = child.get("data", {})
            for xp in (post.get("crosspost_parent_list") or []):
                sr = xp.get("subreddit")
                if sr and sr.lower() != subreddit.lower():
                    xpost_subs.append(sr)
        if xpost_subs:
            return list(dict.fromkeys(xpost_subs))[:25]
        # Fallback: standard search
        return suggest_subreddits(subreddit)[:25]
    except Exception as e:
        log.error(f"Error fetching xpost suggestions for {subreddit}: {e}")
        return []


def suggest_all_methods(subreddit: str) -> List[str]:
    """Aggregate suggestions from all methods in parallel, deduped."""
    fns = [suggest_subreddits, suggest_by_overlap, suggest_by_semantic, suggest_by_graph, suggest_by_xposts]
    results: List[str] = []
    seen: set = set()
    with ThreadPoolExecutor(max_workers=len(fns)) as pool:
        futures = {pool.submit(fn, subreddit): fn for fn in fns}
        for future in as_completed(futures):
            try:
                for name in (future.result() or []):
                    key = name.lower()
                    if key != subreddit.lower() and key not in seen:
                        seen.add(key)
                        results.append(name)
            except Exception as e:
                log.error(f"suggest_all_methods partial error: {e}")
    return results


def fetch_all(config: Dict) -> List[Dict]:
    """Fetch all configured subreddits using batched requests."""
    sources = config.get("reddit_sources", [])
    if not sources:
        return []

    base_limit = config.get("limit_per_sub", 25)
    # Ensure no more overall posts pulled: total posts goal ≈ 10 * base_limit (approx 250)
    # If sources grow, we decrease limit per sub.
    # Minimum limit 2 to avoid useless calls.
    limit = max(2, (10 * base_limit) // len(sources)) if len(sources) > 10 else base_limit
    log.info("fetch_all: adjusted limit_per_sub to %d for %d sources (base: %d)", limit, len(sources), base_limit)

    # Cooldown check
    cooldown = config.get("fetch_cooldown_seconds", 300)
    now = time.time()
    
    active_sources = []
    for s in sources:
        last = s.get("last_fetched", 0)
        if now - last < cooldown:
            log.info("Skipping r/%s (fetched %.1fs ago)", s["subreddit"], now - last)
            continue
        active_sources.append(s)

    if not active_sources:
        log.info("All sources within cooldown. Nothing to fetch.")
        return []

    # Group by sort
    by_sort = {}
    for s in active_sources:
        sort = s.get("sort", "hot")
        by_sort.setdefault(sort, []).append(s)

    # Create batches of 10
    batches = []
    for sort, srcs in by_sort.items():
        for i in range(0, len(srcs), 10):
            batches.append(srcs[i:i+10])

    items: List[Dict] = []
    with ThreadPoolExecutor(max_workers=min(len(batches), 4)) as ex:
        futures = {ex.submit(fetch_subreddits_batch, b, limit): b for b in batches}
        for fut in as_completed(futures):
            try:
                items.extend(fut.result())
                # Update last_fetched for these sources
                batch_srcs = futures[fut]
                for s in batch_srcs:
                    s["last_fetched"] = now
            except Exception as exc:
                log.error("fetch_all batch error: %s", exc)

    log.info("fetch_all() total: %d posts from %d active sources", len(items), len(active_sources))
    return items
```

---

## `app.py`

Due to the large size of app.py (1400+ lines), it contains the Flask application with routes for:
- `/` - Main UI
- `/fetch` - Fetch Reddit posts
- `/sources` - Manage subreddit sources
- `/export` - Email export
- `/export/images` - Screenshot export
- `/export/screenshots/*` - Async screenshot job management
- `/api/bug-report` - Generate diagnostic report
- Subreddit suggestion endpoints

---

## `templates/index.html`

The frontend HTML file contains a complete single-page application with:
- Dark theme UI matching GitHub's design
- Post fetching and filtering
- Export options (text, card view, screenshots)
- Subreddit management with similarity search
- Real-time progress tracking
- Bug report generation
- Client-side console logging for diagnostics
