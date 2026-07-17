from flask import Blueprint, jsonify, request, session
import json
import os
from collections import deque

from panel.core.process import run_args
from panel.core.validation import valid_safe_name

logs_bp = Blueprint('logs', __name__)

LOG_SOURCES = {
    'nginx_error': '/var/log/nginx/error.log',
    'nginx_access': '/var/log/nginx/access.log',
    'dotserve': '/var/log/dotserve/error.log',
    'syslog': '/var/log/syslog',
}


def req():
    return 'user' in session


def _tail_file(path, lines):
    with open(path, errors='replace') as f:
        return ''.join(deque(f, maxlen=lines))


@logs_bp.route('/api/logs/files')
def log_files():
    if not req():
        return jsonify({'ok': False}), 401
    log_paths = [
        '/var/log/nginx', '/var/log/apache2', '/var/log/httpd',
        '/var/log/mysql', '/var/log/mariadb', '/var/log/mongodb',
        '/var/log/syslog', '/var/log/auth.log',
    ]
    files = []
    for p in log_paths:
        if os.path.isdir(p):
            for name in os.listdir(p):
                if name.endswith(('.log', '.err')):
                    files.append({'name': name, 'path': os.path.join(p, name), 'dir': p})
        elif os.path.isfile(p):
            files.append({'name': os.path.basename(p), 'path': p})
    return jsonify({'ok': True, 'files': files})


@logs_bp.route('/api/logs/sources')
def log_sources():
    if not req():
        return jsonify({'ok': False}), 401
    sources = []
    for key, path in LOG_SOURCES.items():
        if os.path.exists(path):
            sources.append({'id': key, 'label': key.replace('_', ' ').title(), 'path': path})
    out, _, rc = run_args(['pm2', 'jlist'], timeout=10)
    if rc == 0 and out:
        try:
            for app in json.loads(out):
                name = app.get('name', '')
                if valid_safe_name(name):
                    sources.append({'id': 'pm2:' + name, 'label': 'App: ' + name, 'path': ''})
        except Exception:
            pass
    return jsonify({'ok': True, 'sources': sources})


@logs_bp.route('/api/logs/tail')
def tail_log():
    if not req():
        return jsonify({'ok': False}), 401
    source = request.args.get('source', 'dotserve')
    search = request.args.get('search', '').strip().lower()
    try:
        lines = max(1, min(int(request.args.get('lines', 200)), 1000))
    except ValueError:
        lines = 200

    if source.startswith('pm2:'):
        app_name = source[4:]
        if not valid_safe_name(app_name):
            return jsonify({'ok': False, 'error': 'Invalid app name'}), 400
        out, err, _ = run_args(['pm2', 'logs', app_name, '--lines', str(lines), '--nostream'], timeout=20)
        out = out or err
    else:
        path = LOG_SOURCES.get(source)
        if not path or not os.path.exists(path):
            return jsonify({'ok': False, 'error': 'Log source not found'}), 404
        out = _tail_file(path, lines)

    if search:
        out = '\n'.join(l for l in out.split('\n') if search in l.lower())
    return jsonify({'ok': True, 'lines': out or 'No log entries found'})

