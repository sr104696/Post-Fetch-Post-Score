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
