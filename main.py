import time
import json
import os
from datetime import datetime, timedelta
from flask import Flask, render_template, request
from flask_socketio import SocketIO, join_room, leave_room, emit

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'

socketio = SocketIO(app, cors_allowed_origins="*")

DB_FILE = 'focus_data.json'

# --- Database / State Management ---
def load_state():
    """Load state from the JSON file. It now holds a dictionary of users."""
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, 'r') as f:
                loaded_state = json.load(f)
                
                # Safety feature: Pause all timers on server restart
                for user_id, user_data in loaded_state.items():
                    user_data['is_running'] = False
                    user_data['last_start_time'] = None
                        
                return loaded_state
        except Exception as e:
            print(f"Error loading database: {e}")
            return {}
    return {}

def save_state():
    """Save the multi-user state to the JSON file."""
    try:
        with open(DB_FILE, 'w') as f:
            json.dump(state, f, indent=4)
    except Exception as e:
        print(f"Error saving to database: {e}")

# Initialize global multi-user state
state = load_state()

def init_user(user_id):
    """Ensure a user exists in the state dictionary."""
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
            'current_date': datetime.now().strftime('%Y-%m-%d')
        }
        save_state()

def check_new_day(user_id):
    """Check if the date has changed for a specific user."""
    init_user(user_id)
    today = datetime.now().strftime('%Y-%m-%d')
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
    today_str = datetime.now().strftime('%Y-%m-%d')
    seven_days_ago = datetime.now() - timedelta(days=7)
    seven_days_ago = seven_days_ago.replace(hour=0, minute=0, second=0, microsecond=0)
    
    daily_ms = 0
    weekly_ms = 0
    daily_breakdown = {}
    weekly_breakdown = {}

    for session in u_state.get('session_history', []):
        session_date_str = session.get('date', today_str) 
        try:
            session_date = datetime.strptime(session_date_str, '%Y-%m-%d')
        except ValueError:
            session_date = datetime.now()
            
        t = session['type']
        d_ms = session['duration_ms']
        
        # Aggregate Weekly
        if session_date >= seven_days_ago:
            weekly_ms += d_ms
            if t not in weekly_breakdown:
                weekly_breakdown[t] = {'count': 0, 'total_ms': 0}
            weekly_breakdown[t]['count'] += 1
            weekly_breakdown[t]['total_ms'] += d_ms
            
        # Aggregate Daily
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

def get_leaderboard():
    """Generates the Daily Leaderboard for all active users."""
    today_str = datetime.now().strftime('%Y-%m-%d')
    leaderboard = []
    
    for user_id, u_state in state.items():
        daily_ms = 0
        for session in u_state.get('session_history', []):
            if session.get('date', today_str) == today_str:
                daily_ms += session.get('duration_ms', 0)
                
        if daily_ms > 0:
            leaderboard.append({
                'username': user_id,
                'total_ms': daily_ms,
                'formatted_time': format_time(daily_ms)
            })
            
    # Sort descending by total time
    leaderboard.sort(key=lambda x: x['total_ms'], reverse=True)
    
    # Assign ranks
    for i, entry in enumerate(leaderboard):
        entry['rank'] = i + 1
        
    return leaderboard[:10] # Return Top 10

# --- Flask Routes ---
@app.route('/')
def home():
    return render_template('index.html')

@app.route("/remote")
def remote():
    return render_template('remote.html')

# --- Socket.IO Event Handlers ---
@socketio.on('join')
def handle_join(data):
    user_id = data.get('user_id')
    if not user_id:
        return
    
    # User joins their own private room
    join_room(user_id)
    print(f"Client joined room for user: {user_id}")
    
    check_new_day(user_id)
    u_state = state[user_id]
    
    elapsed = get_current_elapsed_ms(user_id)
    emit('sw_time', {'elapsed_time': elapsed}, room=request.sid)
    
    # Send the live leaderboard to the newly joined user
    emit('leaderboard_update', get_leaderboard(), room=request.sid)
    
    stats = calculate_stats(user_id)
    today_str = datetime.now().strftime('%Y-%m-%d')
    today_sessions = [s for s in u_state['session_history'] if s.get('date') == today_str]
    
    # Repopulate the Dashboard Timeline with ONLY today's sessions
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
        # Initialize/reset the UI numbers
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
        
        end_time_str = datetime.now().strftime("%I:%M %p")
        start_time_str = u_state['current_session'].get('startTime', 'Unknown')
        
        session_record = {
            'date': datetime.now().strftime('%Y-%m-%d'),
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

        # Broadcast the global leaderboard change to EVERYONE connected!
        socketio.emit('leaderboard_update', get_leaderboard())

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

# --- Background Master Clock ---
def background_clock_sync():
    """Iterate over all users and emit ticks to those currently running."""
    while True:
        socketio.sleep(1)
        # Using list() to avoid dictionary changed size during iteration error
        for user_id in list(state.keys()):
            if state[user_id].get('is_running'):
                current_ms = get_current_elapsed_ms(user_id)
                socketio.emit('timer_tick', {'formattedTime': format_time(current_ms)}, room=user_id)

if __name__ == '__main__':
    socketio.start_background_task(background_clock_sync)
    print(f"🚀 FocusFlow Multi-User Backend running on http://127.0.0.1:8080")
    socketio.run(app, host='0.0.0.0', port=8080, debug=False, allow_unsafe_werkzeug=True)
