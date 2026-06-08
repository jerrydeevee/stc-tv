# STC-TV — Uitrol op een nieuwe server

## Vereisten

- Ubuntu/Debian-server (getest op Ubuntu) met root-/sudo-toegang
- Netwerktoegang tot de gewenste IPTV/stream-bronnen
- (Optioneel) een eigen `logo.png` voor branding

## 1. Repository ophalen

```bash
git clone <repo-url> stc-tv
cd stc-tv
```

## 2. Installeren

```bash
sudo bash scripts/install.sh
```

Het script:
1. Installeert pakketten (nginx, python3, python3-pil, MediaMTX)
2. Maakt de mappenstructuur aan (`/opt/camping-tv`, `/var/www/html/...`)
3. Plaatst de applicatiebestanden
4. Plaatst `config.example.json` als startpunt (alléén als er nog geen
   `config.json` bestaat — bestaande installaties blijven onaangeroerd)
5. Installeert en activeert de systemd-services
6. Configureert nginx (genereert een zelfondertekend SSL-certificaat als
   er nog geen is, en vraagt om een admin-gebruikersnaam/wachtwoord)
7. Start alle services en toont de status

## 3. Eerste configuratie

Na installatie:

1. Open `https://<server-ip>/admin/editor.html` (log in met het zojuist
   aangemaakte admin-account)
2. Vul de **globale instellingen** in (cache, timeouts, user-agent)
3. Voeg je eigen kanalen toe:
   - **Extern**: stream-URL (m3u8) van je IPTV-bron
   - **Lokaal (OBS)**: kies een stream-key, vink eventueel "startkanaal" aan,
     en upload splash-afbeeldingen voor wanneer er geen actieve uitzending is
4. Upload eigen kanaallogo's per kanaal
5. Plaats je eigen `logo.png` in `/var/www/html/logo.png` voor de branding
   in de speler en admin-omgeving

## 4. Bijwerken naar een nieuwe versie

```bash
cd stc-tv
git pull
sudo bash scripts/update.sh
```

`update.sh`:
- Maakt een back-up van de huidige bestanden in `/opt/camping-tv/backups/<tijdstempel>/`
- Werkt **alleen** de applicatiebestanden bij (proxy, webfrontend, service-units)
- **Raakt nooit** `config.json`, logo's, splash-afbeeldingen of SSL-certificaten aan
- Waarschuwt als de nginx-template afwijkt van de huidige serverconfiguratie
  (voor handmatige review — voorkomt dat lokale aanpassingen verloren gaan)

## 5. Belangrijke paden op de server

| Pad | Inhoud |
|---|---|
| `/opt/camping-tv/hls-proxy.py` | De HLS-proxy (kern van het systeem) |
| `/opt/camping-tv/config.json` | Kanalen + globale instellingen (servereigen) |
| `/opt/mediamtx/` | RTMP-ingest voor lokale OBS-kanalen |
| `/var/www/html/` | Webfrontend (speler + admin) |
| `/var/www/html/logos/` | Kanaal-logo's (servereigen, geüpload via editor) |
| `/var/www/html/splash/<num>/` | Splash-afbeeldingen per lokaal kanaal |
| `/etc/nginx/sites-available/camping` | nginx-configuratie |
| `/etc/nginx/.htpasswd` | Inloggegevens voor `/admin` |

## 6. Diagnose

```bash
systemctl status camping-tv-proxy mediamtx nginx
journalctl -u camping-tv-proxy -f      # live-logs van de proxy
curl -sk https://localhost/stream/stats   # statistieken-endpoint
```

## 7. OBS instellen voor lokale kanalen

In OBS, onder *Instellingen → Uitzending*:
- **Service**: Aangepast
- **Server**: `rtmp://<server-ip>/`
- **Stream-key**: de key die je in de kanaal-editor hebt ingesteld
  (bv. `local2`)

De HLS-uitvoer wordt automatisch beschikbaar zodra OBS verbinding maakt; de
speler schakelt zelf tussen de splash-diashow en de live-uitzending.
