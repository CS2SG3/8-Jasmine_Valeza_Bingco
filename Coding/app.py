import sys
import os
import webbrowser
import threading
import time
import requests
import json
import calendar
import winshell
from win32com.client import Dispatch
from threading import Timer
from flask import Flask, render_template, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta, timezone

# --- ENVIRONMENT & PATHING ---
if getattr(sys, 'frozen', False):
    base_dir = os.path.dirname(sys.executable)
    template_folder = os.path.join(sys._MEIPASS, 'templates')
    app = Flask(__name__, template_folder=template_folder)
else:
    base_dir = os.path.abspath(os.path.dirname(__file__))
    app = Flask(__name__)

db_path = os.path.join(base_dir, 'test.db')
SETTINGS_FILE = os.path.join(base_dir, 'settings.json')

app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + db_path
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

TOKEN = "8106344330:AAGMtCtqWqhRO2XhK_OpFiA3OMKQpQQ_9os"
PH_TZ = timezone(timedelta(hours=8))

def get_user_data():
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, 'r') as f:
                return json.load(f)
        except: return {}
    return {}

def send_telegram(msg):
    data = get_user_data()
    chat_id = data.get('chat_id')
    if not chat_id: return
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    try: requests.post(url, data={"chat_id": chat_id, "text": msg}, timeout=5)
    except: pass

def create_startup_shortcut():
    try:
        startup_path = winshell.startup()
        shortcut_path = os.path.join(startup_path, "Timely.lnk")
        if not os.path.exists(shortcut_path):
            target = sys.executable if getattr(sys, 'frozen', False) else os.path.abspath(__file__)
            shell = Dispatch('WScript.Shell')
            shortcut = shell.CreateShortCut(shortcut_path)
            shortcut.Targetpath = target
            shortcut.WorkingDirectory = os.path.dirname(target)
            shortcut.save()
    except: pass

# --- DATABASE MODEL ---
class Todo(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.String(200), nullable=False)
    day = db.Column(db.Integer, default=0) 
    month = db.Column(db.Integer, default=0)
    year = db.Column(db.Integer, default=0)
    reminder_time = db.Column(db.String(5), nullable=True)
    completed = db.Column(db.Boolean, default=False)
    position = db.Column(db.Integer, default=0) # For Drag-and-Drop order

def run_notification_bot():
    sent_today = set()
    while True:
        now_ph = datetime.now(PH_TZ)
        current_hm = now_ph.strftime("%H:%M")
        with app.app_context():
            # Refresh the session to see new database entries
            db.session.expire_all()
            
            # Find tasks scheduled for the current time
            tasks = Todo.query.filter(Todo.day > 0, Todo.reminder_time == current_hm).all()
            
            for t in tasks:
                # Unique key prevents duplicate messages in the same minute
                key = f"{t.id}_{datetime.now().date()}"
                if key not in sent_today:
                    # Format the time for the message (e.g., 08:30 PM)
                    t_obj = datetime.strptime(t.reminder_time, "%H:%M")
                    nice_time = t_obj.strftime("%I:%M %p")
                    
                    # --- UPDATED TELEGRAM MESSAGE ---
                    # Includes the month and day from the database
                    date_str = f"{calendar.month_name[t.month]} {t.day}"
                    message = (
                        f"📌 TIMELY REMINDER\n"
                        f"Requirement: {t.content}\n"
                        f"📅 Date: {date_str}\n"
                        f"⏰ Time: {nice_time}"
                    )
                    
                    send_telegram(message)
                    sent_today.add(key)
                    
        # Clear the 'sent' cache at midnight to allow reminders for the next day
        if current_hm == "00:00":
            sent_today.clear()
            
        time.sleep(45)

# --- ROUTES ---
@app.route('/')
def index():
    now = datetime.now()
    user_data = get_user_data()
    # Sort by position for persistent drag-and-drop
    tasks = Todo.query.filter_by(day=0).order_by(Todo.position.asc()).all()
    return render_template('index.html', tasks=tasks, today=now.strftime("%B %d, %Y"), 
                           now=now, user_name=user_data.get('name'), 
                           chat_id=user_data.get('chat_id'), 
                           logged_in=bool(user_data.get('chat_id')))

@app.route('/save_id', methods=['POST'])
def save_id():
    cid = request.form.get('chat_id')
    name = request.form.get('name')
    with open(SETTINGS_FILE, 'w') as f:
        json.dump({'chat_id': cid if cid else None, 'name': name if name else None}, f)
    if cid: send_telegram(f"🚀 Timely Dashboard unlocked for {name}!")
    return jsonify({"success": True})

@app.route('/tasks', methods=['POST'])
def add_task():
    # Set position to the current max + 1
    max_pos = db.session.query(db.func.max(Todo.position)).filter(Todo.day == 0).scalar() or 0
    new_t = Todo(content=request.form.get('content'), day=0, position=max_pos + 1)
    db.session.add(new_t); db.session.commit()
    return jsonify({"id": new_t.id, "content": new_t.content})

@app.route('/reorder_tasks', methods=['POST'])
def reorder_tasks():
    order = request.json.get('order') # List of IDs
    for idx, item_id in enumerate(order):
        task = Todo.query.get(int(item_id))
        if task: task.position = idx
    db.session.commit()
    return jsonify({"success": True})

@app.route('/add_event', methods=['POST'])
def add_event():
    new_e = Todo(content=request.form.get('content'), day=int(request.form.get('day')), 
                 month=int(request.form.get('month')), year=int(request.form.get('year')),
                 reminder_time=request.form.get('reminder_time'))
    db.session.add(new_e); db.session.commit()
    return jsonify({"success": True})

@app.route('/toggle_task/<int:id>', methods=['POST'])
def toggle_task(id):
    item = Todo.query.get_or_404(id); item.completed = not item.completed; db.session.commit()
    return jsonify({"success": True})

@app.route('/calendar_data/<int:year>/<int:month>')
def calendar_data(year, month):
    events = Todo.query.filter_by(month=month, year=year).all()
    event_list = [{"id": e.id, "content": e.content, "day": e.day, "time": e.reminder_time, 
                   "nice_time": datetime.strptime(e.reminder_time, "%H:%M").strftime("%I:%M %p")} for e in events]
    p_m, p_y = (month-1, year) if month > 1 else (12, year-1)
    n_m, n_y = (month+1, year) if month < 12 else (1, year+1)
    return jsonify({"month_name": calendar.month_name[month], "year": year, "month": month, 
                    "cal": calendar.monthcalendar(year, month), "events": event_list, 
                    "p_m": p_m, "p_y": p_y, "n_m": n_m, "n_y": n_y})

@app.route('/delete/<int:id>', methods=['DELETE'])
def delete_item(id):
    item = Todo.query.get_or_404(id); db.session.delete(item); db.session.commit()
    return jsonify({"success": True})

@app.route('/shutdown', methods=['POST'])
def shutdown(): os._exit(0)

if __name__ == "__main__":
    with app.app_context(): db.create_all()
    create_startup_shortcut()
    threading.Thread(target=run_notification_bot, daemon=True).start()
    if not os.environ.get("WERKZEUG_RUN_MAIN"): 
        Timer(1.5, lambda: webbrowser.open("http://127.0.0.1:5000/")).start()
    app.run(port=5000, debug=False)