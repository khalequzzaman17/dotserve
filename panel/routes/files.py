from flask import Blueprint, jsonify, request, session
import base64
import fnmatch
import json
import mimetypes
import os
import shutil
import subprocess
import tarfile
import threading
import urllib.request
import zipfile

from panel.core.config import FILE_MANAGER_ROOTS, WEB_ROOTS
from panel.core.process import run_args
from panel.core.validation import (
    clean_filename,
    normalize_abs_path,
    path_blocked,
    valid_public_download_url,
    within_any_root,
)

files_bp = Blueprint('files', __name__)
MAX_EDIT_SIZE = 1024 * 1024
MAX_SEARCH_RESULTS = 100


def req():
    return 'user' in session


def get_webroot():
    for p in WEB_ROOTS:
        if os.path.isdir(p):
            return p
    os.makedirs(WEB_ROOTS[0], exist_ok=True)
    return WEB_ROOTS[0]


def safe_path(path, default='/'):
    real = normalize_abs_path(path, default)
    if path_blocked(real) or not within_any_root(real, FILE_MANAGER_ROOTS):
        raise ValueError('Path is outside allowed file manager roots')
    return real


def _json_error(message, status=400):
    return jsonify({'ok': False, 'error': message}), status


def _request_path(value, default='/'):
    try:
        return safe_path(value, default)
    except ValueError as e:
        return None, _json_error(str(e), 400)


def _dir_size(path):
    if os.path.isfile(path):
        return os.path.getsize(path)
    total = 0
    for root, dirs, files in os.walk(path):
        if path_blocked(root):
            dirs[:] = []
            continue
        for name in files:
            fp = os.path.join(root, name)
            try:
                total += os.path.getsize(fp)
            except OSError:
                pass
    return total


def _is_within_directory(base, target):
    base = os.path.realpath(base)
    target = os.path.realpath(target)
    return target == base or target.startswith(base + os.sep)


def _safe_zip_extract(src, dst):
    with zipfile.ZipFile(src) as zf:
        for member in zf.infolist():
            target = os.path.join(dst, member.filename)
            if not _is_within_directory(dst, target):
                raise ValueError('Archive contains unsafe paths')
        zf.extractall(dst)


def _safe_tar_extract(src, dst, mode='r:*'):
    with tarfile.open(src, mode) as tf:
        for member in tf.getmembers():
            target = os.path.join(dst, member.name)
            if not _is_within_directory(dst, target):
                raise ValueError('Archive contains unsafe paths')
        tf.extractall(dst)


@files_bp.route('/api/files/list')
def list_files():
    if not req():
        return jsonify({'ok': False}), 401
    path, err = _request_path(request.args.get('path', get_webroot()), get_webroot())
    if err:
        return err
    if not os.path.isdir(path):
        return _json_error('Not a directory', 400)
    items = []
    try:
        for name in sorted(os.listdir(path)):
            fp = os.path.join(path, name)
            if path_blocked(fp):
                continue
            st = os.stat(fp)
            items.append({
                'name': name,
                'path': fp,
                'type': 'dir' if os.path.isdir(fp) else 'file',
                'size': st.st_size,
                'mtime': int(st.st_mtime),
                'perms': oct(st.st_mode)[-3:],
            })
    except PermissionError:
        return _json_error('Permission denied', 403)
    return jsonify({'ok': True, 'path': path, 'items': items})


@files_bp.route('/api/files/read')
def read_file():
    if not req():
        return jsonify({'ok': False}), 401
    path, err = _request_path(request.args.get('path', ''))
    if err:
        return err
    if not os.path.isfile(path):
        return _json_error('Not a file', 404)
    if os.path.getsize(path) > MAX_EDIT_SIZE:
        return _json_error('File too large to edit (max 1MB)', 400)
    try:
        with open(path, 'r', errors='replace') as f:
            return jsonify({'ok': True, 'content': f.read(), 'path': path})
    except Exception as e:
        return _json_error(str(e), 500)


@files_bp.route('/api/files/write', methods=['POST'])
def write_file():
    if not req():
        return jsonify({'ok': False}), 401
    d = request.get_json() or {}
    path, err = _request_path(d.get('path', ''))
    if err:
        return err
    content = d.get('content', '')
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
        return jsonify({'ok': True})
    except Exception as e:
        return _json_error(str(e), 500)


@files_bp.route('/api/files/delete', methods=['POST'])
def delete_file():
    if not req():
        return jsonify({'ok': False}), 401
    path, err = _request_path((request.get_json() or {}).get('path', ''))
    if err:
        return err
    try:
        if os.path.isdir(path):
            shutil.rmtree(path)
        else:
            os.unlink(path)
        return jsonify({'ok': True})
    except Exception as e:
        return _json_error(str(e), 500)


@files_bp.route('/api/files/mkdir', methods=['POST'])
def make_dir():
    if not req():
        return jsonify({'ok': False}), 401
    path, err = _request_path((request.get_json() or {}).get('path', ''))
    if err:
        return err
    os.makedirs(path, exist_ok=True)
    return jsonify({'ok': True})


@files_bp.route('/api/files/rename', methods=['POST'])
def rename_file():
    if not req():
        return jsonify({'ok': False}), 401
    d = request.get_json() or {}
    src, err = _request_path(d.get('src', ''))
    if err:
        return err
    dst, err = _request_path(d.get('dst', ''))
    if err:
        return err
    try:
        shutil.move(src, dst)
        return jsonify({'ok': True})
    except Exception as e:
        return _json_error(str(e), 500)


@files_bp.route('/api/files/chmod', methods=['POST'])
def chmod_file():
    if not req():
        return jsonify({'ok': False}), 401
    d = request.get_json() or {}
    path, err = _request_path(d.get('path', ''))
    if err:
        return err
    try:
        mode = int(str(d.get('mode', '755')), 8)
        if mode & 0o002:
            return _json_error('World-writable permissions are not allowed', 400)
        os.chmod(path, mode)
        return jsonify({'ok': True})
    except Exception as e:
        return _json_error(str(e), 400)


@files_bp.route('/api/files/upload', methods=['POST'])
def upload_file():
    if not req():
        return jsonify({'ok': False}), 401
    path, err = _request_path(request.form.get('path', get_webroot()), get_webroot())
    if err:
        return err
    f = request.files.get('file')
    if not f:
        return _json_error('No file', 400)
    dest = os.path.join(path, clean_filename(f.filename))
    if not _is_within_directory(path, dest):
        return _json_error('Invalid filename', 400)
    f.save(dest)
    return jsonify({'ok': True, 'path': dest})


@files_bp.route('/api/files/copy', methods=['POST'])
def copy_file():
    if not req():
        return jsonify({'ok': False}), 401
    d = request.get_json() or {}
    src, err = _request_path(d.get('src', ''))
    if err:
        return err
    dst, err = _request_path(d.get('dst', ''))
    if err:
        return err
    try:
        if os.path.isdir(src):
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)
        return jsonify({'ok': True})
    except Exception as e:
        return _json_error(str(e), 500)


@files_bp.route('/api/files/move', methods=['POST'])
def move_file():
    return rename_file()


@files_bp.route('/api/files/compress', methods=['POST'])
def compress_file():
    if not req():
        return jsonify({'ok': False}), 401
    d = request.get_json() or {}
    try:
        paths = [safe_path(p) for p in d.get('paths', [])]
        output = safe_path(d.get('output', ''))
    except ValueError as e:
        return _json_error(str(e), 400)
    fmt = d.get('format', 'zip')
    if not paths or not output:
        return _json_error('paths and output required', 400)
    try:
        parent = os.path.dirname(paths[0])
        if fmt == 'zip':
            with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as zf:
                for path in paths:
                    arc_root = os.path.relpath(path, parent)
                    if os.path.isdir(path):
                        for root, _, files in os.walk(path):
                            for name in files:
                                fp = os.path.join(root, name)
                                zf.write(fp, os.path.join(arc_root, os.path.relpath(fp, path)))
                    else:
                        zf.write(path, arc_root)
        else:
            with tarfile.open(output, 'w:gz') as tf:
                for path in paths:
                    tf.add(path, arcname=os.path.relpath(path, parent))
        return jsonify({'ok': True, 'output': output})
    except Exception as e:
        return _json_error(str(e), 500)


@files_bp.route('/api/files/extract', methods=['POST'])
def extract_file():
    if not req():
        return jsonify({'ok': False}), 401
    d = request.get_json() or {}
    src, err = _request_path(d.get('path', ''))
    if err:
        return err
    dst, err = _request_path(d.get('dest', os.path.dirname(src)))
    if err:
        return err
    os.makedirs(dst, exist_ok=True)
    try:
        lower = src.lower()
        if lower.endswith('.zip'):
            _safe_zip_extract(src, dst)
        elif lower.endswith(('.tar.gz', '.tgz', '.tar.bz2', '.tar.xz', '.tar')):
            _safe_tar_extract(src, dst)
        elif lower.endswith(('.7z', '.rar')):
            out, err_msg, rc = run_args(['7z', 'x', src, f'-o{dst}', '-y'], timeout=300)
            if rc != 0:
                return _json_error((err_msg or out)[:300], 400)
        else:
            return _json_error('Unsupported archive type', 400)
        return jsonify({'ok': True, 'error': ''})
    except Exception as e:
        return _json_error(str(e), 500)


@files_bp.route('/api/files/search')
def search_files():
    if not req():
        return jsonify({'ok': False}), 401
    path, err = _request_path(request.args.get('path', get_webroot()), get_webroot())
    if err:
        return err
    keyword = request.args.get('q', '').strip()
    in_file = request.args.get('content', 'false') == 'true'
    if not keyword:
        return jsonify({'ok': True, 'results': []})
    results = []
    needle = keyword.lower()
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if not path_blocked(os.path.join(root, d))]
        if in_file:
            for name in files:
                fp = os.path.join(root, name)
                try:
                    with open(fp, 'r', errors='ignore') as f:
                        if needle in f.read(128 * 1024).lower():
                            results.append({'path': fp, 'type': 'file', 'name': name})
                except Exception:
                    pass
                if len(results) >= 50:
                    return jsonify({'ok': True, 'results': results})
        else:
            for name in dirs + files:
                fp = os.path.join(root, name)
                if fnmatch.fnmatch(name.lower(), f'*{needle}*'):
                    results.append({'path': fp, 'type': 'dir' if os.path.isdir(fp) else 'file', 'name': name})
                if len(results) >= MAX_SEARCH_RESULTS:
                    return jsonify({'ok': True, 'results': results})
    return jsonify({'ok': True, 'results': results})


@files_bp.route('/api/files/size')
def calc_size():
    if not req():
        return jsonify({'ok': False}), 401
    path, err = _request_path(request.args.get('path', ''))
    if err:
        return err
    try:
        return jsonify({'ok': True, 'size': _dir_size(path)})
    except Exception:
        return jsonify({'ok': True, 'size': 0})


@files_bp.route('/api/files/remote-download', methods=['POST'])
def remote_download():
    if not req():
        return jsonify({'ok': False}), 401
    d = request.get_json() or {}
    url = d.get('url', '').strip()
    dest, err = _request_path(d.get('dest', get_webroot()), get_webroot())
    if err:
        return err
    if not valid_public_download_url(url):
        return _json_error('Only public http(s) URLs are allowed', 400)
    fname = clean_filename(url.split('/')[-1].split('?')[0], 'download')
    fpath = os.path.join(dest, fname)

    def do_dl():
        try:
            req_obj = urllib.request.Request(url, headers={'User-Agent': 'DotServe/1.0'})
            with urllib.request.urlopen(req_obj, timeout=30) as resp, open(fpath, 'wb') as out:
                shutil.copyfileobj(resp, out, length=1024 * 1024)
        except Exception:
            pass

    threading.Thread(target=do_dl, daemon=True).start()
    return jsonify({'ok': True, 'filename': fname, 'path': fpath, 'message': 'Download started in background'})


@files_bp.route('/api/files/properties')
def file_properties():
    if not req():
        return jsonify({'ok': False}), 401
    path, err = _request_path(request.args.get('path', ''))
    if err:
        return err
    if not os.path.exists(path):
        return _json_error('Not found', 404)
    st = os.stat(path)
    import time as t
    return jsonify({'ok': True, 'props': {
        'path': path,
        'name': os.path.basename(path),
        'type': 'directory' if os.path.isdir(path) else 'file',
        'size': _dir_size(path),
        'perms': oct(st.st_mode)[-3:],
        'owner': str(getattr(st, 'st_uid', '')),
        'group': str(getattr(st, 'st_gid', '')),
        'mtime': t.strftime('%Y-%m-%d %H:%M:%S', t.localtime(st.st_mtime)),
        'atime': t.strftime('%Y-%m-%d %H:%M:%S', t.localtime(st.st_atime)),
    }})


@files_bp.route('/api/files/lint', methods=['POST'])
def lint_file():
    if not req():
        return jsonify({'ok': False}), 401
    d = request.get_json() or {}
    path, err = _request_path(d.get('path', ''))
    if err:
        return err
    ext = os.path.splitext(path)[1].lower()
    errors = []
    try:
        if ext == '.php':
            out, err_msg, rc = run_args(['php', '-l', path], timeout=10)
            if rc != 0:
                errors.append((out or err_msg).strip())
        elif ext == '.py':
            out, err_msg, rc = run_args(['python', '-m', 'py_compile', path], timeout=10)
            if rc != 0:
                errors.append((err_msg or out).strip())
        elif ext == '.json':
            with open(path, encoding='utf-8') as f:
                json.load(f)
    except Exception as e:
        errors.append(str(e))
    return jsonify({'ok': True, 'errors': errors, 'clean': len(errors) == 0})


@files_bp.route('/api/files/scan', methods=['POST'])
def scan_file():
    if not req():
        return jsonify({'ok': False}), 401
    d = request.get_json() or {}
    path, err = _request_path(d.get('path', ''))
    if err:
        return err
    if not os.path.exists(path):
        return _json_error('Path not found', 404)
    socket = '/var/run/clamav/clamd.sock'
    if os.path.exists(socket):
        args = ['clamdscan', '--config-file=/usr/local/etc/clamav/clamd.conf', '--no-summary', path]
    else:
        args = ['clamscan', '--database=/var/lib/clamav', '--recursive', path]
    out, err_msg, rc = run_args(args, timeout=300)
    output = (out + '\n' + err_msg).strip()
    infected = []
    for line in output.splitlines():
        if 'FOUND' in line:
            parts = line.rsplit(':', 1)
            if len(parts) == 2:
                infected.append({'file': parts[0].strip(), 'virus': parts[1].replace('FOUND', '').strip()})
    return jsonify({'ok': True, 'clean': rc == 0, 'infected': infected, 'output': output, 'path': path})

