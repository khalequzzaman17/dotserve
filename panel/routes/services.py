from flask import Blueprint, jsonify, session

from panel.core.process import run_args


services_bp = Blueprint('services', __name__)


def req():
    return 'user' in session


SERVICE_GROUPS = [
    {'name': 'webserver', 'label': 'Web Server', 'icon': 'WEB', 'aliases': ['lsws', 'nginx', 'apache2', 'httpd', 'caddy']},
    {'name': 'database', 'label': 'Database', 'icon': 'DB', 'aliases': ['mariadb', 'mysql', 'postgresql', 'mongod']},
    {'name': 'redis', 'label': 'Redis', 'icon': 'RD', 'aliases': ['redis', 'redis-server']},
    {'name': 'supervisor', 'label': 'Supervisor', 'icon': 'SUP', 'aliases': ['supervisord', 'supervisor']},
    {'name': 'firewall', 'label': 'Firewall', 'icon': 'FW', 'aliases': ['firewalld', 'ufw']},
    {'name': 'docker', 'label': 'Docker', 'icon': 'DK', 'aliases': ['docker']},
    {'name': 'php-fpm', 'label': 'PHP-FPM', 'icon': 'PHP', 'aliases': [
        'php8.5-fpm', 'php8.4-fpm', 'php8.3-fpm', 'php8.2-fpm', 'php8.1-fpm', 'php8.0-fpm', 'php7.4-fpm',
        'php85-php-fpm', 'php84-php-fpm', 'php83-php-fpm', 'php82-php-fpm', 'php81-php-fpm', 'php80-php-fpm', 'php74-php-fpm',
    ]},
    {'name': 'mail', 'label': 'Mail', 'icon': 'MX', 'aliases': ['postfix']},
    {'name': 'imap', 'label': 'IMAP', 'icon': 'IMAP', 'aliases': ['dovecot']},
    {'name': 'ftp', 'label': 'FTP', 'icon': 'FTP', 'aliases': ['pure-ftpd', 'proftpd', 'vsftpd']},
    {'name': 'dns', 'label': 'DNS', 'icon': 'DNS', 'aliases': ['named', 'bind9']},
    {'name': 'fail2ban', 'label': 'Fail2ban', 'icon': 'F2B', 'aliases': ['fail2ban']},
    {'name': 'memcached', 'label': 'Memcached', 'icon': 'MC', 'aliases': ['memcached']},
]
SERVICE_NAMES = {group['name'] for group in SERVICE_GROUPS}
for group in SERVICE_GROUPS:
    SERVICE_NAMES.update(group['aliases'])


def _systemctl(*args):
    out, _, rc = run_args(['systemctl', *args], timeout=15)
    return out.strip(), rc


def _unit_status(unit):
    status, _ = _systemctl('is-active', unit)
    status = status.strip().splitlines()[0] if status else 'unknown'
    enabled, _ = _systemctl('is-enabled', unit)
    enabled = enabled.strip().splitlines()[0] if enabled else 'disabled'
    known = status not in ('unknown', '') or enabled not in ('disabled', 'unknown', '')
    return {'unit': unit, 'status': status, 'enabled': enabled == 'enabled', 'known': known}


def _summarize_group(group):
    checked = [_unit_status(unit) for unit in group['aliases']]
    active = next((s for s in checked if s['status'] == 'active'), None)
    known = next((s for s in checked if s['known']), None)
    selected = active or known or checked[0]
    label = group['label']
    if group['name'] in ('webserver', 'database') and selected['known']:
        label = f"{group['label']} ({selected['unit']})"
    return {
        'name': group['name'],
        'unit': selected['unit'],
        'label': label,
        'icon': group['icon'],
        'status': selected['status'] if selected['status'] != 'unknown' else 'inactive',
        'enabled': selected['enabled'],
        'installed': selected['known'],
        'aliases': group['aliases'],
    }


def service_summary(include_unknown=False):
    services = [_summarize_group(group) for group in SERVICE_GROUPS]
    if not include_unknown:
        services = [svc for svc in services if svc['installed'] or svc['status'] == 'active']
    return services


def dashboard_service_summary():
    priority = ['webserver', 'database', 'redis', 'supervisor', 'firewall', 'docker']
    services = {svc['name']: svc for svc in service_summary(include_unknown=True)}
    return [services[name] for name in priority if name in services]


def resolve_service(name):
    for group in SERVICE_GROUPS:
        if name == group['name'] or name in group['aliases']:
            summary = _summarize_group(group)
            return summary['unit'], group['name']
    return None, None


@services_bp.route('/api/services')
def list_svcs():
    if not req():
        return jsonify({'ok': False}), 401
    return jsonify({'ok': True, 'services': service_summary(include_unknown=True)})


@services_bp.route('/api/services/<name>/<action>', methods=['POST'])
def control(name, action):
    if not req():
        return jsonify({'ok': False}), 401
    unit, group_name = resolve_service(name)
    if not unit or name not in SERVICE_NAMES:
        return jsonify({'ok': False, 'error': 'Invalid service'}), 400
    if action not in ('start', 'stop', 'restart', 'reload', 'enable', 'disable'):
        return jsonify({'ok': False, 'error': 'Invalid action'}), 400
    _, rc = _systemctl(action, unit)
    status, _ = _systemctl('is-active', unit)
    return jsonify({'ok': rc == 0, 'name': group_name, 'unit': unit, 'status': status})
