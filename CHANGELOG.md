# Changelog

## v1.01 — 2026-06-08

Eerste gerepliceerde release: de complete, op productie (10.0.1.132) geteste
versie van het systeem, samengevoegd in één uitrolbare repository.

### Toegevoegd
- **Per-IP kijker-tracking** met bandbreedtegegevens in het admin-monitordashboard
- **Responsieve, transparante EPG-overlay** (65% transparant, gecentreerd,
  schaalt automatisch mee op mobiel) inclusief klein zender-logo
- **Kanaallogo's** in zenderlijst, EPG-overlay en admin-monitor
- **Kanaal & Stream Editor** (`/admin/editor.html`): volledige CRUD op kanalen
  en globale proxy-instellingen, met logo-upload, EPG tvg-id-koppeling,
  per-kanaal proxy-overrides (poll-interval, TTL, timeouts, user-agent),
  validatie en automatische in-process herstart van de proxy na opslaan
- **Unified `config.json`**-configuratie (vervangt het oude `streams.conf` +
  `channels.json`-duo); `channels.json` voor de speler wordt nu automatisch
  gegenereerd uit `config.json` bij elke wijziging
- **Lokale OBS-narrowcasting-kanalen**: RTMP-ingest via MediaMTX, automatische
  RTMP→HLS-omzetting, live-detectie, en transparante doorgifte via de bestaande
  proxy-infrastructuur (incl. correcte afhandeling van MediaMTX's
  master-/media-playlist-structuur met sessie-cookies)
- **Splash-screen-diashow**: vrij te uploaden afbeeldingen per lokaal kanaal,
  automatisch passend gemaakt naar HD-formaat (1920×1080, letterbox),
  instelbare wisselinterval, getoond zolang er geen actieve OBS-uitzending is
- **Startkanaal**: instelbaar per lokaal kanaal (max. 1), wordt bij opstarten
  van de speler als eerste getoond, ongeacht de positie in de zenderlijst
- Branding/credits: "STC-TV by DVC-it" op admin-pagina's, "Software & design:
  Jeroen J. de Vries" in de EPG-overlay

### Opgelost
- nginx upload-limiet verhoogd (150 MB) zodat HD-splash-/logo-uploads niet
  meer stranden op een 413-fout
- Bestandsrechten op `/var/www/html/logos` gecorrigeerd (eigendom www-data)
- Veilige JSON-foutafhandeling in de editor (geen cryptische
  "Unexpected token '<'"-meldingen meer bij server-/timeoutfouten)
