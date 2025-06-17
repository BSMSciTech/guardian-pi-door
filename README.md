
# Raspberry Pi Door Monitoring System

A comprehensive door monitoring system for Raspberry Pi 4 with GPIO control, web dashboard, user management, and event logging.

## Features

- **GPIO Control**: Monitor door sensor and control LEDs
- **Timer-based Alarms**: Configurable countdown with persistent state
- **Scheduled Access**: Define access windows (morning, afternoon, evening)
- **User Management**: Role-based access (Admin, Manager, Supervisor, User)
- **Event Logging**: SQLite database with all system events
- **Web Dashboard**: Real-time status, settings, and reports
- **Report Generation**: Export events as CSV
- **Backup System**: Create and download system backups

## Hardware Setup

Connect the following components to your Raspberry Pi 4:

- **Door Sensor (Magnetic)** → GPIO17 (BOARD 11)
- **Green LED** → GPIO25 (BOARD 22) - System running indicator
- **Red LED** → GPIO27 (BOARD 13) - Timer active (blinking)
- **White LED** → GPIO23 (BOARD 16) - Alarm triggered
- **Optional Switch** → GPIO24 (BOARD 18)

## Installation

1. **Clone or download the files to your Raspberry Pi**

2. **Install required packages:**
   ```bash
   sudo apt update
   sudo apt install python3-pip
   pip3 install -r requirements.txt
   ```

3. **Optional: Install audio support**
   ```bash
   sudo apt install python3-pygame
   # Place your alarm sound file as 'alarm.wav' in the same directory
   ```

4. **Run the application:**
   ```bash
   python3 app.py
   ```

5. **Access the web interface:**
   - Open your browser and go to `http://[raspberry-pi-ip]:5000`
   - Default login: `admin` / `admin123`

## Usage

### Web Dashboard
- **Live Status**: View real-time door status and LED indicators
- **Events**: Monitor all system events with timestamps
- **Settings**: Configure timer duration and alarm modes
- **Schedules**: Set access windows for different days
- **Users**: Admin can manage user accounts and roles
- **Reports**: Export event logs and create system backups

### System Behavior
- **Door Open**: Triggers timer or instant alarm based on schedule
- **Timer Active**: Red LED blinks during countdown
- **Alarm Triggered**: White LED on, audio plays (if available)
- **Normal Hours**: Access allowed within scheduled windows

### User Roles
- **Admin**: Full system access, user management, backups
- **Manager**: System settings and reports
- **Supervisor**: View status and events
- **User**: Basic monitoring access

## Configuration Files

- `system_state.json`: Persistent system state
- `schedules.json`: Access schedule configuration
- `door_monitor.db`: SQLite database with users and events
- `backups/`: System backup files

## Auto-start on Boot (Optional)

Create a systemd service:

```bash
sudo nano /etc/systemd/system/door-monitor.service
```

Add:
```ini
[Unit]
Description=Door Monitoring System
After=network.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/door-monitor
ExecStart=/usr/bin/python3 /home/pi/door-monitor/app.py
Restart=always

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl enable door-monitor.service
sudo systemctl start door-monitor.service
```

## Troubleshooting

- **GPIO not available**: System runs in simulation mode
- **Audio not working**: Install pygame or check `alarm.wav` file
- **Permission errors**: Run with `sudo` or adjust GPIO permissions
- **Web interface not accessible**: Check firewall settings

## Security Notes

- Change the default admin password immediately
- Use strong passwords for all accounts
- Consider running behind a reverse proxy for HTTPS
- Regular backups are recommended

## License

This project is provided as-is for educational and personal use.
