"""
patch.py — Monkey-patch for reddit-lens app.py

This file adds two new routes to the Flask server:
- GET /gem-analyzer - Serves the integration_file.html (Gem Analyzer UI)
- GET /posts - Returns stored posts as JSON with scoring and delta tracking

Usage Option A: Add this line to the bottom of app.py:
    import patch  # noqa

Usage Option B: Run this file directly instead of app.py:
    python patch.py
"""

import os
import json
import math
import sqlite3
from datetime import datetime, timezone
from flask import jsonify, send_file, request, Response

# Try to import from app.py (when used as a patch)
try:
    from app import app, DB_PATH, CONFIG, log_error
    _patched = True
except ImportError:
    # Running standalone - need to set up Flask app
    from flask import Flask
    app = Flask(__name__)
    _patched = False
    DB_PATH = 'reddit_lens.db'
    CONFIG = {}
    
    def log_error(where, exc):
        print(f"[ERROR] {where}: {exc}")


def get_db_connection():
    """Get a database connection with row factory."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


@app.route('/gem-analyzer')
def serve_gem_analyzer():
    """Serve the integrated Gem Analyzer UI."""
    # Look in the same directory as this patch file
    html_path = os.path.join(os.path.dirname(__file__), 'integration_file.html')
    if os.path.exists(html_path):
        return send_file(html_path)
    else:
        # Try looking one level up (if copied to reddit-lens root)
        html_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'integration_file.html')
        if os.path.exists(html_path):
            return send_file(html_path)
    return "integration_file.html not found. Please ensure it's in the same directory as patch.py or copy it to the project root.", 404


@app.route('/posts', methods=['GET'])
def get_posts():
    """Return all stored posts as JSON with scoring."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Get subreddit filter from query params
        subreddits = request.args.get('subreddits', '')

        if subreddits:
            # Filter by specific subreddits
            subreddit_list = [s.strip().lower() for s in subreddits.split(',') if s.strip()]
            placeholders = ','.join(['?' for _ in subreddit_list])
            query = f"""
                SELECT * FROM posts
                WHERE LOWER(subreddit) IN ({placeholders})
                ORDER BY created_utc DESC
            """
            cursor.execute(query, subreddit_list)
        else:
            # Get all posts
            cursor.execute("SELECT * FROM posts ORDER BY created_utc DESC")

        rows = cursor.fetchall()
        conn.close()

        # Calculate scores for each post
        posts = []
        for row in rows:
            post_dict = dict(row)
            score_data = calculate_gem_score(post_dict)
            post_dict.update(score_data)
            posts.append(post_dict)

        return jsonify({
            'success': True,
            'count': len(posts),
            'posts': posts
        })
    except Exception as exc:
        log_error("/posts", exc)
        return jsonify({'success': False, 'error': str(exc)}), 500


def calculate_gem_score(post):
    """Calculate gem score components based on the scoring formulas."""
    score = post.get('score', 0) or 0
    comments = post.get('num_comments', 0) or 0
    upvote_ratio = post.get('upvote_ratio', 0.5) or 0.5
    created_utc = post.get('created_utc', 0) or 0

    # Calculate age in hours
    now = datetime.now(timezone.utc).timestamp()
    age_seconds = max(now - created_utc, 60)  # Minimum 1 minute
    age_hours = age_seconds / 3600

    # Controversy: (1 − upvote_ratio) × 2 × log10(score+1) × 50
    controversy = (1 - upvote_ratio) * 2 * (math.log10(score + 1) if score > 0 else 0) * 50

    # Engagement: log10(score + comments×3 + 1) × 20
    engagement = math.log10(score + comments * 3 + 1) * 20

    # Velocity: Engagement / log10(age_hours+2) × 5
    velocity = engagement / (math.log10(age_hours + 2)) * 5 if age_hours >= 0 else 0

    # Comment Ratio: min(comments / max(score,5) × 30, 100)
    comment_ratio = min((comments / max(score, 5)) * 30, 100) if score > 0 else 0

    # Gem Score (Balanced weights - default)
    gem_score = (
        controversy * 0.25 +
        velocity * 0.25 +
        comment_ratio * 0.25 +
        engagement * 0.25
    )

    return {
        'controversy': round(controversy, 2),
        'velocity': round(velocity, 2),
        'comment_ratio': round(comment_ratio, 2),
        'engagement': round(engagement, 2),
        'gem_score': round(gem_score, 2),
        'age_hours': round(age_hours, 2)
    }


# If running standalone (not as a patch), start the server
if __name__ == '__main__' and not _patched:
    print("=" * 60)
    print("Running patch.py standalone mode")
    print("=" * 60)
    print("\n⚠️  WARNING: This mode doesn't have access to reddit-lens database!")
    print("For full functionality, use one of these options:\n")
    print("Option A: Add this line to the bottom of reddit-lens/app.py:")
    print("    import patch  # noqa\n")
    print("Option B: Copy integration_file.html to reddit-lens/ and run:")
    print("    python app.py\n")
    print("Then open: http://localhost:5001/gem-analyzer")
    print("=" * 60)
    app.run(host='0.0.0.0', port=5001, debug=True)
