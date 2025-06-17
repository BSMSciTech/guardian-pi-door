
import React, { useState, useEffect } from 'react';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Badge } from '@/components/ui/badge';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { useToast } from '@/hooks/use-toast';
import { 
  Shield, 
  Door, 
  Clock, 
  Bell, 
  Power, 
  Settings, 
  Users, 
  FileText, 
  Calendar,
  Download,
  AlertTriangle,
  CheckCircle,
  XCircle
} from 'lucide-react';

interface SystemStatus {
  success: boolean;
  door_open: boolean;
  timer_active: boolean;
  alarm_triggered: boolean;
  remaining_time: number;
  timer_duration: number;
  gpio_available: boolean;
  timestamp: string;
}

interface Event {
  timestamp: string;
  event_type: string;
  description: string;
  username: string;
  severity: string;
}

interface User {
  username: string;
  password: string;
}

const DoorMonitor = () => {
  const [isLoggedIn, setIsLoggedIn] = useState(false);
  const [user, setUser] = useState<User>({ username: '', password: '' });
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [events, setEvents] = useState<Event[]>([]);
  const [timerDuration, setTimerDuration] = useState(30);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const { toast } = useToast();

  const API_BASE = 'http://localhost:5000';

  // Check login status on mount
  useEffect(() => {
    checkLoginStatus();
  }, []);

  // Poll status and events when logged in
  useEffect(() => {
    if (isLoggedIn) {
      const statusInterval = setInterval(fetchStatus, 2000);
      const eventsInterval = setInterval(fetchEvents, 5000);
      
      return () => {
        clearInterval(statusInterval);
        clearInterval(eventsInterval);
      };
    }
  }, [isLoggedIn]);

  const checkLoginStatus = async () => {
    try {
      const response = await fetch(`${API_BASE}/api/status`, {
        credentials: 'include'
      });
      if (response.ok) {
        setIsLoggedIn(true);
        fetchStatus();
        fetchEvents();
      }
    } catch (error) {
      console.log('Not logged in');
    }
  };

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError('');

    try {
      const formData = new FormData();
      formData.append('username', user.username);
      formData.append('password', user.password);

      const response = await fetch(`${API_BASE}/login`, {
        method: 'POST',
        body: formData,
        credentials: 'include'
      });

      if (response.ok) {
        setIsLoggedIn(true);
        toast({
          title: "Login Successful",
          description: "Welcome to the Door Monitoring System",
        });
        fetchStatus();
        fetchEvents();
      } else {
        setError('Invalid credentials');
      }
    } catch (error) {
      setError('Connection failed. Make sure the Flask server is running on port 5000.');
    } finally {
      setLoading(false);
    }
  };

  const handleLogout = async () => {
    try {
      await fetch(`${API_BASE}/logout`, {
        credentials: 'include'
      });
      setIsLoggedIn(false);
      setStatus(null);
      setEvents([]);
    } catch (error) {
      console.error('Logout error:', error);
    }
  };

  const fetchStatus = async () => {
    try {
      const response = await fetch(`${API_BASE}/api/status`, {
        credentials: 'include'
      });
      if (response.ok) {
        const data = await response.json();
        setStatus(data);
      }
    } catch (error) {
      console.error('Status fetch error:', error);
    }
  };

  const fetchEvents = async () => {
    try {
      const response = await fetch(`${API_BASE}/api/events`, {
        credentials: 'include'
      });
      if (response.ok) {
        const data = await response.json();
        if (data.success) {
          setEvents(data.events);
        }
      }
    } catch (error) {
      console.error('Events fetch error:', error);
    }
  };

  const resetSystem = async () => {
    try {
      const response = await fetch(`${API_BASE}/api/reset`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include'
      });
      
      if (response.ok) {
        toast({
          title: "System Reset",
          description: "System has been reset successfully",
        });
        fetchStatus();
        fetchEvents();
      }
    } catch (error) {
      toast({
        title: "Reset Failed",
        description: "Failed to reset system",
        variant: "destructive"
      });
    }
  };

  const updateTimer = async () => {
    try {
      const response = await fetch(`${API_BASE}/api/update_timer`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ duration: timerDuration }),
        credentials: 'include'
      });
      
      if (response.ok) {
        toast({
          title: "Timer Updated",
          description: `Timer duration set to ${timerDuration} seconds`,
        });
        fetchStatus();
        fetchEvents();
      }
    } catch (error) {
      toast({
        title: "Update Failed",
        description: "Failed to update timer",
        variant: "destructive"
      });
    }
  };

  const downloadReport = () => {
    window.open(`${API_BASE}/api/download_report`, '_blank');
  };

  if (!isLoggedIn) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-blue-50 to-indigo-100 flex items-center justify-center p-4">
        <Card className="w-full max-w-md">
          <CardHeader className="text-center">
            <div className="mx-auto mb-4 w-16 h-16 bg-blue-100 rounded-full flex items-center justify-center">
              <Shield className="w-8 h-8 text-blue-600" />
            </div>
            <CardTitle className="text-2xl">Door Security Monitor</CardTitle>
            <p className="text-gray-600">Secure Access Required</p>
          </CardHeader>
          <CardContent>
            {error && (
              <Alert className="mb-4" variant="destructive">
                <AlertTriangle className="h-4 w-4" />
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            )}
            
            <form onSubmit={handleLogin} className="space-y-4">
              <div>
                <Label htmlFor="username">Username</Label>
                <Input
                  id="username"
                  type="text"
                  value={user.username}
                  onChange={(e) => setUser({ ...user, username: e.target.value })}
                  placeholder="Enter username"
                  required
                />
              </div>
              <div>
                <Label htmlFor="password">Password</Label>
                <Input
                  id="password"
                  type="password"
                  value={user.password}
                  onChange={(e) => setUser({ ...user, password: e.target.value })}
                  placeholder="Enter password"
                  required
                />
              </div>
              <Button type="submit" className="w-full" disabled={loading}>
                {loading ? 'Logging in...' : 'Login'}
              </Button>
            </form>
            
            <div className="mt-4 text-center text-sm text-gray-500">
              Default: admin / admin123
            </div>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-blue-50 to-indigo-100">
      {/* Header */}
      <div className="bg-white shadow-sm border-b">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex justify-between items-center h-16">
            <div className="flex items-center">
              <Shield className="w-8 h-8 text-blue-600 mr-3" />
              <h1 className="text-xl font-semibold text-gray-900">Door Security Monitor</h1>
            </div>
            <Button onClick={handleLogout} variant="outline">
              Logout
            </Button>
          </div>
        </div>
      </div>

      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
        {/* Status Cards */}
        <div className="grid grid-cols-1 md:grid-cols-4 gap-6 mb-8">
          <Card>
            <CardContent className="p-6">
              <div className="flex items-center">
                <Door className={`w-8 h-8 mr-3 ${status?.door_open ? 'text-red-500' : 'text-green-500'}`} />
                <div>
                  <p className="text-sm font-medium text-gray-600">Door Status</p>
                  <p className={`text-lg font-semibold ${status?.door_open ? 'text-red-500' : 'text-green-500'}`}>
                    {status?.door_open ? 'Open' : 'Closed'}
                  </p>
                </div>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardContent className="p-6">
              <div className="flex items-center">
                <Clock className={`w-8 h-8 mr-3 ${status?.timer_active ? 'text-yellow-500' : 'text-gray-400'}`} />
                <div>
                  <p className="text-sm font-medium text-gray-600">Timer</p>
                  <p className={`text-lg font-semibold ${status?.timer_active ? 'text-yellow-500' : 'text-gray-500'}`}>
                    {status?.timer_active ? `${Math.ceil(status.remaining_time)}s` : 'Inactive'}
                  </p>
                </div>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardContent className="p-6">
              <div className="flex items-center">
                <Bell className={`w-8 h-8 mr-3 ${status?.alarm_triggered ? 'text-red-500' : 'text-green-500'}`} />
                <div>
                  <p className="text-sm font-medium text-gray-600">Alarm</p>
                  <p className={`text-lg font-semibold ${status?.alarm_triggered ? 'text-red-500' : 'text-green-500'}`}>
                    {status?.alarm_triggered ? 'TRIGGERED' : 'Normal'}
                  </p>
                </div>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardContent className="p-6">
              <div className="flex items-center">
                <div className={`w-8 h-8 mr-3 rounded-full flex items-center justify-center ${status?.gpio_available ? 'bg-green-100' : 'bg-gray-100'}`}>
                  {status?.gpio_available ? (
                    <CheckCircle className="w-5 h-5 text-green-600" />
                  ) : (
                    <XCircle className="w-5 h-5 text-gray-600" />
                  )}
                </div>
                <div>
                  <p className="text-sm font-medium text-gray-600">GPIO</p>
                  <p className={`text-lg font-semibold ${status?.gpio_available ? 'text-green-500' : 'text-gray-500'}`}>
                    {status?.gpio_available ? 'Active' : 'Simulation'}
                  </p>
                </div>
              </div>
            </CardContent>
          </Card>
        </div>

        {/* Reset Button */}
        <div className="mb-6">
          <Button onClick={resetSystem} variant="destructive" size="lg">
            <Power className="w-4 h-4 mr-2" />
            Reset System
          </Button>
        </div>

        {/* Main Content Tabs */}
        <Tabs defaultValue="events" className="space-y-6">
          <TabsList className="grid w-full grid-cols-5">
            <TabsTrigger value="events">
              <FileText className="w-4 h-4 mr-2" />
              Events
            </TabsTrigger>
            <TabsTrigger value="settings">
              <Settings className="w-4 h-4 mr-2" />
              Settings
            </TabsTrigger>
            <TabsTrigger value="users">
              <Users className="w-4 h-4 mr-2" />
              Users
            </TabsTrigger>
            <TabsTrigger value="reports">
              <Download className="w-4 h-4 mr-2" />
              Reports
            </TabsTrigger>
            <TabsTrigger value="schedules">
              <Calendar className="w-4 h-4 mr-2" />
              Schedules
            </TabsTrigger>
          </TabsList>

          <TabsContent value="events">
            <Card>
              <CardHeader>
                <CardTitle>Recent Events</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="space-y-3 max-h-96 overflow-y-auto">
                  {events.length === 0 ? (
                    <p className="text-gray-500 text-center py-4">No events recorded yet.</p>
                  ) : (
                    events.map((event, index) => (
                      <div key={index} className="border-l-4 border-blue-500 pl-4 py-2 bg-gray-50 rounded-r">
                        <div className="flex justify-between items-start">
                          <div>
                            <div className="flex items-center gap-2">
                              <Badge variant={event.severity === 'CRITICAL' ? 'destructive' : 
                                             event.severity === 'WARNING' ? 'default' : 'secondary'}>
                                {event.severity}
                              </Badge>
                              <span className="font-medium">{event.event_type}</span>
                            </div>
                            <p className="text-sm text-gray-600 mt-1">{event.description}</p>
                            <p className="text-xs text-gray-400 mt-1">
                              {event.username} • {new Date(event.timestamp).toLocaleString()}
                            </p>
                          </div>
                        </div>
                      </div>
                    ))
                  )}
                </div>
              </CardContent>
            </Card>
          </TabsContent>

          <TabsContent value="settings">
            <Card>
              <CardHeader>
                <CardTitle>System Settings</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="space-y-4">
                  <div>
                    <Label htmlFor="timer">Timer Duration (seconds)</Label>
                    <div className="flex gap-2 mt-1">
                      <Input
                        id="timer"
                        type="number"
                        min="1"
                        max="86400"
                        value={timerDuration}
                        onChange={(e) => setTimerDuration(parseInt(e.target.value))}
                        className="max-w-xs"
                      />
                      <Button onClick={updateTimer}>Update</Button>
                    </div>
                  </div>
                  <div className="bg-blue-50 p-4 rounded-lg">
                    <p className="text-sm text-blue-800">
                      Current timer duration: {status?.timer_duration} seconds
                    </p>
                  </div>
                </div>
              </CardContent>
            </Card>
          </TabsContent>

          <TabsContent value="users">
            <Card>
              <CardHeader>
                <CardTitle>User Management</CardTitle>
              </CardHeader>
              <CardContent>
                <Alert>
                  <AlertTriangle className="h-4 w-4" />
                  <AlertDescription>
                    User management interface coming soon. Contact administrator for user account changes.
                  </AlertDescription>
                </Alert>
              </CardContent>
            </Card>
          </TabsContent>

          <TabsContent value="reports">
            <Card>
              <CardHeader>
                <CardTitle>Generate Reports</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="space-y-4">
                  <Button onClick={downloadReport}>
                    <Download className="w-4 h-4 mr-2" />
                    Download CSV Report
                  </Button>
                  <div className="bg-blue-50 p-4 rounded-lg">
                    <h4 className="font-medium text-blue-900 mb-2">Report Contents</h4>
                    <ul className="text-sm text-blue-800 space-y-1">
                      <li>• All system events</li>
                      <li>• User activities</li>
                      <li>• Door status changes</li>
                      <li>• Alarm triggers</li>
                    </ul>
                  </div>
                </div>
              </CardContent>
            </Card>
          </TabsContent>

          <TabsContent value="schedules">
            <Card>
              <CardHeader>
                <CardTitle>Access Schedules</CardTitle>
              </CardHeader>
              <CardContent>
                <Alert>
                  <Calendar className="h-4 w-4" />
                  <AlertDescription>
                    Schedule management interface coming soon. Contact administrator for schedule changes.
                  </AlertDescription>
                </Alert>
              </CardContent>
            </Card>
          </TabsContent>
        </Tabs>
      </div>
    </div>
  );
};

export default DoorMonitor;
