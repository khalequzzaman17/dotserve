from flask import Blueprint, jsonify, request, session
import os
import re
import shutil
import subprocess

from panel.core.process import run_args


ftp_bp = Blueprint('ftp', __name__)
USER_RE = re.compile(r'^[A-Za-z0-9_-]{1,32}$')


def req():
    return 'user' in session


def _safe_user(value):
    value = (value or '').strip()
    return value if USER_RE.fullmatch(value) else ''


def _safe_home(value, user):
    home = os.path.abspath(value or f'/www/wwwroot/{user}')
    allowed_roots = ['/www/wwwroot', '/var/www', '/home']
    if any(home == root or home.startswith(root + os.sep) for root in allowed_roots):
        return home
    return ''


def get_ftp_daemon():
    for daemon in ['pure-ftpd', 'proftpd', 'vsftpd']:
        if shutil.which(daemon):
            out, _, _ = run_args(['systemctl', 'is-active', daemon], timeout=5)
            return daemon, out.strip() or 'inactive'
    return None, 'none'


def is_ftp_installed():
    daemon, _ = get_ftp_daemon()
    return daemon is not None


def _list_system_ftp_accounts(seen):
    accounts = []
    try:
        with open('/etc/passwd') as fh:
            for line in fh:
                parts = line.rstrip('\n').split(':')
                if len(parts) < 7:
                    continue
                user, home, shell = parts[0], parts[5], parts[6]
                if user not in seen and ('nologin' in shell or shell.endswith('/false')) and '/www' in home:
                    seen.add(user)
                    accounts.append({'user': user, 'home': home})
    except Exception:
        pass
    return accounts


def _list_ftp_accounts():
    accounts = []
    seen = set()
    for path in ['/etc/pure-ftpd/pureftpd.passwd', '/etc/pureftpd.passwd', '/etc/proftpd/ftpd.passwd']:
        if not os.path.exists(path):
            continue
        with open(path) as fh:
            for line in fh:
                parts = line.strip().split(':')
                if len(parts) >= 6 and parts[0] not in seen:
                    seen.add(parts[0])
                    accounts.append({'user': parts[0], 'home': parts[5]})
    if not accounts:
        accounts.extend(_list_system_ftp_accounts(seen))
    return accounts


def _pure_pw(args, password=None):
    stdin = None
    if password is not None:
        stdin = f'{password}\n{password}\n'
    return subprocess.run(['pure-pw', *args], input=stdin, capture_output=True,
                          text=True, timeout=30)


def _chpasswd(user, password):
    return subprocess.run(['chpasswd'], input=f'{user}:{password}\n',
                          capture_output=True, text=True, timeout=30)


@ftp_bp.route('/api/ftp/users')
def list_users_alias():
    return list_accounts()


@ftp_bp.route('/api/ftp/status')
def ftp_status():
    if not req():
        return jsonify({'ok': False}), 401
    daemon, status = get_ftp_daemon()
    return jsonify({'ok': True, 'installed': daemon is not None, 'daemon': daemon or 'none',
                    'status': status, 'accounts_count': len(_list_ftp_accounts())})


@ftp_bp.route('/api/ftp/accounts')
def list_accounts():
    if not req():
        return jsonify({'ok': False}), 401
    if not is_ftp_installed():
        return jsonify({'ok': False, 'installed': False, 'error': 'FTP daemon not installed'}), 200
    return jsonify({'ok': True, 'installed': True, 'accounts': _list_ftp_accounts()})


@ftp_bp.route('/api/ftp/accounts', methods=['POST'])
def create_account():
    if not req():
        return jsonify({'ok': False}), 401
    if not is_ftp_installed():
        return jsonify({'ok': False, 'error': 'Install Pure-FTPd or ProFTPD via Modules first'}), 400
    data = request.get_json() or {}
    user = _safe_user(data.get('user', ''))
    password = data.get('password', '')
    home = _safe_home(data.get('home'), user)
    if not user:
        return jsonify({'ok': False, 'error': 'Valid username required'}), 400
    if len(password) < 6:
        return jsonify({'ok': False, 'error': 'Password must be at least 6 characters'}), 400
    if not home:
        return jsonify({'ok': False, 'error': 'Home must be inside /www/wwwroot, /var/www, or /home'}), 400
    os.makedirs(home, exist_ok=True)
    daemon, _ = get_ftp_daemon()
    if daemon == 'pure-ftpd':
        run_args(['useradd', '-s', '/bin/false', '-d', home, user], timeout=20)
        result = _pure_pw(['useradd', user, '-u', user, '-d', home], password=password)
        run_args(['pure-pw', 'mkdb'], timeout=20)
        run_args(['systemctl', 'reload', 'pure-ftpd'], timeout=20)
        if result.returncode != 0:
            return jsonify({'ok': False, 'error': result.stderr.strip() or 'Failed to create Pure-FTPd user'}), 500
    else:
        run_args(['useradd', '-m', '-d', home, '-s', '/sbin/nologin', user], timeout=20)
        result = _chpasswd(user, password)
        if result.returncode != 0:
            return jsonify({'ok': False, 'error': result.stderr.strip() or 'Failed to set password'}), 500
    return jsonify({'ok': True, 'user': user, 'home': home})


@ftp_bp.route('/api/ftp/accounts/<user>', methods=['DELETE'])
def delete_account(user):
    if not req():
        return jsonify({'ok': False}), 401
    user = _safe_user(user)
    if not user:
        return jsonify({'ok': False, 'error': 'Invalid username'}), 400
    daemon, _ = get_ftp_daemon()
    if daemon == 'pure-ftpd':
        run_args(['pure-pw', 'userdel', user], timeout=20)
        run_args(['pure-pw', 'mkdb'], timeout=20)
    run_args(['userdel', user], timeout=20)
    return jsonify({'ok': True})


@ftp_bp.route('/api/ftp/accounts/<user>/password', methods=['PUT'])
def change_password(user):
    if not req():
        return jsonify({'ok': False}), 401
    user = _safe_user(user)
    password = (request.get_json() or {}).get('password', '')
    if not user:
        return jsonify({'ok': False, 'error': 'Invalid username'}), 400
    if len(password) < 6:
        return jsonify({'ok': False, 'error': 'Min 6 characters'}), 400
    daemon, _ = get_ftp_daemon()
    if daemon == 'pure-ftpd':
        result = _pure_pw(['passwd', user], password=password)
        run_args(['pure-pw', 'mkdb'], timeout=20)
    else:
        result = _chpasswd(user, password)
    if result.returncode != 0:
        return jsonify({'ok': False, 'error': result.stderr.strip() or 'Password update failed'}), 500
    return jsonify({'ok': True})
