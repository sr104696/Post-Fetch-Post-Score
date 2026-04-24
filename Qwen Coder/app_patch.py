"""
app_patch.py - Monkey-patch for reddit-lens app.py

This file adds two new routes to the Flask server:
- GET /gem - Serves the reddit_gem_integrated.html file
- GET /posts - Returns stored posts as JSON with delta tracking

Usage Option A: Add this line to the bottom of app.py:
    import app_patch  # noqa

Usage Option B: Run this file directly instead of app.py:
    python app_patch.py
"""

import os
import json
import sqlite3
from datetime import datetime
from flask import jsonify, send_file, request

# Try to import from app.py (when used as a patch)
try:
    from app import app, db, scraper, get_db_connection
    _patched = True
except ImportError:
    # Running standalone - need to set up Flask app
    from flask import Flask
    app = Flask(__name__)
    _patched = False
    db = None
    scraper = None
    
    def get_db_connection():
        conn = sqlite3.connect('posts.db')
        conn.row_factory = sqlite3.Row
        return conn


@app.route('/gem')
def serve_gem_ui():
    """Serve the integrated Reddit Gem Finder UI"""
    html_path = os.path.join(os.path.dirname(__file__), 'reddit_gem_integrated.html')
    if os.path.exists(html_path):
        return send_file(html_path)
    else:
        # Try looking in parent directory
        html_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'reddit_gem_integrated.html')
        if os.path.exists(html_path):
            return send_file(html_path)
        return "reddit_gem_integrated.html not found", 404


@app.route('/posts', methods=['GET'])
def get_posts():
    """Return all stored posts as JSON with scoring and delta tracking"""
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
    
    # Calculate scores and deltas
    posts = []
    for row in rows:
        post_dict = dict(row)
        
        # Calculate gem score
        score_data = calculate_gem_score(post_dict)
        post_dict.update(score_data)
        
        # Calculate deltas (compare to previous fetch if available)
        deltas = calculate_deltas(post_dict)
        post_dict['deltas'] = deltas
        
        posts.append(post_dict)
    
    return jsonify({
        'success': True,
        'count': len(posts),
        'posts': posts
    })


def calculate_gem_score(post):
    """Calculate gem score components based on the scoring formulas"""
    score = post.get('score', 0) or 0
    comments = post.get('num_comments', 0) or 0
    upvote_ratio = post.get('upvote_ratio', 0.5) or 0.5
    created_utc = post.get('created_utc', 0) or 0
    
    # Calculate age in hours
    now = datetime.utcnow().timestamp()
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
    
    # Gem Score (Balanced weights)
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


def calculate_deltas(post):
    """Calculate deltas compared to previously stored data"""
    # This would need historical data tracking
    # For now, return zeros - can be enhanced later
    return {
        'score_delta': 0,
        'comments_delta': 0,
        'gem_score_delta': 0
    }


# Import math for calculations
import math

# If running standalone (not as a patch), start the server
if __name__ == '__main__' and not _patched:
    print("Running app_patch.py standalone mode")
    print("Note: This mode doesn't have access to reddit-lens database")
    print("For full functionality, import this module in app.py instead")
    app.run(host='0.0.0.0', port=5001, debug=True)
