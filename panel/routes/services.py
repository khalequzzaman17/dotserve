from flask import Blueprint, jsonify, session

from panel.core.process import run_args


services_bp = Blueprint('services', __name__)


def req():
    return 'user' in session


SERVICES = [
    {'name': 'nginx', 'label': 'Nginx', 'icon': 'NG'},
    {'name': 'apache2', 'label': 'Apache2', 'icon': 'AP'},
    {'name': 'mysql', 'label': 'MySQL', 'icon': 'DB'},
    {'name': 'mariadb', 'label': 'MariaDB', 'icon': 'DB'},
    {'name': 'redis-server', 'label': 'Redis', 'icon': 'RD'},
    {'name': 'memcached', 'label': 'Memcached', 'icon': 'MC'},
    {'name': 'php8.3-fpm', 'label': 'PHP 8.3-FPM', 'icon': 'PHP'},
    {'name': 'php8.2-fpm', 'label': 'PHP 8.2-FPM', 'icon': 'PHP'},
    {'name': 'php8.1-fpm', 'label': 'PHP 8.1-FPM', 'icon': 'PHP'},
    {'name': 'postfix', 'label': 'Postfix (Mail)', 'icon': 'MX'},
    {'name': 'dovecot', 'label': 'Dovecot (IMAP)', 'icon': 'IMAP'},
    {'name': 'docker', 'label': 'Docker', 'icon': 'DK'},
    {'name': 'fail2ban', 'label': 'Fail2ban', 'icon': 'F2B'},
    {'name': 'ufw', 'label': 'UFW Firewall', 'icon': 'FW'},
    {'name': 'bind9', 'label': 'BIND9 (DNS)', 'icon': 'DNS'},
    {'name': 'proftpd', 'label': 'ProFTPD', 'icon': 'FTP'},
    {'name': 'vsftpd', 'label': 'vsftpd', 'icon': 'FTP'},
]
SERVICE_NAMES = {svc['name'] for svc in SERVICES}


def _systemctl(*args):
    out, _, rc = run_args(['systemctl', *args], timeout=15)
    return out.strip(), rc


@services_bp.route('/api/services')
def list_svcs():
    if not req():
        return jsonify({'ok': False}), 401
    result = []
    for svc in SERVICES:
        status, _ = _systemctl('is-active', svc['name'])
        if status:
            enabled, _ = _systemctl('is-enabled', svc['name'])
            result.append({**svc, 'status': status, 'enabled': enabled == 'enabled'})
    return jsonify({'ok': True, 'services': result})


@services_bp.route('/api/services/<name>/<action>', methods=['POST'])
def control(name, action):
    if not req():
        return jsonify({'ok': False}), 401
    if name not in SERVICE_NAMES:
        return jsonify({'ok': False, 'error': 'Invalid service'}), 400
    if action not in ('start', 'stop', 'restart', 'reload', 'enable', 'disable'):
        return jsonify({'ok': False, 'error': 'Invalid action'}), 400
    _, rc = _systemctl(action, name)
    status, _ = _systemctl('is-active', name)
    return jsonify({'ok': rc == 0, 'status': status})
