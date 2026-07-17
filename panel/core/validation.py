import ipaddress
import os
import re
from urllib.parse import urlparse
from werkzeug.utils import secure_filename

from panel.core.config import BLOCKED_FILE_PREFIXES

DOMAIN_RE = re.compile(r'^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$', re.I)
SAFE_NAME_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$')
DOCKER_IMAGE_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._/:@-]{0,255}$')


def valid_domain(value):
    return bool(DOMAIN_RE.fullmatch((value or '').strip()))


def valid_safe_name(value):
    return bool(SAFE_NAME_RE.fullmatch((value or '').strip()))


def valid_port(value):
    try:
        port = int(value)
        return 1 <= port <= 65535
    except Exception:
        return False


def clean_filename(value, fallback='upload.bin'):
    cleaned = secure_filename(value or '')
    return cleaned or fallback


def normalize_abs_path(path, default='/'):
    raw = path or default
    if not os.path.isabs(raw):
        raw = '/' + raw
    return os.path.realpath(os.path.normpath(raw))


def path_blocked(path):
    real = normalize_abs_path(path)
    return any(real == p or real.startswith(p + os.sep) for p in BLOCKED_FILE_PREFIXES)


def within_any_root(path, roots):
    real = normalize_abs_path(path)
    for root in roots:
        r = normalize_abs_path(root)
        if r == '/' or real == r or real.startswith(r + os.sep):
            return True
    return False


def valid_public_download_url(url):
    parsed = urlparse((url or '').strip())
    if parsed.scheme not in ('http', 'https') or not parsed.hostname:
        return False
    host = parsed.hostname.lower()
    if host in ('localhost',):
        return False
    try:
        ip = ipaddress.ip_address(host)
        return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast)
    except ValueError:
        return True


def valid_docker_image(value):
    return bool(DOCKER_IMAGE_RE.fullmatch((value or '').strip()))

