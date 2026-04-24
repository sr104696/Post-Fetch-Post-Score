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

def controversy_score(post: Dict) -> float:
    """
    Controversy based on upvote_ratio — the only controversy signal since
    Reddit hid raw downvotes in 2014. High score + near-50% ratio = controversial.
    Returns 0–100.
    """
    score = post.get("score", 0)
    ratio = post.get("upvote_ratio", 1.0)
    if score <= 0:
        return 0.0
    return min(100.0, (1 - ratio) * 2 * math.log10(score + 1) * 50)


def velocity_score(post: Dict) -> float:
    """
    Engagement velocity: comment + upvote momentum relative to post age.
    Returns 0–100.
    """
    score = max(post.get("score", 0), 0)
    comments = max(post.get("num_comments", 0), 0)
    age_hours = max((time.time() - post.get("created_utc", time.time())) / 3600, 0.1)
    engagement = math.log10(score + comments * 3 + 1) * 20
    return min(100.0, engagement / math.log10(age_hours + 2) * 5)


def comment_ratio_score(post: Dict) -> float:
    """
    Comment-to-upvote ratio — high ratio signals discussion-heavy posts.
    Returns 0–100.
    """
    score = max(post.get("score", 1), 1)
    comments = max(post.get("num_comments", 0), 0)
    return min(100.0, (comments / score) * 30)


def gem_score(post: Dict) -> float:
    """
    Composite score: weighs controversy, velocity, comment ratio, and raw engagement.
    Returns 0–100.
    """
    c = controversy_score(post)
    v = velocity_score(post)
    r = comment_ratio_score(post)
    score = max(post.get("score", 0), 0)
    comments = max(post.get("num_comments", 0), 0)
    e = min(100.0, math.log10(score + comments * 3 + 1) * 20)
    return round(c * 0.30 + v * 0.30 + r * 0.25 + e * 0.15, 2)


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

    t_param = "&t=week" if sort in ("controversial", "top") else ""
    url = f"https://www.reddit.com/r/{sub_query}/{sort}.json?limit={limit * len(subs)}&raw_json=1{t_param}"

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
                log.warning(
                    "Reddit %d on batch %s (attempt %d), retrying in %.1fs",
                    resp.status_code, subs[:2], attempt + 1, sleep_time,
                )
                time.sleep(sleep_time)
                continue
            resp.raise_for_status()
            data = resp.json()
            break
        except Exception as exc:
            log.error("Error fetching batch %s: %s", subs[:2], exc)
            if attempt == 2:
                return []
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
            "title":        title,
            "url":          url,
            "subreddit":    d.get("subreddit", ""),
            "sort":         sort,
            "score":        d.get("score", 0),
            "upvote_ratio": d.get("upvote_ratio", 1.0),
            "num_comments": d.get("num_comments", 0),
            "created_utc":  d.get("created_utc", time.time()),
            "over_18":      d.get("over_18", False),
            "flair":        d.get("link_flair_text") or "",
            "author":       d.get("author", ""),
            "preview":      (d.get("selftext") or "")[:200].strip(),
            "is_crosspost": bool(d.get("crosspost_parent")),
            "permalink":    f"https://reddit.com{d.get('permalink', '')}",
        }

        # Apply specific filters for the subreddit this post came from
        source_cfg = cfg_map.get(sub)
        if source_cfg and not passes_filters(post, source_cfg):
            continue

        # Compute all scores (post dict is fully populated above)
        post["controversy_score"]  = controversy_score(post)
        post["velocity"]           = velocity_score(post)
        post["comment_ratio_score"] = comment_ratio_score(post)
        post["gem_score"]          = gem_score(post)

        results.append(post)

    log.info("Batch [%s...] (%s) → %d posts", subs[0], sort, len(results))
    return results


def suggest_subreddits(query: str) -> List[str]:
    """Fetch similar/related subreddits using Reddit search."""
    try:
        url = f"https://www.reddit.com/subreddits/search.json?q={query}&limit=25"
        resp = requests.get(url, headers={"User-Agent": _ua()}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        subs = []
        for child in data.get("data", {}).get("children", []):
            sub_data = child.get("data", {})
            sub_name = sub_data.get("display_name")
            if sub_name and not sub_data.get("over18", False):
                subs.append(sub_name)
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

    base_limit = config.get("limit_per_sub", 50)
    limit = max(2, (10 * base_limit) // len(sources)) if len(sources) > 10 else base_limit
    log.info("fetch_all: adjusted limit_per_sub to %d for %d sources (base: %d)", limit, len(sources), base_limit)

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
            batches.append(srcs[i:i + 10])

    items: List[Dict] = []
    with ThreadPoolExecutor(max_workers=min(len(batches), 4)) as ex:
        futures = {ex.submit(fetch_subreddits_batch, b, limit): b for b in batches}
        for fut in as_completed(futures):
            try:
                items.extend(fut.result())
                batch_srcs = futures[fut]
                for s in batch_srcs:
                    s["last_fetched"] = now
            except Exception as exc:
                log.error("fetch_all batch error: %s", exc)

    log.info("fetch_all() total: %d posts from %d active sources", len(items), len(active_sources))
    return items
