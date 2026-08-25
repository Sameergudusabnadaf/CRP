from flask import Flask, request, jsonify, send_from_directory, send_file
from flask_cors import CORS
import json
import os
import time
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.join(BASE_DIR, '..', 'frontend')

app = Flask(__name__, static_folder=FRONTEND_DIR, static_url_path='')
CORS(app)

PORT = int(os.environ.get('PORT', 3000))

SETTINGS_FILE = os.path.join(BASE_DIR, 'settings.json')

DEFAULT_URLS = {
    'kannada': 'https://docs.google.com/spreadsheets/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms/edit?usp=sharing',
    'english': 'https://docs.google.com/spreadsheets/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms/edit?usp=sharing',
    'maths': 'https://docs.google.com/spreadsheets/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms/edit?usp=sharing'
}

# In-memory active sessions (replaces Node.js Map)
active_sessions = {}


import sqlite3

DB_FILE = os.path.join(BASE_DIR, 'dad.db')

def get_db_connection():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Create tables
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS subject_sheets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject TEXT NOT NULL,
            name TEXT NOT NULL,
            url TEXT NOT NULL,
            UNIQUE(subject, name)
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            teacher TEXT NOT NULL,
            subject TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            sheet_url TEXT NOT NULL,
            UNIQUE(teacher, subject, sheet_url)
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            email TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            password TEXT NOT NULL
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS counters (
            key TEXT PRIMARY KEY,
            value INTEGER NOT NULL
        )
    ''')
    
    # Seed default counters if not present
    cursor.execute("INSERT OR IGNORE INTO counters (key, value) VALUES ('completedCount', 35)")
    cursor.execute("INSERT OR IGNORE INTO counters (key, value) VALUES ('notCompletedCount', 12)")
    
    # Migration from settings.json if it exists and SQLite is empty
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # Check if we already migrated
            cursor.execute("SELECT COUNT(*) FROM users")
            user_count = cursor.fetchone()[0]
            
            if user_count == 0:
                print("Migrating data from settings.json to SQLite database...")
                # Migrate users
                for u in data.get('users', []):
                    cursor.execute("INSERT OR IGNORE INTO users (email, name, password) VALUES (?, ?, ?)",
                                   (u.get('email'), u.get('name'), u.get('password')))
                
                # Migrate subject_sheets
                for subj in ['kannada', 'english', 'maths']:
                    for sheet in data.get(subj, []):
                        cursor.execute("INSERT OR IGNORE INTO subject_sheets (subject, name, url) VALUES (?, ?, ?)",
                                       (subj, sheet.get('name'), sheet.get('url')))
                
                # Migrate submissions
                submissions = data.get('submissions', {})
                for k, sub in submissions.items():
                    cursor.execute("INSERT OR IGNORE INTO submissions (teacher, subject, timestamp, sheet_url) VALUES (?, ?, ?, ?)",
                                   (sub.get('teacher'), sub.get('subject'), sub.get('timestamp'), sub.get('sheetUrl', '')))
                
                # Migrate counters
                if 'completedCount' in data:
                    cursor.execute("INSERT OR REPLACE INTO counters (key, value) VALUES ('completedCount', ?)", (data['completedCount'],))
                if 'notCompletedCount' in data:
                    cursor.execute("INSERT OR REPLACE INTO counters (key, value) VALUES ('notCompletedCount', ?)", (data['notCompletedCount'],))
                
                conn.commit()
                print("Migration completed successfully.")
        except Exception as e:
            print("Error migrating settings.json:", e)
            
    conn.commit()
    conn.close()

# Initialize the SQLite database
init_db()


def has_subject_changed(old_list, new_list):
    """Check if a subject's link list has changed."""
    if old_list is None and new_list is None:
        return False
    if old_list is None or new_list is None:
        return True
    if len(old_list) != len(new_list):
        return True
    for old_item, new_item in zip(old_list, new_list):
        if old_item.get('name') != new_item.get('name') or old_item.get('url') != new_item.get('url'):
            return True
    return False


# ──────────────────────────────────────────────
# GET /api/settings
# ──────────────────────────────────────────────
@app.route('/api/settings', methods=['GET'])
def get_settings():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT subject, name, url FROM subject_sheets")
    rows = cursor.fetchall()
    
    settings = {
        'kannada': [],
        'english': [],
        'maths': []
    }
    for row in rows:
        subj = row['subject']
        if subj in settings:
            settings[subj].append({
                'name': row['name'],
                'url': row['url']
            })
            
    conn.close()
    
    # Fallback to defaults if a subject has no sheets
    for subj in ['kannada', 'english', 'maths']:
        if not settings[subj]:
            settings[subj] = [{'name': 'Default Sheet', 'url': DEFAULT_URLS[subj]}]
            
    return jsonify(settings)


# ──────────────────────────────────────────────
# POST /api/settings
# ──────────────────────────────────────────────
def get_subject_sheets_from_db(cursor, subject):
    cursor.execute("SELECT name, url FROM subject_sheets WHERE subject = ?", (subject,))
    return [{'name': r['name'], 'url': r['url']} for r in cursor.fetchall()]

@app.route('/api/settings', methods=['POST'])
def post_settings():
    new_settings = request.get_json(force=True)
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cleared_submissions = 0
    
    for subject in ['kannada', 'english', 'maths']:
        if subject in new_settings:
            old_list = get_subject_sheets_from_db(cursor, subject)
            new_list = new_settings[subject]
            
            if has_subject_changed(old_list, new_list):
                # Count and delete submissions for this subject
                cursor.execute("SELECT COUNT(*) FROM submissions WHERE subject = ?", (subject,))
                cleared_submissions += cursor.fetchone()[0]
                
                cursor.execute("DELETE FROM submissions WHERE subject = ?", (subject,))
            
            # Delete old sheets and insert new ones
            cursor.execute("DELETE FROM subject_sheets WHERE subject = ?", (subject,))
            for sheet in new_list:
                cursor.execute("INSERT INTO subject_sheets (subject, name, url) VALUES (?, ?, ?)",
                               (subject, sheet.get('name'), sheet.get('url')))
                
    if cleared_submissions > 0:
        cursor.execute("SELECT value FROM counters WHERE key = 'completedCount'")
        completed = cursor.fetchone()[0]
        cursor.execute("SELECT value FROM counters WHERE key = 'notCompletedCount'")
        pending = cursor.fetchone()[0]
        
        new_completed = max(0, completed - cleared_submissions)
        new_pending = pending + cleared_submissions
        
        cursor.execute("INSERT OR REPLACE INTO counters (key, value) VALUES ('completedCount', ?)", (new_completed,))
        cursor.execute("INSERT OR REPLACE INTO counters (key, value) VALUES ('notCompletedCount', ?)", (new_pending,))
        
    conn.commit()
    conn.close()
    return jsonify({'success': True})


# ──────────────────────────────────────────────
# POST /api/heartbeat
# ──────────────────────────────────────────────
@app.route('/api/heartbeat', methods=['POST'])
def heartbeat():
    data = request.get_json(force=True)
    user_id = data.get('userId')
    role = data.get('role', '')
    if user_id:
        active_sessions[f"{role}:{user_id}"] = time.time()
    return jsonify({'success': True})


# ──────────────────────────────────────────────
# GET /api/stats
# ──────────────────────────────────────────────
@app.route('/api/stats', methods=['GET'])
def get_stats():
    now = time.time()
    stale_keys = [k for k, ts in active_sessions.items() if now - ts > 30]
    for k in stale_keys:
        del active_sessions[k]
        
    active_teacher_count = sum(1 for k in active_sessions if k.startswith('teacher'))
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Get users
    cursor.execute("SELECT email, name, password FROM users")
    users_rows = cursor.fetchall()
    users_list = []
    registered_teachers = set()
    for r in users_rows:
        users_list.append({
            'email': r['email'],
            'name': r['name'],
            'password': r['password']
        })
        if r['name'].lower() != 'admin' and r['email'].lower() != 'admin':
            registered_teachers.add(r['name'].lower())
    
    # Get submissions
    cursor.execute("SELECT teacher, subject, timestamp, sheet_url FROM submissions")
    submissions_rows = cursor.fetchall()
    submissions_list = []
    today_prefix = datetime.utcnow().isoformat()[:10]
    daily_submitted_teachers = set()
    
    for r in submissions_rows:
        submissions_list.append({
            'teacher': r['teacher'],
            'subject': r['subject'],
            'timestamp': r['timestamp'],
            'sheetUrl': r['sheet_url']
        })
        # Check if submission is from today
        if r['timestamp'] and str(r['timestamp']).startswith(today_prefix):
            daily_submitted_teachers.add(r['teacher'].lower())
            
    # Calculate daily completed & pending counts
    completed = len(daily_submitted_teachers.intersection(registered_teachers))
    not_completed = len(registered_teachers) - completed
    if not_completed < 0:
        not_completed = 0

    active_teachers_list = []
    for k in active_sessions:
        if k.startswith('teacher:'):
            username = k.split(':', 1)[1]
            cursor.execute("SELECT name FROM users WHERE email = ?", (username,))
            row = cursor.fetchone()
            name = row['name'] if row else username
            active_teachers_list.append({
                'email': username,
                'name': name
            })
            
    conn.close()
    
    return jsonify({
        'activeTeachers': 24 + active_teacher_count,
        'completedTasks': completed,
        'notCompletedTasks': not_completed,
        'submissions': submissions_list,
        'users': users_list,
        'activeTeachersList': active_teachers_list
    })


# ──────────────────────────────────────────────
# POST /api/submit
# ──────────────────────────────────────────────
@app.route('/api/submit', methods=['POST'])
def submit_progress():
    data = request.get_json(force=True)
    teacher = data.get('teacher')
    subject = data.get('subject')
    sheet_url = data.get('sheetUrl', '')

    if not teacher or not subject:
        return jsonify({'error': 'Missing teacher or subject parameter'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Check if this exact submission exists
    cursor.execute("""
        SELECT COUNT(*) FROM submissions 
        WHERE teacher = ? AND subject = ? AND sheet_url = ?
    """, (teacher, subject, sheet_url))
    exists = cursor.fetchone()[0] > 0
    
    if not exists:
        timestamp = datetime.utcnow().isoformat() + 'Z'
        cursor.execute("""
            INSERT OR IGNORE INTO submissions (teacher, subject, timestamp, sheet_url)
            VALUES (?, ?, ?, ?)
        """, (teacher, subject, timestamp, sheet_url))
        
        conn.commit()
        conn.close()
        
        return jsonify({
            'success': True
        })
    else:
        conn.close()
        return jsonify({'success': True, 'message': 'Already submitted'})


# ──────────────────────────────────────────────
# POST /api/delete-submission
# ──────────────────────────────────────────────
@app.route('/api/delete-submission', methods=['POST'])
def delete_submission():
    data = request.get_json(force=True)
    teacher = data.get('teacher')
    subject = data.get('subject')
    sheet_url = data.get('sheetUrl', '')

    if not teacher or not subject:
        return jsonify({'error': 'Missing teacher or subject parameter'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Check if exists
    cursor.execute("""
        SELECT COUNT(*) FROM submissions 
        WHERE teacher = ? AND subject = ? AND sheet_url = ?
    """, (teacher, subject, sheet_url))
    exists = cursor.fetchone()[0] > 0
    
    if exists:
        cursor.execute("""
            DELETE FROM submissions 
            WHERE teacher = ? AND subject = ? AND sheet_url = ?
        """, (teacher, subject, sheet_url))
        
        # Adjust counters
        cursor.execute("SELECT value FROM counters WHERE key = 'completedCount'")
        completed = cursor.fetchone()[0]
        cursor.execute("SELECT value FROM counters WHERE key = 'notCompletedCount'")
        pending = cursor.fetchone()[0]
        
        new_completed = max(0, completed - 1)
        new_pending = pending + 1
        
        cursor.execute("INSERT OR REPLACE INTO counters (key, value) VALUES ('completedCount', ?)", (new_completed,))
        cursor.execute("INSERT OR REPLACE INTO counters (key, value) VALUES ('notCompletedCount', ?)", (new_pending,))
        
        conn.commit()
        conn.close()
        return jsonify({
            'success': True,
            'completed': new_completed,
            'notCompleted': new_pending
        })
    else:
        conn.close()
        return jsonify({'success': False, 'message': 'Submission not found'}), 404


# ──────────────────────────────────────────────
# POST /api/add-user
# ──────────────────────────────────────────────
@app.route('/api/add-user', methods=['POST'])
def add_user():
    data = request.get_json(force=True)
    email = data.get('email')
    name = data.get('name')
    password = data.get('password')

    if not email or not name or not password:
        return jsonify({'error': 'Missing email, name, or password parameter'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Check duplicate
    cursor.execute("SELECT COUNT(*) FROM users WHERE email = ?", (email,))
    exists = cursor.fetchone()[0] > 0
    
    if not exists:
        cursor.execute("INSERT INTO users (email, name, password) VALUES (?, ?, ?)",
                       (email, name, password))
        conn.commit()
        
        # Fetch updated users list
        cursor.execute("SELECT email, name, password FROM users")
        users_rows = cursor.fetchall()
        users_list = [{'email': r['email'], 'name': r['name'], 'password': r['password']} for r in users_rows]
        
        conn.close()
        return jsonify({'success': True, 'users': users_list})
    else:
        conn.close()
        return jsonify({'success': False, 'message': 'User already exists'}), 409


# ──────────────────────────────────────────────
# POST /api/delete-user
# ──────────────────────────────────────────────
@app.route('/api/delete-user', methods=['POST'])
def delete_user():
    data = request.get_json(force=True)
    email = data.get('email')

    if not email:
        return jsonify({'error': 'Missing email parameter'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Check if exists
    cursor.execute("SELECT COUNT(*) FROM users WHERE email = ?", (email,))
    exists = cursor.fetchone()[0] > 0
    
    if exists:
        cursor.execute("DELETE FROM users WHERE email = ?", (email,))
        conn.commit()
        
        # Fetch updated users list
        cursor.execute("SELECT email, name, password FROM users")
        users_rows = cursor.fetchall()
        users_list = [{'email': r['email'], 'name': r['name'], 'password': r['password']} for r in users_rows]
        
        conn.close()
        return jsonify({'success': True, 'users': users_list})
    else:
        conn.close()
        return jsonify({'success': False, 'message': 'User not found'}), 404


# ──────────────────────────────────────────────
# POST /api/edit-user
# ──────────────────────────────────────────────
@app.route('/api/edit-user', methods=['POST'])
def edit_user():
    data = request.get_json(force=True)
    email = data.get('email')
    new_name = data.get('name')
    new_password = data.get('password')

    if not email:
        return jsonify({'error': 'Missing email parameter'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()

    # Verify user exists
    cursor.execute("SELECT COUNT(*) FROM users WHERE email = ?", (email,))
    exists = cursor.fetchone()[0] > 0
    if not exists:
        conn.close()
        return jsonify({'success': False, 'message': 'User not found'}), 404

    # Perform update based on what is provided
    if new_name and new_password:
        cursor.execute("UPDATE users SET name = ?, password = ? WHERE email = ?", (new_name, new_password, email))
    elif new_name:
        cursor.execute("UPDATE users SET name = ? WHERE email = ?", (new_name, email))
    elif new_password:
        cursor.execute("UPDATE users SET password = ? WHERE email = ?", (new_password, email))

    conn.commit()
    conn.close()
    return jsonify({'success': True})


# ──────────────────────────────────────────────
# Serve index.html at root
# ──────────────────────────────────────────────
@app.route('/')
def index():
    return send_file(os.path.join(FRONTEND_DIR, 'index.html'))


# ──────────────────────────────────────────────
# Serve static files (HTML, CSS, JS, images)
# ──────────────────────────────────────────────
@app.route('/<path:filename>')
def serve_static(filename):
    return send_from_directory(FRONTEND_DIR, filename)


if __name__ == '__main__':
    print(f"Server is running at http://localhost:{PORT}")
    print(f"Accessible on local network at http://YOUR_IP_ADDRESS:{PORT}")
    app.run(host='0.0.0.0', port=PORT, debug=True)
