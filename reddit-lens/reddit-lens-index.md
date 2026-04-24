# Reddit Lens — File Index

## Core Application Files

### `app.py`
**Purpose:** Main Flask web application server  
**Key Features:**
- Flask routes for UI and API endpoints
- Async screenshot job management with threading
- Email export functionality (text, images, ZIP)
- Subreddit suggestion endpoints (overlap, semantic, graph, xposts)
- Bug report generation with diagnostics
- Graceful shutdown handling
- Error logging and tracking

**Main Routes:**
- `/` - Web UI
- `/fetch` - Fetch posts from Reddit
- `/sources` - Manage subreddit sources (GET/POST)
- `/export` - Email text export
- `/export/images` - Email card view screenshot
- `/export/screenshots/*` - Async screenshot job system
- `/suggest_*` - Various subreddit suggestion methods
- `/api/bug-report` - Download diagnostic report
- `/stats` - Database statistics

### `scraper.py`
**Purpose:** Reddit data fetching and scoring logic  
**Key Features:**
- Batched subreddit fetching (up to 10 subs per request)
- Controversy score calculation (vote split analysis)
- Velocity score (comments per hour)
- Post filtering (score, NSFW, flair, keywords)
- Multiple subreddit suggestion methods
- Retry logic with exponential backoff
- User agent rotation

**Main Functions:**
- `fetch_all()` - Fetch from all configured sources
- `fetch_subreddits_batch()` - Batch fetch multiple subs
- `controversy_score()` - Calculate controversy metric
- `velocity_score()` - Calculate engagement velocity
- `suggest_*()` - Various similarity algorithms

### `db.py`
**Purpose:** SQLite database operations  
**Key Features:**
- Post deduplication by URL
- Automatic schema migrations
- WAL mode for concurrent access
- Retry logic for locked database
- Statistics and export queries

**Main Functions:**
- `init_db()` - Create/migrate schema
- `insert_new_posts()` - Deduplicate and insert
- `get_posts_for_export()` - Fetch for email/screenshot
- `get_stats()` - Database statistics

## Frontend

### `templates/index.html`
**Purpose:** Single-page web application UI  
**Key Features:**
- Dark theme (GitHub-inspired)
- Real-time post fetching and filtering
- Export options (text, card, screenshots)
- Subreddit management with similarity search
- Progress tracking for async jobs
- Client-side console logging
- Bug report generation

**UI Sections:**
- Header with action buttons
- Export options panel
- Fetch subreddit selector
- Sort/filter bar
- Subreddit source management
- Post grid display
- Similar subreddits modal

## Configuration

### `config.example.json`
**Purpose:** Configuration template  
**Settings:**
- `reddit_sources` - List of subreddits to monitor
- `limit_per_sub` - Posts per subreddit
- `fetch_cooldown_seconds` - Rate limiting
- `db_path` - SQLite database location
- `server_host` / `server_port_start` - Web server config
- `max_display` - UI post limit
- `max_screenshots` - Screenshot export limit
- `email_export` - SMTP configuration

### `requirements.txt`
**Purpose:** Python dependencies  
**Packages:**
- `flask` - Web framework
- `requests` - HTTP client
- `playwright` - Browser automation for screenshots

## Utilities

### `start.bat`
**Purpose:** Windows startup script  
**Actions:**
- Check for Python installation
- Create virtual environment if missing
- Install dependencies
- Launch app with pythonw (no console window)

### `.gitignore`
**Purpose:** Git exclusion rules  
**Excludes:**
- Virtual environment (`.venv/`)
- Python cache (`__pycache__/`, `*.pyc`)
- IDE files (`.idea/`, `.junie/`)
- Runtime files (`config.json`, `*.log`, `*.db`)

## Data Files (Runtime)

### `reddit_lens.db`
SQLite database storing fetched posts (created on first run)

### `reddit_lens.db-wal` / `reddit_lens.db-shm`
SQLite Write-Ahead Log files for concurrent access

### `config.json`
Active configuration (copy from `config.example.json`)

### `error.log`
Application error log

### `.port`
Current server port number

## Architecture Overview

```
User Request
    ↓
Flask (app.py)
    ↓
├─→ Scraper (scraper.py) → Reddit API
│       ↓
├─→ Database (db.py) → SQLite
│       ↓
└─→ Templates (index.html) → Browser
```

## Data Flow

1. **Fetch:** User clicks Fetch → Flask calls `scraper.fetch_all()` → Reddit JSON API → `db.insert_new_posts()` → Return new posts
2. **Export:** User selects export → Flask applies filters → Email via SMTP or Screenshot via Playwright
3. **Screenshot:** Async job created → Worker thread launches Playwright → Captures old.reddit.com pages → ZIP file → Download/Email
4. **Suggestions:** User searches similar subs → Multiple algorithms (overlap, semantic, graph, xposts) → Deduplicated results

## Key Design Patterns

- **Batching:** Multiple subreddits fetched in single Reddit API call
- **Deduplication:** Posts tracked by URL to avoid duplicates
- **Async Jobs:** Long-running screenshot tasks use background threads with polling
- **Retry Logic:** Database locks and Reddit rate limits handled with backoff
- **Filtering:** Posts filtered at fetch time (source config) and display time (UI)
