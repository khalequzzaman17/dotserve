from flask import Blueprint, jsonify, request, session
import os, time
from panel.core.config import DATA_DIR
from panel.core.storage import load_json, save_json

onboarding_bp = Blueprint('onboarding', __name__)

CONFIG_FILE = os.path.join(DATA_DIR, 'onboarding.json')
DEFAULT_STEPS = [
    {'id': 'server', 'label': 'Server profile', 'done': False},
    {'id': 'domain', 'label': 'Add first domain', 'done': False},
    {'id': 'email', 'label': 'Email delivery', 'done': False},
    {'id': 'security', 'label': 'Security checklist', 'done': False},
    {'id': 'backup', 'label': 'Backup destination', 'done': False},
]


def req():
    return 'user' in session


def _load():
    data = load_json(CONFIG_FILE, {})
    data.setdefault('completed', False)
    data.setdefault('created_at', int(time.time()))
    data.setdefault('steps', DEFAULT_STEPS.copy())
    data.setdefault('server', {})
    data.setdefault('email', {})
    data.setdefault('security', {})
    return data


def _save(data):
    save_json(CONFIG_FILE, data)


@onboarding_bp.route('/api/onboarding')
def get_onboarding():
    if not req():
        return jsonify({'ok': False}), 401
    return jsonify({'ok': True, **_load()})


@onboarding_bp.route('/api/onboarding', methods=['PUT'])
def save_onboarding():
    if not req():
        return jsonify({'ok': False}), 401
    data = _load()
    incoming = request.get_json() or {}
    for key in ('server', 'email', 'security', 'steps'):
        if key in incoming:
            data[key] = incoming[key]
    data['completed'] = bool(incoming.get('completed', data.get('completed')))
    data['updated_at'] = int(time.time())
    _save(data)
    return jsonify({'ok': True, **data})
