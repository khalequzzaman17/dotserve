from flask import Blueprint, jsonify, request, session, send_file
import gzip
import glob
import json
import os
import re
import shutil
import subprocess
import tarfile
import threading
import time
import uuid

from panel.core.process import run_args
from panel.core.validation import valid_domain

backups_bp = Blueprint('backups', __name__)
def req(): return 'user' in session
BACKUP_DIR = '/opt/dotserve/backups'
SCHEDULE_FILE = '/opt/dotserve/backup_schedule.json'

DB_NAME_RE = re.compile(r'^[A-Za-z0-9_-]{1,64}$')


def _safe_name(value, fallback):
    value = re.sub(r'[^A-Za-z0-9_.-]', '_', value or '')
    return value[:120] or fallback


def _add_path_to_tar(tf, path, arc_prefix=''):
    path = os.path.abspath(path)
    if not os.path.exists(path):
        return
    arcname = os.path.join(arc_prefix, path.lstrip(os.sep)).replace('\\', '/')
    tf.add(path, arcname=arcname, recursive=True)


def _create_tar_gz(dest, paths):
    with tarfile.open(dest, 'w:gz') as tf:
        for path in paths:
            _add_path_to_tar(tf, path)


def _safe_extract_tar(archive, dest):
    root = os.path.abspath(dest)
    with tarfile.open(archive, 'r:gz') as tf:
        members = []
        for member in tf.getmembers():
            if member.issym() or member.islnk():
                raise ValueError(f'Archive links are not allowed: {member.name}')
            name = member.name
            parts = [p for p in name.replace('\\', '/').split('/') if p and p not in ('.', '..')]
            if len(parts) > 2 and parts[0] in ('www', 'var', 'opt', 'home', 'srv'):
                parts = parts[2:]
            safe_name = '/'.join(parts)
            target = os.path.abspath(os.path.join(root, safe_name))
            if target != root and not target.startswith(root + os.sep):
                raise ValueError(f'Unsafe archive path rejected: {member.name}')
            member.name = safe_name
            members.append(member)
        tf.extractall(root, members=members)


def _mysql_bin():
    return 'mariadb' if shutil.which('mariadb') else 'mysql'


def _mysqldump_bin():
    return 'mariadb-dump' if shutil.which('mariadb-dump') else 'mysqldump'


def _dump_mysql(dest, database=''):
    args = [_mysqldump_bin(), '-u', 'root', '--single-transaction', '--routines', '--triggers']
    if database:
        args.append(database)
    else:
        args.append('--all-databases')
    try:
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        with gzip.open(dest, 'wb') as gz:
            shutil.copyfileobj(proc.stdout, gz)
        err = proc.stderr.read().decode(errors='replace') if proc.stderr else ''
        rc = proc.wait(timeout=300)
        if rc != 0:
            return err or 'mysqldump failed', rc
        return '', 0
    except Exception as e:
        return str(e), 1

def get_webroot():
    for p in ['/www/wwwroot', '/var/www/html', '/var/www']:
        if os.path.isdir(p): return p
    return '/www/wwwroot'

def mysql_available():
    if not shutil.which(_mysql_bin()):
        return False
    _, _, rc = run_args([_mysql_bin(), '-u', 'root', '-e', 'SELECT 1;'], timeout=5)
    return rc == 0

def get_databases():
    dbs = []
    bin_name = _mysql_bin()
    for svc in ['mariadb','mysql']:
        out, _, rc = run_args(['systemctl', 'is-active', svc], timeout=3)
        if rc == 0 and out.strip() == 'active':
            out2, _, rc2 = run_args([bin_name, '-u', 'root', '-e', 'SHOW DATABASES;'], timeout=10)
            if rc2 == 0:
                skip = {'information_schema','performance_schema','mysql','sys','Database'}
                dbs += [d.strip() for d in out2.split('\n') if d.strip() and d.strip() not in skip]
            break
    out, _, rc = run_args(['systemctl', 'is-active', 'postgresql'], timeout=3)
    if rc == 0 and out.strip() == 'active':
        out2, _, rc2 = run_args(['sudo', '-u', 'postgres', 'psql', '-t', '-c',
                                 'SELECT datname FROM pg_database WHERE datistemplate=false;'], timeout=10)
        if rc2 == 0:
            dbs += [d.strip() for d in out2.split('\n') if d.strip() and d.strip() != 'postgres']
    return list(set(dbs))

def get_websites():
    sites = []
    import re as _re
    for conf_dir in ['/etc/nginx/dotserve', '/etc/nginx/sites-available', '/etc/nginx/conf.d']:
        if not os.path.isdir(conf_dir): continue
        for f in os.listdir(conf_dir):
            fp = os.path.join(conf_dir, f)
            if not os.path.isfile(fp): continue
            try:
                with open(fp) as fh: c = fh.read()
                domains = _re.findall(r'server_name\s+([^;]+);', c)
                if not domains: continue
                domain = domains[0].strip().split()[0]
                if domain in ('_', 'localhost', 'default'): continue
                path_m = _re.search(r'root\s+([^;]+);', c)
                path = path_m.group(1).strip() if path_m else get_webroot()+'/'+domain
                if os.path.isdir(path) and not any(s['domain']==domain for s in sites):
                    sites.append({'domain': domain, 'path': path})
            except: pass
    return sites

def _load_schedule():
    try:
        with open(SCHEDULE_FILE) as f:
            return json.load(f)
    except Exception:
        return {'enabled': False, 'frequency': 'daily', 'time': '02:00',
                'type': 'full', 'domain': '', 'database': '', 'cloud_upload': True}

def _save_schedule(cfg):
    os.makedirs(os.path.dirname(SCHEDULE_FILE), exist_ok=True)
    with open(SCHEDULE_FILE, 'w') as f:
        json.dump(cfg, f, indent=2)

# Job tracking for backup progress
_jobs = {}

@backups_bp.route('/api/backups')
def list_backups():
    if not req(): return jsonify({'ok':False}), 401
    os.makedirs(BACKUP_DIR, exist_ok=True)
    files = []
    for f in sorted(glob.glob(f'{BACKUP_DIR}/*.tar.gz') +
                    glob.glob(f'{BACKUP_DIR}/*.sql.gz') +
                    glob.glob(f'{BACKUP_DIR}/*.zip'), reverse=True):
        st    = os.stat(f)
        name  = os.path.basename(f)
        # Parse metadata from name: type_domain_timestamp.ext
        parts = name.split('_')
        btype = parts[0] if parts else 'unknown'
        files.append({
            'name':  name,
            'size':  st.st_size,
            'mtime': int(st.st_mtime),
            'path':  f,
            'type':  btype,
        })
    return jsonify({'ok':True, 'backups':files})

@backups_bp.route('/api/backups/info')
def backup_info():
    """Return what can be backed up"""
    if not req(): return jsonify({'ok':False}), 401
    dbs      = get_databases()
    websites = get_websites()
    return jsonify({
        'ok':      True,
        'databases': dbs,
        'websites':  websites,
        'mysql':     mysql_available(),
        'webroot':   get_webroot(),
        'schedule':  _load_schedule(),
    })

@backups_bp.route('/api/backups/schedule', methods=['GET', 'PUT'])
def backup_schedule():
    if not req(): return jsonify({'ok':False}), 401
    if request.method == 'GET':
        return jsonify({'ok': True, 'schedule': _load_schedule()})
    current = _load_schedule()
    d = request.get_json() or {}
    for key in ('enabled', 'frequency', 'time', 'type', 'domain', 'database', 'cloud_upload'):
        if key in d:
            current[key] = d[key]
    if current.get('frequency') not in ('hourly', 'daily', 'weekly', 'monthly'):
        return jsonify({'ok': False, 'error': 'Invalid frequency'}), 400
    _save_schedule(current)
    return jsonify({'ok': True, 'schedule': current})

@backups_bp.route('/api/backups/create', methods=['POST'])
def create_backup():
    if not req(): return jsonify({'ok':False}), 401
    d      = request.get_json() or {}
    btype  = d.get('type', 'website')  # website | database | full
    domain = d.get('domain', '')       # specific domain or empty for all
    db     = d.get('database', '')     # specific DB or empty for all
    ts     = time.strftime('%Y%m%d_%H%M%S')
    os.makedirs(BACKUP_DIR, exist_ok=True)
    job_id = str(uuid.uuid4())[:8]
    _jobs[job_id] = {'done':False,'success':False,'name':'','size':0,'error':'','lines':[]}

    def do_backup():
        try:
            if btype == 'website':
                # Validate: get path for domain
                if domain:
                    if not valid_domain(domain):
                        _jobs[job_id].update({'done':True,'error':'Invalid domain'})
                        return
                    sites = get_websites()
                    site  = next((s for s in sites if s['domain']==domain), None)
                    if not site:
                        # Fallback: check if directory exists
                        path = os.path.join(get_webroot(), domain)
                        if not os.path.isdir(path):
                            _jobs[job_id].update({'done':True,'error':f'Website path not found for {domain}'})
                            return
                    else:
                        path = site['path']
                    name = f'website_{_safe_name(domain, "site")}_{ts}.tar.gz'
                else:
                    path = get_webroot()
                    name = f'website_all_{ts}.tar.gz'
                dest = os.path.join(BACKUP_DIR, name)
                _jobs[job_id]['lines'].append(f'Archiving {path}...')
                try:
                    _create_tar_gz(dest, [path])
                except Exception as e:
                    _jobs[job_id].update({'done':True,'error':f'Archive failed: {e}'})
                    return

            elif btype == 'database':
                dbs = get_databases()
                if not dbs:
                    _jobs[job_id].update({'done':True,'error':'No databases found. Create a database first.'})
                    return
                if db and db not in dbs:
                    _jobs[job_id].update({'done':True,'error':f'Database "{db}" not found'})
                    return
                if db and not DB_NAME_RE.fullmatch(db):
                    _jobs[job_id].update({'done':True,'error':'Invalid database name'})
                    return
                target_dbs = [db] if db else dbs
                name = f'database_{_safe_name(db or "all", "all")}_{ts}.sql.gz'
                dest = os.path.join(BACKUP_DIR, name)
                if db:
                    _jobs[job_id]['lines'].append(f'Dumping database: {db}')
                else:
                    _jobs[job_id]['lines'].append(f'Dumping {len(dbs)} databases: {", ".join(dbs)}')
                err, rc = _dump_mysql(dest, db)
                if rc != 0:
                    _jobs[job_id].update({'done':True,'error':f'mysqldump failed: {err}'})
                    return

            elif btype == 'full':
                name  = f'full_{ts}.tar.gz'
                dest  = os.path.join(BACKUP_DIR, name)
                webroot = get_webroot()
                _jobs[job_id]['lines'].append('Archiving websites, nginx configs, caddyfile...')
                include = [webroot]
                for extra in ['/etc/nginx', '/etc/caddy', '/opt/dotserve/backups']:
                    if os.path.isdir(extra):
                        include.append(extra)
                try:
                    _create_tar_gz(dest, include)
                except Exception as e:
                    _jobs[job_id].update({'done':True,'error':f'Full backup failed: {e}'})
                    return
            else:
                _jobs[job_id].update({'done':True,'error':f'Unknown backup type: {btype}'})
                return

            size = os.path.getsize(dest) if os.path.exists(dest) else 0
            _jobs[job_id].update({'done':True,'success':True,'name':name,'size':size})
            _jobs[job_id]['lines'].append(f'✓ Backup complete: {name} ({size//1024}KB)')
            # Auto-upload to cloud if configured
            try:
                from panel.routes.cloud_backup import load_config as _cb_load, get_client as _cb_client
                cfg = _cb_load()
                if cfg.get('bucket') and cfg.get('auto_upload'):
                    _jobs[job_id]['lines'].append('Uploading to cloud storage...')
                    client = _cb_client(cfg)
                    prefix = cfg.get('prefix','dotserve-backups/')
                    client.upload_file(dest, cfg['bucket'], prefix+name)
                    _jobs[job_id]['lines'].append('✓ Uploaded to cloud storage')
            except Exception as _e:
                _jobs[job_id]['lines'].append(f'⚠ Cloud upload failed: {_e}')

        except Exception as e:
            _jobs[job_id].update({'done':True,'error':str(e)})

    threading.Thread(target=do_backup, daemon=True).start()
    return jsonify({'ok':True, 'job_id':job_id})

@backups_bp.route('/api/backups/job/<job_id>')
def job_status(job_id):
    if not req(): return jsonify({'ok':False}), 401
    job = _jobs.get(job_id)
    if not job: return jsonify({'ok':False,'error':'Job not found'}), 404
    return jsonify({'ok':True, **job})

@backups_bp.route('/api/backups/download/<name>')
def download_backup(name):
    if not req(): return jsonify({'ok':False}), 401
    # Sanitize filename — no path traversal
    name = os.path.basename(name)
    path = os.path.join(BACKUP_DIR, name)
    if not os.path.exists(path):
        return jsonify({'ok':False,'error':'File not found'}), 404
    return send_file(path, as_attachment=True, download_name=name)

@backups_bp.route('/api/backups/<name>', methods=['DELETE'])
def delete_backup(name):
    if not req(): return jsonify({'ok':False}), 401
    name = os.path.basename(name)
    path = os.path.join(BACKUP_DIR, name)
    if os.path.exists(path): os.unlink(path)
    return jsonify({'ok':True})

@backups_bp.route('/api/backups/restore', methods=['POST'])
def restore_backup():
    if not req(): return jsonify({'ok':False}), 401
    d      = request.get_json() or {}
    name   = os.path.basename(d.get('name',''))
    btype  = d.get('type','')    # website | database
    target = d.get('target','')  # restore path for website, db name for database
    path   = os.path.join(BACKUP_DIR, name)

    if not name or not os.path.exists(path):
        return jsonify({'ok':False,'error':'Backup file not found'}), 404

    job_id = str(uuid.uuid4())[:8]
    _jobs[job_id] = {'done':False,'success':False,'error':'','lines':[]}

    def do_restore():
        try:
            if btype == 'website' or name.endswith('.tar.gz') and 'database' not in name and 'sql' not in name:
                restore_path = os.path.abspath(target or get_webroot())
                webroot = os.path.abspath(get_webroot())
                if restore_path != webroot and not restore_path.startswith(webroot + os.sep):
                    _jobs[job_id].update({'done':True,'error':'Website restore target must be inside the webroot'})
                    return
                _jobs[job_id]['lines'].append(f'Restoring to {restore_path}...')
                os.makedirs(restore_path, exist_ok=True)
                try:
                    _safe_extract_tar(path, restore_path)
                except Exception as e:
                    _jobs[job_id].update({'done':True,'error':f'Restore failed: {e}'})
                    return
                _jobs[job_id].update({'done':True,'success':True})
                _jobs[job_id]['lines'].append(f'✓ Restored to {restore_path}')

            elif btype == 'database' or name.endswith('.sql.gz') or 'database' in name:
                if not target:
                    _jobs[job_id].update({'done':True,'error':'Database name required for restore'})
                    return
                # Defense in depth: a MySQL
                # database name has no legitimate reason to contain anything
                # outside this charset, so reject anything else outright
                # rather than trying to safely quote arbitrary input.
                if not DB_NAME_RE.fullmatch(target):
                    _jobs[job_id].update({'done':True,'error':'Invalid database name — only letters, numbers, underscore and hyphen are allowed'})
                    return
                _jobs[job_id]['lines'].append(f'Restoring database {target}...')
                # Create DB if not exists. Run via subprocess directly (no
                # shell) so the backticks used for SQL identifier quoting
                # aren't misread by /bin/sh as command substitution (which
                # would otherwise try to *execute* {target} as a command).
                subprocess.run(
                    ['mysql', '-u', 'root', '-e', f'CREATE DATABASE IF NOT EXISTS `{target}`;'],
                    capture_output=True, text=True, timeout=30
                )
                # Same injection class as above for the actual data import —
                # stream the decompressed dump into mysql's stdin directly
                # stream the dump directly, so neither path nor target is
                # interpreted by a command shell.
                try:
                    with gzip.open(path, 'rb') as gz:
                        r = subprocess.run(['mysql', '-u', 'root', target],
                                            stdin=gz, capture_output=True, text=True, timeout=300)
                    rc, err = r.returncode, r.stderr.strip()
                except Exception as e:
                    rc, err = 1, str(e)
                if rc != 0:
                    _jobs[job_id].update({'done':True,'error':f'Restore failed: {err}'})
                    return
                _jobs[job_id].update({'done':True,'success':True})
                _jobs[job_id]['lines'].append(f'✓ Database {target} restored')
            else:
                _jobs[job_id].update({'done':True,'error':'Cannot determine backup type. Specify type explicitly.'})
        except Exception as e:
            _jobs[job_id].update({'done':True,'error':str(e)})

    threading.Thread(target=do_restore, daemon=True).start()
    return jsonify({'ok':True,'job_id':job_id})

@backups_bp.route('/api/backups/upload', methods=['POST'])
def upload_restore():
    """Upload a .tar.gz or .sql.gz file and restore it"""
    if not req(): return jsonify({'ok':False}), 401
    f      = request.files.get('file')
    btype  = request.form.get('type','website')
    target = request.form.get('target','')
    if not f: return jsonify({'ok':False,'error':'No file uploaded'}), 400

    name = os.path.basename(f.filename)
    if not name.endswith(('.tar.gz','.sql.gz','.zip','.sql')):
        return jsonify({'ok':False,'error':'Only .tar.gz, .sql.gz, .zip, .sql files are supported'}), 400

    upload_path = os.path.join(BACKUP_DIR, f'upload_{name}')
    f.save(upload_path)
    # Trigger restore
    return restore_backup.__wrapped__(request.json) if hasattr(restore_backup,'__wrapped__') else jsonify({'ok':True,'path':upload_path,'message':'File uploaded. Use restore endpoint with this path.'})
