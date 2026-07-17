from collections import deque
from flask import Blueprint, jsonify, request, session
import os
import shutil
import signal
import subprocess
import time

from panel.core.process import run_args


monitoring_bp = Blueprint('monitoring', __name__)


def req():
    return 'user' in session


def _tail(path, lines):
    try:
        with open(path, errors='ignore') as fh:
            return ''.join(deque(fh, maxlen=lines))
    except Exception:
        return ''


def _top_processes(limit=20):
    out, _, rc = run_args(['ps', 'aux', '--sort=-%cpu'], timeout=5)
    if rc != 0:
        return []
    procs = []
    for line in out.splitlines()[1:limit + 1]:
        parts = line.split(None, 10)
        if len(parts) >= 11:
            procs.append({'user': parts[0], 'pid': parts[1], 'cpu': parts[2],
                          'mem': parts[3], 'cmd': parts[10][:60],
                          'status': parts[7], 'name': parts[10][:40]})
    return procs


def _cpu_percent():
    def read_stat():
        with open('/proc/stat') as fh:
            values = [int(x) for x in fh.readline().split()[1:]]
        idle = values[3] + values[4]
        total = sum(values)
        return idle, total
    try:
        idle1, total1 = read_stat()
        time.sleep(0.1)
        idle2, total2 = read_stat()
        total_delta = total2 - total1
        idle_delta = idle2 - idle1
        return round((1 - idle_delta / total_delta) * 100, 1) if total_delta else 0.0
    except Exception:
        return 0.0


def _memory():
    values = {}
    try:
        with open('/proc/meminfo') as fh:
            for line in fh:
                key, value = line.split(':', 1)
                values[key] = int(value.strip().split()[0])
        total = values.get('MemTotal', 0) // 1024
        available = values.get('MemAvailable', 0) // 1024
        used = max(0, total - available)
        pct = round(used / total * 100, 1) if total else 0
        return f'{used} MB / {total} MB', pct
    except Exception:
        return '', 0


def _uptime():
    try:
        seconds = int(float(open('/proc/uptime').read().split()[0]))
        days, rem = divmod(seconds, 86400)
        hours, rem = divmod(rem, 3600)
        minutes = rem // 60
        if days:
            return f'up {days} days, {hours} hours'
        if hours:
            return f'up {hours} hours, {minutes} minutes'
        return f'up {minutes} minutes'
    except Exception:
        return ''


def _loadavg():
    try:
        return ' '.join(open('/proc/loadavg').read().split()[:3])
    except Exception:
        return ''


@monitoring_bp.route('/api/monitor/stats')
def monitor_stats_alias():
    return monitoring_overview()


@monitoring_bp.route('/api/monitor/processes')
def monitor_processes_alias():
    return processes()


@monitoring_bp.route('/api/monitoring/processes')
def processes():
    if not req():
        return jsonify({'ok': False}), 401
    return jsonify({'ok': True, 'processes': _top_processes(20)})


@monitoring_bp.route('/api/monitoring/logs')
def logs():
    if not req():
        return jsonify({'ok': False}), 401
    paths = {
        'nginx_error': '/var/log/nginx/error.log',
        'nginx_access': '/var/log/nginx/access.log',
        'mysql': '/var/log/mysql/error.log',
        'syslog': '/var/log/syslog',
        'auth': '/var/log/auth.log',
        'mail': '/var/log/mail.log',
    }
    path = paths.get(request.args.get('log', 'nginx_error'), '/var/log/syslog')
    try:
        lines = max(1, min(1000, int(request.args.get('lines', 100))))
    except Exception:
        lines = 100
    return jsonify({'ok': True, 'content': _tail(path, lines), 'path': path})


@monitoring_bp.route('/api/monitoring/diskio')
def diskio():
    if not req():
        return jsonify({'ok': False}), 401
    out, _, _ = run_args(['iostat', '-d', '1', '1'], timeout=5)
    disks = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 6 and parts[0] not in ('Device', 'Linux'):
            disks.append({'device': parts[0], 'reads': parts[3], 'writes': parts[4]})
    return jsonify({'ok': True, 'disks': disks})


@monitoring_bp.route('/api/monitoring/netstat')
def netstat():
    if not req():
        return jsonify({'ok': False}), 401
    out, err, _ = run_args(['ss', '-tlnp'], timeout=5)
    return jsonify({'ok': True, 'output': '\n'.join((out or err).splitlines()[:30])})


@monitoring_bp.route('/api/monitoring/fail2ban')
def fail2ban():
    if not req():
        return jsonify({'ok': False}), 401
    out, err, _ = run_args(['fail2ban-client', 'status'], timeout=10)
    return jsonify({'ok': True, 'output': out or err})


@monitoring_bp.route('/api/monitoring')
def monitoring_overview():
    if not req():
        return jsonify({'ok': False}), 401
    ram_str, ram_pct = _memory()
    usage = shutil.disk_usage('/')
    disk = round(usage.used / usage.total * 100) if usage.total else 0
    return jsonify({'ok': True, 'cpu': _cpu_percent(), 'ram': ram_str, 'ram_pct': ram_pct,
                    'disk': disk, 'uptime': _uptime(), 'load': _loadavg(),
                    'processes': _top_processes(10)})


@monitoring_bp.route('/api/monitoring/processes/kill', methods=['POST'])
def kill_process():
    if not req():
        return jsonify({'ok': False}), 401
    pid = str((request.get_json() or {}).get('pid', '')).strip()
    if not pid.isdigit():
        return jsonify({'ok': False, 'error': 'Invalid PID'}), 400
    pid_int = int(pid)
    if pid_int in (1, os.getpid()):
        return jsonify({'ok': False, 'error': 'Refusing to kill init or the panel process itself'}), 400
    sig = signal.SIGKILL if (request.get_json() or {}).get('force') else signal.SIGTERM
    try:
        os.kill(pid_int, sig)
        return jsonify({'ok': True, 'output': ''})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500
