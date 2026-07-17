#!/usr/bin/env bash
# DotServe recovery and stack repair helper.
# Safe to run when the panel UI is down.

set -Eeuo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/dotserve}"
PANEL_PORT="${PANEL_PORT:-3334}"
SERVICE_FILE="${SERVICE_FILE:-/etc/systemd/system/dotserve.service}"
CONFIG_FILE="${CONFIG_FILE:-$INSTALL_DIR/config.json}"
SSL_DIR="${SSL_DIR:-$INSTALL_DIR/ssl}"

OS_ID=""
OS_VERSION=""
OS_FAMILY="debian"
PKG_MGR="apt"

log() { printf '[DotServe Repair] %s\n' "$*"; }
warn() { printf '[DotServe Repair][WARN] %s\n' "$*" >&2; }
die() { printf '[DotServe Repair][ERROR] %s\n' "$*" >&2; exit 1; }

require_root() {
    [ "$(id -u)" -eq 0 ] || die "Run as root."
}

detect_os() {
    if [ -f /etc/os-release ]; then
        # shellcheck disable=SC1091
        . /etc/os-release
        OS_ID="${ID,,}"
        OS_VERSION="${VERSION_ID:-}"
    fi
    case "$OS_ID" in
        ubuntu|debian|linuxmint|pop) OS_FAMILY="debian"; PKG_MGR="apt" ;;
        fedora) OS_FAMILY="fedora"; PKG_MGR="dnf" ;;
        rhel|centos|almalinux|rocky|ol|cloudlinux) OS_FAMILY="rhel"; PKG_MGR="$(command -v dnf >/dev/null 2>&1 && echo dnf || echo yum)" ;;
        *) OS_FAMILY="debian"; PKG_MGR="$(command -v apt-get >/dev/null 2>&1 && echo apt || echo dnf)" ;;
    esac
}

pkg_update() {
    if [ "$PKG_MGR" = "apt" ]; then
        export DEBIAN_FRONTEND=noninteractive
        apt-get update -qq || true
    else
        "$PKG_MGR" makecache -q || true
        "$PKG_MGR" install -y epel-release >/dev/null 2>&1 || true
    fi
}

pkg_install() {
    if [ "$PKG_MGR" = "apt" ]; then
        export DEBIAN_FRONTEND=noninteractive
        apt-get install -y "$@"
    else
        "$PKG_MGR" install -y "$@"
    fi
}

enable_service() {
    local svc="$1"
    systemctl enable --now "$svc" >/dev/null 2>&1 || true
}

open_port() {
    local port="$1"
    if command -v firewall-cmd >/dev/null 2>&1; then
        systemctl enable --now firewalld >/dev/null 2>&1 || true
        firewall-cmd --permanent --add-port="${port}/tcp" >/dev/null 2>&1 || true
        firewall-cmd --reload >/dev/null 2>&1 || true
    elif command -v ufw >/dev/null 2>&1; then
        ufw allow "${port}/tcp" >/dev/null 2>&1 || true
    elif command -v iptables >/dev/null 2>&1; then
        iptables -C INPUT -p tcp --dport "$port" -j ACCEPT >/dev/null 2>&1 ||
            iptables -I INPUT -p tcp --dport "$port" -j ACCEPT >/dev/null 2>&1 || true
        service iptables save >/dev/null 2>&1 || true
    fi
}

json_set_panel_defaults() {
    python3 - "$CONFIG_FILE" "$PANEL_PORT" <<'PY'
import json, os, sys
path, port = sys.argv[1], int(sys.argv[2])
data = {}
if os.path.exists(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f) or {}
    except Exception:
        data = {}
data["ssl_enabled"] = False
data["port"] = port
data["panel_domain"] = ""
os.makedirs(os.path.dirname(path), exist_ok=True)
with open(path, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2)
PY
}

reset_panel_ssl() {
    log "Resetting panel HTTPS settings to plain HTTP on port $PANEL_PORT..."
    [ -f "$SERVICE_FILE" ] || die "Missing service file: $SERVICE_FILE"

    cp -a "$SERVICE_FILE" "${SERVICE_FILE}.bak.$(date +%Y%m%d-%H%M%S)"
    python3 - "$SERVICE_FILE" "$PANEL_PORT" <<'PY'
import re, sys
path, port = sys.argv[1], sys.argv[2]
text = open(path, encoding="utf-8").read()
text = re.sub(r"\s+--certfile\s+\S+", "", text)
text = re.sub(r"\s+--keyfile\s+\S+", "", text)
text = re.sub(r"(--bind|-b)\s+\S+:\d+", r"\1 0.0.0.0:" + port, text)
text = re.sub(r"Environment=DOTSERVE_COOKIE_SECURE=.*\n", "", text)
open(path, "w", encoding="utf-8").write(text)
PY

    json_set_panel_defaults
    rm -f /etc/nginx/conf.d/dotserve-https.conf 2>/dev/null || true
    open_port "$PANEL_PORT"
    systemctl daemon-reload
    systemctl restart dotserve
    sleep 2
    panel_health
}

panel_health() {
    log "Panel service status:"
    systemctl --no-pager --full status dotserve | sed -n '1,12p' || true
    log "HTTP health check:"
    curl -k -sS -o /dev/null -w 'HTTP: %{http_code}\n' "http://127.0.0.1:${PANEL_PORT}/" || true
    log "HTTPS health check:"
    curl -k -sS -o /dev/null -w 'HTTPS: %{http_code}\n' "https://127.0.0.1:${PANEL_PORT}/" || true
}

check_webserver() {
    if systemctl is-active --quiet lsws 2>/dev/null; then log "OpenLiteSpeed: running"; return 0; fi
    if systemctl is-active --quiet nginx 2>/dev/null; then log "Nginx: running"; return 0; fi
    if systemctl is-active --quiet apache2 2>/dev/null || systemctl is-active --quiet httpd 2>/dev/null; then log "Apache: running"; return 0; fi
    warn "No supported webserver is running."
    return 1
}

install_openlitespeed() {
    log "Installing OpenLiteSpeed..."
    pkg_update
    if ! command -v lshttpd >/dev/null 2>&1 && [ ! -x /usr/local/lsws/bin/lshttpd ]; then
        if curl -fsSL https://repo.litespeed.sh -o /tmp/dotserve-litespeed-repo.sh ||
           wget -qO /tmp/dotserve-litespeed-repo.sh https://repo.litespeed.sh; then
            bash /tmp/dotserve-litespeed-repo.sh >/dev/null 2>&1 || true
        fi
        if [ "$PKG_MGR" = "apt" ]; then
            pkg_install openlitespeed || warn "OpenLiteSpeed package install failed."
        else
            pkg_install openlitespeed || warn "OpenLiteSpeed package install failed."
        fi
    fi
    enable_service lsws
    open_port 80
    open_port 443
    open_port 7080
    open_port 8088
}

check_db() {
    if systemctl is-active --quiet mariadb 2>/dev/null || systemctl is-active --quiet mysql 2>/dev/null; then
        log "Database: running"
        return 0
    fi
    warn "Database is not running."
    return 1
}

install_mariadb() {
    log "Installing MariaDB..."
    pkg_update
    if ! command -v mariadbd >/dev/null 2>&1 && ! command -v mysqld >/dev/null 2>&1; then
        curl -fsSL https://downloads.mariadb.com/MariaDB/mariadb_repo_setup -o /tmp/dotserve-mariadb-repo.sh &&
            bash /tmp/dotserve-mariadb-repo.sh --mariadb-server-version="mariadb-11.7" >/dev/null 2>&1 || true
        if [ "$PKG_MGR" = "apt" ]; then
            pkg_install mariadb-server mariadb-client
        else
            pkg_install mariadb-server mariadb
        fi
    fi
    enable_service mariadb
}

check_php() {
    if command -v php >/dev/null 2>&1 || compgen -c php | grep -Eq '^php[0-9]+\.[0-9]+$'; then
        log "PHP: installed"
        return 0
    fi
    warn "PHP is not installed."
    return 1
}

php_binary_for_version() {
    local version="$1"
    local short="${version/./}"
    local candidates=(
        "/usr/bin/php${version}"
        "/usr/local/bin/php${version}"
        "/opt/remi/php${short}/root/usr/bin/php"
        "/usr/bin/php${short}"
    )
    local bin
    for bin in "${candidates[@]}"; do
        if [ -x "$bin" ]; then
            printf '%s\n' "$bin"
            return 0
        fi
    done
    return 1
}

install_php_attempt() {
    local version="$1"
    local short="${version/./}"
    local log_file="/tmp/dotserve-php-${short}.log"
    shift

    rm -f "$log_file"
    if "$@" >"$log_file" 2>&1; then
        return 0
    fi

    local reason
    reason="$(tail -n 5 "$log_file" 2>/dev/null | tr '\n' ' ' | sed 's/[[:space:]][[:space:]]*/ /g' | cut -c1-260)"
    warn "PHP ${version} was not installed: ${reason:-package not available on this OS/repository}"
    warn "Install log: $log_file"
    return 1
}

install_php_optional_package() {
    local version="$1"
    local package="$2"
    local short="${version/./}"
    local log_file="/tmp/dotserve-php-${short}-${package//[^A-Za-z0-9_.-]/_}.log"

    if pkg_install "$package" >"$log_file" 2>&1; then
        log "PHP ${version} extension installed: ${package}"
        return 0
    fi
    warn "PHP ${version} extension skipped: ${package} (not available). Log: $log_file"
    return 1
}

install_php() {
    log "Installing PHP 7.4-8.5 where available..."
    log "Detected OS: ${OS_ID:-unknown} ${OS_VERSION:-} (${OS_FAMILY}, package manager: ${PKG_MGR})"
    pkg_update
    local versions=(7.4 8.0 8.1 8.2 8.3 8.4 8.5)
    local installed=()
    local skipped=()

    if [ "$PKG_MGR" = "apt" ]; then
        log "Preparing PHP repository..."
        pkg_install lsb-release ca-certificates apt-transport-https software-properties-common gnupg curl || true
        if [ "$OS_ID" = "ubuntu" ]; then
            if add-apt-repository -y ppa:ondrej/php >/tmp/dotserve-php-repo.log 2>&1; then
                log "Enabled Ondrej PHP PPA."
            else
                warn "Could not enable Ondrej PHP PPA. Continuing with configured apt repositories. Log: /tmp/dotserve-php-repo.log"
            fi
        else
            curl -fsSL https://packages.sury.org/php/apt.gpg -o /usr/share/keyrings/deb.sury.org-php.gpg >/dev/null 2>&1 || true
            echo "deb [signed-by=/usr/share/keyrings/deb.sury.org-php.gpg] https://packages.sury.org/php/ $(lsb_release -sc) main" > /etc/apt/sources.list.d/php-sury.list
            log "Enabled Sury PHP repository."
        fi
        apt-get update -qq || true
        for v in "${versions[@]}"; do
            log "Installing PHP ${v}..."
            local required=(
                "php${v}" "php${v}-fpm" "php${v}-cli" "php${v}-common"
                "php${v}-mysql" "php${v}-xml" "php${v}-curl" "php${v}-gd"
                "php${v}-mbstring" "php${v}-zip" "php${v}-bcmath" "php${v}-intl"
                "php${v}-soap" "php${v}-opcache" "php${v}-readline"
            )
            if install_php_attempt "$v" apt-get install -y "${required[@]}"; then
                local optional=(
                    "php${v}-redis" "php${v}-imagick" "php${v}-imap" "php${v}-ldap"
                    "php${v}-gmp" "php${v}-pgsql" "php${v}-sqlite3" "php${v}-exif"
                    "php${v}-sodium" "php${v}-xmlrpc" "php${v}-igbinary" "php${v}-memcached"
                    "php${v}-ioncube" "php${v}-sourceguardian" "php${v}-snmp"
                    "php${v}-tidy" "php${v}-xsl" "php${v}-yaml" "php${v}-mailparse"
                    "php${v}-mongodb" "php${v}-amqp" "php${v}-ssh2" "php${v}-uuid"
                    "php${v}-maxminddb" "php${v}-uploadprogress" "php${v}-pspell"
                )
                local ext
                for ext in "${optional[@]}"; do
                    install_php_optional_package "$v" "$ext" || true
                done
                enable_service "php${v}-fpm"
                local bin
                bin="$(php_binary_for_version "$v" || true)"
                log "PHP ${v} installed${bin:+: $($bin -v 2>/dev/null | head -n 1)}"
                installed+=("$v")
            else
                skipped+=("$v")
            fi
        done
    else
        local rhel_major
        rhel_major="$(rpm -E '%{rhel}' 2>/dev/null || true)"
        if ! [[ "$rhel_major" =~ ^[0-9]+$ ]]; then
            rhel_major="${OS_VERSION%%.*}"
        fi
        if ! [[ "$rhel_major" =~ ^[0-9]+$ ]]; then
            rhel_major="9"
        fi

        log "Preparing EPEL/Remi repositories for RHEL-compatible major version ${rhel_major}..."
        pkg_install epel-release dnf-utils yum-utils ca-certificates curl || true
        if pkg_install "https://rpms.remirepo.net/enterprise/remi-release-${rhel_major}.rpm" >/tmp/dotserve-php-remi.log 2>&1; then
            log "Enabled Remi repository."
        else
            warn "Could not enable Remi repository. Continuing with configured repositories. Log: /tmp/dotserve-php-remi.log"
        fi
        "$PKG_MGR" makecache -q || true

        for v in "${versions[@]}"; do
            local short="${v/./}"
            log "Installing PHP ${v}..."
            local required=(
                "php${short}-php" "php${short}-php-fpm" "php${short}-php-cli"
                "php${short}-php-common" "php${short}-php-mysqlnd" "php${short}-php-xml"
                "php${short}-php-gd" "php${short}-php-mbstring" "php${short}-php-pecl-zip"
                "php${short}-php-bcmath" "php${short}-php-intl" "php${short}-php-soap"
                "php${short}-php-opcache" "php${short}-php-process"
            )
            if install_php_attempt "$v" pkg_install "${required[@]}"; then
                local optional=(
                    "php${short}-php-pecl-redis5" "php${short}-php-pecl-imagick-im7"
                    "php${short}-php-pecl-imagick" "php${short}-php-imap"
                    "php${short}-php-ldap" "php${short}-php-gmp" "php${short}-php-pgsql"
                    "php${short}-php-sqlite3" "php${short}-php-sodium"
                    "php${short}-php-pecl-igbinary" "php${short}-php-pecl-memcached"
                    "php${short}-php-ioncube-loader" "php${short}-php-sourceguardian"
                    "php${short}-php-snmp" "php${short}-php-tidy" "php${short}-php-xsl"
                    "php${short}-php-pecl-yaml" "php${short}-php-pecl-mailparse"
                    "php${short}-php-pecl-mongodb" "php${short}-php-pecl-amqp"
                    "php${short}-php-pecl-ssh2" "php${short}-php-pecl-uuid"
                    "php${short}-php-pecl-maxminddb" "php${short}-php-pecl-uploadprogress"
                    "php${short}-php-pspell"
                )
                local ext
                for ext in "${optional[@]}"; do
                    install_php_optional_package "$v" "$ext" || true
                done
                enable_service "php${short}-php-fpm"
                if [ -x "/opt/remi/php${short}/root/usr/bin/php" ]; then
                    ln -sf "/opt/remi/php${short}/root/usr/bin/php" "/usr/local/bin/php${v}"
                fi
                local bin
                bin="$(php_binary_for_version "$v" || true)"
                log "PHP ${v} installed${bin:+: $($bin -v 2>/dev/null | head -n 1)}"
                installed+=("$v")
            else
                skipped+=("$v")
            fi
        done
    fi

    if [ "${#installed[@]}" -eq 0 ]; then
        die "No PHP versions were installed. Check /tmp/dotserve-php-*.log and repository connectivity."
    fi

    log "Installed PHP versions: ${installed[*]}"
    if [ "${#skipped[@]}" -gt 0 ]; then
        warn "Skipped PHP versions: ${skipped[*]}"
    fi
    log "Versioned PHP commands are available as php7.4/php8.0/etc where the OS package exposes them; on RHEL-compatible systems DotServe also creates /usr/local/bin/phpX.Y symlinks for Remi packages."
}

check_redis() {
    if systemctl is-active --quiet redis-server 2>/dev/null || systemctl is-active --quiet redis 2>/dev/null; then
        log "Redis: running"
        return 0
    fi
    warn "Redis is not running."
    return 1
}

install_redis() {
    log "Installing Redis..."
    pkg_update
    if [ "$PKG_MGR" = "apt" ]; then
        pkg_install redis-server
        enable_service redis-server
    else
        pkg_install redis
        enable_service redis
    fi
}

check_supervisor() {
    if systemctl is-active --quiet supervisor 2>/dev/null || systemctl is-active --quiet supervisord 2>/dev/null; then
        log "Supervisor: running"
        return 0
    fi
    warn "Supervisor is not running."
    return 1
}

install_supervisor() {
    log "Installing Supervisor..."
    pkg_update
    pkg_install supervisor
    enable_service supervisor
    enable_service supervisord
}

check_phpmyadmin() {
    if [ -d /usr/share/phpmyadmin ] &&
        [ -f /usr/share/phpmyadmin/index.php ] &&
        [ -f /usr/share/phpmyadmin/config.inc.php ] &&
        systemctl is-active --quiet dotserve-phpmyadmin 2>/dev/null; then
        log "phpMyAdmin: installed"
        return 0
    fi
    warn "phpMyAdmin is not installed or not running."
    return 1
}

phpmyadmin_php_bin() {
    local candidates=(
        /usr/bin/php8.4 /usr/bin/php8.3 /usr/bin/php8.2 /usr/bin/php8.1
        /usr/bin/php8.0 /usr/bin/php7.4 /usr/bin/php
        /usr/local/bin/php8.4 /usr/local/bin/php8.3 /usr/local/bin/php8.2
        /usr/local/bin/php8.1 /usr/local/bin/php8.0 /usr/local/bin/php7.4
        /opt/remi/php84/root/usr/bin/php /opt/remi/php83/root/usr/bin/php
        /opt/remi/php82/root/usr/bin/php /opt/remi/php81/root/usr/bin/php
        /opt/remi/php80/root/usr/bin/php /opt/remi/php74/root/usr/bin/php
    )
    local bin
    for bin in "${candidates[@]}"; do
        [ -x "$bin" ] && { printf '%s\n' "$bin"; return 0; }
    done
    command -v php 2>/dev/null || return 1
}

phpmyadmin_sql() {
    local sql_file
    sql_file="$(mktemp /tmp/dotserve-pma-sql.XXXXXX)"
    cat > "$sql_file"
    local cli="mysql"
    command -v mariadb >/dev/null 2>&1 && cli="mariadb"
    local sockets=(/run/mysqld/mysqld.sock /var/run/mysqld/mysqld.sock /tmp/mysql.sock)
    local sock
    for sock in "${sockets[@]}"; do
        if [ -S "$sock" ] && "$cli" -u root --socket="$sock" < "$sql_file" >/tmp/dotserve-pma-sql.log 2>&1; then
            rm -f "$sql_file"
            return 0
        fi
    done
    if "$cli" -u root < "$sql_file" >/tmp/dotserve-pma-sql.log 2>&1; then
        rm -f "$sql_file"
        return 0
    fi
    rm -f "$sql_file"
    warn "Could not configure phpMyAdmin database user. Log: /tmp/dotserve-pma-sql.log"
    return 1
}

phpmyadmin_autologin_config() {
    log "Configuring phpMyAdmin automatic login..."
    install -d -m 0700 /etc/dotserve
    install -d -m 1777 /var/lib/phpmyadmin/tmp

    local user="dotserve_pma"
    local password secret
    password="$(openssl rand -hex 32)"
    secret="$(openssl rand -hex 32)"

    cat > /etc/dotserve/phpmyadmin.json <<EOF
{"user":"$user","password":"$password","blowfish_secret":"$secret"}
EOF
    chmod 0600 /etc/dotserve/phpmyadmin.json

    phpmyadmin_sql <<EOF
CREATE USER IF NOT EXISTS '$user'@'localhost' IDENTIFIED BY '$password';
ALTER USER '$user'@'localhost' IDENTIFIED BY '$password';
GRANT ALL PRIVILEGES ON *.* TO '$user'@'localhost' WITH GRANT OPTION;
FLUSH PRIVILEGES;
EOF

    cat > /usr/share/phpmyadmin/config.inc.php <<EOF
<?php
declare(strict_types=1);

\$cfg['blowfish_secret'] = '$secret';
\$i = 0;
\$i++;
\$cfg['Servers'][\$i]['auth_type'] = 'config';
\$cfg['Servers'][\$i]['host'] = 'localhost';
\$cfg['Servers'][\$i]['connect_type'] = 'tcp';
\$cfg['Servers'][\$i]['compress'] = false;
\$cfg['Servers'][\$i]['AllowNoPassword'] = false;
\$cfg['Servers'][\$i]['AllowRoot'] = false;
\$cfg['Servers'][\$i]['user'] = '$user';
\$cfg['Servers'][\$i]['password'] = '$password';
\$cfg['UploadDir'] = '';
\$cfg['SaveDir'] = '';
\$cfg['TempDir'] = '/var/lib/phpmyadmin/tmp';
EOF
    chmod 0644 /usr/share/phpmyadmin/config.inc.php
}

install_phpmyadmin_service() {
    local php_bin user
    php_bin="$(phpmyadmin_php_bin)" || die "No PHP CLI binary found. Run install-php first."
    user="nobody"
    id www-data >/dev/null 2>&1 && user="www-data"
    id apache >/dev/null 2>&1 && user="apache"
    id nginx >/dev/null 2>&1 && user="nginx"

    log "Creating phpMyAdmin service on port 8082 using $php_bin as $user..."
    cat > /etc/systemd/system/dotserve-phpmyadmin.service <<EOF
[Unit]
Description=DotServe phpMyAdmin
After=network.target mariadb.service mysql.service

[Service]
Type=simple
User=$user
WorkingDirectory=/usr/share/phpmyadmin
ExecStart=$php_bin -d variables_order=EGPCS -S 0.0.0.0:8082 -t /usr/share/phpmyadmin
Restart=always
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    systemctl enable --now dotserve-phpmyadmin
    open_port 8082
}

install_phpmyadmin() {
    log "Installing phpMyAdmin..."
    check_db || install_mariadb
    check_php || install_php
    pkg_update
    pkg_install wget tar gzip openssl >/dev/null 2>&1 || true
    rm -rf /tmp/dotserve-pma.tar.gz /tmp/dotserve-pma
    wget -q https://files.phpmyadmin.net/phpMyAdmin/5.2.2/phpMyAdmin-5.2.2-all-languages.tar.gz -O /tmp/dotserve-pma.tar.gz ||
        die "Could not download phpMyAdmin 5.2.2."
    rm -rf /usr/share/phpmyadmin
    install -d -m 0755 /usr/share/phpmyadmin
    tar -xzf /tmp/dotserve-pma.tar.gz -C /usr/share/phpmyadmin --strip-components=1
    phpmyadmin_autologin_config
    install_phpmyadmin_service
    log "phpMyAdmin installed: http://SERVER-IP:8082"
}

doctor() {
    panel_health
    check_webserver || true
    check_db || true
    check_php || true
    check_redis || true
    check_supervisor || true
    check_phpmyadmin || true
}

repair_stack() {
    check_webserver || install_openlitespeed
    check_db || install_mariadb
    check_php || install_php
    check_redis || install_redis
    check_supervisor || install_supervisor
    check_phpmyadmin || install_phpmyadmin
}

usage() {
    cat <<EOF
Usage: $0 <action>

Panel recovery:
  reset-panel-ssl       Disable panel HTTPS and restore HTTP on port $PANEL_PORT
  panel-health          Show dotserve status and HTTP/HTTPS local checks

Stack checks:
  doctor                Check panel, webserver, DB, PHP, Redis, Supervisor
  check-webserver       Check OpenLiteSpeed/Nginx/Apache
  check-db              Check MariaDB/MySQL
  check-php             Check PHP
  check-redis           Check Redis
  check-supervisor      Check Supervisor
  check-phpmyadmin      Check phpMyAdmin

Install/repair:
  repair-stack          Install missing webserver, MariaDB, PHP, Redis, Supervisor, phpMyAdmin
  install-openlitespeed Install OpenLiteSpeed
  install-mariadb       Install MariaDB
  install-php           Install PHP 7.4-8.5 where available
  install-redis         Install Redis
  install-supervisor    Install Supervisor
  install-phpmyadmin    Install phpMyAdmin with DotServe automatic login
EOF
}

main() {
    require_root
    detect_os
    case "${1:-}" in
        reset-panel-ssl|restore-http) reset_panel_ssl ;;
        panel-health|status) panel_health ;;
        doctor) doctor ;;
        check-webserver) check_webserver ;;
        check-db) check_db ;;
        check-php) check_php ;;
        check-redis) check_redis ;;
        check-supervisor) check_supervisor ;;
        check-phpmyadmin) check_phpmyadmin ;;
        repair-stack) repair_stack ;;
        install-openlitespeed) install_openlitespeed ;;
        install-mariadb) install_mariadb ;;
        install-php) install_php ;;
        install-redis) install_redis ;;
        install-supervisor) install_supervisor ;;
        install-phpmyadmin) install_phpmyadmin ;;
        -h|--help|help|"") usage ;;
        *) usage; exit 2 ;;
    esac
}

main "$@"
