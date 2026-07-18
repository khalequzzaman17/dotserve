from flask import Blueprint, jsonify, request, session
import subprocess, re, os, time, json, uuid, threading, hashlib

cron_bp = Blueprint('cron', __name__)
def req(): return 'user' in session

CRON_META_FILE = '/opt/dotserve/cron_meta.json'
_run_logs = {}  # job_id -> {lines, done, exit_code}

def sh(c, t=30):
    try:
        r = subprocess.run(c, shell=True, capture_output=True, text=True, timeout=t)
        return r.stdout.strip(), r.stderr.strip(), r.returncode
    except: return '', '', 1

def load_meta():
    if os.path.exists(CRON_META_FILE):
        try:
            with open(CRON_META_FILE) as f: return json.load(f)
        except: pass
    return {}

def save_meta(meta):
    os.makedirs(os.path.dirname(CRON_META_FILE), exist_ok=True)
    with open(CRON_META_FILE, 'w') as f: json.dump(meta, f, indent=2)

def get_crontab():
    out, _, _ = sh('crontab -l 2>/dev/null')
    return out or ''

def set_crontab(content):
    r = subprocess.run('crontab -', input=content, shell=True, text=True)
    return r.returncode == 0

def stable_line_id(clean_line):
    return 'line-' + hashlib.sha1(clean_line.encode('utf-8')).hexdigest()[:16]

def clean_cron_line(line):
    return re.sub(r'\s*#\s*vp:[a-f0-9-]+', '', line.strip()).strip()

def line_matches_job(line, job_id):
    s = line.strip()
    if not s:
        return False
    if f'# vp:{job_id}' in s:
        return True
    return stable_line_id(clean_cron_line(s)) == job_id

def command_from_line(line):
    clean = clean_cron_line(line)
    parts = clean.split(None, 5)
    if len(parts) < 6:
        return ''
    return parts[5].strip()

def parse_crontab(raw, meta):
    jobs = []
    for line in raw.split('\n'):
        s = line.strip()
        if not s or s.startswith('#'): continue
        # Extract vp-id tag if present: # vp:uuid
        vid_m = re.search(r'#\s*vp:([a-f0-9-]+)', s)
        vid   = vid_m.group(1) if vid_m else None
        # Strip meta tag from line for display
        clean = clean_cron_line(s)
        parts = clean.split(None, 5)
        if len(parts) < 6: continue
        schedule = ' '.join(parts[:5])
        command  = parts[5]
        m = meta.get(vid, {}) if vid else {}
        jobs.append({
            'id':        vid or stable_line_id(clean),
            'managed':   bool(vid),
            'schedule':  schedule,
            'command':   command,
            'name':      m.get('name', ''),
            'type':      m.get('type', 'shell'),
            'user':      m.get('user', 'root'),
            'logs':      m.get('last_log', ''),
            'last_run':  m.get('last_run', ''),
            'last_exit': m.get('last_exit', ''),
            'enabled':   not s.startswith('#'),
            'raw_line':  s,
        })
    return jobs

def human_schedule(schedule):
    """Convert cron expression to human-readable string"""
    parts = schedule.split()
    if len(parts) != 5: return schedule
    mn, hr, dom, mon, dow = parts
    if schedule == '* * * * *':   return 'Every minute'
    if mn == '*/5' and hr == '*': return 'Every 5 minutes'
    if mn == '*/10':              return 'Every 10 minutes'
    if mn == '*/15':              return 'Every 15 minutes'
    if mn == '*/30':              return 'Every 30 minutes'
    if hr == '*' and mn != '*':   return f'Every hour at :{mn.zfill(2)}'
    if dom == '*' and mon == '*' and dow == '*':
        return f'Daily at {hr.zfill(2)}:{mn.zfill(2)}'
    if dow != '*':
        days = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat']
        try:
            day_name = days[int(dow)]
            return f'Every {day_name} at {hr.zfill(2)}:{mn.zfill(2)}'
        except: pass
    if dom != '*':
        return f'Monthly on day {dom} at {hr.zfill(2)}:{mn.zfill(2)}'
    return schedule

# --- PRESETS --------------------------------------------------------------------
SCHEDULE_PRESETS = [
    {'label':'Every minute',      'value':'* * * * *'},
    {'label':'Every 5 minutes',   'value':'*/5 * * * *'},
    {'label':'Every 10 minutes',  'value':'*/10 * * * *'},
    {'label':'Every 15 minutes',  'value':'*/15 * * * *'},
    {'label':'Every 30 minutes',  'value':'*/30 * * * *'},
    {'label':'Every hour',        'value':'0 * * * *'},
    {'label':'Every 2 hours',     'value':'0 */2 * * *'},
    {'label':'Every 6 hours',     'value':'0 */6 * * *'},
    {'label':'Every 12 hours',    'value':'0 */12 * * *'},
    {'label':'Daily at midnight', 'value':'0 0 * * *'},
    {'label':'Daily at 1:00 AM',  'value':'0 1 * * *'},
    {'label':'Daily at 3:00 AM',  'value':'0 3 * * *'},
    {'label':'Every Sunday',      'value':'0 0 * * 0'},
    {'label':'Every Monday',      'value':'0 0 * * 1'},
    {'label':'First of month',    'value':'0 0 1 * *'},
    {'label':'Custom...',         'value':'custom'},
]

TASK_TEMPLATES = [
    {'id':'shell',      'label':'Shell Script',      'icon':'⌨',  'desc':'Run any shell command or script',
     'cmd':'','hint':'/usr/bin/bash /path/to/script.sh'},
    {'id':'php',        'label':'PHP Script',         'icon':'🐘', 'desc':'Execute a PHP file with php-cli',
     'cmd':'/usr/bin/php ','hint':'/www/wwwroot/site.com/cron.php'},
    {'id':'python',     'label':'Python Script',      'icon':'🐍', 'desc':'Run a Python script',
     'cmd':'/usr/bin/python3 ','hint':'/www/wwwroot/app/task.py'},
    {'id':'node',       'label':'Node.js Script',     'icon':'🟢', 'desc':'Execute a Node.js script',
     'cmd':'/usr/bin/node ','hint':'/www/wwwroot/app/cron.js'},
    {'id':'url',        'label':'URL Request',        'icon':'🌐', 'desc':'Fetch a URL (website cron trigger)',
     'cmd':'/usr/bin/curl -s ','hint':'https://example.com/cron?token=abc'},
    {'id':'backup',     'label':'Website Backup',     'icon':'💾', 'desc':'Backup a website directory',
     'cmd':'tar -czf /opt/dotserve/backups/cron_backup_$(date +\\%Y\\%m\\%d).tar.gz ','hint':'/www/wwwroot/site.com'},
    {'id':'db_backup',  'label':'Database Backup',    'icon':'🗄', 'desc':'Dump a MySQL/MariaDB database',
     'cmd':'mysqldump -u root ','hint':'dbname | gzip > /opt/dotserve/backups/db_$(date +\\%Y\\%m\\%d).sql.gz'},
    {'id':'certbot',    'label':'SSL Certificate Renewal','icon':'🔒','desc':'Renew Let\'s Encrypt certificates',
     'cmd':'/usr/bin/certbot renew --quiet','hint':''},
    {'id':'log_clear',  'label':'Clear Nginx Logs',   'icon':'🧹', 'desc':'Rotate/clear Nginx access logs',
     'cmd':'> /var/log/nginx/access.log && systemctl reload nginx','hint':''},
    {'id':'cloud_sync', 'label':'Cloud Backup Sync',  'icon':'☁',  'desc':'Upload any new local backups to cloud storage',
     'cmd':'/opt/dotserve/venv/bin/python3 /opt/dotserve/scripts/cloud_sync.py','hint':''},
    {'id':'custom',     'label':'Custom Command',     'icon':'⚙',  'desc':'Enter any custom command',
     'cmd':'','hint':'Enter your command...'},
]

@cron_bp.route('/api/cron/presets')
def get_presets():
    if not req(): return jsonify({'ok':False}), 401
    return jsonify({'ok':True, 'schedules':SCHEDULE_PRESETS, 'templates':TASK_TEMPLATES})

@cron_bp.route('/api/cron/jobs')
def list_jobs():
    if not req(): return jsonify({'ok':False}), 401
    raw  = get_crontab()
    meta = load_meta()
    jobs = parse_crontab(raw, meta)
    # Add human-readable schedule
    for j in jobs:
        j['schedule_human'] = human_schedule(j['schedule'])
    return jsonify({'ok':True, 'jobs':jobs, 'count':len(jobs)})

@cron_bp.route('/api/cron/jobs', methods=['POST'])
def add_job():
    if not req(): return jsonify({'ok':False}), 401
    d        = request.get_json() or {}
    schedule = d.get('schedule','0 * * * *').strip()
    command  = d.get('command','').strip()
    name     = d.get('name','').strip()
    jtype    = d.get('type','shell')
    user     = d.get('user','root')

    if not command: return jsonify({'ok':False,'error':'Command required'}), 400
    # Validate schedule (basic: 5 parts)
    if len(schedule.split()) != 5:
        return jsonify({'ok':False,'error':'Invalid cron schedule — must be 5 parts (min hour day month weekday)'}), 400

    vid  = str(uuid.uuid4())[:8]
    line = f'{schedule} {command} # vp:{vid}'

    raw  = get_crontab()
    new  = (raw.rstrip() + '\n' + line + '\n') if raw else line + '\n'
    if not set_crontab(new):
        return jsonify({'ok':False,'error':'Failed to update crontab'}), 500

    meta = load_meta()
    meta[vid] = {'name':name, 'type':jtype, 'user':user, 'created':time.strftime('%Y-%m-%d %H:%M:%S'), 'last_log':'', 'last_run':'', 'last_exit':''}
    save_meta(meta)
    return jsonify({'ok':True, 'id':vid, 'schedule_human':human_schedule(schedule)})

@cron_bp.route('/api/cron/jobs/<vid>', methods=['PUT'])
def edit_job(vid):
    if not req(): return jsonify({'ok':False}), 401
    d        = request.get_json() or {}
    schedule = d.get('schedule','').strip()
    command  = d.get('command','').strip()
    name     = d.get('name','')
    jtype    = d.get('type','shell')

    if not command: return jsonify({'ok':False,'error':'Command required'}), 400

    raw   = get_crontab()
    lines = raw.split('\n')
    new_lines = []
    found = False
    meta_key = vid
    for line in lines:
        if line_matches_job(line, vid):
            tag = vid if not vid.startswith('line-') else str(uuid.uuid4())[:8]
            meta_key = tag
            new_lines.append(f'{schedule} {command} # vp:{tag}')
            found = True
        else:
            new_lines.append(line)
    if not found:
        return jsonify({'ok':False,'error':'Job not found'}), 404

    if not set_crontab('\n'.join(new_lines) + '\n'):
        return jsonify({'ok':False,'error':'Failed to update crontab'}), 500
    meta = load_meta()
    meta[meta_key] = {**meta.get(meta_key, {}), 'name':name, 'type':jtype}
    if vid in meta and meta_key != vid:
        meta.pop(vid, None)
    save_meta(meta)
    return jsonify({'ok':True,'schedule_human':human_schedule(schedule)})

@cron_bp.route('/api/cron/jobs/<vid>', methods=['DELETE'])
def delete_job(vid):
    if not req(): return jsonify({'ok':False}), 401
    raw   = get_crontab()
    lines = [l for l in raw.split('\n') if not line_matches_job(l, vid)]
    if not set_crontab('\n'.join(lines) + '\n'):
        return jsonify({'ok':False,'error':'Failed to update crontab'}), 500
    meta = load_meta()
    meta.pop(vid, None)
    save_meta(meta)
    return jsonify({'ok':True})

@cron_bp.route('/api/cron/jobs/<vid>/toggle', methods=['POST'])
def toggle_job(vid):
    if not req(): return jsonify({'ok':False}), 401
    enable = (request.get_json() or {}).get('enable', True)
    raw    = get_crontab()
    lines  = raw.split('\n')
    new_lines = []
    for line in lines:
        if line_matches_job(line, vid):
            s = line.strip()
            if enable:
                new_lines.append(re.sub(r'^#+\s*', '', s))
            else:
                new_lines.append('# ' + s if not s.startswith('#') else s)
        else:
            new_lines.append(line)
    if not set_crontab('\n'.join(new_lines) + '\n'):
        return jsonify({'ok':False,'error':'Failed to update crontab'}), 500
    return jsonify({'ok':True, 'enabled':enable})

@cron_bp.route('/api/cron/jobs/<vid>/run', methods=['POST'])
def run_now(vid):
    if not req(): return jsonify({'ok':False}), 401
    raw  = get_crontab()
    cmd  = ''
    for line in raw.split('\n'):
        if line_matches_job(line, vid) and not line.strip().startswith('#'):
            cmd = command_from_line(line)
    if not cmd:
        return jsonify({'ok':False,'error':'Job not found or disabled'}), 404

    run_id = str(uuid.uuid4())[:8]
    _run_logs[run_id] = {'lines':[], 'done':False, 'exit_code':None, 'start': time.time()}

    def execute():
        start = time.time()
        _run_logs[run_id]['lines'].append(f'[DotServe] Executing: {cmd}')
        _run_logs[run_id]['lines'].append(f'[DotServe] Started: {time.strftime("%Y-%m-%d %H:%M:%S")}')
        proc = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        for line in proc.stdout:
            _run_logs[run_id]['lines'].append(line.rstrip())
        proc.wait()
        elapsed = round(time.time() - start, 2)
        _run_logs[run_id].update({'done':True,'exit_code':proc.returncode})
        _run_logs[run_id]['lines'].append(f'[DotServe] Finished in {elapsed}s — exit code: {proc.returncode}')
        # Save to meta
        meta = load_meta()
        if vid in meta:
            log_str = '\n'.join(_run_logs[run_id]['lines'])
            meta[vid].update({
                'last_run':  time.strftime('%Y-%m-%d %H:%M:%S'),
                'last_exit': str(proc.returncode),
                'last_log':  log_str[-2000:],
            })
            save_meta(meta)

    threading.Thread(target=execute, daemon=True).start()
    return jsonify({'ok':True, 'run_id':run_id})

@cron_bp.route('/api/cron/run/<run_id>')
def run_status(run_id):
    if not req(): return jsonify({'ok':False}), 401
    job = _run_logs.get(run_id)
    if not job: return jsonify({'ok':False,'error':'Run not found'}), 404
    return jsonify({'ok':True, **job})

@cron_bp.route('/api/cron/jobs/<vid>/logs')
def job_logs(vid):
    if not req(): return jsonify({'ok':False}), 401
    meta = load_meta()
    info = meta.get(vid, {})
    return jsonify({'ok':True,'log':info.get('last_log',''),'last_run':info.get('last_run',''),'last_exit':info.get('last_exit','')})
