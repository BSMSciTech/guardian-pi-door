
#!/usr/bin/env python3
"""
Raspberry Pi Door Monitoring System
Complete single-file application with GPIO control, web dashboard, user management, and logging
"""

import os
import json
import sqlite3
import threading
import time
import datetime
import hashlib
import zipfile
import csv
import io
from threading import Timer
from dataclasses import dataclass
from typing import Optional, Dict, List, Any

# Flask and web dependencies
from flask import Flask, render_template_string, request, jsonify, redirect, url_for, session, flash, send_file
from werkzeug.security import generate_password_hash, check_password_hash

# GPIO control (will work on Raspberry Pi)
try:
    from gpiozero import LED, Button, DigitalInputDevice
    GPIO_AVAILABLE = True
except ImportError:
    print("GPIO not available - running in simulation mode")
    GPIO_AVAILABLE = False

# Audio playback
try:
    import pygame
    pygame.mixer.init()
    AUDIO_AVAILABLE = True
except ImportError:
    print("Audio not available - install pygame for sound")
    AUDIO_AVAILABLE = False

# Configuration
class Config:
    SECRET_KEY = 'your-secret-key-change-this'
    DATABASE = 'door_monitor.db'
    BACKUP_DIR = 'backups'
    AUDIO_FILE = 'alarm.wav'  # Place your alarm sound file here
    
    # GPIO Pin assignments
    DOOR_SENSOR_PIN = 17    # GPIO17 (BOARD 11)
    GREEN_LED_PIN = 25      # GPIO25 (BOARD 22)
    RED_LED_PIN = 27        # GPIO27 (BOARD 13)  
    WHITE_LED_PIN = 23      # GPIO23 (BOARD 16)
    SWITCH_PIN = 24         # GPIO24 (BOARD 18)

@dataclass
class SystemState:
    door_open: bool = False
    timer_active: bool = False
    alarm_active: bool = False
    timer_start_time: Optional[float] = None
    timer_duration: int = 30  # seconds
    instant_alarm_mode: bool = False
    
class DoorMonitoringSystem:
    def __init__(self):
        self.state = SystemState()
        self.load_state()
        
        # Initialize GPIO if available
        if GPIO_AVAILABLE:
            self.setup_gpio()
        else:
            print("Running in simulation mode - GPIO not available")
            
        # Initialize database
        self.init_database()
        
        # Timer for alarm
        self.alarm_timer = None
        self.red_led_blink_thread = None
        self.stop_blink = False
        
        # Schedules (3 windows per day)
        self.schedules = self.load_schedules()
        
    def setup_gpio(self):
        """Initialize GPIO components"""
        try:
            self.door_sensor = DigitalInputDevice(Config.DOOR_SENSOR_PIN, pull_up=True)
            self.green_led = LED(Config.GREEN_LED_PIN)
            self.red_led = LED(Config.RED_LED_PIN)
            self.white_led = LED(Config.WHITE_LED_PIN)
            
            # Set up door sensor callback
            self.door_sensor.when_activated = self.on_door_open
            self.door_sensor.when_deactivated = self.on_door_close
            
            # Green LED always on when system running
            self.green_led.on()
            
            print("GPIO initialized successfully")
        except Exception as e:
            print(f"GPIO initialization failed: {e}")
            
    def init_database(self):
        """Initialize SQLite database"""
        conn = sqlite3.connect(Config.DATABASE)
        cursor = conn.cursor()
        
        # Users table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                email TEXT,
                department TEXT,
                contact TEXT,
                reporting_manager TEXT,
                role TEXT DEFAULT 'User',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Events table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                event_type TEXT NOT NULL,
                description TEXT,
                user_id INTEGER,
                door_id TEXT DEFAULT 'Door-1',
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        ''')
        
        # Create default admin user if not exists
        cursor.execute("SELECT COUNT(*) FROM users WHERE username = 'admin'")
        if cursor.fetchone()[0] == 0:
            admin_hash = generate_password_hash('admin123')
            cursor.execute('''
                INSERT INTO users (username, password_hash, email, role)
                VALUES (?, ?, ?, ?)
            ''', ('admin', admin_hash, 'admin@example.com', 'Admin'))
            
        conn.commit()
        conn.close()
        
    def load_state(self):
        """Load system state from file"""
        try:
            if os.path.exists('system_state.json'):
                with open('system_state.json', 'r') as f:
                    data = json.load(f)
                    self.state.timer_duration = data.get('timer_duration', 30)
                    self.state.instant_alarm_mode = data.get('instant_alarm_mode', False)
        except Exception as e:
            print(f"Error loading state: {e}")
            
    def save_state(self):
        """Save system state to file"""
        try:
            data = {
                'timer_duration': self.state.timer_duration,
                'instant_alarm_mode': self.state.instant_alarm_mode,
                'door_open': self.state.door_open,
                'timer_active': self.state.timer_active,
                'alarm_active': self.state.alarm_active
            }
            with open('system_state.json', 'w') as f:
                json.dump(data, f)
        except Exception as e:
            print(f"Error saving state: {e}")
            
    def load_schedules(self):
        """Load access schedules"""
        try:
            if os.path.exists('schedules.json'):
                with open('schedules.json', 'r') as f:
                    return json.load(f)
        except Exception as e:
            print(f"Error loading schedules: {e}")
            
        # Default schedule
        return {
            'weekday': {
                'morning': {'start': '08:00', 'end': '12:00'},
                'afternoon': {'start': '13:00', 'end': '17:00'},
                'evening': {'start': '18:00', 'end': '20:00'}
            },
            'weekend': {
                'morning': {'start': '09:00', 'end': '12:00'},
                'afternoon': {'start': '13:00', 'end': '16:00'},
                'evening': {'start': '17:00', 'end': '19:00'}
            }
        }
        
    def save_schedules(self):
        """Save access schedules"""
        try:
            with open('schedules.json', 'w') as f:
                json.dump(self.schedules, f)
        except Exception as e:
            print(f"Error saving schedules: {e}")
            
    def is_access_time(self):
        """Check if current time is within access windows"""
        now = datetime.datetime.now()
        current_time = now.strftime('%H:%M')
        day_type = 'weekend' if now.weekday() >= 5 else 'weekday'
        
        schedule_day = self.schedules.get(day_type, {})
        
        for window in ['morning', 'afternoon', 'evening']:
            window_data = schedule_day.get(window, {})
            start_time = window_data.get('start')
            end_time = window_data.get('end')
            
            if start_time and end_time:
                if start_time <= current_time <= end_time:
                    return True
                    
        return False
        
    def on_door_open(self):
        """Handle door open event"""
        print("Door opened!")
        self.state.door_open = True
        self.log_event('DOOR_OPEN', 'Door was opened')
        
        if not self.is_access_time():
            if self.state.instant_alarm_mode:
                self.trigger_alarm()
            else:
                self.start_timer()
        else:
            print("Access allowed - within scheduled hours")
            
        self.save_state()
        
    def on_door_close(self):
        """Handle door close event"""
        print("Door closed!")
        self.state.door_open = False
        self.log_event('DOOR_CLOSE', 'Door was closed')
        
        # Stop timer if running (but don't reset alarm if already triggered)
        if self.state.timer_active and not self.state.alarm_active:
            self.stop_timer()
            
        self.save_state()
        
    def start_timer(self):
        """Start countdown timer"""
        if self.state.timer_active:
            return
            
        print(f"Starting timer for {self.state.timer_duration} seconds")
        self.state.timer_active = True
        self.state.timer_start_time = time.time()
        
        # Start red LED blinking
        self.start_red_led_blink()
        
        # Set alarm timer
        self.alarm_timer = Timer(self.state.timer_duration, self.on_timer_expire)
        self.alarm_timer.start()
        
        self.log_event('TIMER_START', f'Timer started for {self.state.timer_duration} seconds')
        
    def stop_timer(self):
        """Stop countdown timer"""
        if not self.state.timer_active:
            return
            
        print("Stopping timer")
        self.state.timer_active = False
        self.state.timer_start_time = None
        
        if self.alarm_timer:
            self.alarm_timer.cancel()
            self.alarm_timer = None
            
        # Stop red LED blinking
        self.stop_red_led_blink()
        
        self.log_event('TIMER_STOP', 'Timer stopped')
        
    def on_timer_expire(self):
        """Handle timer expiration"""
        print("Timer expired - triggering alarm!")
        self.state.timer_active = False
        self.trigger_alarm()
        
    def trigger_alarm(self):
        """Trigger alarm system"""
        print("ALARM TRIGGERED!")
        self.state.alarm_active = True
        
        # Stop red LED blinking and turn on white LED
        self.stop_red_led_blink()
        if GPIO_AVAILABLE:
            self.white_led.on()
            
        # Play alarm sound
        self.play_alarm_sound()
        
        self.log_event('ALARM_TRIGGER', 'Alarm was triggered')
        self.save_state()
        
    def reset_alarm(self):
        """Reset alarm system"""
        print("Resetting alarm")
        self.state.alarm_active = False
        self.state.timer_active = False
        
        if GPIO_AVAILABLE:
            self.white_led.off()
            self.red_led.off()
            
        self.stop_red_led_blink()
        
        if self.alarm_timer:
            self.alarm_timer.cancel()
            self.alarm_timer = None
            
        self.log_event('ALARM_RESET', 'Alarm was reset')
        self.save_state()
        
    def start_red_led_blink(self):
        """Start red LED blinking"""
        if not GPIO_AVAILABLE:
            return
            
        self.stop_blink = False
        self.red_led_blink_thread = threading.Thread(target=self._blink_red_led)
        self.red_led_blink_thread.start()
        
    def stop_red_led_blink(self):
        """Stop red LED blinking"""
        self.stop_blink = True
        if self.red_led_blink_thread and self.red_led_blink_thread.is_alive():
            self.red_led_blink_thread.join(timeout=1)
            
        if GPIO_AVAILABLE:
            self.red_led.off()
            
    def _blink_red_led(self):
        """Blink red LED in separate thread"""
        while not self.stop_blink:
            if GPIO_AVAILABLE:
                self.red_led.on()
            time.sleep(0.5)
            if GPIO_AVAILABLE:
                self.red_led.off()
            time.sleep(0.5)
            
    def play_alarm_sound(self):
        """Play alarm sound if available"""
        if AUDIO_AVAILABLE and os.path.exists(Config.AUDIO_FILE):
            try:
                pygame.mixer.music.load(Config.AUDIO_FILE)
                pygame.mixer.music.play()
            except Exception as e:
                print(f"Error playing alarm sound: {e}")
        else:
            # Fallback: system beep
            try:
                os.system('echo -e "\a"')
            except:
                pass
                
    def log_event(self, event_type: str, description: str, user_id: Optional[int] = None):
        """Log event to database"""
        try:
            conn = sqlite3.connect(Config.DATABASE)
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO events (event_type, description, user_id)
                VALUES (?, ?, ?)
            ''', (event_type, description, user_id))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"Error logging event: {e}")
            
    def get_events(self, limit: int = 25, offset: int = 0, event_type: str = None):
        """Get events from database"""
        try:
            conn = sqlite3.connect(Config.DATABASE)
            cursor = conn.cursor()
            
            query = '''
                SELECT e.*, u.username 
                FROM events e 
                LEFT JOIN users u ON e.user_id = u.id
            '''
            params = []
            
            if event_type:
                query += ' WHERE e.event_type = ?'
                params.append(event_type)
                
            query += ' ORDER BY e.timestamp DESC LIMIT ? OFFSET ?'
            params.extend([limit, offset])
            
            cursor.execute(query, params)
            events = cursor.fetchall()
            conn.close()
            return events
        except Exception as e:
            print(f"Error getting events: {e}")
            return []
            
    def get_system_status(self):
        """Get current system status"""
        remaining_time = None
        if self.state.timer_active and self.state.timer_start_time:
            elapsed = time.time() - self.state.timer_start_time
            remaining_time = max(0, self.state.timer_duration - elapsed)
            
        return {
            'door_open': self.state.door_open,
            'timer_active': self.state.timer_active,
            'alarm_active': self.state.alarm_active,
            'remaining_time': remaining_time,
            'timer_duration': self.state.timer_duration,
            'instant_alarm_mode': self.state.instant_alarm_mode,
            'access_time': self.is_access_time(),
            'green_led': True,  # Always on when system running
            'red_led': self.state.timer_active,
            'white_led': self.state.alarm_active
        }

# Initialize the monitoring system
monitor = DoorMonitoringSystem()

# Flask application
app = Flask(__name__)
app.secret_key = Config.SECRET_KEY

# HTML Templates
MAIN_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Door Monitoring System</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/css/bootstrap.min.css" rel="stylesheet">
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css" rel="stylesheet">
    <style>
        .status-indicator {
            width: 20px;
            height: 20px;
            border-radius: 50%;
            display: inline-block;
            margin-right: 10px;
        }
        .status-on { background-color: #28a745; }
        .status-off { background-color: #6c757d; }
        .status-blink { 
            background-color: #dc3545; 
            animation: blink 1s infinite;
        }
        @keyframes blink {
            0%, 50% { opacity: 1; }
            51%, 100% { opacity: 0.3; }
        }
        .alarm-active {
            background-color: #fff3cd !important;
            border: 2px solid #ffc107 !important;
        }
        .navbar-brand { font-weight: bold; }
        .card-header { background-color: #f8f9fa; }
        .system-status { font-size: 1.1em; }
        .btn-group-sm .btn { font-size: 0.875rem; }
    </style>
</head>
<body>
    <nav class="navbar navbar-expand-lg navbar-dark bg-dark">
        <div class="container">
            <a class="navbar-brand" href="/">
                <i class="fas fa-door-open"></i> Door Monitor
            </a>
            <div class="navbar-nav ml-auto">
                {% if session.get('user_id') %}
                    <span class="navbar-text me-3">
                        Welcome, {{ session.get('username', 'User') }}
                        ({{ session.get('role', 'User') }})
                    </span>
                    <a class="nav-link" href="/logout">Logout</a>
                {% else %}
                    <a class="nav-link" href="/login">Login</a>
                {% endif %}
            </div>
        </div>
    </nav>

    <div class="container mt-4">
        {% with messages = get_flashed_messages() %}
            {% if messages %}
                {% for message in messages %}
                    <div class="alert alert-info alert-dismissible fade show" role="alert">
                        {{ message }}
                        <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
                    </div>
                {% endfor %}
            {% endif %}
        {% endwith %}

        {% if not session.get('user_id') %}
            <div class="row justify-content-center">
                <div class="col-md-6">
                    <div class="card">
                        <div class="card-header">
                            <h5><i class="fas fa-sign-in-alt"></i> Login Required</h5>
                        </div>
                        <div class="card-body">
                            <p>Please login to access the Door Monitoring System.</p>
                            <a href="/login" class="btn btn-primary">
                                <i class="fas fa-sign-in-alt"></i> Login
                            </a>
                        </div>
                    </div>
                </div>
            </div>
        {% else %}
            <!-- System Status Card -->
            <div class="row mb-4">
                <div class="col-12">
                    <div class="card {% if status.alarm_active %}alarm-active{% endif %}">
                        <div class="card-header">
                            <h5><i class="fas fa-tachometer-alt"></i> System Status</h5>
                        </div>
                        <div class="card-body system-status">
                            <div class="row">
                                <div class="col-md-3">
                                    <div class="mb-2">
                                        <span class="status-indicator {% if status.door_open %}status-blink{% else %}status-off{% endif %}"></span>
                                        Door: {% if status.door_open %}<strong class="text-danger">OPEN</strong>{% else %}<strong class="text-success">CLOSED</strong>{% endif %}
                                    </div>
                                </div>
                                <div class="col-md-3">
                                    <div class="mb-2">
                                        <span class="status-indicator {% if status.green_led %}status-on{% else %}status-off{% endif %}"></span>
                                        System: <strong class="text-success">RUNNING</strong>
                                    </div>
                                </div>
                                <div class="col-md-3">
                                    <div class="mb-2">
                                        <span class="status-indicator {% if status.red_led %}status-blink{% else %}status-off{% endif %}"></span>
                                        Timer: {% if status.timer_active %}<strong class="text-warning">ACTIVE</strong>{% else %}<strong>INACTIVE</strong>{% endif %}
                                    </div>
                                </div>
                                <div class="col-md-3">
                                    <div class="mb-2">
                                        <span class="status-indicator {% if status.white_led %}status-on{% else %}status-off{% endif %}"></span>
                                        Alarm: {% if status.alarm_active %}<strong class="text-danger">TRIGGERED</strong>{% else %}<strong>NORMAL</strong>{% endif %}
                                    </div>
                                </div>
                            </div>
                            {% if status.timer_active and status.remaining_time %}
                                <div class="mt-2">
                                    <div class="progress">
                                        <div class="progress-bar progress-bar-striped progress-bar-animated bg-warning" 
                                             style="width: {{ (status.remaining_time / status.timer_duration * 100) }}%">
                                            Timer: {{ "%.1f"|format(status.remaining_time) }}s remaining
                                        </div>
                                    </div>
                                </div>
                            {% endif %}
                            {% if status.alarm_active %}
                                <div class="mt-3">
                                    <button class="btn btn-danger" onclick="resetAlarm()">
                                        <i class="fas fa-exclamation-triangle"></i> Reset Alarm
                                    </button>
                                </div>
                            {% endif %}
                        </div>
                    </div>
                </div>
            </div>

            <!-- Navigation Tabs -->
            <ul class="nav nav-tabs" id="systemTabs" role="tablist">
                <li class="nav-item" role="presentation">
                    <button class="nav-link active" id="events-tab" data-bs-toggle="tab" data-bs-target="#events" type="button">
                        <i class="fas fa-list"></i> Events
                    </button>
                </li>
                <li class="nav-item" role="presentation">
                    <button class="nav-link" id="settings-tab" data-bs-toggle="tab" data-bs-target="#settings" type="button">
                        <i class="fas fa-cog"></i> Settings
                    </button>
                </li>
                <li class="nav-item" role="presentation">
                    <button class="nav-link" id="schedules-tab" data-bs-toggle="tab" data-bs-target="#schedules" type="button">
                        <i class="fas fa-calendar"></i> Schedules
                    </button>
                </li>
                {% if session.get('role') == 'Admin' %}
                <li class="nav-item" role="presentation">
                    <button class="nav-link" id="users-tab" data-bs-toggle="tab" data-bs-target="#users" type="button">
                        <i class="fas fa-users"></i> Users
                    </button>
                </li>
                {% endif %}
                <li class="nav-item" role="presentation">
                    <button class="nav-link" id="reports-tab" data-bs-toggle="tab" data-bs-target="#reports" type="button">
                        <i class="fas fa-chart-line"></i> Reports
                    </button>
                </li>
            </ul>

            <!-- Tab Content -->
            <div class="tab-content mt-3" id="systemTabsContent">
                <!-- Events Tab -->
                <div class="tab-pane fade show active" id="events" role="tabpanel">
                    <div class="card">
                        <div class="card-header d-flex justify-content-between align-items-center">
                            <h5><i class="fas fa-list"></i> Recent Events</h5>
                            <button class="btn btn-sm btn-outline-primary" onclick="refreshEvents()">
                                <i class="fas fa-refresh"></i> Refresh
                            </button>
                        </div>
                        <div class="card-body">
                            <div class="table-responsive">
                                <table class="table table-striped" id="eventsTable">
                                    <thead>
                                        <tr>
                                            <th>Timestamp</th>
                                            <th>Event</th>
                                            <th>Description</th>
                                            <th>User</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {% for event in events %}
                                        <tr>
                                            <td>{{ event[1] }}</td>
                                            <td>
                                                {% if event[2] == 'DOOR_OPEN' %}
                                                    <span class="badge bg-warning">{{ event[2] }}</span>
                                                {% elif event[2] == 'DOOR_CLOSE' %}
                                                    <span class="badge bg-success">{{ event[2] }}</span>
                                                {% elif event[2] == 'ALARM_TRIGGER' %}
                                                    <span class="badge bg-danger">{{ event[2] }}</span>
                                                {% else %}
                                                    <span class="badge bg-info">{{ event[2] }}</span>
                                                {% endif %}
                                            </td>
                                            <td>{{ event[3] }}</td>
                                            <td>{{ event[6] or 'System' }}</td>
                                        </tr>
                                        {% endfor %}
                                    </tbody>
                                </table>
                            </div>
                        </div>
                    </div>
                </div>

                <!-- Settings Tab -->
                <div class="tab-pane fade" id="settings" role="tabpanel">
                    <div class="row">
                        <div class="col-md-6">
                            <div class="card">
                                <div class="card-header">
                                    <h5><i class="fas fa-clock"></i> Timer Settings</h5>
                                </div>
                                <div class="card-body">
                                    <form onsubmit="updateSettings(event)">
                                        <div class="mb-3">
                                            <label for="timerDuration" class="form-label">Timer Duration (seconds)</label>
                                            <input type="number" class="form-control" id="timerDuration" 
                                                   value="{{ status.timer_duration }}" min="1" max="86400">
                                        </div>
                                        <div class="mb-3">
                                            <div class="form-check">
                                                <input class="form-check-input" type="checkbox" id="instantAlarm" 
                                                       {% if status.instant_alarm_mode %}checked{% endif %}>
                                                <label class="form-check-label" for="instantAlarm">
                                                    Instant Alarm Mode (outside scheduled hours)
                                                </label>
                                            </div>
                                        </div>
                                        <button type="submit" class="btn btn-primary">
                                            <i class="fas fa-save"></i> Save Settings
                                        </button>
                                    </form>
                                </div>
                            </div>
                        </div>
                        <div class="col-md-6">
                            <div class="card">
                                <div class="card-header">
                                    <h5><i class="fas fa-tools"></i> System Controls</h5>
                                </div>
                                <div class="card-body">
                                    <div class="d-grid gap-2">
                                        <button class="btn btn-warning" onclick="testAlarm()">
                                            <i class="fas fa-volume-up"></i> Test Alarm
                                        </button>
                                        <button class="btn btn-info" onclick="testLEDs()">
                                            <i class="fas fa-lightbulb"></i> Test LEDs
                                        </button>
                                        {% if status.alarm_active %}
                                        <button class="btn btn-danger" onclick="resetAlarm()">
                                            <i class="fas fa-stop"></i> Reset Alarm
                                        </button>
                                        {% endif %}
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>

                <!-- Schedules Tab -->
                <div class="tab-pane fade" id="schedules" role="tabpanel">
                    <div class="card">
                        <div class="card-header">
                            <h5><i class="fas fa-calendar"></i> Access Schedules</h5>
                        </div>
                        <div class="card-body">
                            <div class="row">
                                <div class="col-md-6">
                                    <h6>Weekday Schedule</h6>
                                    <div class="mb-3">
                                        <label class="form-label">Morning</label>
                                        <div class="row">
                                            <div class="col">
                                                <input type="time" class="form-control" id="weekday_morning_start" 
                                                       value="{{ schedules.weekday.morning.start }}">
                                            </div>
                                            <div class="col">
                                                <input type="time" class="form-control" id="weekday_morning_end" 
                                                       value="{{ schedules.weekday.morning.end }}">
                                            </div>
                                        </div>
                                    </div>
                                    <div class="mb-3">
                                        <label class="form-label">Afternoon</label>
                                        <div class="row">
                                            <div class="col">
                                                <input type="time" class="form-control" id="weekday_afternoon_start" 
                                                       value="{{ schedules.weekday.afternoon.start }}">
                                            </div>
                                            <div class="col">
                                                <input type="time" class="form-control" id="weekday_afternoon_end" 
                                                       value="{{ schedules.weekday.afternoon.end }}">
                                            </div>
                                        </div>
                                    </div>
                                    <div class="mb-3">
                                        <label class="form-label">Evening</label>
                                        <div class="row">
                                            <div class="col">
                                                <input type="time" class="form-control" id="weekday_evening_start" 
                                                       value="{{ schedules.weekday.evening.start }}">
                                            </div>
                                            <div class="col">
                                                <input type="time" class="form-control" id="weekday_evening_end" 
                                                       value="{{ schedules.weekday.evening.end }}">
                                            </div>
                                        </div>
                                    </div>
                                </div>
                                <div class="col-md-6">
                                    <h6>Weekend Schedule</h6>
                                    <div class="mb-3">
                                        <label class="form-label">Morning</label>
                                        <div class="row">
                                            <div class="col">
                                                <input type="time" class="form-control" id="weekend_morning_start" 
                                                       value="{{ schedules.weekend.morning.start }}">
                                            </div>
                                            <div class="col">
                                                <input type="time" class="form-control" id="weekend_morning_end" 
                                                       value="{{ schedules.weekend.morning.end }}">
                                            </div>
                                        </div>
                                    </div>
                                    <div class="mb-3">
                                        <label class="form-label">Afternoon</label>
                                        <div class="row">
                                            <div class="col">
                                                <input type="time" class="form-control" id="weekend_afternoon_start" 
                                                       value="{{ schedules.weekend.afternoon.start }}">
                                            </div>
                                            <div class="col">
                                                <input type="time" class="form-control" id="weekend_afternoon_end" 
                                                       value="{{ schedules.weekend.afternoon.end }}">
                                            </div>
                                        </div>
                                    </div>
                                    <div class="mb-3">
                                        <label class="form-label">Evening</label>
                                        <div class="row">
                                            <div class="col">
                                                <input type="time" class="form-control" id="weekend_evening_start" 
                                                       value="{{ schedules.weekend.evening.start }}">
                                            </div>
                                            <div class="col">
                                                <input type="time" class="form-control" id="weekend_evening_end" 
                                                       value="{{ schedules.weekend.evening.end }}">
                                            </div>
                                        </div>
                                    </div>
                                </div>
                            </div>
                            <button class="btn btn-primary" onclick="saveSchedules()">
                                <i class="fas fa-save"></i> Save Schedules
                            </button>
                        </div>
                    </div>
                </div>

                <!-- Users Tab (Admin only) -->
                {% if session.get('role') == 'Admin' %}
                <div class="tab-pane fade" id="users" role="tabpanel">
                    <div class="card">
                        <div class="card-header d-flex justify-content-between align-items-center">
                            <h5><i class="fas fa-users"></i> User Management</h5>
                            <button class="btn btn-sm btn-primary" data-bs-toggle="modal" data-bs-target="#addUserModal">
                                <i class="fas fa-plus"></i> Add User
                            </button>
                        </div>
                        <div class="card-body">
                            <div class="table-responsive">
                                <table class="table table-striped">
                                    <thead>
                                        <tr>
                                            <th>Username</th>
                                            <th>Email</th>
                                            <th>Department</th>
                                            <th>Role</th>
                                            <th>Actions</th>
                                        </tr>
                                    </thead>
                                    <tbody id="usersTableBody">
                                        {% for user in users %}
                                        <tr>
                                            <td>{{ user[1] }}</td>
                                            <td>{{ user[3] or '-' }}</td>
                                            <td>{{ user[4] or '-' }}</td>
                                            <td>
                                                <span class="badge bg-{% if user[7] == 'Admin' %}danger{% elif user[7] == 'Manager' %}warning{% else %}info{% endif %}">
                                                    {{ user[7] }}
                                                </span>
                                            </td>
                                            <td>
                                                {% if user[1] != 'admin' %}
                                                <button class="btn btn-sm btn-outline-danger" onclick="deleteUser({{ user[0] }})">
                                                    <i class="fas fa-trash"></i>
                                                </button>
                                                {% endif %}
                                            </td>
                                        </tr>
                                        {% endfor %}
                                    </tbody>
                                </table>
                            </div>
                        </div>
                    </div>
                </div>
                {% endif %}

                <!-- Reports Tab -->
                <div class="tab-pane fade" id="reports" role="tabpanel">
                    <div class="card">
                        <div class="card-header">
                            <h5><i class="fas fa-chart-line"></i> Reports & Export</h5>
                        </div>
                        <div class="card-body">
                            <div class="row">
                                <div class="col-md-6">
                                    <h6>Export Events</h6>
                                    <form onsubmit="exportData(event)">
                                        <div class="mb-3">
                                            <label for="exportFormat" class="form-label">Format</label>
                                            <select class="form-select" id="exportFormat">
                                                <option value="csv">CSV</option>
                                                <option value="pdf">PDF</option>
                                            </select>
                                        </div>
                                        <div class="mb-3">
                                            <label for="dateFrom" class="form-label">From Date</label>
                                            <input type="date" class="form-control" id="dateFrom">
                                        </div>
                                        <div class="mb-3">
                                            <label for="dateTo" class="form-label">To Date</label>
                                            <input type="date" class="form-control" id="dateTo">
                                        </div>
                                        <button type="submit" class="btn btn-success">
                                            <i class="fas fa-download"></i> Export
                                        </button>
                                    </form>
                                </div>
                                <div class="col-md-6">
                                    <h6>System Backup</h6>
                                    <p>Create a backup of all system data including database and logs.</p>
                                    <button class="btn btn-warning" onclick="createBackup()">
                                        <i class="fas fa-archive"></i> Create Backup
                                    </button>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        {% endif %}
    </div>

    <!-- Add User Modal -->
    <div class="modal fade" id="addUserModal" tabindex="-1">
        <div class="modal-dialog">
            <div class="modal-content">
                <div class="modal-header">
                    <h5 class="modal-title">Add New User</h5>
                    <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
                </div>
                <form onsubmit="addUser(event)">
                    <div class="modal-body">
                        <div class="mb-3">
                            <label for="newUsername" class="form-label">Username</label>
                            <input type="text" class="form-control" id="newUsername" required>
                        </div>
                        <div class="mb-3">
                            <label for="newPassword" class="form-label">Password</label>
                            <input type="password" class="form-control" id="newPassword" required>
                        </div>
                        <div class="mb-3">
                            <label for="newEmail" class="form-label">Email</label>
                            <input type="email" class="form-control" id="newEmail">
                        </div>
                        <div class="mb-3">
                            <label for="newDepartment" class="form-label">Department</label>
                            <input type="text" class="form-control" id="newDepartment">
                        </div>
                        <div class="mb-3">
                            <label for="newRole" class="form-label">Role</label>
                            <select class="form-select" id="newRole">
                                <option value="User">User</option>
                                <option value="Supervisor">Supervisor</option>
                                <option value="Manager">Manager</option>
                                <option value="Admin">Admin</option>
                            </select>
                        </div>
                    </div>
                    <div class="modal-footer">
                        <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cancel</button>
                        <button type="submit" class="btn btn-primary">Add User</button>
                    </div>
                </form>
            </div>
        </div>
    </div>

    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/js/bootstrap.bundle.min.js"></script>
    <script>
        // Auto-refresh status every 2 seconds
        setInterval(function() {
            if (document.visibilityState === 'visible') {
                location.reload();
            }
        }, 2000);

        function resetAlarm() {
            fetch('/api/reset_alarm', { method: 'POST' })
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        location.reload();
                    }
                });
        }

        function testAlarm() {
            fetch('/api/test_alarm', { method: 'POST' })
                .then(response => response.json())
                .then(data => alert(data.message));
        }

        function testLEDs() {
            fetch('/api/test_leds', { method: 'POST' })
                .then(response => response.json())
                .then(data => alert(data.message));
        }

        function updateSettings(event) {
            event.preventDefault();
            const data = {
                timer_duration: document.getElementById('timerDuration').value,
                instant_alarm_mode: document.getElementById('instantAlarm').checked
            };
            
            fetch('/api/update_settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(data)
            })
            .then(response => response.json())
            .then(data => {
                alert(data.message);
                if (data.success) location.reload();
            });
        }

        function saveSchedules() {
            const schedules = {
                weekday: {
                    morning: {
                        start: document.getElementById('weekday_morning_start').value,
                        end: document.getElementById('weekday_morning_end').value
                    },
                    afternoon: {
                        start: document.getElementById('weekday_afternoon_start').value,
                        end: document.getElementById('weekday_afternoon_end').value
                    },
                    evening: {
                        start: document.getElementById('weekday_evening_start').value,
                        end: document.getElementById('weekday_evening_end').value
                    }
                },
                weekend: {
                    morning: {
                        start: document.getElementById('weekend_morning_start').value,
                        end: document.getElementById('weekend_morning_end').value
                    },
                    afternoon: {
                        start: document.getElementById('weekend_afternoon_start').value,
                        end: document.getElementById('weekend_afternoon_end').value
                    },
                    evening: {
                        start: document.getElementById('weekend_evening_start').value,
                        end: document.getElementById('weekend_evening_end').value
                    }
                }
            };

            fetch('/api/save_schedules', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(schedules)
            })
            .then(response => response.json())
            .then(data => alert(data.message));
        }

        function addUser(event) {
            event.preventDefault();
            const data = {
                username: document.getElementById('newUsername').value,
                password: document.getElementById('newPassword').value,
                email: document.getElementById('newEmail').value,
                department: document.getElementById('newDepartment').value,
                role: document.getElementById('newRole').value
            };

            fetch('/api/add_user', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(data)
            })
            .then(response => response.json())
            .then(data => {
                alert(data.message);
                if (data.success) {
                    document.getElementById('addUserModal').querySelector('.btn-close').click();
                    location.reload();
                }
            });
        }

        function deleteUser(userId) {
            if (confirm('Are you sure you want to delete this user?')) {
                fetch(`/api/delete_user/${userId}`, { method: 'DELETE' })
                    .then(response => response.json())
                    .then(data => {
                        alert(data.message);
                        if (data.success) location.reload();
                    });
            }
        }

        function exportData(event) {
            event.preventDefault();
            const format = document.getElementById('exportFormat').value;
            const dateFrom = document.getElementById('dateFrom').value;
            const dateTo = document.getElementById('dateTo').value;
            
            const params = new URLSearchParams({ format, date_from: dateFrom, date_to: dateTo });
            window.open(`/api/export?${params}`);
        }

        function createBackup() {
            fetch('/api/backup', { method: 'POST' })
                .then(response => response.json())
                .then(data => {
                    alert(data.message);
                    if (data.success && data.file) {
                        window.open(`/api/download_backup?file=${data.file}`);
                    }
                });
        }

        function refreshEvents() {
            location.reload();
        }
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
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/css/bootstrap.min.css" rel="stylesheet">
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css" rel="stylesheet">
</head>
<body class="bg-light">
    <div class="container">
        <div class="row justify-content-center">
            <div class="col-md-6 col-lg-4">
                <div class="card mt-5">
                    <div class="card-header text-center">
                        <h4><i class="fas fa-door-open"></i> Door Monitor</h4>
                        <p class="text-muted">Please sign in to continue</p>
                    </div>
                    <div class="card-body">
                        {% with messages = get_flashed_messages() %}
                            {% if messages %}
                                {% for message in messages %}
                                    <div class="alert alert-danger" role="alert">
                                        {{ message }}
                                    </div>
                                {% endfor %}
                            {% endif %}
                        {% endwith %}
                        
                        <form method="POST">
                            <div class="mb-3">
                                <label for="username" class="form-label">Username</label>
                                <input type="text" class="form-control" id="username" name="username" required>
                            </div>
                            <div class="mb-3">
                                <label for="password" class="form-label">Password</label>
                                <input type="password" class="form-control" id="password" name="password" required>
                            </div>
                            <div class="d-grid">
                                <button type="submit" class="btn btn-primary">
                                    <i class="fas fa-sign-in-alt"></i> Sign In
                                </button>
                            </div>
                        </form>
                    </div>
                    <div class="card-footer text-center text-muted">
                        <small>Default: admin / admin123</small>
                    </div>
                </div>
            </div>
        </div>
    </div>
</body>
</html>
'''

# Flask Routes
@app.route('/')
def index():
    if not session.get('user_id'):
        return render_template_string(MAIN_TEMPLATE)
    
    status = monitor.get_system_status()
    events = monitor.get_events(limit=10)
    schedules = monitor.schedules
    
    # Get users for admin
    users = []
    if session.get('role') == 'Admin':
        try:
            conn = sqlite3.connect(Config.DATABASE)
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users ORDER BY username")
            users = cursor.fetchall()
            conn.close()
        except Exception as e:
            print(f"Error getting users: {e}")
    
    return render_template_string(MAIN_TEMPLATE, 
                                 status=status, 
                                 events=events, 
                                 schedules=schedules,
                                 users=users)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        try:
            conn = sqlite3.connect(Config.DATABASE)
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
            user = cursor.fetchone()
            conn.close()
            
            if user and check_password_hash(user[2], password):
                session['user_id'] = user[0]
                session['username'] = user[1]
                session['role'] = user[7]
                
                monitor.log_event('LOGIN', f'User {username} logged in', user[0])
                flash(f'Welcome, {username}!')
                return redirect(url_for('index'))
            else:
                flash('Invalid username or password')
                
        except Exception as e:
            flash(f'Login error: {e}')
    
    return render_template_string(LOGIN_TEMPLATE)

@app.route('/logout')
def logout():
    if session.get('user_id'):
        monitor.log_event('LOGOUT', f'User {session.get("username")} logged out', session.get('user_id'))
    
    session.clear()
    flash('You have been logged out')
    return redirect(url_for('login'))

# API Routes
@app.route('/api/status')
def api_status():
    return jsonify(monitor.get_system_status())

@app.route('/api/reset_alarm', methods=['POST'])
def api_reset_alarm():
    if not session.get('user_id'):
        return jsonify({'success': False, 'message': 'Not authorized'})
    
    monitor.reset_alarm()
    return jsonify({'success': True, 'message': 'Alarm reset'})

@app.route('/api/test_alarm', methods=['POST'])
def api_test_alarm():
    if not session.get('user_id'):
        return jsonify({'success': False, 'message': 'Not authorized'})
    
    monitor.play_alarm_sound()
    monitor.log_event('TEST_ALARM', 'Alarm test initiated', session.get('user_id'))
    return jsonify({'success': True, 'message': 'Alarm tested'})

@app.route('/api/test_leds', methods=['POST'])
def api_test_leds():
    if not session.get('user_id'):
        return jsonify({'success': False, 'message': 'Not authorized'})
    
    if GPIO_AVAILABLE:
        # Flash all LEDs
        monitor.red_led.on()
        monitor.white_led.on()
        time.sleep(1)
        monitor.red_led.off()
        monitor.white_led.off()
    
    monitor.log_event('TEST_LEDS', 'LED test initiated', session.get('user_id'))
    return jsonify({'success': True, 'message': 'LEDs tested'})

@app.route('/api/update_settings', methods=['POST'])
def api_update_settings():
    if not session.get('user_id'):
        return jsonify({'success': False, 'message': 'Not authorized'})
    
    data = request.get_json()
    
    try:
        monitor.state.timer_duration = int(data.get('timer_duration', 30))
        monitor.state.instant_alarm_mode = bool(data.get('instant_alarm_mode', False))
        monitor.save_state()
        
        monitor.log_event('SETTINGS_UPDATE', 'System settings updated', session.get('user_id'))
        return jsonify({'success': True, 'message': 'Settings updated'})
    except Exception as e:
        return jsonify({'success': False, 'message': f'Error: {e}'})

@app.route('/api/save_schedules', methods=['POST'])
def api_save_schedules():
    if not session.get('user_id'):
        return jsonify({'success': False, 'message': 'Not authorized'})
    
    data = request.get_json()
    
    try:
        monitor.schedules = data
        monitor.save_schedules()
        
        monitor.log_event('SCHEDULE_UPDATE', 'Access schedules updated', session.get('user_id'))
        return jsonify({'success': True, 'message': 'Schedules saved'})
    except Exception as e:
        return jsonify({'success': False, 'message': f'Error: {e}'})

@app.route('/api/add_user', methods=['POST'])
def api_add_user():
    if session.get('role') != 'Admin':
        return jsonify({'success': False, 'message': 'Admin access required'})
    
    data = request.get_json()
    
    try:
        conn = sqlite3.connect(Config.DATABASE)
        cursor = conn.cursor()
        
        password_hash = generate_password_hash(data['password'])
        cursor.execute('''
            INSERT INTO users (username, password_hash, email, department, role)
            VALUES (?, ?, ?, ?, ?)
        ''', (data['username'], password_hash, data.get('email'), 
              data.get('department'), data.get('role', 'User')))
        
        conn.commit()
        conn.close()
        
        monitor.log_event('USER_CREATE', f'User {data["username"]} created', session.get('user_id'))
        return jsonify({'success': True, 'message': 'User created successfully'})
        
    except sqlite3.IntegrityError:
        return jsonify({'success': False, 'message': 'Username already exists'})
    except Exception as e:
        return jsonify({'success': False, 'message': f'Error: {e}'})

@app.route('/api/delete_user/<int:user_id>', methods=['DELETE'])
def api_delete_user(user_id):
    if session.get('role') != 'Admin':
        return jsonify({'success': False, 'message': 'Admin access required'})
    
    try:
        conn = sqlite3.connect(Config.DATABASE)
        cursor = conn.cursor()
        
        # Get username first
        cursor.execute("SELECT username FROM users WHERE id = ?", (user_id,))
        user = cursor.fetchone()
        
        if user:
            cursor.execute("DELETE FROM users WHERE id = ?", (user_id,))
            conn.commit()
            
            monitor.log_event('USER_DELETE', f'User {user[0]} deleted', session.get('user_id'))
            message = 'User deleted successfully'
            success = True
        else:
            message = 'User not found'
            success = False
            
        conn.close()
        return jsonify({'success': success, 'message': message})
        
    except Exception as e:
        return jsonify({'success': False, 'message': f'Error: {e}'})

@app.route('/api/export')
def api_export():
    if not session.get('user_id'):
        return jsonify({'success': False, 'message': 'Not authorized'})
    
    format_type = request.args.get('format', 'csv')
    date_from = request.args.get('date_from')
    date_to = request.args.get('date_to')
    
    try:
        conn = sqlite3.connect(Config.DATABASE)
        cursor = conn.cursor()
        
        query = '''
            SELECT e.timestamp, e.event_type, e.description, u.username, e.door_id
            FROM events e 
            LEFT JOIN users u ON e.user_id = u.id
            ORDER BY e.timestamp DESC
        '''
        
        cursor.execute(query)
        events = cursor.fetchall()
        conn.close()
        
        if format_type == 'csv':
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(['Timestamp', 'Event Type', 'Description', 'User', 'Door ID'])
            writer.writerows(events)
            
            response = send_file(
                io.BytesIO(output.getvalue().encode()),
                mimetype='text/csv',
                as_attachment=True,
                download_name=f'door_events_{datetime.datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
            )
            
            monitor.log_event('EXPORT_CSV', 'Events exported to CSV', session.get('user_id'))
            return response
            
        else:  # PDF format would require additional implementation
            return jsonify({'success': False, 'message': 'PDF export not implemented yet'})
            
    except Exception as e:
        return jsonify({'success': False, 'message': f'Error: {e}'})

@app.route('/api/backup', methods=['POST'])
def api_backup():
    if session.get('role') != 'Admin':
        return jsonify({'success': False, 'message': 'Admin access required'})
    
    try:
        # Create backup directory if not exists
        os.makedirs(Config.BACKUP_DIR, exist_ok=True)
        
        timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_filename = f'backup_{timestamp}.zip'
        backup_path = os.path.join(Config.BACKUP_DIR, backup_filename)
        
        with zipfile.ZipFile(backup_path, 'w') as backup_zip:
            # Add database
            if os.path.exists(Config.DATABASE):
                backup_zip.write(Config.DATABASE)
            
            # Add state files
            if os.path.exists('system_state.json'):
                backup_zip.write('system_state.json')
            if os.path.exists('schedules.json'):
                backup_zip.write('schedules.json')
        
        monitor.log_event('BACKUP_CREATE', f'System backup created: {backup_filename}', session.get('user_id'))
        return jsonify({'success': True, 'message': 'Backup created successfully', 'file': backup_filename})
        
    except Exception as e:
        return jsonify({'success': False, 'message': f'Error: {e}'})

@app.route('/api/download_backup')
def api_download_backup():
    if session.get('role') != 'Admin':
        return jsonify({'success': False, 'message': 'Admin access required'})
    
    filename = request.args.get('file')
    if not filename:
        return jsonify({'success': False, 'message': 'No file specified'})
    
    backup_path = os.path.join(Config.BACKUP_DIR, filename)
    if os.path.exists(backup_path):
        return send_file(backup_path, as_attachment=True)
    else:
        return jsonify({'success': False, 'message': 'File not found'})

if __name__ == '__main__':
    print("Starting Door Monitoring System...")
    print("GPIO Available:" if GPIO_AVAILABLE else "Running in simulation mode")
    print("Audio Available:" if AUDIO_AVAILABLE else "Audio not available")
    print("Default login: admin / admin123")
    
    # Create necessary directories
    os.makedirs(Config.BACKUP_DIR, exist_ok=True)
    
    # Run Flask app
    app.run(host='0.0.0.0', port=5000, debug=False)
