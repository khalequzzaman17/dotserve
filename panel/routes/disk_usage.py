from flask import Blueprint, jsonify, request, session
import hashlib, os, subprocess
from panel.core.validation import normalize_abs_path, within_any_root

disk_usage_bp = Blueprint('disk_usage', __name__)

SAFE_ROOTS = ['/www/wwwroot', '/var/www', '/home', '/opt/dotserve/backups']


def req():
    return 'user' in session


def _safe_path(path):
    real = normalize_abs_path(path or '/www/wwwroot')
    return real if within_any_root(real, SAFE_ROOTS) else None


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def _du(path, depth=1):
    cmd = ['du', '-B1', f'--max-depth={depth}', path]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    items = []
    for line in r.stdout.splitlines():
        size, _, item_path = line.partition('\t')
        if item_path and size.isdigit():
            items.append({'path': item_path, 'name': os.path.basename(item_path) or item_path, 'size': int(size)})
    items.sort(key=lambda x: x['size'], reverse=True)
    return items


@disk_usage_bp.route('/api/disk-usage/tree')
def disk_tree():
    if not req():
        return jsonify({'ok': False}), 401
    path = _safe_path(request.args.get('path') or '/www/wwwroot')
    if not path or not os.path.exists(path):
        return jsonify({'ok': False, 'error': 'Path is outside allowed web/storage roots'}), 400
    depth = max(1, min(3, int(request.args.get('depth', 1))))
    return jsonify({'ok': True, 'path': path, 'items': _du(path, depth)})


@disk_usage_bp.route('/api/disk-usage/duplicates')
def duplicates():
    if not req():
        return jsonify({'ok': False}), 401
    path = _safe_path(request.args.get('path') or '/www/wwwroot')
    if not path:
        return jsonify({'ok': False, 'error': 'Path is outside allowed web/storage roots'}), 400
    hashes = {}
    for root, _, files in os.walk(path):
        for name in files:
            fp = os.path.join(root, name)
            try:
                if os.path.getsize(fp) == 0:
                    continue
                digest = _sha256_file(fp)
                hashes.setdefault(digest, []).append(fp)
            except Exception:
                pass
    groups = [{'hash': h, 'files': f, 'size': os.path.getsize(f[0])} for h, f in hashes.items() if len(f) > 1]
    groups.sort(key=lambda x: x['size'] * len(x['files']), reverse=True)
    return jsonify({'ok': True, 'groups': groups[:50]})


@disk_usage_bp.route('/api/disk-usage/delete', methods=['POST'])
def delete_path():
    if not req():
        return jsonify({'ok': False}), 401
    path = _safe_path((request.get_json() or {}).get('path'))
    if not path or not os.path.exists(path):
        return jsonify({'ok': False, 'error': 'Path is outside allowed roots or does not exist'}), 400
    if os.path.isdir(path):
        return jsonify({'ok': False, 'error': 'Directory deletion is intentionally disabled from this endpoint'}), 400
    os.remove(path)
    return jsonify({'ok': True})
