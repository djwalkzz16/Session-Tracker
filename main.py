import time
import json
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from flask import Flask, render_template, request
from flask_socketio import SocketIO, join_room, leave_room, emit
import requests

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'

socketio = SocketIO(app, cors_allowed_origins="*")

DB_FILE = 'focus_data.json'
USER_FILE = 'users.json'

# --- Safe User DB Loading ---
def load_users():
    if not os.path.exists(USER_FILE):
        with open(USER_FILE, 'w') as f:
            json.dump({}, f)
        return {}
    try:
        with open(USER_FILE, 'r') as f:
            return json.load(f)
    except Exception:
        return {}

def save_users(users_dict):
    with open(USER_FILE, 'w') as f:
        json.dump(users_dict, f, indent=4)

# --- Database / State Management ---
def load_state():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, 'r') as f:
                loaded_state = json.load(f)
                for user_id, user_data in loaded_state.items():
                    user_data['is_running'] = False
                    user_data['last_start_time'] = None
                return loaded_state
        except Exception as e:
            print(f"Error loading database: {e}")
            return {}
    return {}

def save_state():
    try:
        with open(DB_FILE, 'w') as f:
            json.dump(state, f, indent=4)
    except Exception as e:
        print(f"Error saving to database: {e}")

state = load_state()
load_users()

def init_user(user_id):
    if user_id not in state:
        state[user_id] = {
            'accumulated_time_ms': 0,
            'last_start_time': None,
            'is_running': False,
            'current_session': {
                'type': 'Ready',
                'goal': '',
                'startTime': ''
            },
            'session_history': [],
            'current_date': datetime.now(tz=ZoneInfo("Asia/Kolkata")).strftime('%Y-%m-%d')
        }
        save_state()

def check_new_day(user_id):
    init_user(user_id)
    today = datetime.now(tz=ZoneInfo("Asia/Kolkata")).strftime('%Y-%m-%d')
    if state[user_id].get('current_date') != today:
        state[user_id]['current_date'] = today
        save_state()

# --- Helper Functions ---
def get_current_elapsed_ms(user_id):
    init_user(user_id)
    u_state = state[user_id]
    if u_state['is_running'] and u_state['last_start_time'] is not None:
        now = time.time() * 1000
        return int(u_state['accumulated_time_ms'] + (now - u_state['last_start_time']))
    return int(u_state['accumulated_time_ms'])

def format_time(ms):
    seconds = int((ms / 1000) % 60)
    minutes = int((ms / 60000) % 60)
    hours = int(ms / 3600000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

def calculate_stats(user_id):
    init_user(user_id)
    u_state = state[user_id]
    today_str = datetime.now(tz=ZoneInfo("Asia/Kolkata")).strftime('%Y-%m-%d')
    seven_days_ago_str = (datetime.now(tz=ZoneInfo("Asia/Kolkata")) - timedelta(days=7)).strftime('%Y-%m-%d')
    
    daily_ms = 0
    weekly_ms = 0
    daily_breakdown = {}
    weekly_breakdown = {}

    for session in u_state.get('session_history', []):
        session_date_str = session.get('date', today_str) 
        t = session['type']
        d_ms = session['duration_ms']
        
        # String comparison works perfectly for YYYY-MM-DD dates
        if session_date_str >= seven_days_ago_str:
            weekly_ms += d_ms
            if t not in weekly_breakdown:
                weekly_breakdown[t] = {'count': 0, 'total_ms': 0}
            weekly_breakdown[t]['count'] += 1
            weekly_breakdown[t]['total_ms'] += d_ms
            
        if session_date_str == today_str:
            daily_ms += d_ms
            if t not in daily_breakdown:
                daily_breakdown[t] = {'count': 0, 'total_ms': 0}
            daily_breakdown[t]['count'] += 1
            daily_breakdown[t]['total_ms'] += d_ms

    daily_stats = [{'type': k, 'count': v['count'], 'formatted_time': format_time(v['total_ms']), 'raw': v['total_ms']} for k, v in daily_breakdown.items()]
    weekly_stats = [{'type': k, 'count': v['count'], 'formatted_time': format_time(v['total_ms']), 'raw': v['total_ms']} for k, v in weekly_breakdown.items()]
    
    daily_stats.sort(key=lambda x: x['raw'], reverse=True)
    weekly_stats.sort(key=lambda x: x['raw'], reverse=True)

    return {
        'dailyTotal': format_time(daily_ms),
        'weeklyTotal': format_time(weekly_ms),
        'dailyStats': daily_stats,
        'weeklyStats': weekly_stats
    }

def get_leaderboards():
    today_str = datetime.now(tz=ZoneInfo("Asia/Kolkata")).strftime('%Y-%m-%d')
    seven_days_ago_str = (datetime.now(tz=ZoneInfo("Asia/Kolkata")) - timedelta(days=7)).strftime('%Y-%m-%d')
    
    daily_lb = []
    weekly_lb = []
    
    for user_id, u_state in state.items():
        daily_ms = 0
        weekly_ms = 0
        
        for session in u_state.get('session_history', []):
            session_date = session.get('date', today_str)
            if session_date == today_str:
                daily_ms += session.get('duration_ms', 0)
            if session_date >= seven_days_ago_str:
                weekly_ms += session.get('duration_ms', 0)
                
        is_running = u_state.get('is_running', False)
        current_session = u_state.get('current_session', {}).get('type', '') if is_running else ''
        
        if daily_ms > 0 or is_running:
            daily_lb.append({
                'username': user_id, 'total_ms': daily_ms, 'formatted_time': format_time(daily_ms),
                'is_running': is_running, 'current_session': current_session
            })
            
        if weekly_ms > 0 or is_running:
            weekly_lb.append({
                'username': user_id, 'total_ms': weekly_ms, 'formatted_time': format_time(weekly_ms),
                'is_running': is_running, 'current_session': current_session
            })
            
    daily_lb.sort(key=lambda x: x['total_ms'], reverse=True)
    weekly_lb.sort(key=lambda x: x['total_ms'], reverse=True)
    
    for i, entry in enumerate(daily_lb): entry['rank'] = i + 1
    for i, entry in enumerate(weekly_lb): entry['rank'] = i + 1
        
    return {'daily': daily_lb[:10], 'weekly': weekly_lb[:10]}

# --- End of Day WhatsApp Broadcast ---
def send_msg(message, phone):
    url = "https://wappsync.com/api/SendMessage"
    payload = {
        "chatId": f"+91{phone}",
        "message": message
    }
    headers = {
        'Content-Type': 'application/json',
        'Authorization': 'Bearer caf625afd590317ac9f023ca1442bd81764b903338c6e042ad9eeb5e9af0fdc5',
    }
    response = requests.post(url, json=payload, headers=headers)
    print(f"WhatsApp sent to {phone}: {response.text}")
    return "Done"

def send_daily_whatsapp_summary():
    lbs = get_leaderboards()
    daily_lb = lbs['daily']
    
    msg = "🏆 *FocusFlow Daily Leaderboard* 🏆\n\n"
    if not daily_lb:
        msg += "No sessions recorded today.\n"
    else:
        for idx, entry in enumerate(daily_lb):
            medal = "🥇" if idx == 0 else "🥈" if idx == 1 else "🥉" if idx == 2 else "🔹"
            msg += f"{medal} {entry['username'].capitalize()}: {entry['formatted_time']}\n"
            
    msg += "\nKeep up the great work! 🔥"
    
    users = load_users()
    for user, data in users.items():
        if isinstance(data, dict):
            phone = data.get('phone')
            if phone:
                send_msg(msg, str(phone))

# --- Flask Routes ---
@app.route('/')
def home():
    return render_template('index.html')

@app.route("/remote")
def remote():
    return render_template('remote.html')

@app.route("/login", methods=['POST'])
def login():
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')
    
    if not username: return {"success": False, "message": "Username is required."}, 400
    if not password: return {"success": False, "message": "Password is required."}, 400

    users = load_users()
    if username not in users:
        return {"success": False, "message": "Username not found. Please register first."}, 400
    
    # Handle old string format or new dictionary format
    saved_pwd = users[username].get('pwd') if isinstance(users[username], dict) else users[username]
    
    if password == saved_pwd:
        init_user(username)
        return {"success": True, "message": f"User '{username}' logged in successfully.", "username": username}
    else:
        return {"success": False, "message": "Invalid password."}, 400

@app.route("/register", methods=['POST'])
def register():
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')
    phone = data.get('phone')

    if not username: return {"success": False, "message": "Username is required."}, 400
    if not password: return {"success": False, "message": "Password is required."}, 400

    users = load_users()
    if username in users or username in state:
        return {"success": False, "message": "Username already exists. Please choose another."}, 400

    users[username] = {
        "pwd": password,
        "phone": int(phone) if phone else None
    }
    save_users(users)
    init_user(username)
    
    return {"success": True, "message": f"User '{username}' registered successfully.", "username": username}

# --- Socket.IO Event Handlers ---
@socketio.on('join')
def handle_join(data):
    user_id = data.get('user_id')
    if not user_id: return
    
    join_room(user_id)
    check_new_day(user_id)
    u_state = state[user_id]
    
    elapsed = get_current_elapsed_ms(user_id)
    emit('sw_time', {'elapsed_time': elapsed}, room=request.sid)
    emit('leaderboard_update', get_leaderboards(), room=request.sid)
    
    stats = calculate_stats(user_id)
    today_str = datetime.now(tz=ZoneInfo("Asia/Kolkata")).strftime('%Y-%m-%d')
    today_sessions = [s for s in u_state['session_history'] if s.get('date') == today_str]
    
    if today_sessions:
        for session in today_sessions:
            emit('session_logged', {
                'type': session['type'],
                'range': session['range'],
                'duration': format_time(session['duration_ms']),
                'dailyTotal': stats['dailyTotal'],
                'stats': stats['dailyStats'],
                'weeklyTotal': stats['weeklyTotal'],
                'weeklyStats': stats['weeklyStats']
            }, room=request.sid)
    else:
        emit('session_logged', {
            'type': u_state['current_session'].get('type', 'Ready'),
            'range': '', 
            'duration': '00:00:00',
            'dailyTotal': stats['dailyTotal'],
            'stats': stats['dailyStats'],
            'weeklyTotal': stats['weeklyTotal'],
            'weeklyStats': stats['weeklyStats']
        }, room=request.sid)
    
    if u_state['is_running']:
        emit('start_sw', u_state['current_session'], room=request.sid)

@socketio.on('request_history')
def handle_request_history(data):
    user_id = data.get('user_id')
    if not user_id: return
    
    range_val = data.get('range')
    if range_val == '7days':
        stats = calculate_stats(user_id)
        emit('history_response', {'total': stats['weeklyTotal'], 'stats': stats['weeklyStats']}, room=request.sid)
        return
        
    target_date = range_val
    total_ms = 0
    breakdown = {}
    
    u_state = state[user_id]
    for session in u_state.get('session_history', []):
        if session.get('date') == target_date:
            t = session['type']
            d_ms = session['duration_ms']
            total_ms += d_ms
            if t not in breakdown:
                breakdown[t] = {'count': 0, 'total_ms': 0}
            breakdown[t]['count'] += 1
            breakdown[t]['total_ms'] += d_ms
            
    stats_list = [{'type': k, 'count': v['count'], 'formatted_time': format_time(v['total_ms']), 'raw': v['total_ms']} for k, v in breakdown.items()]
    stats_list.sort(key=lambda x: x['raw'], reverse=True)
    emit('history_response', {'total': format_time(total_ms), 'stats': stats_list}, room=request.sid)

@socketio.on('start_sw')
def handle_start_sw(data):
    user_id = data.get('user_id')
    if not user_id: return
    
    init_user(user_id)
    u_state = state[user_id]

    if not u_state['is_running']:
        u_state['is_running'] = True
        u_state['last_start_time'] = time.time() * 1000
        u_state['current_session'] = data
        save_state()
            
        socketio.emit('start_sw', u_state['current_session'], room=user_id)
        socketio.emit('sw_time', {'elapsed_time': get_current_elapsed_ms(user_id)}, room=user_id)
        socketio.emit('leaderboard_update', get_leaderboards())

@socketio.on('stop_sw')
def handle_stop_sw(data):
    user_id = data.get('user_id')
    if not user_id: return
    
    init_user(user_id)
    u_state = state[user_id]

    if u_state['is_running']:
        now = time.time() * 1000
        session_duration_ms = now - u_state['last_start_time']
        
        u_state['accumulated_time_ms'] += session_duration_ms
        u_state['is_running'] = False
        u_state['last_start_time'] = None
        
        end_time_str = datetime.now(tz=ZoneInfo("Asia/Kolkata")).strftime("%I:%M %p")
        start_time_str = u_state['current_session'].get('startTime', 'Unknown')
        
        session_record = {
            'date': datetime.now(tz=ZoneInfo("Asia/Kolkata")).strftime('%Y-%m-%d'),
            'type': u_state['current_session'].get('type', 'Session'),
            'duration_ms': session_duration_ms,
            'range': f"{start_time_str} - {end_time_str}"
        }
        u_state['session_history'].append(session_record)
        save_state()

        socketio.emit('stop_sw', room=user_id)
        stats = calculate_stats(user_id)
        
        socketio.emit('session_logged', {
            'type': session_record['type'],
            'range': session_record['range'],
            'duration': format_time(session_duration_ms),
            'dailyTotal': stats['dailyTotal'],
            'stats': stats['dailyStats'],
            'weeklyTotal': stats['weeklyTotal'],
            'weeklyStats': stats['weeklyStats']
        }, room=user_id)

        socketio.emit('leaderboard_update', get_leaderboards())

@socketio.on('reset_sw')
def handle_reset_sw(data):
    user_id = data.get('user_id')
    if not user_id: return
    
    init_user(user_id)
    u_state = state[user_id]
    
    u_state['accumulated_time_ms'] = 0
    u_state['is_running'] = False
    u_state['last_start_time'] = None
    u_state['current_session'] = {'type': 'Ready', 'goal': '', 'startTime': ''}
    
    save_state()
    
    socketio.emit('reset_sw', room=user_id)
    socketio.emit('sw_time', {'elapsed_time': 0}, room=user_id)
    socketio.emit('timer_tick', {'formattedTime': '00:00:00'}, room=user_id)
    socketio.emit('leaderboard_update', get_leaderboards())

def background_clock_sync():
    last_sent_date = None
    while True:
        socketio.sleep(1)
        now = datetime.now(tz=ZoneInfo("Asia/Kolkata"))
        current_time = now.strftime('%H:%M:%S')
        current_date = now.strftime('%Y-%m-%d')
        
        for user_id in list(state.keys()):
            if state[user_id].get('is_running'):
                current_ms = get_current_elapsed_ms(user_id)
                socketio.emit('timer_tick', {'formattedTime': format_time(current_ms)}, room=user_id)
                
        # Trigger EOD WhatsApp message at exactly 23:59:00 IST
        if current_time == "23:59:00" and last_sent_date != current_date:
            last_sent_date = current_date
            try:
                send_daily_whatsapp_summary()
            except Exception as e:
                print(f"Error sending WA summary: {e}")

if __name__ == '__main__':
    socketio.start_background_task(background_clock_sync)
    port = int(os.environ.get("PORT", 8080))
    print(f"🚀 FocusFlow Multi-User Backend running on http://127.0.0.1:{port}")
    socketio.run(app, host="0.0.0.0", port=port, debug=False, allow_unsafe_werkzeug=True)
