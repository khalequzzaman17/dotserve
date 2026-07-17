from flask import Blueprint, jsonify, session, Response
import csv
import io
import json
import os
import re
import shutil
import time

from panel.core.process import run_args, run_shell

try:
    from panel.routes.os_utils import get_os, pkg_install
except ImportError:
    try:
        from os_utils import get_os, pkg_install
    except ImportError:
        def get_os(): return {'family': 'debian', 'pkg': 'apt', 'id': 'ubuntu', 'codename': 'noble'}
        def pkg_install(p, f=''): return f'DEBIAN_FRONTEND=noninteractive apt-get install -y {f} {p}'


bandwidth_bp = Blueprint('bandwidth', __name__)


def req():
    return 'user' in session


IFACE_RE = re.compile(r'^[A-Za-z0-9_.:-]{1,64}$')


def _safe_iface(value):
    value = (value or '').strip()
    return value if IFACE_RE.fullmatch(value) else ''


def get_interface():
    """Get primary network interface without shell parsing."""
    try:
        with open('/proc/net/route') as fh:
            for line in fh.readlines()[1:]:
                parts = line.split()
                if len(parts) >= 2 and parts[1] == '00000000':
                    iface = _safe_iface(parts[0])
                    if iface:
                        return iface
    except Exception:
        pass

    out, _, rc = run_args(['ip', 'route'], timeout=5)
    if rc == 0:
        for line in out.splitlines():
            if line.startswith('default '):
                parts = line.split()
                if 'dev' in parts:
                    iface = _safe_iface(parts[parts.index('dev') + 1])
                    if iface:
                        return iface

    try:
        for iface in os.listdir('/sys/class/net'):
            iface = _safe_iface(iface)
            if iface and iface != 'lo':
                return iface
    except Exception:
        pass
    return 'eth0'


def _read_iface_bytes(iface):
    iface = _safe_iface(iface)
    if not iface:
        return 0, 0
    try:
        with open('/proc/net/dev') as fh:
            for line in fh:
                if ':' not in line:
                    continue
                name, data = line.split(':', 1)
                if name.strip() != iface:
                    continue
                parts = data.split()
                return int(parts[0]), int(parts[8])
    except Exception:
        pass
    return 0, 0


def _date_label(item):
    dt = item.get('date') if isinstance(item, dict) else {}
    if not isinstance(dt, dict):
        return ''
    year = dt.get('year', '')
    month = dt.get('month', '')
    day = dt.get('day')
    if day is None:
        return f'{year}-{int(month):02d}' if str(month).isdigit() else f'{year}-{month}'
    try:
        return f'{int(year):04d}-{int(month):02d}-{int(day):02d}'
    except Exception:
        return ''


def _nginx_log_stats(path):
    requests = 0
    bytes_sent = 0
    try:
        with open(path, errors='ignore') as fh:
            for line in fh:
                parts = line.rsplit(None, 2)
                if len(parts) < 2:
                    continue
                requests += 1
                value = parts[-1] if parts[-1].isdigit() else parts[-2]
                if value.isdigit():
                    bytes_sent += int(value)
    except Exception:
        pass
    return requests, bytes_sent


def _domain_rows():
    rows = []
    log_dir = '/var/log/nginx'
    if not os.path.isdir(log_dir):
        return rows
    for name in os.listdir(log_dir):
        if not name.endswith('.access.log'):
            continue
        domain = name[:-len('.access.log')]
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,253}', domain):
            continue
        path = os.path.join(log_dir, name)
        if not os.path.isfile(path):
            continue
        requests, bytes_sent = _nginx_log_stats(path)
        rows.append({'domain': domain, 'requests': requests, 'bytes': bytes_sent})
    rows.sort(key=lambda x: x['bytes'], reverse=True)
    return rows


@bandwidth_bp.route('/api/bandwidth/summary')
def summary():
    if not req():
        return jsonify({'ok': False}), 401
    iface = get_interface()

    if shutil.which('vnstat'):
        run_args(['systemctl', 'start', 'vnstat'], timeout=10)
        out, _, rc = run_args(['vnstat', '-i', iface, '--json'], timeout=10)
        if rc == 0 and out:
            try:
                data = json.loads(out)
                iface_data = data.get('interfaces', [{}])[0] if data.get('interfaces') else {}
                traffic = iface_data.get('traffic', {})
                total_rx = traffic.get('total', {}).get('rx', 0)
                total_tx = traffic.get('total', {}).get('tx', 0)
                monthly = [{'date': _date_label(m), 'rx': m.get('rx', 0), 'tx': m.get('tx', 0)}
                           for m in traffic.get('month', [])[-6:]]
                daily = [{'date': _date_label(d), 'rx': d.get('rx', 0), 'tx': d.get('tx', 0)}
                         for d in traffic.get('day', [])[-7:]]
                return jsonify({'ok': True, 'source': 'vnstat', 'interface': iface,
                                'total_rx': total_rx, 'total_tx': total_tx,
                                'monthly': monthly, 'daily': daily})
            except Exception:
                pass

    rx, tx = _read_iface_bytes(iface)
    return jsonify({'ok': True, 'source': 'proc' if rx or tx else 'none',
                    'interface': iface, 'total_rx': rx, 'total_tx': tx,
                    'monthly': [], 'daily': []})


@bandwidth_bp.route('/api/bandwidth/realtime')
def realtime():
    if not req():
        return jsonify({'ok': False}), 401
    iface = get_interface()
    rx1, tx1 = _read_iface_bytes(iface)
    time.sleep(1)
    rx2, tx2 = _read_iface_bytes(iface)
    return jsonify({'ok': True, 'interface': iface,
                    'rx_per_sec': max(0, rx2 - rx1), 'tx_per_sec': max(0, tx2 - tx1),
                    'rx_total': rx2, 'tx_total': tx2})


@bandwidth_bp.route('/api/bandwidth/domains')
def domain_bandwidth():
    if not req():
        return jsonify({'ok': False}), 401
    if not os.path.isdir('/var/log/nginx'):
        return jsonify({'ok': True, 'domains': [], 'note': 'No Nginx access logs found'})
    return jsonify({'ok': True, 'domains': _domain_rows()})


@bandwidth_bp.route('/api/bandwidth/export.csv')
def export_bandwidth_csv():
    if not req():
        return jsonify({'ok': False}), 401
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=['domain', 'requests', 'bytes'])
    writer.writeheader()
    writer.writerows(_domain_rows())
    return Response(buf.getvalue(), mimetype='text/csv',
                    headers={'Content-Disposition': 'attachment; filename=dotserve-bandwidth.csv'})


@bandwidth_bp.route('/api/bandwidth/install-vnstat', methods=['POST'])
def install_vnstat():
    if not req():
        return jsonify({'ok': False}), 401
    os_info = get_os()
    cmds = []
    if os_info['family'] == 'debian':
        cmds.append('apt-get update -qq 2>/dev/null || true')
    elif os_info['family'] == 'rhel':
        cmds.append('dnf install -y epel-release 2>/dev/null || true')
    cmds.append(pkg_install('vnstat'))
    cmds.append('systemctl enable vnstat 2>/dev/null || true')
    cmds.append('systemctl start vnstat 2>/dev/null || true')
    out, err, _ = run_shell(' && '.join(cmds), timeout=120)
    installed = bool(shutil.which('vnstat'))
    return jsonify({'ok': installed, 'output': (out or err)[-300:]})
