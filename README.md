# STC-TV — by DVC-it

Lokale HLS-streaming-server voor camping-/hospitality-omgevingen: een
Python HLS-proxy met live admin-monitoring, een visuele kanaal- & stream-editor,
EPG-overlay, en ondersteuning voor eigen narrowcasting-kanalen via OBS (RTMP →
HLS, met automatische splash-screen-diashow zolang er geen actieve uitzending is).

**Software & design:** Jeroen J. de Vries

## Inhoud van deze repository

```
proxy/        Python HLS-proxy (kern van het systeem)
web/          Webfrontend: TV-speler + admin (monitor & kanaal-editor)
config/       Voorbeeldconfiguratie (config.example.json, mediamtx.example.yml)
systemd/      systemd service-units
nginx/        nginx reverse-proxy configuratie (template)
scripts/      install.sh / update.sh — uitrol naar (nieuwe) servers
docs/         Uitgebreide documentatie
```

## Architectuur in het kort

- **nginx** — reverse proxy, SSL-terminatie, basis-auth op `/admin`
- **hls-proxy.py** — Python HTTP-server die externe IPTV/m3u8-bronnen ophaalt,
  segmenten cachet (lazy polling — alleen actief bij kijkers), en kanaal-config
  beheert via `config.json` (CRUD via de admin-editor, in-process herstart na wijziging)
- **MediaMTX** — RTMP-ingest voor lokale OBS-kanalen; zet OBS-uitzendingen om
  naar HLS die de proxy transparant kan doorgeven
- **Webfrontend** — speler met EPG-overlay, kanalenlijst, en een admin-omgeving
  (monitoring-dashboard + kanaal/stream-editor)

## Snel starten op een nieuwe server

```bash
git clone <repo-url> stc-tv
cd stc-tv
sudo bash scripts/install.sh
```

Zie [`docs/DEPLOY.md`](docs/DEPLOY.md) voor de volledige uitrolprocedure,
vereisten en aandachtspunten.

## Bijwerken van een bestaande installatie

```bash
cd stc-tv
git pull
sudo bash scripts/update.sh
```

`update.sh` overschrijft alléén de applicatiebestanden — `config.json`,
logo's, splash-afbeeldingen en SSL-certificaten van de server blijven intact.

## Versiebeheer

- `main`-branch = altijd een werkende, geteste versie
- Releases worden getagd: `v1.01`, `v1.02`, …
- Wijzigingen per release: zie [`CHANGELOG.md`](CHANGELOG.md)

## Belangrijk: wat hoort NIET in git

`config.json` (met echte streambronnen/inloggegevens), `.htpasswd`,
SSL-certificaten, en geüploade logo's/splash-afbeeldingen zijn servereigen en
staan in `.gitignore`. Gebruik `config/config.example.json` als startpunt en
vul de echte configuratie in via de admin-editor (`/admin/editor.html`) ná
installatie.
