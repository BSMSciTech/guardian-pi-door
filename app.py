
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
            # Fixed: Inverted door sensor logic
            self.door_sensor = DigitalInputDevice(Config.DOOR_SENSOR_PIN, pull_up=True)
            self.green_led = LED(Config.GREEN_LED_PIN)
            self.red_led = LED(Config.RED_LED_PIN)
            self.white_led = LED(Config.WHITE_LED_PIN)
            
            # Fixed: Corrected door sensor callbacks (inverted logic)
            self.door_sensor.when_deactivated = self.on_door_open  # When pin goes LOW (door opens)
            self.door_sensor.when_activated = self.on_door_close   # When pin goes HIGH (door closes)
            
            # Green LED always on when system running
            self.green_led.on()
            
            # Check initial door state
            self.state.door_open = not self.door_sensor.is_active  # Inverted logic
            
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
                severity TEXT DEFAULT 'INFO',
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
        self.log_event('DOOR_OPEN', 'Door was opened', severity='WARNING')
        
        if not self.is_access_time():
            self.log_event('ACCESS_VIOLATION', 'Door opened outside scheduled hours', severity='WARNING')
            if self.state.instant_alarm_mode:
                self.trigger_alarm()
            else:
                self.start_timer()
        else:
            self.log_event('ACCESS_ALLOWED', 'Door access within scheduled hours', severity='INFO')
            
        self.save_state()
        
    def on_door_close(self):
        """Handle door close event"""
        print("Door closed!")
        self.state.door_open = False
        self.log_event('DOOR_CLOSE', 'Door was closed', severity='INFO')
        
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
        
        self.log_event('TIMER_START', f'Timer started for {self.state.timer_duration} seconds', severity='WARNING')
        
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
        
        self.log_event('TIMER_STOP', 'Timer stopped - door closed in time', severity='INFO')
        
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
        
        self.log_event('ALARM_TRIGGER', 'Security alarm was triggered', severity='CRITICAL')
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
            
        self.log_event('ALARM_RESET', 'Alarm was manually reset', severity='INFO')
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
                
    def log_event(self, event_type: str, description: str, user_id: Optional[int] = None, severity: str = 'INFO'):
        """Enhanced event logging with severity levels"""
        try:
            conn = sqlite3.connect(Config.DATABASE)
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO events (event_type, description, user_id, severity)
                VALUES (?, ?, ?, ?)
            ''', (event_type, description, user_id, severity))
            conn.commit()
            conn.close()
            print(f"[{severity}] {event_type}: {description}")
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

# Enhanced Modern UI Template
MAIN_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Door Monitoring System - Advanced Security Dashboard</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --primary-color: #1e293b;
            --secondary-color: #334155;
            --accent-color: #3b82f6;
            --success-color: #10b981;
            --warning-color: #f59e0b;
            --danger-color: #ef4444;
            --info-color: #06b6d4;
            --light-bg: #f8fafc;
            --card-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06);
            --card-shadow-hover: 0 10px 15px -3px rgba(0, 0, 0, 0.1), 0 4px 6px -2px rgba(0, 0, 0, 0.05);
        }
        
        * { font-family: 'Inter', sans-serif; }
        
        body {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
        }
        
        .main-container {
            background: rgba(255, 255, 255, 0.95);
            backdrop-filter: blur(10px);
            border-radius: 20px;
            box-shadow: var(--card-shadow-hover);
            margin: 20px;
            min-height: calc(100vh - 40px);
        }
        
        .navbar-custom {
            background: linear-gradient(90deg, var(--primary-color) 0%, var(--secondary-color) 100%);
            border-radius: 20px 20px 0 0;
            padding: 1rem 2rem;
        }
        
        .navbar-brand {
            font-weight: 700;
            font-size: 1.5rem;
            color: white !important;
        }
        
        .status-indicator {
            width: 24px;
            height: 24px;
            border-radius: 50%;
            display: inline-block;
            margin-right: 12px;
            box-shadow: 0 0 10px rgba(0,0,0,0.3);
            border: 2px solid white;
        }
        
        .status-on { 
            background: linear-gradient(45deg, var(--success-color), #34d399);
            animation: pulse-green 2s infinite;
        }
        
        .status-off { 
            background: linear-gradient(45deg, #6b7280, #9ca3af);
        }
        
        .status-blink { 
            background: linear-gradient(45deg, var(--danger-color), #f87171);
            animation: blink-red 1s infinite;
        }
        
        @keyframes pulse-green {
            0%, 100% { transform: scale(1); box-shadow: 0 0 10px rgba(16, 185, 129, 0.5); }
            50% { transform: scale(1.1); box-shadow: 0 0 20px rgba(16, 185, 129, 0.8); }
        }
        
        @keyframes blink-red {
            0%, 50% { opacity: 1; transform: scale(1); }
            51%, 100% { opacity: 0.3; transform: scale(0.95); }
        }
        
        .alarm-active {
            background: linear-gradient(135deg, #fef3c7, #fde68a) !important;
            border: 3px solid var(--warning-color) !important;
            animation: alarm-pulse 1.5s infinite;
        }
        
        @keyframes alarm-pulse {
            0%, 100% { box-shadow: 0 0 0 0 rgba(245, 158, 11, 0.7); }
            50% { box-shadow: 0 0 0 20px rgba(245, 158, 11, 0); }
        }
        
        .card-modern {
            border: none;
            border-radius: 16px;
            box-shadow: var(--card-shadow);
            transition: all 0.3s ease;
            overflow: hidden;
        }
        
        .card-modern:hover {
            box-shadow: var(--card-shadow-hover);
            transform: translateY(-2px);
        }
        
        .card-header-modern {
            background: linear-gradient(90deg, var(--light-bg), #ffffff);
            border-bottom: 1px solid #e5e7eb;
            padding: 1.5rem;
            font-weight: 600;
        }
        
        .system-status {
            font-size: 1.2rem;
            font-weight: 500;
        }
        
        .nav-tabs-modern {
            border: none;
            background: var(--light-bg);
            border-radius: 12px;
            padding: 8px;
            margin-bottom: 2rem;
        }
        
        .nav-tabs-modern .nav-link {
            border: none;
            border-radius: 8px;
            color: var(--secondary-color);
            font-weight: 500;
            padding: 12px 24px;
            margin: 0 4px;
            transition: all 0.3s ease;
        }
        
        .nav-tabs-modern .nav-link.active {
            background: white;
            color: var(--accent-color);
            box-shadow: var(--card-shadow);
        }
        
        .nav-tabs-modern .nav-link:hover {
            background: rgba(255, 255, 255, 0.7);
            color: var(--accent-color);
        }
        
        .btn-modern {
            border-radius: 10px;
            padding: 10px 20px;
            font-weight: 500;
            border: none;
            transition: all 0.3s ease;
        }
        
        .btn-modern:hover {
            transform: translateY(-1px);
            box-shadow: var(--card-shadow);
        }
        
        .progress-modern {
            height: 8px;
            border-radius: 10px;
            background: #e5e7eb;
        }
        
        .progress-bar-modern {
            border-radius: 10px;
            background: linear-gradient(90deg, var(--warning-color), #fbbf24);
        }
        
        .table-modern {
            border-radius: 12px;
            overflow: hidden;
            box-shadow: var(--card-shadow);
        }
        
        .table-modern thead {
            background: linear-gradient(90deg, var(--primary-color), var(--secondary-color));
            color: white;
        }
        
        .table-modern tbody tr:hover {
            background: var(--light-bg);
            transform: scale(1.001);
        }
        
        .badge-modern {
            padding: 6px 12px;
            border-radius: 20px;
            font-weight: 500;
        }
        
        .form-control-modern {
            border-radius: 10px;
            border: 2px solid #e5e7eb;
            padding: 12px 16px;
            transition: all 0.3s ease;
        }
        
        .form-control-modern:focus {
            border-color: var(--accent-color);
            box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.1);
        }
        
        .alert-modern {
            border: none;
            border-radius: 12px;
            padding: 1rem 1.5rem;
        }
        
        .dashboard-stats {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 1rem;
            margin-bottom: 2rem;
        }
        
        .stat-card {
            background: white;
            border-radius: 16px;
            padding: 1.5rem;
            box-shadow: var(--card-shadow);
            text-align: center;
            transition: all 0.3s ease;
        }
        
        .stat-card:hover {
            transform: translateY(-4px);
            box-shadow: var(--card-shadow-hover);
        }
        
        .stat-number {
            font-size: 2rem;
            font-weight: 700;
            margin-bottom: 0.5rem;
        }
        
        .stat-label {
            color: var(--secondary-color);
            font-weight: 500;
        }
    </style>
</head>
<body>
    <div class="main-container">
        <nav class="navbar navbar-custom">
            <div class="d-flex align-items-center">
                <i class="fas fa-shield-alt me-3" style="font-size: 1.5rem; color: var(--accent-color);"></i>
                <span class="navbar-brand mb-0">Advanced Door Security System</span>
            </div>
            <div class="d-flex align-items-center text-white">
                {% if session.get('user_id') %}
                    <div class="me-4">
                        <i class="fas fa-user-circle me-2"></i>
                        <span class="fw-semibold">{{ session.get('username', 'User') }}</span>
                        <span class="badge badge-modern ms-2" style="background: var(--accent-color);">{{ session.get('role', 'User') }}</span>
                    </div>
                    <a href="/logout" class="btn btn-outline-light btn-sm btn-modern">
                        <i class="fas fa-sign-out-alt me-1"></i> Logout
                    </a>
                {% else %}
                    <a href="/login" class="btn btn-outline-light btn-modern">
                        <i class="fas fa-sign-in-alt me-1"></i> Login
                    </a>
                {% endif %}
            </div>
        </nav>

        <div class="container-fluid p-4">
            {% with messages = get_flashed_messages() %}
                {% if messages %}
                    {% for message in messages %}
                        <div class="alert alert-info alert-modern alert-dismissible fade show" role="alert">
                            <i class="fas fa-info-circle me-2"></i>{{ message }}
                            <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
                        </div>
                    {% endfor %}
                {% endif %}
            {% endwith %}

            {% if not session.get('user_id') %}
                <div class="row justify-content-center">
                    <div class="col-md-6">
                        <div class="card card-modern">
                            <div class="card-header-modern text-center">
                                <h4><i class="fas fa-lock me-2"></i>Authentication Required</h4>
                            </div>
                            <div class="card-body text-center">
                                <p class="mb-4">Please authenticate to access the Advanced Door Security System.</p>
                                <a href="/login" class="btn btn-primary btn-modern">
                                    <i class="fas fa-shield-alt me-2"></i> Secure Login
                                </a>
                            </div>
                        </div>
                    </div>
                </div>
            {% else %}
                <!-- Enhanced System Status Dashboard -->
                <div class="dashboard-stats">
                    <div class="stat-card">
                        <div class="stat-number" style="color: {% if status.door_open %}var(--danger-color){% else %}var(--success-color){% endif %};">
                            <i class="fas {% if status.door_open %}fa-door-open{% else %}fa-door-closed{% endif %}"></i>
                        </div>
                        <div class="stat-label">
                            Door Status: {% if status.door_open %}<strong style="color: var(--danger-color);">OPEN</strong>{% else %}<strong style="color: var(--success-color);">SECURED</strong>{% endif %}
                        </div>
                    </div>
                    
                    <div class="stat-card">
                        <div class="stat-number" style="color: var(--success-color);">
                            <i class="fas fa-power-off"></i>
                        </div>
                        <div class="stat-label">System: <strong style="color: var(--success-color);">OPERATIONAL</strong></div>
                    </div>
                    
                    <div class="stat-card">
                        <div class="stat-number" style="color: {% if status.timer_active %}var(--warning-color){% else %}var(--info-color){% endif %};">
                            <i class="fas {% if status.timer_active %}fa-hourglass-half{% else %}fa-check-circle{% endif %}"></i>
                        </div>
                        <div class="stat-label">Timer: {% if status.timer_active %}<strong style="color: var(--warning-color);">ACTIVE</strong>{% else %}<strong>STANDBY</strong>{% endif %}</div>
                    </div>
                    
                    <div class="stat-card">
                        <div class="stat-number" style="color: {% if status.alarm_active %}var(--danger-color){% else %}var(--success-color){% endif %};">
                            <i class="fas {% if status.alarm_active %}fa-exclamation-triangle{% else %}fa-shield-check{% endif %}"></i>
                        </div>
                        <div class="stat-label">Security: {% if status.alarm_active %}<strong style="color: var(--danger-color);">BREACH</strong>{% else %}<strong style="color: var(--success-color);">SECURE</strong>{% endif %}</div>
                    </div>
                </div>

                <!-- Enhanced Status Card -->
                <div class="row mb-4">
                    <div class="col-12">
                        <div class="card card-modern {% if status.alarm_active %}alarm-active{% endif %}">
                            <div class="card-header-modern">
                                <h5><i class="fas fa-tachometer-alt me-2"></i>Live System Monitor</h5>
                            </div>
                            <div class="card-body system-status">
                                <div class="row">
                                    <div class="col-md-3 mb-3">
                                        <span class="status-indicator {% if status.door_open %}status-blink{% else %}status-off{% endif %}"></span>
                                        <strong>Door Access Portal</strong><br>
                                        <small class="text-muted">Physical barrier status</small>
                                    </div>
                                    <div class="col-md-3 mb-3">
                                        <span class="status-indicator status-on"></span>
                                        <strong>Core System</strong><br>
                                        <small class="text-muted">Monitoring active</small>
                                    </div>
                                    <div class="col-md-3 mb-3">
                                        <span class="status-indicator {% if status.red_led %}status-blink{% else %}status-off{% endif %}"></span>
                                        <strong>Security Timer</strong><br>
                                        <small class="text-muted">Countdown system</small>
                                    </div>
                                    <div class="col-md-3 mb-3">
                                        <span class="status-indicator {% if status.white_led %}status-on{% else %}status-off{% endif %}"></span>
                                        <strong>Alert System</strong><br>
                                        <small class="text-muted">Breach notification</small>
                                    </div>
                                </div>
                                
                                {% if status.timer_active and status.remaining_time %}
                                    <div class="mt-3">
                                        <div class="d-flex justify-content-between mb-2">
                                            <span><i class="fas fa-stopwatch me-1"></i>Security Timer</span>
                                            <span class="fw-bold">{{ "%.1f"|format(status.remaining_time) }}s remaining</span>
                                        </div>
                                        <div class="progress progress-modern">
                                            <div class="progress-bar progress-bar-modern progress-bar-striped progress-bar-animated" 
                                                 style="width: {{ (status.remaining_time / status.timer_duration * 100) }}%">
                                            </div>
                                        </div>
                                    </div>
                                {% endif %}
                                
                                {% if status.alarm_active %}
                                    <div class="mt-4 text-center">
                                        <div class="alert alert-danger alert-modern mb-3">
                                            <i class="fas fa-exclamation-triangle fa-2x mb-2"></i><br>
                                            <strong>SECURITY BREACH DETECTED</strong><br>
                                            Immediate attention required
                                        </div>
                                        <button class="btn btn-danger btn-modern btn-lg" onclick="resetAlarm()">
                                            <i class="fas fa-shield-alt me-2"></i> Reset Security System
                                        </button>
                                    </div>
                                {% endif %}
                            </div>
                        </div>
                    </div>
                </div>

                <!-- Enhanced Navigation Tabs -->
                <ul class="nav nav-tabs nav-tabs-modern" id="systemTabs" role="tablist">
                    <li class="nav-item" role="presentation">
                        <button class="nav-link active" id="events-tab" data-bs-toggle="tab" data-bs-target="#events" type="button">
                            <i class="fas fa-list-alt me-2"></i> Security Events
                        </button>
                    </li>
                    <li class="nav-item" role="presentation">
                        <button class="nav-link" id="settings-tab" data-bs-toggle="tab" data-bs-target="#settings" type="button">
                            <i class="fas fa-cogs me-2"></i> System Configuration
                        </button>
                    </li>
                    <li class="nav-item" role="presentation">
                        <button class="nav-link" id="schedules-tab" data-bs-toggle="tab" data-bs-target="#schedules" type="button">
                            <i class="fas fa-calendar-alt me-2"></i> Access Schedules
                        </button>
                    </li>
                    {% if session.get('role') == 'Admin' %}
                    <li class="nav-item" role="presentation">
                        <button class="nav-link" id="users-tab" data-bs-toggle="tab" data-bs-target="#users" type="button">
                            <i class="fas fa-users-cog me-2"></i> User Management
                        </button>
                    </li>
                    {% endif %}
                    <li class="nav-item" role="presentation">
                        <button class="nav-link" id="reports-tab" data-bs-toggle="tab" data-bs-target="#reports" type="button">
                            <i class="fas fa-chart-bar me-2"></i> Analytics & Reports
                        </button>
                    </li>
                </ul>

                <!-- Enhanced Tab Content -->
                <div class="tab-content" id="systemTabsContent">
                    <!-- Events Tab -->
                    <div class="tab-pane fade show active" id="events" role="tabpanel">
                        <div class="card card-modern">
                            <div class="card-header-modern d-flex justify-content-between align-items-center">
                                <h5><i class="fas fa-shield-alt me-2"></i>Security Event Log</h5>
                                <button class="btn btn-outline-primary btn-modern btn-sm" onclick="refreshEvents()">
                                    <i class="fas fa-sync-alt me-1"></i> Refresh
                                </button>
                            </div>
                            <div class="card-body">
                                <div class="table-responsive">
                                    <table class="table table-modern" id="eventsTable">
                                        <thead>
                                            <tr>
                                                <th><i class="fas fa-clock me-1"></i>Timestamp</th>
                                                <th><i class="fas fa-tag me-1"></i>Event Type</th>
                                                <th><i class="fas fa-info-circle me-1"></i>Description</th>
                                                <th><i class="fas fa-user me-1"></i>User</th>
                                                <th><i class="fas fa-exclamation-circle me-1"></i>Severity</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {% for event in events %}
                                            <tr>
                                                <td class="fw-semibold">{{ event[1] }}</td>
                                                <td>
                                                    {% if event[2] == 'DOOR_OPEN' %}
                                                        <span class="badge badge-modern" style="background: var(--warning-color);"><i class="fas fa-door-open me-1"></i>{{ event[2] }}</span>
                                                    {% elif event[2] == 'DOOR_CLOSE' %}
                                                        <span class="badge badge-modern" style="background: var(--success-color);"><i class="fas fa-door-closed me-1"></i>{{ event[2] }}</span>
                                                    {% elif event[2] == 'ALARM_TRIGGER' %}
                                                        <span class="badge badge-modern" style="background: var(--danger-color);"><i class="fas fa-exclamation-triangle me-1"></i>{{ event[2] }}</span>
                                                    {% elif event[2] == 'LOGIN' %}
                                                        <span class="badge badge-modern" style="background: var(--info-color);"><i class="fas fa-sign-in-alt me-1"></i>{{ event[2] }}</span>
                                                    {% else %}
                                                        <span class="badge badge-modern" style="background: var(--secondary-color);"><i class="fas fa-cog me-1"></i>{{ event[2] }}</span>
                                                    {% endif %}
                                                </td>
                                                <td>{{ event[3] }}</td>
                                                <td>
                                                    {% if event[7] %}
                                                        <i class="fas fa-user me-1"></i>{{ event[7] }}
                                                    {% else %}
                                                        <i class="fas fa-microchip me-1"></i><em>System</em>
                                                    {% endif %}
                                                </td>
                                                <td>
                                                    {% set severity = event[6] or 'INFO' %}
                                                    {% if severity == 'CRITICAL' %}
                                                        <span class="badge badge-modern" style="background: var(--danger-color);"><i class="fas fa-exclamation-triangle me-1"></i>{{ severity }}</span>
                                                    {% elif severity == 'WARNING' %}
                                                        <span class="badge badge-modern" style="background: var(--warning-color);"><i class="fas fa-exclamation me-1"></i>{{ severity }}</span>
                                                    {% else %}
                                                        <span class="badge badge-modern" style="background: var(--info-color);"><i class="fas fa-info me-1"></i>{{ severity }}</span>
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

                    <!-- Settings Tab -->
                    <div class="tab-pane fade" id="settings" role="tabpanel">
                        <div class="row">
                            <div class="col-md-6">
                                <div class="card card-modern">
                                    <div class="card-header-modern">
                                        <h5><i class="fas fa-stopwatch me-2"></i>Security Timer Configuration</h5>
                                    </div>
                                    <div class="card-body">
                                        <form onsubmit="updateSettings(event)">
                                            <div class="mb-4">
                                                <label for="timerDuration" class="form-label fw-semibold">
                                                    <i class="fas fa-clock me-1"></i>Timer Duration (seconds)
                                                </label>
                                                <input type="number" class="form-control form-control-modern" id="timerDuration" 
                                                       value="{{ status.timer_duration }}" min="1" max="86400">
                                                <div class="form-text">Set countdown time before alarm triggers</div>
                                            </div>
                                            <div class="mb-4">
                                                <div class="form-check">
                                                    <input class="form-check-input" type="checkbox" id="instantAlarm" 
                                                           {% if status.instant_alarm_mode %}checked{% endif %}>
                                                    <label class="form-check-label fw-semibold" for="instantAlarm">
                                                        <i class="fas fa-bolt me-1"></i>Instant Alarm Mode
                                                    </label>
                                                    <div class="form-text">Trigger alarm immediately outside scheduled hours</div>
                                                </div>
                                            </div>
                                            <button type="submit" class="btn btn-primary btn-modern">
                                                <i class="fas fa-save me-2"></i> Save Configuration
                                            </button>
                                        </form>
                                    </div>
                                </div>
                            </div>
                            <div class="col-md-6">
                                <div class="card card-modern">
                                    <div class="card-header-modern">
                                        <h5><i class="fas fa-tools me-2"></i>System Diagnostics</h5>
                                    </div>
                                    <div class="card-body">
                                        <div class="d-grid gap-3">
                                            <button class="btn btn-warning btn-modern" onclick="testAlarm()">
                                                <i class="fas fa-volume-up me-2"></i> Test Audio Alert
                                            </button>
                                            <button class="btn btn-info btn-modern" onclick="testLEDs()">
                                                <i class="fas fa-lightbulb me-2"></i> Test LED Indicators
                                            </button>
                                            {% if status.alarm_active %}
                                            <button class="btn btn-danger btn-modern" onclick="resetAlarm()">
                                                <i class="fas fa-shield-alt me-2"></i> Emergency Reset
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
                        <div class="card card-modern">
                            <div class="card-header-modern">
                                <h5><i class="fas fa-calendar-check me-2"></i>Access Time Management</h5>
                            </div>
                            <div class="card-body">
                                <div class="row">
                                    <div class="col-md-6">
                                        <h6 class="fw-bold mb-3"><i class="fas fa-briefcase me-2"></i>Weekday Schedule</h6>
                                        <div class="mb-4">
                                            <label class="form-label fw-semibold">Morning Access</label>
                                            <div class="row">
                                                <div class="col">
                                                    <input type="time" class="form-control form-control-modern" id="weekday_morning_start" 
                                                           value="{{ schedules.weekday.morning.start }}">
                                                </div>
                                                <div class="col">
                                                    <input type="time" class="form-control form-control-modern" id="weekday_morning_end" 
                                                           value="{{ schedules.weekday.morning.end }}">
                                                </div>
                                            </div>
                                        </div>
                                        <div class="mb-4">
                                            <label class="form-label fw-semibold">Afternoon Access</label>
                                            <div class="row">
                                                <div class="col">
                                                    <input type="time" class="form-control form-control-modern" id="weekday_afternoon_start" 
                                                           value="{{ schedules.weekday.afternoon.start }}">
                                                </div>
                                                <div class="col">
                                                    <input type="time" class="form-control form-control-modern" id="weekday_afternoon_end" 
                                                           value="{{ schedules.weekday.afternoon.end }}">
                                                </div>
                                            </div>
                                        </div>
                                        <div class="mb-4">
                                            <label class="form-label fw-semibold">Evening Access</label>
                                            <div class="row">
                                                <div class="col">
                                                    <input type="time" class="form-control form-control-modern" id="weekday_evening_start" 
                                                           value="{{ schedules.weekday.evening.start }}">
                                                </div>
                                                <div class="col">
                                                    <input type="time" class="form-control form-control-modern" id="weekday_evening_end" 
                                                           value="{{ schedules.weekday.evening.end }}">
                                                </div>
                                            </div>
                                        </div>
                                    </div>
                                    <div class="col-md-6">
                                        <h6 class="fw-bold mb-3"><i class="fas fa-home me-2"></i>Weekend Schedule</h6>
                                        <div class="mb-4">
                                            <label class="form-label fw-semibold">Morning Access</label>
                                            <div class="row">
                                                <div class="col">
                                                    <input type="time" class="form-control form-control-modern" id="weekend_morning_start" 
                                                           value="{{ schedules.weekend.morning.start }}">
                                                </div>
                                                <div class="col">
                                                    <input type="time" class="form-control form-control-modern" id="weekend_morning_end" 
                                                           value="{{ schedules.weekend.morning.end }}">
                                                </div>
                                            </div>
                                        </div>
                                        <div class="mb-4">
                                            <label class="form-label fw-semibold">Afternoon Access</label>
                                            <div class="row">
                                                <div class="col">
                                                    <input type="time" class="form-control form-control-modern" id="weekend_afternoon_start" 
                                                           value="{{ schedules.weekend.afternoon.start }}">
                                                </div>
                                                <div class="col">
                                                    <input type="time" class="form-control form-control-modern" id="weekend_afternoon_end" 
                                                           value="{{ schedules.weekend.afternoon.end }}">
                                                </div>
                                            </div>
                                        </div>
                                        <div class="mb-4">
                                            <label class="form-label fw-semibold">Evening Access</label>
                                            <div class="row">
                                                <div class="col">
                                                    <input type="time" class="form-control form-control-modern" id="weekend_evening_start" 
                                                           value="{{ schedules.weekend.evening.start }}">
                                                </div>
                                                <div class="col">
                                                    <input type="time" class="form-control form-control-modern" id="weekend_evening_end" 
                                                           value="{{ schedules.weekend.evening.end }}">
                                                </div>
                                            </div>
                                        </div>
                                    </div>
                                </div>
                                <div class="text-center">
                                    <button class="btn btn-primary btn-modern btn-lg" onclick="saveSchedules()">
                                        <i class="fas fa-calendar-check me-2"></i> Update Access Schedule
                                    </button>
                                </div>
                            </div>
                        </div>
                    </div>

                    <!-- Users Tab (Admin only) -->
                    {% if session.get('role') == 'Admin' %}
                    <div class="tab-pane fade" id="users" role="tabpanel">
                        <div class="card card-modern">
                            <div class="card-header-modern d-flex justify-content-between align-items-center">
                                <h5><i class="fas fa-users-cog me-2"></i>User Administration</h5>
                                <button class="btn btn-primary btn-modern" data-bs-toggle="modal" data-bs-target="#addUserModal">
                                    <i class="fas fa-user-plus me-2"></i> Add New User
                                </button>
                            </div>
                            <div class="card-body">
                                <div class="table-responsive">
                                    <table class="table table-modern">
                                        <thead>
                                            <tr>
                                                <th><i class="fas fa-user me-1"></i>Username</th>
                                                <th><i class="fas fa-envelope me-1"></i>Email</th>
                                                <th><i class="fas fa-building me-1"></i>Department</th>
                                                <th><i class="fas fa-shield-alt me-1"></i>Role</th>
                                                <th><i class="fas fa-cogs me-1"></i>Actions</th>
                                            </tr>
                                        </thead>
                                        <tbody id="usersTableBody">
                                            {% for user in users %}
                                            <tr>
                                                <td class="fw-semibold">
                                                    <i class="fas fa-user-circle me-2"></i>{{ user[1] }}
                                                </td>
                                                <td>{{ user[3] or '-' }}</td>
                                                <td>{{ user[4] or '-' }}</td>
                                                <td>
                                                    {% if user[7] == 'Admin' %}
                                                        <span class="badge badge-modern" style="background: var(--danger-color);"><i class="fas fa-crown me-1"></i>{{ user[7] }}</span>
                                                    {% elif user[7] == 'Manager' %}
                                                        <span class="badge badge-modern" style="background: var(--warning-color);"><i class="fas fa-star me-1"></i>{{ user[7] }}</span>
                                                    {% else %}
                                                        <span class="badge badge-modern" style="background: var(--info-color);"><i class="fas fa-user me-1"></i>{{ user[7] }}</span>
                                                    {% endif %}
                                                </td>
                                                <td>
                                                    {% if user[1] != 'admin' %}
                                                    <button class="btn btn-outline-danger btn-sm btn-modern" onclick="deleteUser({{ user[0] }})">
                                                        <i class="fas fa-trash-alt"></i>
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
                        <div class="card card-modern">
                            <div class="card-header-modern">
                                <h5><i class="fas fa-chart-line me-2"></i>Advanced Analytics & Export</h5>
                            </div>
                            <div class="card-body">
                                <div class="row">
                                    <div class="col-md-6">
                                        <h6 class="fw-bold mb-3"><i class="fas fa-download me-2"></i>Export Security Events</h6>
                                        <form onsubmit="exportData(event)">
                                            <div class="mb-3">
                                                <label for="exportFormat" class="form-label fw-semibold">Export Format</label>
                                                <select class="form-select form-control-modern" id="exportFormat">
                                                    <option value="csv"><i class="fas fa-file-csv"></i> CSV Spreadsheet</option>
                                                    <option value="pdf"><i class="fas fa-file-pdf"></i> PDF Report</option>
                                                </select>
                                            </div>
                                            <div class="mb-3">
                                                <label for="dateFrom" class="form-label fw-semibold">From Date</label>
                                                <input type="date" class="form-control form-control-modern" id="dateFrom">
                                            </div>
                                            <div class="mb-3">
                                                <label for="dateTo" class="form-label fw-semibold">To Date</label>
                                                <input type="date" class="form-control form-control-modern" id="dateTo">
                                            </div>
                                            <button type="submit" class="btn btn-success btn-modern">
                                                <i class="fas fa-download me-2"></i> Generate Report
                                            </button>
                                        </form>
                                    </div>
                                    <div class="col-md-6">
                                        <h6 class="fw-bold mb-3"><i class="fas fa-database me-2"></i>System Backup</h6>
                                        <div class="alert alert-info alert-modern">
                                            <i class="fas fa-info-circle me-2"></i>
                                            Create comprehensive backup including all security data, user accounts, and system logs.
                                        </div>
                                        <button class="btn btn-warning btn-modern btn-lg" onclick="createBackup()">
                                            <i class="fas fa-shield-alt me-2"></i> Create Secure Backup
                                        </button>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            {% endif %}
        </div>
    </div>

    <!-- Enhanced Add User Modal -->
    <div class="modal fade" id="addUserModal" tabindex="-1">
        <div class="modal-dialog modal-lg">
            <div class="modal-content" style="border-radius: 16px; border: none;">
                <div class="modal-header" style="background: linear-gradient(90deg, var(--primary-color), var(--secondary-color)); color: white; border-radius: 16px 16px 0 0;">
                    <h5 class="modal-title"><i class="fas fa-user-plus me-2"></i>Add New User Account</h5>
                    <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button>
                </div>
                <form onsubmit="addUser(event)">
                    <div class="modal-body p-4">
                        <div class="row">
                            <div class="col-md-6">
                                <div class="mb-3">
                                    <label for="newUsername" class="form-label fw-semibold">
                                        <i class="fas fa-user me-1"></i>Username
                                    </label>
                                    <input type="text" class="form-control form-control-modern" id="newUsername" required>
                                </div>
                                <div class="mb-3">
                                    <label for="newPassword" class="form-label fw-semibold">
                                        <i class="fas fa-lock me-1"></i>Password
                                    </label>
                                    <input type="password" class="form-control form-control-modern" id="newPassword" required>
                                </div>
                                <div class="mb-3">
                                    <label for="newEmail" class="form-label fw-semibold">
                                        <i class="fas fa-envelope me-1"></i>Email Address
                                    </label>
                                    <input type="email" class="form-control form-control-modern" id="newEmail">
                                </div>
                            </div>
                            <div class="col-md-6">
                                <div class="mb-3">
                                    <label for="newDepartment" class="form-label fw-semibold">
                                        <i class="fas fa-building me-1"></i>Department
                                    </label>
                                    <input type="text" class="form-control form-control-modern" id="newDepartment">
                                </div>
                                <div class="mb-3">
                                    <label for="newRole" class="form-label fw-semibold">
                                        <i class="fas fa-shield-alt me-1"></i>Security Role
                                    </label>
                                    <select class="form-select form-control-modern" id="newRole">
                                        <option value="User">Standard User</option>
                                        <option value="Supervisor">Supervisor</option>
                                        <option value="Manager">Manager</option>
                                        <option value="Admin">Administrator</option>
                                    </select>
                                </div>
                            </div>
                        </div>
                    </div>
                    <div class="modal-footer p-4">
                        <button type="button" class="btn btn-secondary btn-modern" data-bs-dismiss="modal">
                            <i class="fas fa-times me-1"></i> Cancel
                        </button>
                        <button type="submit" class="btn btn-primary btn-modern">
                            <i class="fas fa-user-plus me-1"></i> Create User Account
                        </button>
                    </div>
                </form>
            </div>
        </div>
    </div>

    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
    <script>
        // Fixed: Auto-refresh with proper tab state management
        let currentTab = 'events';
        
        // Track active tab
        document.querySelectorAll('[data-bs-toggle="tab"]').forEach(tab => {
            tab.addEventListener('shown.bs.tab', function (e) {
                currentTab = e.target.getAttribute('data-bs-target').replace('#', '');
                console.log('Tab switched to:', currentTab);
            });
        });
        
        // Auto-refresh with tab preservation
        setInterval(function() {
            if (document.visibilityState === 'visible') {
                const urlParams = new URLSearchParams(window.location.search);
                urlParams.set('tab', currentTab);
                const newUrl = window.location.pathname + '?' + urlParams.toString();
                window.location.href = newUrl;
            }
        }, 3000);
        
        // Restore tab on page load
        window.addEventListener('load', function() {
            const urlParams = new URLSearchParams(window.location.search);
            const activeTab = urlParams.get('tab') || 'events';
            
            // Hide all tab content
            document.querySelectorAll('.tab-pane').forEach(pane => {
                pane.classList.remove('show', 'active');
            });
            
            // Remove active from all tab buttons
            document.querySelectorAll('.nav-link').forEach(link => {
                link.classList.remove('active');
            });
            
            // Show selected tab
            const targetPane = document.getElementById(activeTab);
            const targetButton = document.getElementById(activeTab + '-tab');
            
            if (targetPane && targetButton) {
                targetPane.classList.add('show', 'active');
                targetButton.classList.add('active');
                currentTab = activeTab;
            }
        });

        function resetAlarm() {
            fetch('/api/reset_alarm', { method: 'POST' })
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        showNotification('Security system reset successfully', 'success');
                        setTimeout(() => location.reload(), 1000);
                    }
                });
        }

        function testAlarm() {
            fetch('/api/test_alarm', { method: 'POST' })
                .then(response => response.json())
                .then(data => {
                    showNotification(data.message, 'info');
                });
        }

        function testLEDs() {
            fetch('/api/test_leds', { method: 'POST' })
                .then(response => response.json())
                .then(data => {
                    showNotification(data.message, 'info');
                });
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
                showNotification(data.message, data.success ? 'success' : 'danger');
                if (data.success) setTimeout(() => location.reload(), 1000);
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
            .then(data => {
                showNotification(data.message, data.success ? 'success' : 'danger');
            });
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
                showNotification(data.message, data.success ? 'success' : 'danger');
                if (data.success) {
                    document.getElementById('addUserModal').querySelector('.btn-close').click();
                    setTimeout(() => location.reload(), 1000);
                }
            });
        }

        function deleteUser(userId) {
            if (confirm('Are you sure you want to permanently delete this user account?')) {
                fetch(`/api/delete_user/${userId}`, { method: 'DELETE' })
                    .then(response => response.json())
                    .then(data => {
                        showNotification(data.message, data.success ? 'success' : 'danger');
                        if (data.success) setTimeout(() => location.reload(), 1000);
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
            
            showNotification('Report generation started...', 'info');
        }

        function createBackup() {
            fetch('/api/backup', { method: 'POST' })
                .then(response => response.json())
                .then(data => {
                    showNotification(data.message, data.success ? 'success' : 'danger');
                    if (data.success && data.file) {
                        setTimeout(() => {
                            window.open(`/api/download_backup?file=${data.file}`);
                        }, 1000);
                    }
                });
        }

        function refreshEvents() {
            showNotification('Refreshing event log...', 'info');
            setTimeout(() => location.reload(), 500);
        }
        
        // Enhanced notification system
        function showNotification(message, type) {
            const alertDiv = document.createElement('div');
            alertDiv.className = `alert alert-${type} alert-modern alert-dismissible fade show position-fixed`;
            alertDiv.style.cssText = 'top: 20px; right: 20px; z-index: 9999; min-width: 300px;';
            alertDiv.innerHTML = `
                <i class="fas fa-info-circle me-2"></i>${message}
                <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
            `;
            document.body.appendChild(alertDiv);
            
            setTimeout(() => {
                if (alertDiv.parentNode) {
                    alertDiv.remove();
                }
            }, 5000);
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
    <title>Secure Access - Door Monitor</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --primary-color: #1e293b;
            --accent-color: #3b82f6;
            --card-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.1), 0 4px 6px -2px rgba(0, 0, 0, 0.05);
        }
        
        * { font-family: 'Inter', sans-serif; }
        
        body {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        
        .login-card {
            background: rgba(255, 255, 255, 0.95);
            backdrop-filter: blur(10px);
            border-radius: 20px;
            box-shadow: var(--card-shadow);
            border: none;
            overflow: hidden;
        }
        
        .login-header {
            background: linear-gradient(90deg, var(--primary-color), #334155);
            color: white;
            text-align: center;
            padding: 2rem;
        }
        
        .form-control-modern {
            border-radius: 12px;
            border: 2px solid #e5e7eb;
            padding: 14px 18px;
            font-size: 1rem;
            transition: all 0.3s ease;
        }
        
        .form-control-modern:focus {
            border-color: var(--accent-color);
            box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.1);
        }
        
        .btn-modern {
            border-radius: 12px;
            padding: 14px 24px;
            font-weight: 600;
            border: none;
            transition: all 0.3s ease;
        }
        
        .btn-modern:hover {
            transform: translateY(-1px);
            box-shadow: var(--card-shadow);
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="row justify-content-center">
            <div class="col-md-6 col-lg-4">
                <div class="card login-card">
                    <div class="login-header">
                        <i class="fas fa-shield-alt fa-3x mb-3" style="color: var(--accent-color);"></i>
                        <h3 class="fw-bold mb-2">Advanced Door Security</h3>
                        <p class="mb-0 opacity-75">Secure Authentication Required</p>
                    </div>
                    <div class="card-body p-4">
                        {% with messages = get_flashed_messages() %}
                            {% if messages %}
                                {% for message in messages %}
                                    <div class="alert alert-danger" role="alert" style="border-radius: 12px;">
                                        <i class="fas fa-exclamation-triangle me-2"></i>{{ message }}
                                    </div>
                                {% endfor %}
                            {% endif %}
                        {% endwith %}
                        
                        <form method="POST">
                            <div class="mb-4">
                                <label for="username" class="form-label fw-semibold">
                                    <i class="fas fa-user me-2"></i>Username
                                </label>
                                <input type="text" class="form-control form-control-modern" id="username" name="username" required>
                            </div>
                            <div class="mb-4">
                                <label for="password" class="form-label fw-semibold">
                                    <i class="fas fa-lock me-2"></i>Password
                                </label>
                                <input type="password" class="form-control form-control-modern" id="password" name="password" required>
                            </div>
                            <div class="d-grid">
                                <button type="submit" class="btn btn-primary btn-modern">
                                    <i class="fas fa-shield-alt me-2"></i> Secure Access
                                </button>
                            </div>
                        </form>
                    </div>
                    <div class="card-footer text-center text-muted p-3" style="background: #f8fafc;">
                        <small><i class="fas fa-info-circle me-1"></i>Default: admin / admin123</small>
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
    events = monitor.get_events(limit=15)
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
                
                monitor.log_event('LOGIN', f'User {username} logged in successfully', user[0], 'INFO')
                flash(f'Welcome to the Advanced Security System, {username}!')
                return redirect(url_for('index'))
            else:
                monitor.log_event('LOGIN_FAILED', f'Failed login attempt for username: {username}', severity='WARNING')
                flash('Invalid credentials. Access denied.')
                
        except Exception as e:
            flash(f'Authentication error: {e}')
    
    return render_template_string(LOGIN_TEMPLATE)

@app.route('/logout')
def logout():
    if session.get('user_id'):
        monitor.log_event('LOGOUT', f'User {session.get("username")} logged out', session.get('user_id'), 'INFO')
    
    session.clear()
    flash('You have been securely logged out')
    return redirect(url_for('login'))

# Enhanced API Routes with comprehensive logging
@app.route('/api/status')
def api_status():
    return jsonify(monitor.get_system_status())

@app.route('/api/reset_alarm', methods=['POST'])
def api_reset_alarm():
    if not session.get('user_id'):
        return jsonify({'success': False, 'message': 'Authentication required'})
    
    monitor.reset_alarm()
    monitor.log_event('ALARM_RESET', f'Alarm manually reset by {session.get("username")}', session.get('user_id'), 'INFO')
    return jsonify({'success': True, 'message': 'Security system reset successfully'})

@app.route('/api/test_alarm', methods=['POST'])
def api_test_alarm():
    if not session.get('user_id'):
        return jsonify({'success': False, 'message': 'Authentication required'})
    
    monitor.play_alarm_sound()
    monitor.log_event('TEST_ALARM', f'Audio alarm test by {session.get("username")}', session.get('user_id'), 'INFO')
    return jsonify({'success': True, 'message': 'Audio alarm test completed'})

@app.route('/api/test_leds', methods=['POST'])
def api_test_leds():
    if not session.get('user_id'):
        return jsonify({'success': False, 'message': 'Authentication required'})
    
    if GPIO_AVAILABLE:
        # Flash all LEDs
        monitor.red_led.on()
        monitor.white_led.on()
        time.sleep(1)
        monitor.red_led.off()
        monitor.white_led.off()
    
    monitor.log_event('TEST_LEDS', f'LED diagnostics test by {session.get("username")}', session.get('user_id'), 'INFO')
    return jsonify({'success': True, 'message': 'LED diagnostic test completed'})

@app.route('/api/update_settings', methods=['POST'])
def api_update_settings():
    if not session.get('user_id'):
        return jsonify({'success': False, 'message': 'Authentication required'})
    
    data = request.get_json()
    
    try:
        old_timer = monitor.state.timer_duration
        old_instant = monitor.state.instant_alarm_mode
        
        monitor.state.timer_duration = int(data.get('timer_duration', 30))
        monitor.state.instant_alarm_mode = bool(data.get('instant_alarm_mode', False))
        monitor.save_state()
        
        monitor.log_event('SETTINGS_UPDATE', 
                         f'Settings updated by {session.get("username")}: Timer {old_timer}s→{monitor.state.timer_duration}s, Instant mode {old_instant}→{monitor.state.instant_alarm_mode}', 
                         session.get('user_id'), 'INFO')
        return jsonify({'success': True, 'message': 'Security configuration updated successfully'})
    except Exception as e:
        return jsonify({'success': False, 'message': f'Configuration error: {e}'})

@app.route('/api/save_schedules', methods=['POST'])
def api_save_schedules():
    if not session.get('user_id'):
        return jsonify({'success': False, 'message': 'Authentication required'})
    
    data = request.get_json()
    
    try:
        monitor.schedules = data
        monitor.save_schedules()
        
        monitor.log_event('SCHEDULE_UPDATE', f'Access schedules updated by {session.get("username")}', session.get('user_id'), 'INFO')
        return jsonify({'success': True, 'message': 'Access schedules updated successfully'})
    except Exception as e:
        return jsonify({'success': False, 'message': f'Schedule update error: {e}'})

@app.route('/api/add_user', methods=['POST'])
def api_add_user():
    if session.get('role') != 'Admin':
        return jsonify({'success': False, 'message': 'Administrator privileges required'})
    
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
        
        monitor.log_event('USER_CREATE', f'New user account created: {data["username"]} ({data.get("role", "User")}) by {session.get("username")}', session.get('user_id'), 'INFO')
        return jsonify({'success': True, 'message': f'User account {data["username"]} created successfully'})
        
    except sqlite3.IntegrityError:
        return jsonify({'success': False, 'message': 'Username already exists in the system'})
    except Exception as e:
        return jsonify({'success': False, 'message': f'User creation error: {e}'})

@app.route('/api/delete_user/<int:user_id>', methods=['DELETE'])
def api_delete_user(user_id):
    if session.get('role') != 'Admin':
        return jsonify({'success': False, 'message': 'Administrator privileges required'})
    
    try:
        conn = sqlite3.connect(Config.DATABASE)
        cursor = conn.cursor()
        
        # Get username first
        cursor.execute("SELECT username FROM users WHERE id = ?", (user_id,))
        user = cursor.fetchone()
        
        if user:
            cursor.execute("DELETE FROM users WHERE id = ?", (user_id,))
            conn.commit()
            
            monitor.log_event('USER_DELETE', f'User account deleted: {user[0]} by {session.get("username")}', session.get('user_id'), 'WARNING')
            message = f'User account {user[0]} deleted successfully'
            success = True
        else:
            message = 'User account not found'
            success = False
            
        conn.close()
        return jsonify({'success': success, 'message': message})
        
    except Exception as e:
        return jsonify({'success': False, 'message': f'User deletion error: {e}'})

@app.route('/api/export')
def api_export():
    if not session.get('user_id'):
        return jsonify({'success': False, 'message': 'Authentication required'})
    
    format_type = request.args.get('format', 'csv')
    date_from = request.args.get('date_from')
    date_to = request.args.get('date_to')
    
    try:
        conn = sqlite3.connect(Config.DATABASE)
        cursor = conn.cursor()
        
        query = '''
            SELECT e.timestamp, e.event_type, e.description, u.username, e.door_id, e.severity
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
            writer.writerow(['Timestamp', 'Event Type', 'Description', 'User', 'Door ID', 'Severity'])
            writer.writerows(events)
            
            filename = f'security_events_{datetime.datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
            
            monitor.log_event('REPORT_EXPORT', f'Security events exported to CSV by {session.get("username")}', session.get('user_id'), 'INFO')
            
            return send_file(
                io.BytesIO(output.getvalue().encode()),
                mimetype='text/csv',
                as_attachment=True,
                download_name=filename
            )
            
        else:  # PDF format would require additional implementation
            return jsonify({'success': False, 'message': 'PDF export feature coming soon'})
            
    except Exception as e:
        return jsonify({'success': False, 'message': f'Export error: {e}'})

@app.route('/api/backup', methods=['POST'])
def api_backup():
    if session.get('role') != 'Admin':
        return jsonify({'success': False, 'message': 'Administrator privileges required'})
    
    try:
        # Create backup directory if not exists
        os.makedirs(Config.BACKUP_DIR, exist_ok=True)
        
        timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_filename = f'security_backup_{timestamp}.zip'
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
        
        monitor.log_event('BACKUP_CREATE', f'System backup created: {backup_filename} by {session.get("username")}', session.get('user_id'), 'INFO')
        return jsonify({'success': True, 'message': 'Secure backup created successfully', 'file': backup_filename})
        
    except Exception as e:
        return jsonify({'success': False, 'message': f'Backup error: {e}'})

@app.route('/api/download_backup')
def api_download_backup():
    if session.get('role') != 'Admin':
        return jsonify({'success': False, 'message': 'Administrator privileges required'})
    
    filename = request.args.get('file')
    if not filename:
        return jsonify({'success': False, 'message': 'No backup file specified'})
    
    backup_path = os.path.join(Config.BACKUP_DIR, filename)
    if os.path.exists(backup_path):
        monitor.log_event('BACKUP_DOWNLOAD', f'Backup downloaded: {filename} by {session.get("username")}', session.get('user_id'), 'INFO')
        return send_file(backup_path, as_attachment=True)
    else:
        return jsonify({'success': False, 'message': 'Backup file not found'})

if __name__ == '__main__':
    print("🚀 Starting Advanced Door Monitoring System...")
    print("🔧 GPIO Available:" if GPIO_AVAILABLE else "⚠️  Running in simulation mode - GPIO not available")
    print("🔊 Audio Available:" if AUDIO_AVAILABLE else "⚠️  Audio not available - install pygame for sound alerts")
    print("🔐 Default login: admin / admin123")
    print("🌐 Access dashboard at: http://[raspberry-pi-ip]:5000")
    
    # Create necessary directories
    os.makedirs(Config.BACKUP_DIR, exist_ok=True)
    
    # Log system startup
    monitor.log_event('SYSTEM_START', 'Advanced Door Monitoring System started', severity='INFO')
    
    # Run Flask app
    app.run(host='0.0.0.0', port=5000, debug=False)
