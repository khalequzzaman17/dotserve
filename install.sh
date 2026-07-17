#!/usr/bin/env bash
# DotServe Universal Installer
# Supports: Ubuntu 20.04+, Debian 11+, Fedora 38+, RHEL/AlmaLinux/Rocky 8+

set -Eeuo pipefail
umask 077

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

INSTALL_DIR="${INSTALL_DIR:-/opt/dotserve}"
SRC_DIR="${SRC_DIR:-/root/dotserve}"
REPO_URL="${REPO_URL:-https://github.com/khalequzzaman17/dotserve.git}"
PANEL_PORT="${PANEL_PORT:-3334}"
PYTHON_BIN="python3"
OS_ID=""
OS_NAME=""
OS_VERSION=""
OS_FAMILY="debian"
PKG_MGR="apt"

log() { printf '%b[DotServe]%b %s\n' "$GREEN" "$NC" "$*"; }
warn() { printf '%b[WARN]%b %s\n' "$YELLOW" "$NC" "$*"; }
err() { printf '%b[ERROR]%b %s\n' "$RED" "$NC" "$*" >&2; exit 1; }

on_error() {
    err "Installer failed at line $1. Review the output above for the failing command."
}
trap 'on_error $LINENO' ERR

require_root() {
    [ "$(id -u)" -eq 0 ] || err "Run this installer as root."
}

detect_os() {
    [ -f /etc/os-release ] || err "Cannot detect OS: /etc/os-release is missing."
    # shellcheck disable=SC1091
    . /etc/os-release
    OS_ID="${ID,,}"
    OS_NAME="${NAME:-Linux}"
    OS_VERSION="${VERSION_ID:-unknown}"
    case "$OS_ID" in
        ubuntu|debian|linuxmint|pop) OS_FAMILY="debian"; PKG_MGR="apt" ;;
        fedora) OS_FAMILY="fedora"; PKG_MGR="dnf" ;;
        rhel|centos|almalinux|rocky|ol|cloudlinux) OS_FAMILY="rhel"; PKG_MGR="dnf" ;;
        *) warn "Unknown OS: $OS_ID, assuming Debian-like"; OS_FAMILY="debian"; PKG_MGR="apt" ;;
    esac
    log "Detected: $OS_NAME $OS_VERSION ($OS_FAMILY/$PKG_MGR)"
}

install_dependencies() {
    log "Installing dependencies..."
    if [ "$PKG_MGR" = "apt" ]; then
        export DEBIAN_FRONTEND=noninteractive
        apt-get update -qq || true
        apt-get install -y python3 python3-pip python3-venv curl git wget unzip sudo openssl ca-certificates gnupg lsb-release software-properties-common
    else
        dnf install -y python3 python3-pip curl git wget unzip sudo openssl ca-certificates gnupg
        if [[ "$OS_ID" =~ ^(rhel|almalinux|rocky|ol|centos|cloudlinux)$ ]]; then
            dnf install -y epel-release 2>/dev/null || true
        fi
        ensure_modern_python
    fi
}

enable_service_if_present() {
    local svc="$1"
    if systemctl list-unit-files "$svc.service" >/dev/null 2>&1 || systemctl status "$svc" >/dev/null 2>&1; then
        systemctl enable --now "$svc" >/dev/null 2>&1 || true
    fi
}

install_openlitespeed() {
    log "Installing OpenLiteSpeed..."
    if command -v lshttpd >/dev/null 2>&1 || [ -x /usr/local/lsws/bin/lshttpd ]; then
        enable_service_if_present lsws
        return
    fi
    if curl -fsSL https://repo.litespeed.sh -o /tmp/dotserve-litespeed-repo.sh ||
        wget -qO /tmp/dotserve-litespeed-repo.sh https://repo.litespeed.sh; then
        bash /tmp/dotserve-litespeed-repo.sh >/dev/null 2>&1 || warn "OpenLiteSpeed repository setup failed; trying OS packages."
    else
        warn "Could not download OpenLiteSpeed repository setup; trying OS packages."
    fi
    if [ "$PKG_MGR" = "apt" ]; then
        apt-get update -qq || true
        apt-get install -y openlitespeed || warn "OpenLiteSpeed install failed on this OS."
    else
        dnf install -y openlitespeed || yum install -y openlitespeed || warn "OpenLiteSpeed install failed on this OS."
    fi
    enable_service_if_present lsws
}

install_php_stack() {
    log "Installing PHP 7.4-8.5 where packages are available..."
    local php_versions=("7.4" "8.0" "8.1" "8.2" "8.3" "8.4" "8.5")
    if [ "$PKG_MGR" = "apt" ]; then
        if [ "$OS_ID" = "ubuntu" ]; then
            add-apt-repository -y ppa:ondrej/php >/dev/null 2>&1 || true
        else
            curl -fsSL https://packages.sury.org/php/apt.gpg -o /usr/share/keyrings/deb.sury.org-php.gpg >/dev/null 2>&1 || true
            echo "deb [signed-by=/usr/share/keyrings/deb.sury.org-php.gpg] https://packages.sury.org/php/ $(lsb_release -sc) main" > /etc/apt/sources.list.d/php-sury.list
        fi
        apt-get update -qq || true
        for ver in "${php_versions[@]}"; do
            DEBIAN_FRONTEND=noninteractive apt-get install -y \
                "php${ver}" "php${ver}-fpm" "php${ver}-cli" "php${ver}-common" \
                "php${ver}-mysql" "php${ver}-xml" "php${ver}-curl" "php${ver}-gd" \
                "php${ver}-mbstring" "php${ver}-zip" "php${ver}-bcmath" "php${ver}-intl" \
                "php${ver}-soap" "php${ver}-readline" "php${ver}-redis" >/dev/null 2>&1 &&
                enable_service_if_present "php${ver}-fpm" ||
                warn "PHP $ver packages are not available; skipped."
        done
        return
    fi

    if [ "$PKG_MGR" = "dnf" ] || [ "$PKG_MGR" = "yum" ]; then
        local rhel_major
        rhel_major="$(rpm -E %rhel 2>/dev/null || echo 9)"
        dnf install -y "https://rpms.remirepo.net/enterprise/remi-release-${rhel_major}.rpm" >/dev/null 2>&1 || true
        dnf install -y yum-utils >/dev/null 2>&1 || true
        for ver in "${php_versions[@]}"; do
            local short="${ver/./}"
            dnf install -y \
                "php${short}-php" "php${short}-php-fpm" "php${short}-php-cli" \
                "php${short}-php-mysqlnd" "php${short}-php-xml" "php${short}-php-gd" \
                "php${short}-php-mbstring" "php${short}-php-pecl-zip" "php${short}-php-bcmath" \
                "php${short}-php-intl" "php${short}-php-soap" "php${short}-php-pecl-redis5" >/dev/null 2>&1 &&
                enable_service_if_present "php${short}-php-fpm" ||
                warn "PHP $ver Remi packages are not available; skipped."
        done
    fi
}

install_mariadb() {
    log "Installing MariaDB..."
    if command -v mariadbd >/dev/null 2>&1 || command -v mysqld >/dev/null 2>&1; then
        enable_service_if_present mariadb
        return
    fi
    curl -fsSL https://downloads.mariadb.com/MariaDB/mariadb_repo_setup -o /tmp/dotserve-mariadb-repo.sh &&
        bash /tmp/dotserve-mariadb-repo.sh --mariadb-server-version="mariadb-11.7" >/dev/null 2>&1 ||
        warn "MariaDB official repository setup failed; using OS packages."
    if [ "$PKG_MGR" = "apt" ]; then
        apt-get update -qq || true
        DEBIAN_FRONTEND=noninteractive apt-get install -y mariadb-server mariadb-client || warn "MariaDB install failed."
    else
        dnf install -y mariadb-server mariadb || yum install -y mariadb-server mariadb || warn "MariaDB install failed."
    fi
    enable_service_if_present mariadb
}

install_redis() {
    log "Installing Redis..."
    if command -v redis-server >/dev/null 2>&1; then
        enable_service_if_present redis-server
        enable_service_if_present redis
        return
    fi
    if [ "$PKG_MGR" = "apt" ]; then
        rm -f /usr/share/keyrings/redis-archive-keyring.gpg
        curl -fsSL https://packages.redis.io/gpg | gpg --batch --yes --dearmor -o /usr/share/keyrings/redis-archive-keyring.gpg >/dev/null 2>&1 || true
        echo "deb [signed-by=/usr/share/keyrings/redis-archive-keyring.gpg] https://packages.redis.io/deb $(lsb_release -cs) main" > /etc/apt/sources.list.d/redis.list
        apt-get update -qq || true
        DEBIAN_FRONTEND=noninteractive apt-get install -y redis-server || warn "Redis install failed."
        enable_service_if_present redis-server
    else
        dnf install -y redis || yum install -y redis || warn "Redis install failed."
        enable_service_if_present redis
    fi
}

install_supervisor() {
    log "Installing Supervisor..."
    if command -v supervisord >/dev/null 2>&1; then
        enable_service_if_present supervisor
        enable_service_if_present supervisord
        return
    fi
    if [ "$PKG_MGR" = "apt" ]; then
        DEBIAN_FRONTEND=noninteractive apt-get install -y supervisor || warn "Supervisor install failed."
        enable_service_if_present supervisor
    else
        dnf install -y supervisor || yum install -y supervisor || warn "Supervisor install failed."
        enable_service_if_present supervisord
        enable_service_if_present supervisor
    fi
}

install_server_stack() {
    [ "${DOTSERVE_INSTALL_STACK:-1}" = "1" ] || { warn "Skipping server stack install because DOTSERVE_INSTALL_STACK=0"; return; }
    log "Installing server stack: OpenLiteSpeed, PHP, MariaDB, Redis, Supervisor..."
    install_openlitespeed
    install_php_stack
    install_mariadb
    install_redis
    install_supervisor
}

ensure_modern_python() {
    local major minor
    major="$(python3 -c 'import sys; print(sys.version_info[0])' 2>/dev/null || echo 0)"
    minor="$(python3 -c 'import sys; print(sys.version_info[1])' 2>/dev/null || echo 0)"
    if [ "$major" -lt 3 ] || { [ "$major" -eq 3 ] && [ "$minor" -lt 8 ]; }; then
        warn "System python3 is $major.$minor; installing python3.11"
        dnf install -y python3.11 python3.11-pip 2>/dev/null || dnf install -y python3.11
    fi
    if command -v python3.11 >/dev/null 2>&1; then
        PYTHON_BIN="python3.11"
    fi
}

prepare_source() {
    log "Preparing source checkout at $SRC_DIR..."
    if [ -d "$SRC_DIR/.git" ]; then
        git -C "$SRC_DIR" remote set-url origin "$REPO_URL" || true
        git -C "$SRC_DIR" fetch --all --prune || true
        git -C "$SRC_DIR" pull --ff-only || warn "Could not fast-forward source checkout; using existing files."
    elif [ -e "$SRC_DIR" ]; then
        local backup="${SRC_DIR}.bak.$(date +%s)"
        warn "$SRC_DIR exists but is not a git repo; moving it to $backup"
        mv "$SRC_DIR" "$backup"
        git clone "$REPO_URL" "$SRC_DIR"
    else
        git clone "$REPO_URL" "$SRC_DIR"
    fi
}

install_app_files() {
    log "Installing DotServe to $INSTALL_DIR..."
    install -d -m 0755 "$INSTALL_DIR"
    rm -rf "$INSTALL_DIR/panel" "$INSTALL_DIR/web" "$INSTALL_DIR/app.py"
    cp -a "$SRC_DIR/panel" "$SRC_DIR/web" "$SRC_DIR/app.py" "$INSTALL_DIR/"
    [ -f "$SRC_DIR/requirements.txt" ] && cp -a "$SRC_DIR/requirements.txt" "$INSTALL_DIR/"
}

setup_venv() {
    log "Setting up Python environment..."
    "$PYTHON_BIN" -m venv "$INSTALL_DIR/venv"
    "$INSTALL_DIR/venv/bin/pip" install --upgrade pip -q
    if [ -f "$INSTALL_DIR/requirements.txt" ]; then
        "$INSTALL_DIR/venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt" -q
    else
        "$INSTALL_DIR/venv/bin/pip" install flask flask-session flask-sock requests gunicorn boto3 -q
    fi
}

ensure_directories() {
    install -d -m 0750 "$INSTALL_DIR/backups" "$INSTALL_DIR/logs" "$INSTALL_DIR/sessions"
    install -d -m 0750 /var/log/dotserve
    install -d -m 0755 /etc/nginx/dotserve 2>/dev/null || true
}

create_credentials() {
    if [ -f "$INSTALL_DIR/credentials.json" ]; then
        return
    fi
    local pass hash
    pass="$(openssl rand -base64 24 | tr -d '\n')"
    hash="$(DOTSERVE_PASS="$pass" "$INSTALL_DIR/venv/bin/python" -c 'import hashlib, os; p=os.environ["DOTSERVE_PASS"]; print(hashlib.sha256(p.encode()).hexdigest())')"
    cat > "$INSTALL_DIR/credentials.json" <<EOF
{
  "username": "admin",
  "password_hash": "$hash",
  "email": "admin@dotserve.local"
}
EOF
    printf '%s\n' "$pass" > "$INSTALL_DIR/admin_password.txt"
    chmod 0600 "$INSTALL_DIR/credentials.json" "$INSTALL_DIR/admin_password.txt"
    log "Generated admin password: $pass"
}

write_service() {
    log "Writing systemd service..."
    cat > /etc/systemd/system/dotserve.service <<EOF
[Unit]
Description=DotServe Control Panel
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$INSTALL_DIR
Environment=DOTSERVE_PORT=$PANEL_PORT
ExecStart=$INSTALL_DIR/venv/bin/gunicorn --workers 4 --threads 4 --worker-class gthread --bind 0.0.0.0:$PANEL_PORT --timeout 120 --access-logfile /var/log/dotserve/access.log --error-logfile /var/log/dotserve/error.log app:app
Restart=always
RestartSec=3
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    systemctl enable dotserve
    systemctl restart dotserve
}

write_deploy_script() {
    cat > /root/deploy.sh <<EOF
#!/usr/bin/env bash
set -Eeuo pipefail
INSTALL_DIR="$INSTALL_DIR"
SRC_DIR="$SRC_DIR"
PANEL_PORT="$PANEL_PORT"
BACKUP_ROOT="\$INSTALL_DIR/update_backups"

log() { printf '[DotServe] %s\n' "\$*"; }

backup_current() {
    local backup_dir="\$BACKUP_ROOT/\$(date +%Y%m%d-%H%M%S)"
    install -d -m 0750 "\$backup_dir"
    log "Backing up current install to \$backup_dir"
    for item in panel web app.py requirements.txt; do
        [ -e "\$INSTALL_DIR/\$item" ] && cp -a "\$INSTALL_DIR/\$item" "\$backup_dir/"
    done
    for file in config.json credentials.json admin_password.txt cdn_config.json secret.key; do
        [ -f "\$INSTALL_DIR/\$file" ] && cp -a "\$INSTALL_DIR/\$file" "\$backup_dir/"
    done
    printf '%s\n' "\$backup_dir" > "\$INSTALL_DIR/.last_backup"
    find "\$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d | sort -r | tail -n +6 | xargs -r rm -rf
    printf '%s\n' "\$backup_dir"
}

deploy_files() {
    rm -rf "\$INSTALL_DIR/panel" "\$INSTALL_DIR/web" "\$INSTALL_DIR/app.py"
    cp -a "\$SRC_DIR/panel" "\$SRC_DIR/web" "\$SRC_DIR/app.py" "\$INSTALL_DIR/"
    [ -f "\$SRC_DIR/requirements.txt" ] && cp -a "\$SRC_DIR/requirements.txt" "\$INSTALL_DIR/"
    find "\$INSTALL_DIR" -name "__pycache__" -type d -prune -exec rm -rf {} + 2>/dev/null || true
}

main() {
    local backup_dir
    backup_dir="\$(backup_current)"
    git -C "\$SRC_DIR" pull --ff-only || true
    deploy_files
    systemctl restart dotserve
    sleep 2
    curl -s -o /dev/null -w "Panel: %{http_code}\n" "http://127.0.0.1:\$PANEL_PORT/" || true
    log "Deployed. Backup saved: \$backup_dir"
}

main "\$@"
EOF
    chmod 0700 /root/deploy.sh
    log "Created /root/deploy.sh"
}

write_rollback_script() {
    cat > /root/rollback.sh <<EOF
#!/usr/bin/env bash
set -Eeuo pipefail
INSTALL_DIR="$INSTALL_DIR"
BACKUP_ROOT="\$INSTALL_DIR/update_backups"

select_backup() {
    if [ "\${1:-}" ]; then
        printf '%s\n' "\$BACKUP_ROOT/\$1"
    elif [ -f "\$INSTALL_DIR/.last_backup" ]; then
        cat "\$INSTALL_DIR/.last_backup"
    else
        find "\$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d | sort -r | head -1
    fi
}

restore_backup() {
    local backup_dir="\$1"
    [ -d "\$backup_dir" ] || { echo "No backup found."; exit 1; }
    read -r -p "Restore \$backup_dir? [y/N] " confirm
    case "\$confirm" in y|Y) ;; *) echo "Cancelled."; exit 0 ;; esac
    for item in panel web app.py requirements.txt; do
        if [ -e "\$backup_dir/\$item" ]; then
            rm -rf "\$INSTALL_DIR/\$item"
            cp -a "\$backup_dir/\$item" "\$INSTALL_DIR/"
        fi
    done
    for file in config.json credentials.json admin_password.txt cdn_config.json secret.key; do
        [ -f "\$backup_dir/\$file" ] && cp -a "\$backup_dir/\$file" "\$INSTALL_DIR/"
    done
    find "\$INSTALL_DIR" -name "__pycache__" -type d -prune -exec rm -rf {} + 2>/dev/null || true
    systemctl restart dotserve
    echo "Rollback complete."
}

restore_backup "\$(select_backup "\${1:-}")"
EOF
    chmod 0700 /root/rollback.sh
    log "Created /root/rollback.sh"
}

configure_firewall() {
    log "Configuring firewall for SSH, web traffic, and panel port $PANEL_PORT..."
    local ports=("$PANEL_PORT" "80" "443" "7080" "8088")

    if command -v firewall-cmd >/dev/null 2>&1; then
        systemctl enable --now firewalld >/dev/null 2>&1 || true
        firewall-cmd --permanent --add-service=ssh >/dev/null 2>&1 || true
        for port in "${ports[@]}"; do
            firewall-cmd --permanent --add-port="$port/tcp" >/dev/null 2>&1 || true
        done
        firewall-cmd --reload >/dev/null 2>&1 || true
        return
    fi

    if command -v ufw >/dev/null 2>&1; then
        ufw allow OpenSSH >/dev/null 2>&1 || ufw allow 22/tcp >/dev/null 2>&1 || true
        for port in "${ports[@]}"; do
            ufw allow "$port/tcp" >/dev/null 2>&1 || true
        done
        if ufw status 2>/dev/null | grep -qi '^Status: active'; then
            ufw reload >/dev/null 2>&1 || true
        fi
        return
    fi

    if command -v iptables >/dev/null 2>&1; then
        for port in "${ports[@]}"; do
            iptables -C INPUT -p tcp --dport "$port" -j ACCEPT >/dev/null 2>&1 ||
                iptables -I INPUT -p tcp --dport "$port" -j ACCEPT >/dev/null 2>&1 || true
        done
        if command -v netfilter-persistent >/dev/null 2>&1; then
            netfilter-persistent save >/dev/null 2>&1 || true
        elif command -v service >/dev/null 2>&1; then
            service iptables save >/dev/null 2>&1 || true
        elif command -v iptables-save >/dev/null 2>&1 && [ -d /etc/iptables ]; then
            iptables-save > /etc/iptables/rules.v4 2>/dev/null || true
        fi
    fi
}

print_summary() {
    local ip
    ip="$(curl -fsS https://api.ipify.org 2>/dev/null || hostname -I 2>/dev/null | awk '{print $1}' || echo 'SERVER_IP')"
    log "============================================"
    log "DotServe installed successfully."
    log "URL: http://$ip:$PANEL_PORT"
    log "Username: admin"
    log "Password: $(cat "$INSTALL_DIR/admin_password.txt" 2>/dev/null || echo 'See credentials.json')"
    log "============================================"
}

main() {
    require_root
    detect_os
    install_dependencies
    install_server_stack
    prepare_source
    install_app_files
    setup_venv
    ensure_directories
    create_credentials
    write_service
    write_deploy_script
    write_rollback_script
    configure_firewall
    print_summary
}

main "$@"
