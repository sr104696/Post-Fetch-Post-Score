# Reddit Lens — Project Structure

```
reddit-lens/
│
├── app.py                      # Flask web application (main entry point)
├── scraper.py                  # Reddit fetching and scoring logic
├── db.py                       # SQLite database operations
│
├── config.example.json         # Configuration template
├── requirements.txt            # Python dependencies
├── start.bat                   # Windows startup script
├── .gitignore                  # Git exclusion rules
├── README.md                   # Project documentation
│
├── templates/                  # Flask templates
│   └── index.html              # Single-page web UI
│
├── .venv/                      # Python virtual environment (excluded from git)
│   ├── Scripts/                # Windows executables
│   │   ├── python.exe
│   │   ├── pip.exe
│   │   ├── flask.exe
│   │   └── playwright.exe
│   ├── Lib/                    # Python packages
│   │   └── site-packages/
│   └── pyvenv.cfg
│
├── __pycache__/                # Python bytecode cache (excluded from git)
│   ├── app.cpython-313.pyc
│   ├── scraper.cpython-313.pyc
│   └── db.cpython-313.pyc
│
├── .idea/                      # PyCharm/IDE files (excluded from git)
│   ├── workspace.xml
│   └── vcs.xml
│
├── config.json                 # Active configuration (excluded from git)
├── reddit_lens.db              # SQLite database (excluded from git)
├── reddit_lens.db-wal          # SQLite WAL file (excluded from git)
├── reddit_lens.db-shm          # SQLite shared memory (excluded from git)
├── error.log                   # Application error log (excluded from git)
└── .port                       # Current server port (runtime)
```

## File Categories

### Source Code (Tracked in Git)
```
app.py
scraper.py
db.py
templates/index.html
```

### Configuration (Template Tracked)
```
config.example.json    ✓ tracked
config.json            ✗ excluded (contains secrets)
```

### Dependencies
```
requirements.txt       ✓ tracked
.venv/                 ✗ excluded (generated)
```

### Documentation
```
README.md              ✓ tracked
.gitignore             ✓ tracked
```

### Runtime/Generated (Excluded)
```
__pycache__/           ✗ Python bytecode
.idea/                 ✗ IDE files
*.db, *.db-wal, *.db-shm  ✗ Database files
*.log                  ✗ Log files
.port                  ✗ Runtime state
```

## Minimal Setup Structure

For a fresh clone, you only need:
```
reddit-lens/
├── app.py
├── scraper.py
├── db.py
├── templates/
│   └── index.html
├── config.example.json
├── requirements.txt
├── start.bat
├── .gitignore
└── README.md
```

Run `start.bat` to generate:
- `.venv/` (virtual environment)
- `config.json` (copy from example)
- `reddit_lens.db` (on first fetch)

## Directory Sizes (Approximate)

```
.venv/              ~150 MB   (Python + packages)
__pycache__/        ~200 KB   (bytecode cache)
templates/          ~45 KB    (HTML/CSS/JS)
reddit_lens.db      ~150 KB   (grows with posts)
Source files        ~75 KB    (Python code)
```

## Port Discovery

The app finds an available port starting from `5001`:
```
config.json: "server_port_start": 5001
Runtime: .port file contains actual port used
```

## Database Schema

```
reddit_lens.db
└── seen_posts
    ├── id (PRIMARY KEY)
    ├── url (UNIQUE)
    ├── title
    ├── subreddit
    ├── score
    ├── controversy_score
    ├── velocity
    ├── permalink
    ├── created_utc
    ├── over_18
    ├── flair
    ├── preview
    ├── is_crosspost
    ├── upvote_ratio
    ├── num_comments
    └── first_seen
```

## Execution Flow

```
start.bat
    ↓
Check Python
    ↓
Create .venv (if missing)
    ↓
Install requirements.txt
    ↓
Launch app.py
    ↓
├─ Load config.json
├─ Initialize reddit_lens.db
├─ Probe Chromium
├─ Find free port → .port
└─ Start Flask server
    ↓
Open browser → http://127.0.0.1:5001
```
