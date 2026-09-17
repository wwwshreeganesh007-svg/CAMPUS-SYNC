import os
import sqlite3
import csv
from io import StringIO
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, flash, session, Response
from werkzeug.security import generate_password_hash, check_password_hash
import random

app = Flask(__name__)
app.secret_key = "dpmg_campussync_secret_key"
app.permanent_session_lifetime = timedelta(minutes=30)
DB_FILE = 'campussync.db'

def get_db():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (
        email TEXT PRIMARY KEY,
        name TEXT,
        password TEXT,
        role TEXT,
        club TEXT,
        status TEXT
    )''')
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS clubs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE,
        category TEXT,
        description TEXT,
        coordinator TEXT,
        threshold INTEGER DEFAULT 75
    )''')
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        club TEXT,
        name TEXT,
        date TEXT,
        start_time TEXT,
        end_time TEXT,
        venue TEXT,
        code TEXT,
        status TEXT DEFAULT 'Active',
        expires_at TEXT
    )''')
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS members (
        email TEXT,
        name TEXT,
        club TEXT,
        attendance_pct INTEGER DEFAULT 100,
        PRIMARY KEY (email, club)
    )''')
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS attendance (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT,
        event TEXT,
        status TEXT,
        reason TEXT
    )''')
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS correction_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT,
        club TEXT,
        event TEXT,
        reason TEXT,
        status TEXT DEFAULT 'Pending'
    )''')
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS audit_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        action TEXT,
        user TEXT,
        details TEXT,
        timestamp TEXT
    )''')
    
    # Seed default accounts
    admin_pw = generate_password_hash('admin123')
    user_pw = generate_password_hash('password')
    cursor.execute("INSERT OR IGNORE INTO users VALUES ('admin@xime.edu', 'System Admin', ?, 'super_admin', NULL, 'approved')", (admin_pw,))
    cursor.execute("INSERT OR IGNORE INTO users VALUES ('ganesh@college.edu', 'Shree Ganesh', ?, 'club_admin', 'Finance Club', 'approved')", (user_pw,))
    cursor.execute("INSERT OR IGNORE INTO users VALUES ('student@college.edu', 'Mock Student', ?, 'student', 'Finance Club', 'approved')", (user_pw,))
    
    # Seed default clubs
    cursor.execute("INSERT OR IGNORE INTO clubs (name, category, description, coordinator, threshold) VALUES ('Finance Club', 'Finance', 'Finance and trading.', 'ganesh@college.edu', 80)")
    cursor.execute("INSERT OR IGNORE INTO clubs (name, category, description, coordinator, threshold) VALUES ('Marketing Club', 'Marketing', 'Marketing enthusiasts.', 'None', 75)")
    cursor.execute("INSERT OR IGNORE INTO clubs (name, category, description, coordinator, threshold) VALUES ('Matrix Club', 'Operations', 'Operations management.', 'None', 75)")
    cursor.execute("INSERT OR IGNORE INTO clubs (name, category, description, coordinator, threshold) VALUES ('X-Insights Analytics Club', 'Analytics', 'Data analytics.', 'None', 75)")
    cursor.execute("INSERT OR IGNORE INTO clubs (name, category, description, coordinator, threshold) VALUES ('X Tech Club', 'Technology', 'Tech and coding.', 'None', 75)")
    
    cursor.execute("INSERT OR IGNORE INTO members VALUES ('student@college.edu', 'Mock Student', 'Finance Club', 100)")
    
    conn.commit()
    conn.close()

init_db()

def log_audit(action, user, details):
    conn = get_db()
    conn.execute("INSERT INTO audit_logs (action, user, details, timestamp) VALUES (?, ?, ?, ?)",
                 (action, user, details, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()

def recalc_attendance():
    conn = get_db()
    members_list = conn.execute("SELECT email, club FROM members").fetchall()
    for m in members_list:
        total = conn.execute("SELECT COUNT(*) FROM events WHERE club = ? AND status = 'Active'", (m['club'],)).fetchone()[0]
        if total == 0:
            pct = 100
        else:
            attended = conn.execute('''
                SELECT COUNT(*) FROM attendance 
                WHERE email = ? AND status IN ('Present', 'Excused') 
                AND event IN (SELECT name FROM events WHERE club = ? AND status = 'Active')
            ''', (m['email'], m['club'])).fetchone()[0]
            pct = int((attended / total) * 100)
        conn.execute("UPDATE members SET attendance_pct = ? WHERE email = ? AND club = ?", (pct, m['email'], m['club']))
    conn.commit()
    conn.close()

@app.route('/')
def index():
    return redirect(url_for('login'))

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    conn = get_db()
    if request.method == 'POST':
        email = request.form['email']
        name = request.form['name']
        pw = generate_password_hash(request.form['password'])
        role = request.form['role']
        club = request.form.get('club')
        conn.execute("INSERT OR REPLACE INTO users VALUES (?, ?, ?, ?, ?, 'pending')", (email, name, pw, role, club))
        conn.commit()
        conn.close()
        flash("Account created! Status: PENDING APPROVAL.", "success")
        return redirect(url_for('login'))
    clubs_data = conn.execute("SELECT * FROM clubs").fetchall()
    conn.close()
    return render_template('signup.html', clubs=clubs_data)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        session.permanent = True
        email = request.form['email']
        password = request.form['password']
        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        conn.close()
        
        if not user or not check_password_hash(user['password'], password):
            flash("Invalid Email or Password.", "error")
        elif user['status'] == 'pending':
            flash("Your account is pending admin approval.", "error")
        else:
            session['user'] = user['email']
            session['role'] = user['role']
            session['name'] = user['name']
            log_audit("LOGIN", user['email'], "Successful login.")
            
            if user['role'] == 'super_admin': return redirect(url_for('super_admin'))
            elif user['role'] == 'club_admin': return redirect(url_for('club_admin'))
            else: return redirect(url_for('student_dashboard'))
    return render_template('login.html')

@app.route('/super_admin', methods=['GET', 'POST'])
def super_admin():
    if 'user' not in session or session.get('role') != 'super_admin': return redirect(url_for('login'))
    conn = get_db()
    
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'approve_user':
            email = request.form.get('email')
            conn.execute("UPDATE users SET status = 'approved' WHERE email = ?", (email,))
            user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
            if user and user['role'] == 'student' and user['club']:
                conn.execute("INSERT OR IGNORE INTO members VALUES (?, ?, ?, 100)", (user['email'], user['name'], user['club']))
            conn.commit()
            log_audit("APPROVE_USER", session['user'], f"Approved {email}")
            flash(f"User {email} approved.", "success")
        elif action == 'create_club':
            conn.execute("INSERT INTO clubs (name, category, description, coordinator, threshold) VALUES (?, ?, ?, 'None', 75)",
                         (request.form.get('club_name'), request.form.get('category'), request.form.get('description')))
            conn.commit()
            log_audit("CREATE_CLUB", session['user'], f"Created {request.form.get('club_name')}")
            flash("Club created.", "success")
        elif action == 'assign_coordinator':
            conn.execute("UPDATE clubs SET coordinator = ? WHERE id = ?",
                         (request.form.get('coordinator_email'), int(request.form.get('club_id'))))
            conn.commit()
            flash("Coordinator reassigned.", "success")

    pending = conn.execute("SELECT * FROM users WHERE status = 'pending'").fetchall()
    all_clubs = conn.execute("SELECT * FROM clubs").fetchall()
    coords = conn.execute("SELECT * FROM users WHERE role = 'club_admin' AND status = 'approved'").fetchall()
    logs = conn.execute("SELECT * FROM audit_logs ORDER BY id DESC LIMIT 10").fetchall()
    conn.close()
    return render_template('super_admin.html', name=session['name'], pending_users=pending, clubs=all_clubs, coords=coords, logs=logs)

@app.route('/club_admin', methods=['GET', 'POST'])
def club_admin():
    if 'user' not in session or session.get('role') != 'club_admin': return redirect(url_for('login'))
    conn = get_db()
    my_club = conn.execute("SELECT * FROM clubs WHERE coordinator = ?", (session['user'],)).fetchone()
    if not my_club:
        conn.close()
        flash("Not assigned to a club.", "error")
        return redirect(url_for('login'))

    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'create_event':
            new_code = str(random.randint(100000, 999999))
            exp = (datetime.now() + timedelta(minutes=15)).strftime("%Y-%m-%d %H:%M:%S")
            conn.execute('''INSERT INTO events (club, name, date, start_time, end_time, venue, code, status, expires_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, 'Active', ?)''',
                         (my_club['name'], request.form.get('event_name'), request.form.get('date'),
                          request.form.get('start_time'), request.form.get('end_time'), request.form.get('venue'), new_code, exp))
            conn.commit()
            flash(f"Event created! Code: {new_code} (Valid for 15 mins)", "success")
        elif action == 'cancel_event':
            conn.execute("UPDATE events SET status = 'Cancelled' WHERE id = ?", (int(request.form.get('event_id')),))
            conn.commit()
            flash("Event cancelled.", "success")
        elif action == 'manual_override':
            email = request.form.get('student_email')
            ev_name = request.form.get('event_name')
            st = request.form.get('status')
            rs = request.form.get('reason')
            conn.execute("DELETE FROM attendance WHERE email = ? AND event = ?", (email, ev_name))
            conn.execute("INSERT INTO attendance (email, event, status, reason) VALUES (?, ?, ?, ?)", (email, ev_name, st, rs))
            conn.commit()
            log_audit("MANUAL_OVERRIDE", session['user'], f"Marked {email} as {st} for {ev_name}")
            flash("Manual override applied.", "success")
        elif action == 'resolve_request':
            conn.execute("UPDATE correction_requests SET status = 'Resolved' WHERE id = ?", (int(request.form.get('request_id')),))
            conn.commit()
            flash("Ticket resolved.", "success")

    recalc_attendance()
    members_data = conn.execute("SELECT * FROM members WHERE club = ?", (my_club['name'],)).fetchall()
    events_data = conn.execute("SELECT * FROM events WHERE club = ?", (my_club['name'],)).fetchall()
    flagged = conn.execute("SELECT * FROM members WHERE club = ? AND attendance_pct < ?", (my_club['name'], my_club['threshold'])).fetchall()
    requests_data = conn.execute("SELECT * FROM correction_requests WHERE club = ? AND status = 'Pending'", (my_club['name'],)).fetchall()
    attendance_data = conn.execute('''SELECT * FROM attendance WHERE event IN 
                                     (SELECT name FROM events WHERE club = ?)''', (my_club['name'],)).fetchall()
    conn.close()
    return render_template('club_admin.html', name=session['name'], club=my_club, members=members_data, events=events_data, flagged=flagged, requests=requests_data, attendance=attendance_data)

@app.route('/student_dashboard', methods=['GET', 'POST'])
def student_dashboard():
    if 'user' not in session or session.get('role') != 'student': return redirect(url_for('login'))
    conn = get_db()
    student = conn.execute("SELECT * FROM users WHERE email = ?", (session['user'],)).fetchone()
    student_club = student['club'] if student and student['club'] else 'None'
    
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'mark_attendance':
            code = request.form.get('code')
            ev = conn.execute("SELECT * FROM events WHERE club = ? AND code = ? AND status = 'Active'", (student_club, code)).fetchone()
            if ev:
                exp_dt = datetime.strptime(ev['expires_at'], "%Y-%m-%d %H:%M:%S")
                if datetime.now() > exp_dt:
                    flash("❌ This Session Code has expired (15-minute limit exceeded).", "error")
                else:
                    existing = conn.execute("SELECT * FROM attendance WHERE email = ? AND event = ?", (session['user'], ev['name'])).fetchone()
                    if existing:
                        flash("You have already marked attendance for this session.", "error")
                    else:
                        conn.execute("INSERT INTO attendance (email, event, status, reason) VALUES (?, ?, 'Present', 'System Code')", (session['user'], ev['name']))
                        conn.commit()
                        flash(f"✓ Attendance Verified for {ev['name']}!", "success")
            else:
                flash("❌ Invalid or Cancelled Session Code", "error")
        elif action == 'submit_correction':
            conn.execute("INSERT INTO correction_requests (email, club, event, reason, status) VALUES (?, ?, ?, ?, 'Pending')",
                         (session['user'], student_club, request.form.get('event_name'), request.form.get('reason')))
            conn.commit()
            flash("Correction request submitted.", "success")

    recalc_attendance()
    club_events = conn.execute("SELECT * FROM events WHERE club = ? AND status = 'Active'", (student_club,)).fetchall()
    member_record = conn.execute("SELECT * FROM members WHERE email = ? AND club = ?", (session['user'], student_club)).fetchone()
    current_pct = member_record['attendance_pct'] if member_record else 100
    attended_count = conn.execute("SELECT COUNT(*) FROM attendance WHERE email = ? AND status IN ('Present', 'Excused')", (session['user'],)).fetchone()[0]

    student_stats = {
        'total_clubs': 1,
        'workshops_attended': attended_count,
        'avg_attendance': current_pct,
        'memberships': [{'name': student_club, 'attended': attended_count, 'total': len(club_events), 'percentage': current_pct, 'role': 'Active Member'}]
    }
    records = conn.execute("SELECT * FROM attendance WHERE email = ?", (session['user'],)).fetchall()
    conn.close()
    return render_template('student_dashboard.html', name=session['name'], club=student_club, events=club_events, records=records, stats=student_stats)

@app.route('/report/export')
def export_report():
    if 'user' not in session: return redirect(url_for('login'))
    scope = request.args.get('scope')
    recalc_attendance()
    conn = get_db()
    
    if scope == 'System-Wide':
        export_members = conn.execute("SELECT * FROM members").fetchall()
    else:
        export_members = conn.execute("SELECT * FROM members WHERE club = ?", (scope,)).fetchall()
        
    def generate():
        data = StringIO()
        writer = csv.writer(data)
        writer.writerow(['CAMPUS SYNC - OFFICIAL ATTENDANCE REPORT'])
        writer.writerow(['Scope:', scope])
        writer.writerow(['Student Name', 'College Email', 'Club', 'Attendance Percentage'])
        for m in export_members:
            writer.writerow([m['name'], m['email'], m['club'], f"{m['attendance_pct']}%"])
            yield data.getvalue()
            data.seek(0)
            data.truncate(0)

    conn.close()
    response = Response(generate(), mimetype='text/csv')
    response.headers.set("Content-Disposition", f"attachment; filename=CampusSync_{scope}_Report.csv")
    return response

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)