from flask import Blueprint, jsonify, request, session
import os, shutil, time
from panel.core.config import DATA_DIR
from panel.core.storage import load_json, save_json

alerting_bp = Blueprint('alerting', __name__)

CONFIG_FILE = os.path.join(DATA_DIR, 'alerts.json')
HISTORY_FILE = os.path.join(DATA_DIR, 'alert_history.json')

DEFAULT_CONFIG = {
    'cpu_threshold': 85,
    'ram_threshold': 85,
    'disk_threshold': 90,
    'ssl_expiry_days': 14,
    'email_enabled': False,
    'email_to': '',
    'webhook_url': '',
}


def req():
    return 'user' in session


def _current_metrics():
    cpu = 0.0
    try:
        if hasattr(os, 'getloadavg'):
            load1 = os.getloadavg()[0]
            cpu_count = os.cpu_count() or 1
            cpu = round(min(100.0, (load1 / cpu_count) * 100), 1)
    except Exception:
        pass
    ram = 0.0
    try:
        if os.path.exists('/proc/meminfo'):
            info = {}
            with open('/proc/meminfo') as f:
                for line in f:
                    k, v = line.split(':', 1)
                    info[k] = int(v.strip().split()[0])
            total = info.get('MemTotal', 0)
            available = info.get('MemAvailable', 0)
            if total:
                ram = round((total - available) / total * 100, 1)
    except Exception:
        pass
    usage = shutil.disk_usage('/')
    disk = round((usage.used / usage.total) * 100, 1) if usage.total else 0.0
    return {
        'cpu': float(cpu or 0),
        'ram': float(ram or 0),
        'disk': float(disk or 0),
        'checked_at': int(time.time()),
    }


@alerting_bp.route('/api/alerts')
def get_alerts():
    if not req():
        return jsonify({'ok': False}), 401
    return jsonify({
        'ok': True,
        'config': load_json(CONFIG_FILE, DEFAULT_CONFIG),
        'history': load_json(HISTORY_FILE, [])[-100:],
        'metrics': _current_metrics(),
    })


@alerting_bp.route('/api/alerts/config', methods=['PUT'])
def save_alert_config():
    if not req():
        return jsonify({'ok': False}), 401
    data = DEFAULT_CONFIG.copy()
    data.update(load_json(CONFIG_FILE, DEFAULT_CONFIG))
    incoming = request.get_json() or {}
    for key in DEFAULT_CONFIG:
        if key in incoming:
            data[key] = incoming[key]
    save_json(CONFIG_FILE, data)
    return jsonify({'ok': True, 'config': data})


@alerting_bp.route('/api/alerts/evaluate', methods=['POST'])
def evaluate_alerts():
    if not req():
        return jsonify({'ok': False}), 401
    cfg = load_json(CONFIG_FILE, DEFAULT_CONFIG)
    metrics = _current_metrics()
    triggered = []
    for key, label in [('cpu', 'CPU'), ('ram', 'RAM'), ('disk', 'Disk')]:
        threshold = float(cfg.get(f'{key}_threshold', 100))
        if metrics[key] >= threshold:
            triggered.append({
                'type': key,
                'message': f'{label} usage is {metrics[key]}% (threshold {threshold}%)',
                'created_at': metrics['checked_at'],
            })
    if triggered:
        history = load_json(HISTORY_FILE, [])
        history.extend(triggered)
        save_json(HISTORY_FILE, history[-500:])
    return jsonify({'ok': True, 'triggered': triggered, 'metrics': metrics})
