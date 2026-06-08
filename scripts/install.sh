#!/usr/bin/env bash
#
# STC-TV — installatiescript voor een verse Ubuntu/Debian-server
# Gebruik: sudo bash install.sh
#
# Dit script is idempotent: opnieuw draaien op een bestaande installatie
# overschrijft geen config.json, logo's of splash-afbeeldingen.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WEB_ROOT="/var/www/html"
APP_DIR="/opt/camping-tv"
MEDIAMTX_DIR="/opt/mediamtx"
MEDIAMTX_VERSION="v1.19.0"

if [[ $EUID -ne 0 ]]; then
  echo "Dit script moet als root draaien (sudo bash install.sh)"; exit 1
fi

echo "=== STC-TV installatie ==="
echo "Repo:        $REPO_DIR"
echo "Web-root:    $WEB_ROOT"
echo "App-dir:     $APP_DIR"
echo

# ── 1. Pakketten ─────────────────────────────────────────────────────────────
echo "[1/8] Systeempakketten installeren…"
apt-get update -qq
apt-get install -y --no-install-recommends \
    nginx python3 python3-pil curl ca-certificates openssl

# ── 2. Mappenstructuur ───────────────────────────────────────────────────────
echo "[2/8] Mappenstructuur aanmaken…"
mkdir -p "$APP_DIR" "$WEB_ROOT/admin" "$WEB_ROOT/logos" "$WEB_ROOT/splash"
mkdir -p "$MEDIAMTX_DIR"

# ── 3. Applicatiebestanden plaatsen ──────────────────────────────────────────
echo "[3/8] Applicatiebestanden kopiëren…"
cp "$REPO_DIR/proxy/hls-proxy.py" "$APP_DIR/hls-proxy.py"
cp "$REPO_DIR/web/index.html"      "$WEB_ROOT/index.html"
cp "$REPO_DIR/web/admin/index.html" "$WEB_ROOT/admin/index.html"
cp "$REPO_DIR/web/admin/editor.html" "$WEB_ROOT/admin/editor.html"
[[ -f "$REPO_DIR/web/logo.png" ]] && cp "$REPO_DIR/web/logo.png" "$WEB_ROOT/logo.png"

# Config alleen plaatsen als er nog geen bestaat — bestaande installatie nooit overschrijven
if [[ ! -f "$APP_DIR/config.json" ]]; then
  cp "$REPO_DIR/config/config.example.json" "$APP_DIR/config.json"
  echo "   → config.json aangemaakt vanuit template. Pas deze aan via /admin/editor.html"
else
  echo "   → bestaande config.json behouden (niet overschreven)"
fi

# ── 4. MediaMTX (RTMP→HLS ingest voor lokale OBS-kanalen) ───────────────────
echo "[4/8] MediaMTX installeren (indien nog niet aanwezig)…"
if [[ ! -x "$MEDIAMTX_DIR/mediamtx" ]]; then
  ARCH="$(uname -m)"
  case "$ARCH" in
    x86_64)  MTX_ARCH="amd64" ;;
    aarch64) MTX_ARCH="arm64v8" ;;
    *) echo "Onbekende architectuur: $ARCH — installeer MediaMTX handmatig"; MTX_ARCH="" ;;
  esac
  if [[ -n "$MTX_ARCH" ]]; then
    TMP_TGZ="$(mktemp)"
    curl -fsSL -o "$TMP_TGZ" \
      "https://github.com/bluenviron/mediamtx/releases/download/${MEDIAMTX_VERSION}/mediamtx_${MEDIAMTX_VERSION}_linux_${MTX_ARCH}.tar.gz"
    tar -xzf "$TMP_TGZ" -C "$MEDIAMTX_DIR" mediamtx
    rm -f "$TMP_TGZ"
    chmod +x "$MEDIAMTX_DIR/mediamtx"
  fi
fi
if [[ ! -f "$MEDIAMTX_DIR/mediamtx.yml" ]]; then
  cp "$REPO_DIR/config/mediamtx.example.yml" "$MEDIAMTX_DIR/mediamtx.yml"
fi

# ── 5. systemd services ──────────────────────────────────────────────────────
echo "[5/8] systemd-services installeren…"
cp "$REPO_DIR/systemd/camping-tv-proxy.service" /etc/systemd/system/
cp "$REPO_DIR/systemd/mediamtx.service"          /etc/systemd/system/
systemctl daemon-reload
systemctl enable camping-tv-proxy mediamtx

# ── 6. nginx ─────────────────────────────────────────────────────────────────
echo "[6/8] nginx configureren…"
mkdir -p /etc/nginx/ssl
if [[ ! -f /etc/nginx/ssl/camping.crt ]]; then
  echo "   → zelfondertekend SSL-certificaat genereren…"
  openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
    -keyout /etc/nginx/ssl/camping.key \
    -out    /etc/nginx/ssl/camping.crt \
    -subj "/CN=stc-tv" >/dev/null 2>&1
fi
cp "$REPO_DIR/nginx/camping.conf.template" /etc/nginx/sites-available/camping
ln -sf /etc/nginx/sites-available/camping /etc/nginx/sites-enabled/camping
rm -f /etc/nginx/sites-enabled/default

if [[ ! -f /etc/nginx/.htpasswd ]]; then
  echo "   → admin-wachtwoord aanmaken (voor /admin)…"
  read -rp "   Gebruikersnaam voor admin-omgeving [admin]: " ADMIN_USER
  ADMIN_USER="${ADMIN_USER:-admin}"
  htpasswd -c -B /etc/nginx/.htpasswd "$ADMIN_USER" || \
    apt-get install -y apache2-utils && htpasswd -c -B /etc/nginx/.htpasswd "$ADMIN_USER"
fi

# ── 7. Eigendom & rechten ────────────────────────────────────────────────────
echo "[7/8] Eigendom en rechten instellen…"
chown -R www-data:www-data "$APP_DIR" "$MEDIAMTX_DIR" \
    "$WEB_ROOT/logos" "$WEB_ROOT/splash" "$WEB_ROOT/admin"
chown www-data:www-data "$WEB_ROOT/index.html" "$WEB_ROOT/admin/index.html" "$WEB_ROOT/admin/editor.html"

# ── 8. Services starten ──────────────────────────────────────────────────────
echo "[8/8] Services (her)starten…"
nginx -t
systemctl restart nginx mediamtx camping-tv-proxy

sleep 3
echo
echo "=== Installatie voltooid ==="
echo "Status:"
systemctl is-active nginx mediamtx camping-tv-proxy | paste -d' ' <(echo -e "nginx:\nmediamtx:\ncamping-tv-proxy:") -

echo
echo "Open https://<server-ip>/        → TV-speler"
echo "Open https://<server-ip>/admin/  → Monitor + Kanaal-editor (login met je admin-wachtwoord)"
echo
echo "Vergeet niet:"
echo "  • config.json in te vullen via de admin-editor (eigen IPTV-bronnen / lokale kanalen)"
echo "  • logo.png (eigen branding) te plaatsen in $WEB_ROOT/logo.png"
