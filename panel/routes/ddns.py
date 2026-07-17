from flask import Blueprint, jsonify, request, session
import os
import threading
import time
import requests as req_lib

from panel.core.config import DATA_DIR
from panel.core.storage import load_json, save_json
from panel.core.validation import valid_domain

ddns_bp = Blueprint('ddns', __name__)

DDNS_CONFIG = os.path.join(DATA_DIR, 'ddns_config.json')
DDNS_LOG = os.path.join(DATA_DIR, 'ddns.log')


def req():
    return 'user' in session


def load_config():
    return load_json(DDNS_CONFIG, {'domains': [], 'enabled': False, 'interval': 300})


def save_config(cfg):
    save_json(DDNS_CONFIG, cfg)


def get_public_ip():
    for url in ['https://api.ipify.org', 'https://ifconfig.me/ip', 'https://icanhazip.com']:
        try:
            r = req_lib.get(url, timeout=5)
            if r.status_code == 200:
                return r.text.strip()
        except Exception:
            pass
    return None


def update_cloudflare(domain_cfg, ip):
    token = domain_cfg.get('api_token', '')
    domain = domain_cfg.get('domain', '')
    email = domain_cfg.get('email', '')
    api_limit = domain_cfg.get('api_limit', False)
    headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}
    if not api_limit and email:
        headers = {'X-Auth-Email': email, 'X-Auth-Key': token, 'Content-Type': 'application/json'}
    try:
        root_domain = '.'.join(domain.split('.')[-2:])
        r = req_lib.get(f'https://api.cloudflare.com/client/v4/zones?name={root_domain}', headers=headers, timeout=10)
        zones = r.json().get('result', [])
        if not zones:
            return False, f'Zone not found for {root_domain}'
        zone_id = zones[0]['id']
        r = req_lib.get(f'https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records?type=A&name={domain}', headers=headers, timeout=10)
        records = r.json().get('result', [])
        payload = {'type': 'A', 'name': domain, 'content': ip, 'ttl': 120, 'proxied': False}
        if records:
            record_id = records[0]['id']
            if records[0].get('content') == ip:
                return True, f'IP unchanged ({ip})'
            r = req_lib.put(f'https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records/{record_id}', headers=headers, json=payload, timeout=10)
            return (True, f'Updated {domain} -> {ip}') if r.json().get('success') else (False, r.json().get('errors', 'Update failed'))
        r = req_lib.post(f'https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records', headers=headers, json=payload, timeout=10)
        return (True, f'Created {domain} -> {ip}') if r.json().get('success') else (False, r.json().get('errors', 'Create failed'))
    except Exception as e:
        return False, str(e)


def write_log(msg):
    os.makedirs(DATA_DIR, exist_ok=True)
    timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
    with open(DDNS_LOG, 'a', encoding='utf-8') as f:
        f.write(f'[{timestamp}] {msg}\n')
    try:
        with open(DDNS_LOG, encoding='utf-8') as f:
            lines = f.readlines()
        if len(lines) > 1000:
            with open(DDNS_LOG, 'w', encoding='utf-8') as f:
                f.writelines(lines[-1000:])
    except Exception:
        pass


_ddns_thread = None
_ddns_running = False


def ddns_loop():
    global _ddns_running
    write_log('DDNS service started')
    last_ip = None
    while _ddns_running:
        cfg = load_config()
        if not cfg.get('enabled'):
            time.sleep(10)
            continue
        ip = get_public_ip()
        if not ip:
            write_log('Failed to get public IP')
            time.sleep(60)
            continue
        if ip != last_ip:
            write_log(f'IP changed: {last_ip} -> {ip}')
            for d in cfg.get('domains', []):
                if d.get('provider', 'cloudflare') == 'cloudflare':
                    ok, msg = update_cloudflare(d, ip)
                    write_log(f'[{"OK" if ok else "ERR"}] {d.get("domain")}: {msg}')
            last_ip = ip
        time.sleep(max(60, min(int(cfg.get('interval', 300)), 86400)))
    write_log('DDNS service stopped')


def start_ddns():
    global _ddns_thread, _ddns_running
    if _ddns_running:
        return
    _ddns_running = True
    _ddns_thread = threading.Thread(target=ddns_loop, daemon=True)
    _ddns_thread.start()


def stop_ddns():
    global _ddns_running
    _ddns_running = False


if load_config().get('enabled'):
    start_ddns()


@ddns_bp.route('/api/ddns/domains')
def list_domains():
    if not req():
        return jsonify({'ok': False}), 401
    cfg = load_config()
    redacted = []
    for item in cfg.get('domains', []):
        clone = item.copy()
        if clone.get('api_token'):
            clone['api_token'] = '********'
        redacted.append(clone)
    return jsonify({'ok': True, 'domains': redacted, 'enabled': cfg.get('enabled', False)})


@ddns_bp.route('/api/ddns/domains', methods=['POST'])
def add_domain():
    if not req():
        return jsonify({'ok': False}), 401
    d = request.get_json() or {}
    domain = d.get('domain', '').strip().lower()
    provider = d.get('provider', 'cloudflare')
    email = d.get('email', '').strip()
    api_token = d.get('api_token', '').strip()
    api_limit = bool(d.get('api_limit', False))
    if provider != 'cloudflare':
        return jsonify({'ok': False, 'error': 'Unsupported provider'}), 400
    if not valid_domain(domain) or not api_token:
        return jsonify({'ok': False, 'error': 'Valid domain and API token required'}), 400
    cfg = load_config()
    cfg['domains'] = [x for x in cfg.get('domains', []) if x.get('domain') != domain]
    cfg['domains'].append({'domain': domain, 'provider': provider, 'email': email, 'api_token': api_token, 'api_limit': api_limit})
    save_config(cfg)
    return jsonify({'ok': True})


@ddns_bp.route('/api/ddns/domains/<domain>', methods=['DELETE'])
def delete_domain(domain):
    if not req():
        return jsonify({'ok': False}), 401
    if not valid_domain(domain):
        return jsonify({'ok': False, 'error': 'Invalid domain'}), 400
    cfg = load_config()
    cfg['domains'] = [x for x in cfg.get('domains', []) if x.get('domain') != domain]
    save_config(cfg)
    return jsonify({'ok': True})


@ddns_bp.route('/api/ddns/status')
def get_status():
    if not req():
        return jsonify({'ok': False}), 401
    cfg = load_config()
    return jsonify({'ok': True, 'enabled': cfg.get('enabled', False), 'running': _ddns_running,
                    'current_ip': get_public_ip() or 'Unknown', 'interval': cfg.get('interval', 300)})


@ddns_bp.route('/api/ddns/toggle', methods=['POST'])
def toggle():
    if not req():
        return jsonify({'ok': False}), 401
    enable = bool((request.get_json() or {}).get('enable', False))
    cfg = load_config()
    cfg['enabled'] = enable
    save_config(cfg)
    start_ddns() if enable else stop_ddns()
    return jsonify({'ok': True, 'enabled': enable})


@ddns_bp.route('/api/ddns/log')
def get_log():
    if not req():
        return jsonify({'ok': False}), 401
    if not os.path.exists(DDNS_LOG):
        return jsonify({'ok': True, 'log': 'No log entries yet'})
    try:
        with open(DDNS_LOG, encoding='utf-8') as f:
            return jsonify({'ok': True, 'log': ''.join(f.readlines()[-200:])})
    except Exception:
        return jsonify({'ok': True, 'log': 'Could not read log'})


@ddns_bp.route('/api/ddns/test/<domain>', methods=['POST'])
def test_domain(domain):
    if not req():
        return jsonify({'ok': False}), 401
    if not valid_domain(domain):
        return jsonify({'ok': False, 'error': 'Invalid domain'}), 400
    cfg = load_config()
    domain_cfg = next((d for d in cfg.get('domains', []) if d.get('domain') == domain), None)
    if not domain_cfg:
        return jsonify({'ok': False, 'error': 'Domain not found'}), 404
    ip = get_public_ip()
    if not ip:
        return jsonify({'ok': False, 'error': 'Could not get public IP'})
    ok, msg = update_cloudflare(domain_cfg, ip)
    write_log(f'[MANUAL TEST] {domain}: {msg}')
    return jsonify({'ok': ok, 'message': msg, 'ip': ip})

