import os

APP_NAME = 'DotServe'
VERSION = '1.0.0'
DEFAULT_PORT = int(os.environ.get('PORT', '3334'))

DATA_DIR = os.environ.get('DOTSERVE_DATA_DIR', '/opt/dotserve')
LOG_DIR = os.environ.get('DOTSERVE_LOG_DIR', '/var/log/dotserve')

SECRET_KEY_FILE = os.path.join(DATA_DIR, 'secret.key')
SESSION_DIR = os.path.join(DATA_DIR, 'sessions')
CONFIG_FILE = os.path.join(DATA_DIR, 'config.json')
CDN_CONFIG_FILE = os.path.join(DATA_DIR, 'cdn_config.json')
AI_CONFIG_FILE = os.path.join(DATA_DIR, 'ai_config.json')

WEB_ROOTS = ['/www/wwwroot', '/var/www/html', '/var/www', '/srv/www']
FILE_MANAGER_ROOTS = [
    p.strip() for p in os.environ.get('DOTSERVE_FILE_MANAGER_ROOTS', '/').split(os.pathsep)
    if p.strip()
]
BLOCKED_FILE_PREFIXES = [
    '/proc',
    '/sys',
    '/dev',
    '/run',
]


def ensure_data_dirs():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(SESSION_DIR, exist_ok=True)
    for path in (CONFIG_FILE, CDN_CONFIG_FILE, AI_CONFIG_FILE):
        if not os.path.exists(path):
            with open(path, 'w') as f:
                f.write('{}')
