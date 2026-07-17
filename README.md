# DotServe

**DotServe** is a free, open-source Linux server management panel for websites, databases, DNS, mail, backups, monitoring, security, Docker, and app hosting.

- Repository: `khalequzzaman17/dotserve`
- Version: `1.0.0`
- Default panel port: `3334`
- License: MIT
- Backend: Python, Flask, Gunicorn
- Frontend: TailwindCSS, Alpine.js, vanilla JavaScript
- Theme: dark blue DotServe UI

## Quick Install

```bash
wget -O install.sh https://raw.githubusercontent.com/khalequzzaman17/dotserve/main/install.sh && bash install.sh
```

Open:

```text
http://YOUR-SERVER-IP:3334
```

The installer detects Debian/Ubuntu and RHEL-family systems, installs dependencies, creates `/opt/dotserve`, sets up a Python virtual environment, writes a hardened systemd service, generates admin credentials, installs the default server stack including phpMyAdmin on port `8082`, and creates `/root/deploy.sh`, `/root/rollback.sh`, plus `/root/dotserve-repair.sh`.

## Core Features

### Dashboard and Monitoring

- CPU, RAM, disk, load, uptime, and network overview
- Process list with kill action safeguards
- Realtime bandwidth endpoint
- Service health summary
- Global SSL expiry alert aggregation
- Log viewer for common system and service logs

### Onboarding

- First-run onboarding wizard
- Server configuration setup flow
- Domain addition walkthrough
- Email configuration guidance
- Security best-practices checklist

### User Interface

- TailwindCSS-based dark blue DotServe theme
- Responsive sidebar
- Mobile-friendly layouts
- Touch-friendly controls
- `data-cfasync="false"` on active JavaScript and CSS URLs

### Website Management

- Nginx, Apache, OpenLiteSpeed, and Caddy tooling
- PHP version selection per site
- Reverse proxy support
- SSL workflows
- Composer integration
- Website security helpers
- Website integrity checks
- Node.js and Go project hosting

### Bandwidth Monitor

- Interface-level total and realtime traffic
- `vnstat` integration when available
- `/proc/net/dev` fallback
- Per-domain Nginx access-log traffic summary
- CSV export

### Backups

- Website backup
- Database backup
- Full backup
- Restore workflows
- Upload-and-restore endpoint
- Backup scheduling configuration
- S3-compatible cloud upload integration through cloud backup config
- Safe tar extraction checks for restore paths

### Website Import

- Upload-based import wizard for cPanel, aaPanel, and Hestia-style backups
- Detect, preview, confirm, import flow
- Files and database import
- Extended migration artifact detection for email, cron, SSL, and DNS files
- Safer archive extraction with path traversal and link checks
- Fresh generated database credentials during import

### Security

- Session hardening
- Security headers, including CSP and `X-Frame-Options`
- CSRF-style Origin/Referer rejection for cross-origin mutating API requests
- Argon2id-capable authentication migration path
- Login lockout/audit support
- 2FA/TOTP support
- IP allowlist support
- SSH hardening tools
- Firewall management for UFW and firewalld
- Fail2ban and ModSecurity tooling
- PHP webshell scanner with quarantine/delete workflows

### Disk Usage Analyzer

- Directory size breakdown
- Duplicate file finder
- Delete from panel with path validation
- Storage alert support

### Alerting

- CPU threshold alerts
- RAM usage alerts
- Disk usage alerts
- SSL expiry warnings
- Email/webhook notification config
- Alert history

### File Manager

- File browser
- Upload/download
- Edit, rename, delete, compress, extract
- Search
- Lint and scan helpers
- Restricted-root and blocked-system-path checks

### DNS, Mail, FTP, CDN

- BIND zone management
- Cloudflare DDNS
- Postfix/Dovecot mail domains and accounts
- DKIM helpers
- FTP virtual/system account workflows
- Cloudflare, BunnyCDN, KeyCDN, CloudFront, Akamai, Google CDN, StackPath, and Sucuri configuration surfaces
- Nginx CDN cache-header helper

### Docker and Apps

- Docker container/image/network/volume management
- Container domain assignment
- Module installer catalog
- WordPress toolkit
- Database management for MySQL, MariaDB, PostgreSQL, and MongoDB

## Security Hardening In This Release

DotServe 1.0.0 includes a security pass over high-risk routes:

- Removed user-controlled shell execution from bandwidth, backups, import, mail, services, DNS, FTP, monitoring, dashboard, Caddy, and CDN routes
- Added shared core helpers under `panel/core/`
- Added input validation for domains, emails, ports, usernames, paths, Docker names/images, and DNS records
- Added safe archive extraction for imports and restores
- Added safer backup/database dump execution using argument-list subprocess calls
- Added stronger installer defaults with `set -Eeuo pipefail`, `umask 077`, protected credentials, systemd hardening, and structured functions
- Added `.gitattributes` to preserve Linux-friendly LF endings for shell/source files

Some privileged admin modules still intentionally execute system-management commands. Keep DotServe behind trusted admin access, enable HTTPS, use strong credentials, enable 2FA, and restrict access by firewall/IP allowlist.

## Supported Operating Systems

| Family | Versions |
|---|---|
| Ubuntu | 20.04, 22.04, 24.04+ |
| Debian | 11, 12+ |
| AlmaLinux | 8, 9, 10 |
| Rocky Linux | 8, 9, 10 |
| RHEL | 8, 9, 10 |
| Oracle Linux | 8, 9 |
| CentOS Stream | 8, 9 |
| CloudLinux | 8, 9, 10 |
| Fedora | 38+ |

Minimum recommended server:

- 1 CPU core
- 1 GB RAM
- 2 GB free disk
- Root access
- systemd-based Linux distribution

## Local Development

```bash
git clone https://github.com/khalequzzaman17/dotserve.git
cd dotserve
python3 -m venv venv
. venv/bin/activate
pip install -r requirements.txt
python3 app.py
```

Open:

```text
http://127.0.0.1:3334
```

On Windows PowerShell:

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

## Deployment Helpers

The installer writes:

- `/root/deploy.sh` - pulls/deploys project files, creates an update backup, restarts DotServe
- `/root/rollback.sh` - restores the latest or selected update backup
- `/root/dotserve-repair.sh` - checks and repairs panel SSL, webserver, MariaDB, PHP, Redis, Supervisor, and phpMyAdmin

Source checkout:

```text
/root/dotserve
```

Install directory:

```text
/opt/dotserve
```

## Repository Layout

```text
app.py                  Flask app bootstrap and blueprint registration
install.sh              Universal Linux installer
panel/core/             Shared config, security, storage, process, validation helpers
panel/routes/           API route modules
panel/utils/            Utility helpers
web/templates/          Main HTML shell
web/static/css/         Tailwind-compatible DotServe theme CSS
web/static/js/          Frontend application code
web/static/icons/       Module icons
web/static/lang/        UI translations
```

## Verification

Recent checks performed before release:

```bash
bash -n install.sh
python -c "import app; print(len(app.app.blueprints))"
node --check web/static/js/app.js
node --check web/static/js/i18n.js
```

The panel was smoke-tested locally on port `3334` and returned HTTP `200` with security headers.

## Version

Current release:

```text
1.0.0
```

Git tag:

```text
v1.0.0
```

## Contributing

Issues and pull requests are welcome:

- Issues: <https://github.com/khalequzzaman17/dotserve/issues>
- Pull requests: <https://github.com/khalequzzaman17/dotserve/pulls>

Before submitting a major feature, open an issue to discuss scope and implementation.

## License

DotServe is released under the MIT License. See [LICENSE](LICENSE).
