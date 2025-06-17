#!/usr/bin/env python3
"""
Raspberry Pi Door Monitoring System
Complete single-file application with GPIO control, web dashboard, and monitoring
"""

import os
import json
import sqlite3
import hashlib
import threading
import time
from datetime import datetime, timedelta
from flask import Flask, render_template_string, request, jsonify, redirect, url_for, session, send_file
from flask_cors import CORS
from functools import wraps
import csv
import io
import zipfile

# GPIO setup with fallback for non-Pi systems
try:
    from gpiozero import LED, Button
    import pygame
    GPIO_AVAILABLE = True
    pygame.mixer.init()
except ImportError:
    print("GPIO/Audio not available - running in simulation mode")
    GPIO_AVAILABLE = False

# Flask app setup
app = Flask(__name__)
app.secret_key = 'your-secret-key-change-this-in-production'

# Enable CORS for React frontend
CORS(app, supports_credentials=True, origins=["http://localhost:5173", "http://127.0.0.1:5173"])

# Hardware configuration
DOOR_SENSOR_PIN = 17
GREEN_LED_PIN = 25
RED_LED_PIN = 27
WHITE_LED_PIN = 23
SWITCH_PIN = 24

# Initialize GPIO components
if GPIO_AVAILABLE:
    try:
        door_sensor = Button(DOOR_SENSOR_PIN, pull_up=False)  # Fixed: pull_up=False for normally closed door sensor
        green_led = LED(GREEN_LED_PIN)
        red_led = LED(RED_LED_PIN)
        white_led = LED(WHITE_LED_PIN)
        green_led.on()  # System running indicator
    except Exception as e:
        print(f"GPIO initialization error: {e}")
        GPIO_AVAILABLE = False

# Global state management
class SystemState:
    def __init__(self):
        self.door_open = False
        self.timer_active = False
        self.alarm_triggered = False
        self.timer_start_time = None
        self.timer_duration = 30  # seconds
        self.blink_thread = None
        self.stop_blink = False
        self.last_scroll_position = 0
        self.load_state()
    
    def save_state(self):
        """Save persistent state to JSON file"""
        state_data = {
            'timer_duration': self.timer_duration,
            'timer_active': self.timer_active,
            'timer_start_time': self.timer_start_time.isoformat() if self.timer_start_time else None,
            'alarm_triggered': self.alarm_triggered,
            'door_open': self.door_open
        }
        try:
            with open('system_state.json', 'w') as f:
                json.dump(state_data, f, indent=2)
        except Exception as e:
            print(f"Error saving state: {e}")
    
    def load_state(self):
        """Load persistent state from JSON file"""
        try:
            if os.path.exists('system_state.json'):
                with open('system_state.json', 'r') as f:
                    state_data = json.load(f)
                    self.timer_duration = state_data.get('timer_duration', 30)
                    self.timer_active = state_data.get('timer_active', False)
                    self.alarm_triggered = state_data.get('alarm_triggered', False)
                    self.door_open = state_data.get('door_open', False)
                    
                    # Restore timer if it was active
                    if state_data.get('timer_start_time'):
                        self.timer_start_time = datetime.fromisoformat(state_data['timer_start_time'])
                        # Check if timer should have expired
                        if self.timer_active:
                            elapsed = (datetime.now() - self.timer_start_time).total_seconds()
                            if elapsed >= self.timer_duration:
                                self.trigger_alarm()
        except Exception as e:
            print(f"Error loading state: {e}")

# Initialize system state
system_state = SystemState()

# Database setup
def init_db():
    """Initialize SQLite database with proper error handling"""
    try:
        conn = sqlite3.connect('door_monitor.db')
        cursor = conn.cursor()
        
        # Users table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'User',
                email TEXT,
                department TEXT,
                contact TEXT,
                reporting_manager TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_login TIMESTAMP
            )
        ''')
        
        # Events table with enhanced logging
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                event_type TEXT NOT NULL,
                description TEXT NOT NULL,
                user_id INTEGER,
                severity TEXT DEFAULT 'INFO',
                additional_data TEXT,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        ''')
        
        # Create default admin user
        admin_hash = hashlib.sha256('admin123'.encode()).hexdigest()
        cursor.execute('''
            INSERT OR IGNORE INTO users (username, password_hash, role, email, department)
            VALUES (?, ?, ?, ?, ?)
        ''', ('admin', admin_hash, 'Admin', 'admin@doormonitor.local', 'IT'))
        
        conn.commit()
        conn.close()
        log_event('SYSTEM', 'Database initialized successfully', severity='INFO')
    except Exception as e:
        print(f"Database initialization error: {e}")
        log_event('SYSTEM', f'Database initialization failed: {str(e)}', severity='ERROR')

# Authentication decorator
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        if session.get('role') != 'Admin':
            return jsonify({'success': False, 'message': 'Admin access required'})
        return f(*args, **kwargs)
    return decorated_function

# Event logging with enhanced features
def log_event(event_type, description, user_id=None, severity='INFO', additional_data=None):
    """Enhanced event logging with severity levels and additional data"""
    try:
        conn = sqlite3.connect('door_monitor.db')
        cursor = conn.cursor()
        
        if user_id is None and 'user_id' in session:
            user_id = session['user_id']
            
        cursor.execute('''
            INSERT INTO events (event_type, description, user_id, severity, additional_data)
            VALUES (?, ?, ?, ?, ?)
        ''', (event_type, description, user_id, severity, additional_data))
        
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Error logging event: {e}")

def start_timer():
    """Start countdown timer with proper state management"""
    if not system_state.timer_active and not system_state.alarm_triggered:
        system_state.timer_active = True
        system_state.timer_start_time = datetime.now()
        system_state.save_state()
        
        log_event('TIMER', f'Timer started - Duration: {system_state.timer_duration}s', 
                 severity='WARNING')
        
        # Start blinking red LED
        start_blink_red_led()
        
        # Start timer countdown in separate thread
        timer_thread = threading.Thread(target=countdown_timer)
        timer_thread.daemon = True
        timer_thread.start()

def countdown_timer():
    """Countdown timer logic"""
    try:
        time.sleep(system_state.timer_duration)
        if system_state.timer_active and not system_state.alarm_triggered:
            trigger_alarm()
    except Exception as e:
        log_event('SYSTEM', f'Timer countdown error: {str(e)}', severity='ERROR')

def trigger_alarm():
    """Trigger alarm with comprehensive logging"""
    try:
        system_state.alarm_triggered = True
        system_state.timer_active = False
        system_state.stop_blink = True
        system_state.save_state()
        
        if GPIO_AVAILABLE:
            red_led.off()
            white_led.on()
            try:
                pygame.mixer.music.load('alarm.wav')
                pygame.mixer.music.play(-1)
            except:
                pass
        
        log_event('ALARM', 'Security alarm triggered - Unauthorized access detected', 
                 severity='CRITICAL', 
                 additional_data=json.dumps({
                     'door_status': 'open',
                     'timer_duration': system_state.timer_duration,
                     'trigger_time': datetime.now().isoformat()
                 }))
        
        print("🚨 ALARM TRIGGERED! 🚨")
    except Exception as e:
        log_event('SYSTEM', f'Alarm trigger error: {str(e)}', severity='ERROR')

def start_blink_red_led():
    """Start blinking red LED in separate thread"""
    if system_state.blink_thread and system_state.blink_thread.is_alive():
        return
        
    system_state.stop_blink = False
    system_state.blink_thread = threading.Thread(target=blink_red_led)
    system_state.blink_thread.daemon = True
    system_state.blink_thread.start()

def blink_red_led():
    """Blink red LED while timer is active"""
    try:
        while system_state.timer_active and not system_state.stop_blink:
            if GPIO_AVAILABLE:
                red_led.on()
                time.sleep(0.5)
                red_led.off()
                time.sleep(0.5)
            else:
                time.sleep(1)
    except Exception as e:
        log_event('SYSTEM', f'LED blink error: {str(e)}', severity='ERROR')

def reset_system():
    """Reset system state with proper cleanup"""
    try:
        system_state.timer_active = False
        system_state.alarm_triggered = False
        system_state.stop_blink = True
        system_state.timer_start_time = None
        system_state.save_state()
        
        if GPIO_AVAILABLE:
            red_led.off()
            white_led.off()
            green_led.on()
            pygame.mixer.music.stop()
        
        log_event('SYSTEM', 'System manually reset', severity='INFO')
    except Exception as e:
        log_event('SYSTEM', f'System reset error: {str(e)}', severity='ERROR')

# Door sensor monitoring
def monitor_door():
    """Monitor door sensor with CORRECT logic for normally closed sensor"""
    def door_opened():
        try:
            system_state.door_open = True
            system_state.save_state()
            log_event('DOOR', 'Door opened - Security timer started', severity='WARNING')
            
            if not system_state.alarm_triggered:
                start_timer()
        except Exception as e:
            log_event('SYSTEM', f'Door open handler error: {str(e)}', severity='ERROR')
    
    def door_closed():
        try:
            system_state.door_open = False
            system_state.save_state()
            log_event('DOOR', 'Door closed - Access secured', severity='INFO')
        except Exception as e:
            log_event('SYSTEM', f'Door close handler error: {str(e)}', severity='ERROR')
    
    if GPIO_AVAILABLE:
        # For normally closed door sensor: when_released = door opened, when_pressed = door closed
        door_sensor.when_released = door_opened  # Door opens when sensor releases
        door_sensor.when_pressed = door_closed   # Door closes when sensor presses

# Web routes
@app.route('/')
@login_required
def dashboard():
    return render_template_string(DASHBOARD_TEMPLATE)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        try:
            username = request.form['username']
            password = request.form['password']
            password_hash = hashlib.sha256(password.encode()).hexdigest()
            
            conn = sqlite3.connect('door_monitor.db')
            cursor = conn.cursor()
            cursor.execute('SELECT id, role, username FROM users WHERE username = ? AND password_hash = ?',
                         (username, password_hash))
            user = cursor.fetchone()
            conn.close()
            
            if user:
                session['user_id'] = user[0]
                session['role'] = user[1]
                session['username'] = user[2]
                
                # Update last login
                conn = sqlite3.connect('door_monitor.db')
                cursor = conn.cursor()
                cursor.execute('UPDATE users SET last_login = CURRENT_TIMESTAMP WHERE id = ?', (user[0],))
                conn.commit()
                conn.close()
                
                log_event('AUTH', f'User login successful', user_id=user[0], severity='INFO')
                return jsonify({'success': True, 'message': 'Login successful'})
            else:
                log_event('AUTH', f'Failed login attempt for username: {username}', severity='WARNING')
                return jsonify({'success': False, 'message': 'Invalid credentials'}), 401
        except Exception as e:
            log_event('SYSTEM', f'Login error: {str(e)}', severity='ERROR')
            return jsonify({'success': False, 'message': 'Login system error'}), 500
    
    return jsonify({'success': False, 'message': 'Method not allowed'}), 405

@app.route('/logout')
def logout():
    user_id = session.get('user_id')
    if user_id:
        log_event('AUTH', 'User logout', user_id=user_id, severity='INFO')
    session.clear()
    return redirect(url_for('login'))

# API Routes with enhanced error handling
@app.route('/api/status')
@login_required
def api_status():
    """Get system status with scroll position preservation"""
    try:
        remaining_time = 0
        if system_state.timer_active and system_state.timer_start_time:
            elapsed = (datetime.now() - system_state.timer_start_time).total_seconds()
            remaining_time = max(0, system_state.timer_duration - elapsed)
        
        return jsonify({
            'success': True,
            'door_open': system_state.door_open,
            'timer_active': system_state.timer_active,
            'alarm_triggered': system_state.alarm_triggered,
            'remaining_time': remaining_time,
            'timer_duration': system_state.timer_duration,
            'gpio_available': GPIO_AVAILABLE,
            'timestamp': datetime.now().isoformat()
        })
    except Exception as e:
        log_event('SYSTEM', f'Status API error: {str(e)}', severity='ERROR')
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/events')
@login_required
def api_events():
    """Get events with pagination and filtering"""
    try:
        page = int(request.args.get('page', 1))
        per_page = 25
        offset = (page - 1) * per_page
        
        conn = sqlite3.connect('door_monitor.db')
        cursor = conn.cursor()
        
        # Get total count
        cursor.execute('SELECT COUNT(*) FROM events')
        total_events = cursor.fetchone()[0]
        
        # Get events with user info
        cursor.execute('''
            SELECT e.timestamp, e.event_type, e.description, u.username, e.severity
            FROM events e
            LEFT JOIN users u ON e.user_id = u.id
            ORDER BY e.timestamp DESC
            LIMIT ? OFFSET ?
        ''', (per_page, offset))
        
        events = []
        for row in cursor.fetchall():
            events.append({
                'timestamp': row[0],
                'event_type': row[1],
                'description': row[2],
                'username': row[3] or 'System',
                'severity': row[4]
            })
        
        conn.close()
        
        return jsonify({
            'success': True,
            'events': events,
            'total_events': total_events,
            'page': page,
            'total_pages': (total_events + per_page - 1) // per_page
        })
    except Exception as e:
        log_event('SYSTEM', f'Events API error: {str(e)}', severity='ERROR')
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/reset', methods=['POST'])
@login_required
def api_reset():
    """Reset system with proper authorization"""
    try:
        if session.get('role') not in ['Admin', 'Manager']:
            return jsonify({'success': False, 'message': 'Insufficient permissions'})
        
        reset_system()
        return jsonify({'success': True, 'message': 'System reset successfully'})
    except Exception as e:
        log_event('SYSTEM', f'Reset API error: {str(e)}', severity='ERROR')
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/update_timer', methods=['POST'])
@login_required
def api_update_timer():
    """Update timer duration with validation"""
    try:
        if session.get('role') not in ['Admin', 'Manager']:
            return jsonify({'success': False, 'message': 'Insufficient permissions'})
        
        duration = int(request.json.get('duration', 30))
        if duration < 1 or duration > 86400:  # 1 second to 24 hours
            return jsonify({'success': False, 'message': 'Invalid timer duration'})
        
        old_duration = system_state.timer_duration
        system_state.timer_duration = duration
        system_state.save_state()
        
        log_event('SETTINGS', f'Timer duration updated from {old_duration}s to {duration}s', 
                 severity='INFO')
        
        return jsonify({'success': True, 'message': 'Timer updated successfully'})
        
    except Exception as e:
        log_event('SYSTEM', f'Update timer API error: {str(e)}', severity='ERROR')
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/download_report')
@login_required
def download_report():
    """Generate and download CSV report"""
    try:
        if session.get('role') not in ['Admin', 'Manager', 'Supervisor']:
            return jsonify({'success': False, 'message': 'Insufficient permissions'})
        
        conn = sqlite3.connect('door_monitor.db')
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT e.timestamp, e.event_type, e.description, u.username, e.severity
            FROM events e
            LEFT JOIN users u ON e.user_id = u.id
            ORDER BY e.timestamp DESC
        ''')
        
        events = cursor.fetchall()
        conn.close()
        
        # Create CSV in memory
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['Timestamp', 'Event Type', 'Description', 'User', 'Severity'])
        
        for event in events:
            writer.writerow(event)
        
        # Create file response
        mem = io.BytesIO()
        mem.write(output.getvalue().encode())
        mem.seek(0)
        
        log_event('REPORT', 'Event report downloaded', severity='INFO')
        
        return send_file(
            io.BytesIO(output.getvalue().encode()),
            mimetype='text/csv',
            as_attachment=True,
            download_name=f'door_monitor_report_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
        )
        
    except Exception as e:
        log_event('SYSTEM', f'Report download error: {str(e)}', severity='ERROR')
        return jsonify({'success': False, 'error': str(e)})

# Enhanced HTML templates with scroll position preservation
DASHBOARD_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Door Monitoring System</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
    <style>
        :root {
            --primary-gradient: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            --success-gradient: linear-gradient(135deg, #4facfe 0%, #00f2fe 100%);
            --warning-gradient: linear-gradient(135deg, #fa709a 0%, #fee140 100%);
            --danger-gradient: linear-gradient(135deg, #ff6b6b 0%, #ffa500 100%);
        }

        body {
            background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%);
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            min-height: 100vh;
        }

        .navbar {
            background: var(--primary-gradient);
            box-shadow: 0 4px 20px rgba(0,0,0,0.1);
        }

        .card {
            border: none;
            border-radius: 15px;
            box-shadow: 0 8px 30px rgba(0,0,0,0.1);
            transition: transform 0.3s ease, box-shadow 0.3s ease;
            backdrop-filter: blur(10px);
            background: rgba(255,255,255,0.9);
        }

        .card:hover {
            transform: translateY(-5px);
            box-shadow: 0 15px 40px rgba(0,0,0,0.15);
        }

        .status-card {
            background: var(--success-gradient);
            color: white;
        }

        .status-card.warning {
            background: var(--warning-gradient);
        }

        .status-card.danger {
            background: var(--danger-gradient);
        }

        .nav-pills .nav-link {
            border-radius: 25px;
            margin: 0 5px;
            transition: all 0.3s ease;
            border: 2px solid transparent;
        }

        .nav-pills .nav-link:hover {
            transform: translateY(-2px);
            box-shadow: 0 5px 15px rgba(0,0,0,0.2);
        }

        .nav-pills .nav-link.active {
            background: var(--primary-gradient);
            border-color: rgba(255,255,255,0.3);
        }

        .btn-custom {
            border-radius: 25px;
            padding: 10px 25px;
            font-weight: 600;
            transition: all 0.3s ease;
            border: none;
            text-transform: uppercase;
            letter-spacing: 1px;
        }

        .btn-primary-custom {
            background: var(--primary-gradient);
            color: white;
        }

        .btn-primary-custom:hover {
            transform: translateY(-2px);
            box-shadow: 0 8px 25px rgba(102, 126, 234, 0.4);
        }

        .event-item {
            border-left: 4px solid #007bff;
            transition: all 0.3s ease;
        }

        .event-item:hover {
            background-color: rgba(0,123,255,0.05);
            transform: translateX(5px);
        }

        .event-critical { border-left-color: #dc3545; }
        .event-warning { border-left-color: #ffc107; }
        .event-info { border-left-color: #17a2b8; }

        .status-indicator {
            width: 20px;
            height: 20px;
            border-radius: 50%;
            display: inline-block;
            animation: pulse 2s infinite;
            margin-right: 10px;
        }

        .status-green { background-color: #28a745; }
        .status-red { background-color: #dc3545; }
        .status-white { background-color: #ffc107; }

        @keyframes pulse {
            0% { opacity: 1; }
            50% { opacity: 0.5; }
            100% { opacity: 1; }
        }

        .stats-card {
            background: linear-gradient(135deg, rgba(255,255,255,0.1), rgba(255,255,255,0.05));
            backdrop-filter: blur(10px);
            border: 1px solid rgba(255,255,255,0.2);
        }

        .notification-toast {
            position: fixed;
            top: 20px;
            right: 20px;
            z-index: 9999;
            max-width: 350px;
            border-radius: 10px;
            box-shadow: 0 8px 30px rgba(0,0,0,0.3);
        }

        .scroll-preserve {
            overflow-y: auto;
            max-height: 400px;
        }

        /* Prevent layout shift during updates */
        .status-container {
            min-height: 200px;
        }

        .events-container {
            min-height: 300px;
        }
    </style>
</head>
<body>
    <nav class="navbar navbar-expand-lg navbar-dark">
        <div class="container">
            <a class="navbar-brand fw-bold">
                <i class="fas fa-shield-alt me-2"></i>
                Door Security Monitor
            </a>
            <div class="navbar-nav ms-auto">
                <span class="navbar-text me-3">
                    <i class="fas fa-user-circle me-1"></i>
                    Welcome, {{ session.username }} ({{ session.role }})
                </span>
                <a class="btn btn-outline-light btn-sm" href="/logout">
                    <i class="fas fa-sign-out-alt me-1"></i>Logout
                </a>
            </div>
        </div>
    </nav>

    <div class="container mt-4">
        <!-- Status Dashboard -->
        <div class="row mb-4">
            <div class="col-12">
                <div class="card">
                    <div class="card-body">
                        <h5 class="card-title mb-4">
                            <i class="fas fa-tachometer-alt me-2"></i>System Status
                        </h5>
                        <div class="row status-container" id="statusContainer">
                            <div class="col-md-3 mb-3">
                                <div class="card status-card" id="doorStatusCard">
                                    <div class="card-body text-center">
                                        <i class="fas fa-door-open fa-2x mb-2"></i>
                                        <h6>Door Status</h6>
                                        <span id="doorStatus">Loading...</span>
                                    </div>
                                </div>
                            </div>
                            <div class="col-md-3 mb-3">
                                <div class="card status-card" id="timerStatusCard">
                                    <div class="card-body text-center">
                                        <i class="fas fa-clock fa-2x mb-2"></i>
                                        <h6>Timer Status</h6>
                                        <span id="timerStatus">Loading...</span>
                                    </div>
                                </div>
                            </div>
                            <div class="col-md-3 mb-3">
                                <div class="card status-card" id="alarmStatusCard">
                                    <div class="card-body text-center">
                                        <i class="fas fa-bell fa-2x mb-2"></i>
                                        <h6>Alarm Status</h6>
                                        <span id="alarmStatus">Loading...</span>
                                    </div>
                                </div>
                            </div>
                            <div class="col-md-3 mb-3">
                                <div class="card status-card">
                                    <div class="card-body text-center">
                                        <i class="fas fa-microchip fa-2x mb-2"></i>
                                        <h6>GPIO Status</h6>
                                        <span id="gpioStatus">Loading...</span>
                                    </div>
                                </div>
                            </div>
                        </div>
                        <div class="text-center mt-3">
                            <button class="btn btn-danger btn-custom" onclick="resetSystem()">
                                <i class="fas fa-power-off me-2"></i>Reset System
                            </button>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <!-- Navigation Tabs -->
        <ul class="nav nav-pills mb-4 justify-content-center" id="mainTabs">
            <li class="nav-item">
                <a class="nav-link active" data-bs-toggle="pill" href="#events-tab">
                    <i class="fas fa-list me-2"></i>Events
                </a>
            </li>
            <li class="nav-item">
                <a class="nav-link" data-bs-toggle="pill" href="#settings-tab">
                    <i class="fas fa-cog me-2"></i>Settings
                </a>
            </li>
            <li class="nav-item">
                <a class="nav-link" data-bs-toggle="pill" href="#users-tab">
                    <i class="fas fa-users me-2"></i>Users
                </a>
            </li>
            <li class="nav-item">
                <a class="nav-link" data-bs-toggle="pill" href="#reports-tab">
                    <i class="fas fa-chart-bar me-2"></i>Reports
                </a>
            </li>
            <li class="nav-item">
                <a class="nav-link" data-bs-toggle="pill" href="#schedules-tab">
                    <i class="fas fa-calendar me-2"></i>Schedules
                </a>
            </li>
        </ul>

        <!-- Tab Content -->
        <div class="tab-content">
            <!-- Events Tab -->
            <div class="tab-pane fade show active" id="events-tab">
                <div class="card">
                    <div class="card-header">
                        <h5 class="mb-0">
                            <i class="fas fa-history me-2"></i>Recent Events
                        </h5>
                    </div>
                    <div class="card-body">
                        <div class="events-container scroll-preserve" id="eventsContainer">
                            <div class="text-center">
                                <div class="spinner-border text-primary" role="status">
                                    <span class="visually-hidden">Loading events...</span>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            <!-- Settings Tab -->
            <div class="tab-pane fade" id="settings-tab">
                <div class="card">
                    <div class="card-header">
                        <h5 class="mb-0">
                            <i class="fas fa-sliders-h me-2"></i>System Settings
                        </h5>
                    </div>
                    <div class="card-body">
                        <div class="row">
                            <div class="col-md-6">
                                <div class="mb-3">
                                    <label for="timerDuration" class="form-label">Timer Duration (seconds)</label>
                                    <input type="number" class="form-control" id="timerDuration" min="1" max="86400" value="30">
                                </div>
                                <button class="btn btn-primary btn-custom" onclick="updateTimer()">
                                    <i class="fas fa-save me-2"></i>Update Timer
                                </button>
                            </div>
                            <div class="col-md-6">
                                <div class="alert alert-info">
                                    <h6><i class="fas fa-info-circle me-2"></i>Current Settings</h6>
                                    <p class="mb-1">Timer Duration: <span id="currentTimer">30</span> seconds</p>
                                    <p class="mb-0">Last Updated: <span id="lastUpdated">Never</span></p>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            <!-- Users Tab -->
            <div class="tab-pane fade" id="users-tab">
                <div class="card">
                    <div class="card-header">
                        <h5 class="mb-0">
                            <i class="fas fa-user-cog me-2"></i>User Management
                        </h5>
                    </div>
                    <div class="card-body">
                        <div class="alert alert-warning">
                            <i class="fas fa-construction me-2"></i>
                            User management interface coming soon. Contact administrator for user account changes.
                        </div>
                    </div>
                </div>
            </div>

            <!-- Reports Tab -->
            <div class="tab-pane fade" id="reports-tab">
                <div class="card">
                    <div class="card-header">
                        <h5 class="mb-0">
                            <i class="fas fa-download me-2"></i>Generate Reports
                        </h5>
                    </div>
                    <div class="card-body">
                        <div class="row">
                            <div class="col-md-6">
                                <h6>Export Options</h6>
                                <button class="btn btn-success btn-custom me-2" onclick="downloadReport()">
                                    <i class="fas fa-file-csv me-2"></i>Download CSV Report
                                </button>
                            </div>
                            <div class="col-md-6">
                                <div class="alert alert-info">
                                    <h6><i class="fas fa-info-circle me-2"></i>Report Contents</h6>
                                    <ul class="mb-0">
                                        <li>All system events</li>
                                        <li>User activities</li>
                                        <li>Door status changes</li>
                                        <li>Alarm triggers</li>
                                    </ul>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            <!-- Schedules Tab -->
            <div class="tab-pane fade" id="schedules-tab">
                <div class="card">
                    <div class="card-header">
                        <h5 class="mb-0">
                            <i class="fas fa-clock me-2"></i>Access Schedules
                        </h5>
                    </div>
                    <div class="card-body">
                        <div class="alert alert-info">
                            <i class="fas fa-calendar-check me-2"></i>
                            Schedule management interface coming soon. Contact administrator for schedule changes.
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <!-- Notification Toast Container -->
    <div id="toastContainer" class="position-fixed top-0 end-0 p-3" style="z-index: 9999;"></div>

    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
    <script>
        let lastScrollPosition = 0;
        let updateInProgress = false;
        
        // Preserve scroll position during updates
        function preserveScroll(callback) {
            if (updateInProgress) return;
            updateInProgress = true;
            
            const scrollableElements = document.querySelectorAll('.scroll-preserve');
            const scrollPositions = {};
            
            scrollableElements.forEach((element, index) => {
                scrollPositions[index] = element.scrollTop;
            });
            
            callback();
            
            setTimeout(() => {
                scrollableElements.forEach((element, index) => {
                    if (scrollPositions[index] !== undefined) {
                        element.scrollTop = scrollPositions[index];
                    }
                });
                updateInProgress = false;
            }, 100);
        }
        
        // Show notification toast
        function showNotification(message, type = 'info') {
            const toastContainer = document.getElementById('toastContainer');
            const toastId = 'toast-' + Date.now();
            
            const toastHtml = `
                <div id="${toastId}" class="toast notification-toast" role="alert">
                    <div class="toast-header bg-${type} text-white">
                        <i class="fas fa-${type === 'success' ? 'check-circle' : type === 'danger' ? 'exclamation-triangle' : 'info-circle'} me-2"></i>
                        <strong class="me-auto">System Alert</strong>
                        <button type="button" class="btn-close btn-close-white" data-bs-dismiss="toast"></button>
                    </div>
                    <div class="toast-body">${message}</div>
                </div>
            `;
            
            toastContainer.insertAdjacentHTML('beforeend', toastHtml);
            
            const toast = new bootstrap.Toast(document.getElementById(toastId));
            toast.show();
            
            // Auto remove after toast hides
            document.getElementById(toastId).addEventListener('hidden.bs.toast', function() {
                this.remove();
            });
        }
        
        // Update system status with scroll preservation
        function updateStatus() {
            preserveScroll(() => {
                fetch('/api/status')
                    .then(response => response.json())
                    .then(data => {
                        if (data.success) {
                            // Update door status
                            const doorCard = document.getElementById('doorStatusCard');
                            const doorStatus = document.getElementById('doorStatus');
                            if (data.door_open) {
                                doorCard.className = 'card status-card danger';
                                doorStatus.innerHTML = '<i class="fas fa-door-open me-1"></i>Open';
                            } else {
                                doorCard.className = 'card status-card';
                                doorStatus.innerHTML = '<i class="fas fa-door-closed me-1"></i>Closed';
                            }
                            
                            // Update timer status
                            const timerCard = document.getElementById('timerStatusCard');
                            const timerStatus = document.getElementById('timerStatus');
                            if (data.timer_active) {
                                timerCard.className = 'card status-card warning';
                                timerStatus.innerHTML = `<i class="fas fa-hourglass-half me-1"></i>${Math.ceil(data.remaining_time)}s`;
                            } else {
                                timerCard.className = 'card status-card';
                                timerStatus.innerHTML = '<i class="fas fa-pause me-1"></i>Inactive';
                            }
                            
                            // Update alarm status
                            const alarmCard = document.getElementById('alarmStatusCard');
                            const alarmStatus = document.getElementById('alarmStatus');
                            if (data.alarm_triggered) {
                                alarmCard.className = 'card status-card danger';
                                alarmStatus.innerHTML = '<i class="fas fa-exclamation-triangle me-1"></i>TRIGGERED';
                            } else {
                                alarmCard.className = 'card status-card';
                                alarmStatus.innerHTML = '<i class="fas fa-check me-1"></i>Normal';
                            }
                            
                            // Update GPIO status
                            document.getElementById('gpioStatus').innerHTML = data.gpio_available ? 
                                '<i class="fas fa-check me-1"></i>Active' : 
                                '<i class="fas fa-times me-1"></i>Simulation';
                            
                            // Update current timer display
                            document.getElementById('currentTimer').textContent = data.timer_duration;
                            document.getElementById('timerDuration').value = data.timer_duration;
                        }
                    })
                    .catch(error => {
                        console.error('Status update error:', error);
                        if (!updateInProgress) {
                            showNotification('Connection error - retrying...', 'danger');
                        }
                    });
            });
        }
        
        // Load events with scroll preservation
        function loadEvents() {
            preserveScroll(() => {
                fetch('/api/events')
                    .then(response => response.json())
                    .then(data => {
                        if (data.success) {
                            const eventsContainer = document.getElementById('eventsContainer');
                            if (data.events.length === 0) {
                                eventsContainer.innerHTML = '<div class="text-center text-muted">No events recorded yet.</div>';
                                return;
                            }
                            
                            let eventsHtml = '';
                            data.events.forEach(event => {
                                const severityClass = event.severity.toLowerCase();
                                const iconClass = {
                                    'critical': 'fas fa-exclamation-triangle text-danger',
                                    'warning': 'fas fa-exclamation-circle text-warning',
                                    'info': 'fas fa-info-circle text-info',
                                    'error': 'fas fa-times-circle text-danger'
                                }[severityClass] || 'fas fa-circle text-secondary';
                                
                                eventsHtml += `
                                    <div class="event-item event-${severityClass} p-3 mb-2 bg-light rounded">
                                        <div class="d-flex justify-content-between align-items-start">
                                            <div class="flex-grow-1">
                                                <div class="d-flex align-items-center mb-1">
                                                    <i class="${iconClass} me-2"></i>
                                                    <strong>${event.event_type}</strong>
                                                    <span class="badge bg-${severityClass === 'critical' ? 'danger' : severityClass} ms-2">${event.severity}</span>
                                                </div>
                                                <p class="mb-1">${event.description}</p>
                                                <small class="text-muted">
                                                    <i class="fas fa-user me-1"></i>${event.username} • 
                                                    <i class="fas fa-clock me-1"></i>${new Date(event.timestamp).toLocaleString()}
                                                </small>
                                            </div>
                                        </div>
                                    </div>
                                `;
                            });
                            
                            eventsContainer.innerHTML = eventsHtml;
                        }
                    })
                    .catch(error => {
                        console.error('Events loading error:', error);
                        document.getElementById('eventsContainer').innerHTML = 
                            '<div class="alert alert-danger">Error loading events. Please refresh the page.</div>';
                    });
            });
        }
        
        // Reset system
        function resetSystem() {
            if (confirm('Are you sure you want to reset the system? This will clear all active alarms and timers.')) {
                fetch('/api/reset', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' }
                })
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        showNotification('System reset successfully', 'success');
                        updateStatus();
                        loadEvents();
                    } else {
                        showNotification(data.message || 'Reset failed', 'danger');
                    }
                })
                .catch(error => {
                    console.error('Reset error:', error);
                    showNotification('Reset failed - connection error', 'danger');
                });
            }
        }
        
        // Update timer duration
        function updateTimer() {
            const duration = parseInt(document.getElementById('timerDuration').value);
            if (duration < 1 || duration > 86400) {
                showNotification('Timer duration must be between 1 second and 24 hours', 'danger');
                return;
            }
            
            fetch('/api/update_timer', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ duration: duration })
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    showNotification('Timer updated successfully', 'success');
                    document.getElementById('lastUpdated').textContent = new Date().toLocaleString();
                    updateStatus();
                    loadEvents();
                } else {
                    showNotification(data.message || 'Update failed', 'danger');
                }
            })
            .catch(error => {
                console.error('Timer update error:', error);
                showNotification('Update failed - connection error', 'danger');
            });
        }
        
        // Download report
        function downloadReport() {
            showNotification('Generating report...', 'info');
            window.location.href = '/api/download_report';
        }
        
        // Initialize page
        document.addEventListener('DOMContentLoaded', function() {
            updateStatus();
            loadEvents();
            
            // Set up regular updates with reduced frequency to prevent scroll issues
            setInterval(updateStatus, 3000);  // Every 3 seconds instead of 1
            setInterval(loadEvents, 10000);   // Every 10 seconds for events
            
            // Handle tab switching without page scroll
            const tabLinks = document.querySelectorAll('[data-bs-toggle="pill"]');
            tabLinks.forEach(link => {
                link.addEventListener('shown.bs.tab', function(e) {
                    // Prevent any automatic scrolling on tab change
                    e.preventDefault();
                    window.scrollTo(0, 0);
                });
            });
        });
    </script>
</body>
</html>
'''

LOGIN_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Door Monitor - Login</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
    <style>
        body {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
        }
        .login-card {
            backdrop-filter: blur(10px);
            background: rgba(255,255,255,0.1);
            border: 1px solid rgba(255,255,255,0.2);
            border-radius: 15px;
            box-shadow: 0 8px 30px rgba(0,0,0,0.3);
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="row justify-content-center">
            <div class="col-md-6">
                <div class="card login-card">
                    <div class="card-body p-5">
                        <div class="text-center mb-4">
                            <i class="fas fa-shield-alt fa-3x text-white mb-3"></i>
                            <h2 class="text-white">Door Security Monitor</h2>
                            <p class="text-white-50">Secure Access Required</p>
                        </div>
                        
                        {% if error %}
                        <div class="alert alert-danger">{{ error }}</div>
                        {% endif %}
                        
                        <form method="POST">
                            <div class="mb-3">
                                <label for="username" class="form-label text-white">Username</label>
                                <input type="text" class="form-control" id="username" name="username" required>
                            </div>
                            <div class="mb-3">
                                <label for="password" class="form-label text-white">Password</label>
                                <input type="password" class="form-control" id="password" name="password" required>
                            </div>
                            <button type="submit" class="btn btn-light w-100 fw-bold">
                                <i class="fas fa-sign-in-alt me-2"></i>Login
                            </button>
                        </form>
                        
                        <div class="text-center mt-4">
                            <small class="text-white-50">
                                Default: admin / admin123
                            </small>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>
</body>
</html>
'''

if __name__ == '__main__':
    init_db()
    if GPIO_AVAILABLE:
        monitor_door()
    
    print("🚀 Door Monitoring System Starting...")
    print(f"🌐 Flask API: http://localhost:5000")
    print(f"🌐 React Frontend: http://localhost:5173")
    print(f"🔧 GPIO Mode: {'Hardware' if GPIO_AVAILABLE else 'Simulation'}")
    print("🔐 Default Login: admin / admin123")
    
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)
