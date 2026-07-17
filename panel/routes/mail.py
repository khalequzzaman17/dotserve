from collections import deque
from flask import Blueprint, jsonify, request, session
import os
import re

from panel.core.process import run_args
from panel.core.validation import valid_domain


mail_bp = Blueprint('mail', __name__)
MAIL_USERS_FILE = '/opt/dotserve/mail_users.txt'
VIRTUAL_ALIAS_FILE = '/etc/postfix/virtual_alias_maps'
VIRTUAL_MAILBOX_MAPS = '/etc/postfix/virtual_mailbox_maps'
VIRTUAL_DOMAINS_FILE = '/etc/postfix/virtual_mailbox_domains'
EMAIL_RE = re.compile(r'^[A-Za-z0-9._%+\-]{1,64}@([A-Za-z0-9-]+\.)+[A-Za-z]{2,63}$')


def req():
    return 'user' in session


def _email(value):
    value = (value or '').strip().lower()
    return value if EMAIL_RE.fullmatch(value) else ''


def _service_status(service):
    out, _, _ = run_args(['systemctl', 'is-active', service], timeout=5)
    return out.strip()


def _postmap(path):
    if os.path.exists(path):
        run_args(['postmap', path], timeout=20)


def _reload(*services):
    if services:
        run_args(['systemctl', 'reload', *services], timeout=20)


def _tail(path, lines=200):
    try:
        with open(path, errors='ignore') as fh:
            return ''.join(deque(fh, maxlen=lines))
    except Exception:
        return ''


def _queue_count(raw):
    match = re.search(r'(\d+)\s+Request', raw or '')
    return int(match.group(1)) if match else 0


def _doveadm_hash(password):
    out, _, rc = run_args(['doveadm', 'pw', '-s', 'SHA512-CRYPT', '-p', password], timeout=20)
    return out.strip() if rc == 0 else ''


@mail_bp.route('/api/mail/status')
def mail_status():
    if not req():
        return jsonify({'ok': False}), 401
    queue, _, _ = run_args(['mailq'], timeout=20)
    return jsonify({'ok': True, 'postfix': _service_status('postfix'),
                    'dovecot': _service_status('dovecot'), 'queue': _queue_count(queue)})


@mail_bp.route('/api/mail/domains')
def mail_domains():
    if not req():
        return jsonify({'ok': False}), 401
    domains = []
    if os.path.exists(VIRTUAL_DOMAINS_FILE):
        with open(VIRTUAL_DOMAINS_FILE) as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith('#'):
                    domain = line.split()[0].lower()
                    if valid_domain(domain):
                        domains.append(domain)
    return jsonify({'ok': True, 'domains': domains})


@mail_bp.route('/api/mail/domains', methods=['POST'])
def add_domain():
    if not req():
        return jsonify({'ok': False}), 401
    domain = (request.get_json() or {}).get('domain', '').strip().lower()
    if not valid_domain(domain):
        return jsonify({'ok': False, 'error': 'Valid domain required'}), 400
    os.makedirs(os.path.dirname(VIRTUAL_DOMAINS_FILE), exist_ok=True)
    existing = set()
    if os.path.exists(VIRTUAL_DOMAINS_FILE):
        with open(VIRTUAL_DOMAINS_FILE) as fh:
            existing = {line.split()[0].lower() for line in fh if line.strip() and not line.startswith('#')}
    if domain not in existing:
        with open(VIRTUAL_DOMAINS_FILE, 'a') as fh:
            fh.write(f'{domain} OK\n')
        _postmap(VIRTUAL_DOMAINS_FILE)
        _reload('postfix')
    return jsonify({'ok': True})


@mail_bp.route('/api/mail/accounts')
def mail_accounts():
    if not req():
        return jsonify({'ok': False}), 401
    domain_filter = request.args.get('domain', '').strip().lower()
    if domain_filter and not valid_domain(domain_filter):
        return jsonify({'ok': False, 'error': 'Invalid domain'}), 400
    accounts = []
    seen = set()
    for path in [VIRTUAL_MAILBOX_MAPS, MAIL_USERS_FILE]:
        if not os.path.exists(path):
            continue
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith('#') or '@' not in line:
                    continue
                email = _email(line.split(':')[0].split()[0])
                if not email or email in seen:
                    continue
                if domain_filter and not email.endswith('@' + domain_filter):
                    continue
                seen.add(email)
                accounts.append({'email': email})
    return jsonify({'ok': True, 'accounts': accounts})


@mail_bp.route('/api/mail/accounts', methods=['POST'])
def create_account():
    if not req():
        return jsonify({'ok': False}), 401
    data = request.get_json() or {}
    email = _email(data.get('email', ''))
    password = data.get('password', '')
    if not email:
        return jsonify({'ok': False, 'error': 'Valid email required'}), 400
    if not password:
        return jsonify({'ok': False, 'error': 'Password required'}), 400
    user, domain = email.split('@', 1)
    for sub in ('cur', 'new', 'tmp'):
        os.makedirs(os.path.join('/var/mail/vhosts', domain, user, sub), exist_ok=True)
    run_args(['chown', '-R', 'vmail:vmail', '/var/mail/vhosts'], timeout=60)

    if os.path.exists(VIRTUAL_MAILBOX_MAPS):
        with open(VIRTUAL_MAILBOX_MAPS, 'a') as fh:
            fh.write(f'{email} {domain}/{user}/\n')
        _postmap(VIRTUAL_MAILBOX_MAPS)

    pw_hash = _doveadm_hash(password)
    if not pw_hash:
        return jsonify({'ok': False, 'error': 'Failed to hash password'}), 500
    os.makedirs(os.path.dirname(MAIL_USERS_FILE), exist_ok=True)
    with open(MAIL_USERS_FILE, 'a') as fh:
        fh.write(f'{email}:{pw_hash}\n')
    _reload('postfix', 'dovecot')
    return jsonify({'ok': True, 'email': email})


@mail_bp.route('/api/mail/accounts/<path:email>', methods=['DELETE'])
def delete_account(email):
    if not req():
        return jsonify({'ok': False}), 401
    email = _email(email)
    if not email:
        return jsonify({'ok': False, 'error': 'Invalid email'}), 400
    for path in [VIRTUAL_MAILBOX_MAPS, MAIL_USERS_FILE]:
        if not os.path.exists(path):
            continue
        with open(path) as fh:
            lines = fh.readlines()
        with open(path, 'w') as fh:
            fh.writelines(line for line in lines if not line.startswith(email + ' ') and not line.startswith(email + ':'))
        if path == VIRTUAL_MAILBOX_MAPS:
            _postmap(path)
    _reload('postfix', 'dovecot')
    return jsonify({'ok': True})


@mail_bp.route('/api/mail/accounts/<path:email>/password', methods=['PUT'])
def reset_mail_password(email):
    if not req():
        return jsonify({'ok': False}), 401
    email = _email(email)
    password = (request.get_json() or {}).get('password', '')
    if not email:
        return jsonify({'ok': False, 'error': 'Invalid email'}), 400
    if not password:
        return jsonify({'ok': False, 'error': 'Password required'}), 400
    pw_hash = _doveadm_hash(password)
    if not pw_hash:
        return jsonify({'ok': False, 'error': 'Failed to hash password'}), 500
    os.makedirs(os.path.dirname(MAIL_USERS_FILE), exist_ok=True)
    lines = []
    updated = False
    if os.path.exists(MAIL_USERS_FILE):
        with open(MAIL_USERS_FILE) as fh:
            lines = fh.readlines()
    with open(MAIL_USERS_FILE, 'w') as fh:
        for line in lines:
            if line.startswith(email + ':'):
                fh.write(f'{email}:{pw_hash}\n')
                updated = True
            else:
                fh.write(line)
        if not updated:
            fh.write(f'{email}:{pw_hash}\n')
    _reload('dovecot')
    return jsonify({'ok': True})


@mail_bp.route('/api/mail/queue')
def mail_queue():
    if not req():
        return jsonify({'ok': False}), 401
    out, err, _ = run_args(['mailq'], timeout=20)
    return jsonify({'ok': True, 'output': out or err})


@mail_bp.route('/api/mail/queue/flush', methods=['POST'])
def flush_queue():
    if not req():
        return jsonify({'ok': False}), 401
    run_args(['postqueue', '-f'], timeout=20)
    return jsonify({'ok': True})


@mail_bp.route('/api/mail/dkim/<domain>')
def get_dkim(domain):
    if not req():
        return jsonify({'ok': False}), 401
    domain = domain.strip().lower()
    if not valid_domain(domain):
        return jsonify({'ok': False, 'error': 'Invalid domain'}), 400
    key_file = os.path.join('/etc/opendkim/keys', domain, 'default.txt')
    if os.path.exists(key_file):
        with open(key_file) as fh:
            return jsonify({'ok': True, 'record': fh.read()})
    return jsonify({'ok': False, 'error': 'DKIM key not generated yet'})


@mail_bp.route('/api/mail/dkim/<domain>', methods=['POST'])
def gen_dkim(domain):
    if not req():
        return jsonify({'ok': False}), 401
    domain = domain.strip().lower()
    if not valid_domain(domain):
        return jsonify({'ok': False, 'error': 'Invalid domain'}), 400
    key_dir = os.path.join('/etc/opendkim/keys', domain)
    os.makedirs(key_dir, exist_ok=True)
    run_args(['opendkim-genkey', '-t', '-s', 'default', '-d', domain, '-D', key_dir], timeout=30)
    key_file = os.path.join(key_dir, 'default.txt')
    if os.path.exists(key_file):
        with open(key_file) as fh:
            return jsonify({'ok': True, 'record': fh.read()})
    return jsonify({'ok': False, 'error': 'opendkim-genkey failed or not installed'})


@mail_bp.route('/api/mail/control', methods=['POST'])
def control_mail():
    if not req():
        return jsonify({'ok': False}), 401
    data = request.get_json() or {}
    service = data.get('service', 'postfix')
    action = data.get('action', 'restart')
    if action not in ('start', 'stop', 'restart', 'reload', 'status'):
        return jsonify({'ok': False, 'error': 'Invalid action'}), 400
    svc = {'postfix': 'postfix', 'dovecot': 'dovecot', 'opendkim': 'opendkim'}.get(service)
    if not svc:
        return jsonify({'ok': False, 'error': 'Invalid service'}), 400
    if action != 'status':
        run_args(['systemctl', action, svc], timeout=30)
    return jsonify({'ok': True, 'status': _service_status(svc)})


@mail_bp.route('/api/mail/forwarding')
def list_forwarding():
    if not req():
        return jsonify({'ok': False}), 401
    domain = request.args.get('domain', '').strip().lower()
    if domain and not valid_domain(domain):
        return jsonify({'ok': False, 'error': 'Invalid domain'}), 400
    rules = []
    if os.path.exists(VIRTUAL_ALIAS_FILE):
        with open(VIRTUAL_ALIAS_FILE) as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                parts = line.split(None, 1)
                if len(parts) != 2:
                    continue
                source, dest = parts
                if domain and not source.endswith('@' + domain):
                    continue
                rules.append({'source': source, 'destination': dest})
    return jsonify({'ok': True, 'rules': rules})


@mail_bp.route('/api/mail/forwarding', methods=['POST'])
def add_forwarding():
    if not req():
        return jsonify({'ok': False}), 401
    data = request.get_json() or {}
    source = _email(data.get('source', ''))
    dest = _email(data.get('destination', ''))
    if not source or not dest:
        return jsonify({'ok': False, 'error': 'Valid source and destination email addresses required'}), 400
    lines = []
    if os.path.exists(VIRTUAL_ALIAS_FILE):
        with open(VIRTUAL_ALIAS_FILE) as fh:
            lines = fh.readlines()
    lines = [line for line in lines if not line.strip().startswith(source + ' ') and not line.strip().startswith(source + '\t')]
    lines.append(f'{source}\t{dest}\n')
    with open(VIRTUAL_ALIAS_FILE, 'w') as fh:
        fh.writelines(lines)
    _postmap(VIRTUAL_ALIAS_FILE)
    _reload('postfix')
    return jsonify({'ok': True})


@mail_bp.route('/api/mail/forwarding', methods=['DELETE'])
def del_forwarding():
    if not req():
        return jsonify({'ok': False}), 401
    source = _email((request.get_json() or {}).get('source', ''))
    if not source:
        return jsonify({'ok': False, 'error': 'Valid source required'}), 400
    if os.path.exists(VIRTUAL_ALIAS_FILE):
        with open(VIRTUAL_ALIAS_FILE) as fh:
            lines = fh.readlines()
        lines = [line for line in lines if not line.strip().startswith(source + ' ') and not line.strip().startswith(source + '\t')]
        with open(VIRTUAL_ALIAS_FILE, 'w') as fh:
            fh.writelines(lines)
        _postmap(VIRTUAL_ALIAS_FILE)
        _reload('postfix')
    return jsonify({'ok': True})


@mail_bp.route('/api/mail/logs')
def mail_logs():
    if not req():
        return jsonify({'ok': False}), 401
    which = request.args.get('which', 'mail')
    try:
        lines = max(50, min(1000, int(request.args.get('lines', 200))))
    except Exception:
        lines = 200
    path = next((p for p in ['/var/log/mail.log', '/var/log/maillog'] if os.path.exists(p)), None)
    if path:
        out = _tail(path, lines)
        if which == 'postfix':
            out = '\n'.join(line for line in out.splitlines() if 'postfix' in line.lower())
        elif which == 'dovecot':
            out = '\n'.join(line for line in out.splitlines() if 'dovecot' in line.lower())
        return jsonify({'ok': True, 'lines': out or 'No log entries found', 'source': path})

    svc = 'postfix' if which == 'postfix' else 'dovecot' if which == 'dovecot' else ''
    args = ['journalctl']
    if svc:
        args += ['-u', svc]
    args += ['-n', str(lines), '--no-pager']
    out, err, _ = run_args(args, timeout=20)
    if not svc:
        out = '\n'.join(line for line in out.splitlines()
                        if any(token in line.lower() for token in ('postfix', 'dovecot', 'smtp', 'imap')))
    return jsonify({'ok': True, 'lines': out or err or 'No log entries found (journalctl fallback)',
                    'source': 'journalctl'})
