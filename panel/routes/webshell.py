from flask import Blueprint, jsonify, request, session
import os, re, shutil, time

webshell_bp = Blueprint('webshell', __name__)

QUARANTINE_DIR = '/opt/dotserve/quarantine'
PATTERNS = [
    (re.compile(r'\beval\s*\(\s*\$_(POST|GET|REQUEST|COOKIE)', re.I), 'Direct eval of request data'),
    (re.compile(r'base64_decode\s*\(', re.I), 'base64 decode usage'),
    (re.compile(r'(gzinflate|str_rot13|shell_exec|passthru|proc_open|popen)\s*\(', re.I), 'High-risk PHP function'),
    (re.compile(r'assert\s*\(\s*\$_(POST|GET|REQUEST|COOKIE)', re.I), 'Assert on request data'),
    (re.compile(r'preg_replace\s*\(.+\/e[\'"]', re.I | re.S), 'Deprecated preg_replace /e execution'),
]


def req():
    return 'user' in session


def _webroot():
    for path in ['/www/wwwroot', '/var/www/html', '/var/www']:
        if os.path.isdir(path):
            return path
    return '/www/wwwroot'


def _safe_web_path(path):
    root = os.path.realpath(_webroot())
    real = os.path.realpath(path or root)
    if real == root or real.startswith(root + os.sep):
        return real
    return None


@webshell_bp.route('/api/webshell/scan', methods=['POST'])
def scan_webshells():
    if not req():
        return jsonify({'ok': False}), 401
    d = request.get_json() or {}
    root = _safe_web_path(d.get('path') or _webroot())
    if not root:
        return jsonify({'ok': False, 'error': 'Path is outside the web root'}), 400
    max_files = max(100, min(50000, int(d.get('max_files', 10000))))
    findings = []
    scanned = 0
    for cur, _, files in os.walk(root):
        for name in files:
            if scanned >= max_files:
                break
            if not name.lower().endswith(('.php', '.phtml', '.php5', '.inc')):
                continue
            fp = os.path.join(cur, name)
            scanned += 1
            try:
                with open(fp, errors='ignore') as f:
                    content = f.read(512000)
                hits = [label for pattern, label in PATTERNS if pattern.search(content)]
                if hits:
                    findings.append({
                        'path': fp,
                        'size': os.path.getsize(fp),
                        'mtime': int(os.path.getmtime(fp)),
                        'severity': 'high' if any('request data' in h.lower() for h in hits) else 'medium',
                        'reasons': hits,
                    })
            except Exception:
                pass
    findings.sort(key=lambda x: (x['severity'] != 'high', -x['mtime']))
    return jsonify({'ok': True, 'root': root, 'scanned': scanned, 'findings': findings})


@webshell_bp.route('/api/webshell/quarantine', methods=['POST'])
def quarantine_file():
    if not req():
        return jsonify({'ok': False}), 401
    path = _safe_web_path((request.get_json() or {}).get('path'))
    if not path or not os.path.isfile(path):
        return jsonify({'ok': False, 'error': 'Invalid web-root file'}), 400
    os.makedirs(QUARANTINE_DIR, exist_ok=True)
    dest = os.path.join(QUARANTINE_DIR, f'{int(time.time())}_{os.path.basename(path)}')
    shutil.move(path, dest)
    return jsonify({'ok': True, 'quarantine_path': dest})


@webshell_bp.route('/api/webshell/delete', methods=['POST'])
def delete_webshell():
    if not req():
        return jsonify({'ok': False}), 401
    path = _safe_web_path((request.get_json() or {}).get('path'))
    if not path or not os.path.isfile(path):
        return jsonify({'ok': False, 'error': 'Invalid web-root file'}), 400
    os.remove(path)
    return jsonify({'ok': True})

