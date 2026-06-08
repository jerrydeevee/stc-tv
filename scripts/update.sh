#!/usr/bin/env bash
#
# STC-TV — update van een bestaande installatie naar de versie in deze repo
# Gebruik: sudo bash update.sh
#
# Werkt alleen de applicatiebestanden bij (proxy + webpagina's + service-units +
# nginx-template). Laat config.json, logo's, splash-afbeeldingen en SSL-certs
# ONGEMOEID — die zijn servereigen.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WEB_ROOT="/var/www/html"
APP_DIR="/opt/camping-tv"

if [[ $EUID -ne 0 ]]; then
  echo "Dit script moet als root draaien (sudo bash update.sh)"; exit 1
fi

VERSION="$(cat "$REPO_DIR/VERSION" 2>/dev/null || echo '?')"
echo "=== STC-TV bijwerken naar versie $VERSION ==="

echo "[1/5] Back-up van huidige bestanden naar /opt/camping-tv/backups/…"
BACKUP_DIR="$APP_DIR/backups/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$BACKUP_DIR"
cp -a "$APP_DIR/hls-proxy.py"          "$BACKUP_DIR/" 2>/dev/null || true
cp -a "$WEB_ROOT/index.html"           "$BACKUP_DIR/" 2>/dev/null || true
cp -a "$WEB_ROOT/admin/index.html"     "$BACKUP_DIR/admin-index.html" 2>/dev/null || true
cp -a "$WEB_ROOT/admin/editor.html"    "$BACKUP_DIR/" 2>/dev/null || true

echo "[2/5] Applicatiebestanden bijwerken…"
cp "$REPO_DIR/proxy/hls-proxy.py"        "$APP_DIR/hls-proxy.py"
cp "$REPO_DIR/web/index.html"            "$WEB_ROOT/index.html"
cp "$REPO_DIR/web/admin/index.html"      "$WEB_ROOT/admin/index.html"
cp "$REPO_DIR/web/admin/editor.html"     "$WEB_ROOT/admin/editor.html"

echo "[3/5] systemd-units en nginx-template bijwerken…"
cp "$REPO_DIR/systemd/camping-tv-proxy.service" /etc/systemd/system/
cp "$REPO_DIR/systemd/mediamtx.service"          /etc/systemd/system/
systemctl daemon-reload
# nginx-template alleen tonen als verschillend — handmatige merge i.v.m. eventuele lokale aanpassingen
if ! diff -q "$REPO_DIR/nginx/camping.conf.template" /etc/nginx/sites-available/camping >/dev/null 2>&1; then
  cp "$REPO_DIR/nginx/camping.conf.template" /etc/nginx/sites-available/camping.new
  echo "   ⚠ nginx-config is gewijzigd t.o.v. de repo-versie."
  echo "     Nieuwe versie weggeschreven als: /etc/nginx/sites-available/camping.new"
  echo "     Vergelijk handmatig en voer over indien gewenst (mv camping.new camping)."
fi

echo "[4/5] Eigendom herstellen…"
chown -R www-data:www-data "$APP_DIR" "$WEB_ROOT/admin"
chown www-data:www-data "$WEB_ROOT/index.html"

echo "[5/5] Services herstarten…"
nginx -t
systemctl restart camping-tv-proxy nginx
sleep 2
systemctl is-active camping-tv-proxy nginx mediamtx

echo
echo "=== Bijgewerkt naar versie $VERSION ==="
echo "Back-up van vorige bestanden: $BACKUP_DIR"
