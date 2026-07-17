from flask import Blueprint, jsonify, request, session
import ipaddress
import os
import re
import time

from panel.core.process import run_args
from panel.core.validation import valid_domain


dns_bp = Blueprint('dns', __name__)
ZONES_DIR = '/etc/bind/zones'
NAMED_CONF_CANDIDATES = ['/etc/bind/named.conf.local', '/etc/named/named.conf.local']
RECORD_TYPES = {'A', 'AAAA', 'CNAME', 'MX', 'TXT', 'NS', 'SRV', 'CAA'}
HOST_RE = re.compile(r'^(@|\*|[A-Za-z0-9_.-]{1,253})$')


def req():
    return 'user' in session


def _zone_path(domain):
    return os.path.join(ZONES_DIR, f'db.{domain}')


def _valid_ip(value):
    try:
        ipaddress.ip_address(value)
        return True
    except Exception:
        return False


def _reload_dns():
    for cmd in (['rndc', 'reload'], ['systemctl', 'reload', 'bind9'], ['systemctl', 'reload', 'named']):
        _, _, rc = run_args(cmd, timeout=20)
        if rc == 0:
            return True
    return False


def _read_named_conf():
    chunks = []
    for path in NAMED_CONF_CANDIDATES:
        if os.path.exists(path):
            with open(path, errors='ignore') as fh:
                chunks.append(fh.read())
    return '\n'.join(chunks)


@dns_bp.route('/api/dns/zones')
def list_zones():
    if not req():
        return jsonify({'ok': False}), 401
    zones = []
    if os.path.isdir(ZONES_DIR):
        for filename in os.listdir(ZONES_DIR):
            if filename.startswith('db.'):
                domain = filename[3:].lower()
                if valid_domain(domain):
                    zones.append({'domain': domain, 'file': filename})
    for match in re.finditer(r'zone\s+"([^"]+)"', _read_named_conf()):
        domain = match.group(1).lower()
        if valid_domain(domain) and not any(z['domain'] == domain for z in zones):
            zones.append({'domain': domain, 'file': f'db.{domain}'})
    return jsonify({'ok': True, 'zones': zones})


@dns_bp.route('/api/dns/zones', methods=['POST'])
def create_zone():
    if not req():
        return jsonify({'ok': False}), 401
    data = request.get_json() or {}
    domain = data.get('domain', '').strip().lower().rstrip('.')
    ip = data.get('ip', '127.0.0.1').strip()
    if not valid_domain(domain):
        return jsonify({'ok': False, 'error': 'Valid domain required'}), 400
    if not _valid_ip(ip):
        return jsonify({'ok': False, 'error': 'Valid IP required'}), 400
    os.makedirs(ZONES_DIR, exist_ok=True)
    serial = int(time.strftime('%Y%m%d')) * 100 + 1
    template = f"""$ORIGIN {domain}.
$TTL 3600
@   IN SOA  ns1.{domain}. admin.{domain}. (
        {serial} ; Serial
        3600       ; Refresh
        900        ; Retry
        604800     ; Expire
        300 )      ; Minimum

@   IN NS   ns1.{domain}.
@   IN A    {ip}
ns1 IN A    {ip}
www IN A    {ip}
mail IN A   {ip}
@   IN MX 10 mail.{domain}.
"""
    with open(_zone_path(domain), 'w') as fh:
        fh.write(template)
    _reload_dns()
    return jsonify({'ok': True, 'domain': domain})


@dns_bp.route('/api/dns/zones/<domain>/records')
def get_records(domain):
    if not req():
        return jsonify({'ok': False}), 401
    domain = domain.strip().lower()
    if not valid_domain(domain):
        return jsonify({'ok': False, 'error': 'Invalid domain'}), 400
    zone_file = _zone_path(domain)
    if not os.path.exists(zone_file):
        return jsonify({'ok': False, 'error': 'Zone not found'}), 404
    with open(zone_file) as fh:
        content = fh.read()
    records = []
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith(';') or line.startswith('$'):
            continue
        match = re.match(r'^(\S+)\s+(?:\d+\s+)?(?:IN\s+)?(\w+)\s+(.+)$', line)
        if match:
            records.append({'host': match.group(1), 'type': match.group(2), 'value': match.group(3)})
    return jsonify({'ok': True, 'records': records, 'content': content})


@dns_bp.route('/api/dns/zones/<domain>', methods=['DELETE'])
def delete_zone(domain):
    if not req():
        return jsonify({'ok': False}), 401
    domain = domain.strip().lower()
    if not valid_domain(domain):
        return jsonify({'ok': False, 'error': 'Invalid domain'}), 400
    zone_file = _zone_path(domain)
    try:
        if os.path.exists(zone_file):
            os.unlink(zone_file)
        conf = '/etc/bind/named.conf.local'
        if os.path.exists(conf):
            with open(conf) as fh:
                content = fh.read()
            content = re.sub(rf'zone\s+"{re.escape(domain)}"[^}}]+}}\s*;?\s*', '', content, flags=re.DOTALL)
            with open(conf, 'w') as fh:
                fh.write(content)
        _reload_dns()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})


@dns_bp.route('/api/dns/zones/<domain>/records', methods=['POST'])
def add_record(domain):
    if not req():
        return jsonify({'ok': False}), 401
    domain = domain.strip().lower()
    if not valid_domain(domain):
        return jsonify({'ok': False, 'error': 'Invalid domain'}), 400
    data = request.get_json() or {}
    host = (data.get('host') or '@').strip()
    rtype = (data.get('type') or 'A').strip().upper()
    value = (data.get('value') or '').strip()
    ttl = str(data.get('ttl') or '3600').strip()
    if not HOST_RE.fullmatch(host):
        return jsonify({'ok': False, 'error': 'Invalid host'}), 400
    if rtype not in RECORD_TYPES:
        return jsonify({'ok': False, 'error': 'Invalid record type'}), 400
    if not value or '\n' in value or '\r' in value:
        return jsonify({'ok': False, 'error': 'Valid value required'}), 400
    if not ttl.isdigit() or not (60 <= int(ttl) <= 86400):
        return jsonify({'ok': False, 'error': 'Invalid TTL'}), 400
    zone_file = _zone_path(domain)
    if not os.path.exists(zone_file):
        return jsonify({'ok': False, 'error': 'Zone not found'}), 404
    with open(zone_file) as fh:
        content = fh.read()
    serial = str(int(time.strftime('%Y%m%d')) * 100 + 1)
    content = re.sub(r'(\d{10})\s*;\s*Serial', serial + ' ; Serial', content)
    record_line = f'{host}\t{ttl}\tIN\t{rtype}\t{value}\n'
    with open(zone_file, 'w') as fh:
        fh.write(content + record_line)
    _reload_dns()
    return jsonify({'ok': True})


@dns_bp.route('/api/dns/zones/<domain>/records/delete', methods=['POST'])
def delete_record(domain):
    if not req():
        return jsonify({'ok': False}), 401
    domain = domain.strip().lower()
    if not valid_domain(domain):
        return jsonify({'ok': False, 'error': 'Invalid domain'}), 400
    try:
        idx = int((request.get_json() or {}).get('index', -1))
    except Exception:
        idx = -1
    zone_file = _zone_path(domain)
    if not os.path.exists(zone_file):
        return jsonify({'ok': False, 'error': 'Zone not found'}), 404
    with open(zone_file) as fh:
        lines = fh.readlines()
    record_lines = [i for i, line in enumerate(lines)
                    if line.strip() and not line.strip().startswith((';', '$')) and 'IN' in line and 'SOA' not in line]
    if 0 <= idx < len(record_lines):
        del lines[record_lines[idx]]
        with open(zone_file, 'w') as fh:
            fh.writelines(lines)
        _reload_dns()
    return jsonify({'ok': True})
