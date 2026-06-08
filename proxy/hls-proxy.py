#!/usr/bin/env python3
"""
Camping TV — HLS Proxy met statistieken en config-API
Pre-downloadt segmenten voor actieve kanalen, exposeert /stats en /admin/config.
Config (kanalen + globale instellingen) leeft in config.json; wijzigingen via
de admin-editor herstarten het proces in-place (os.execv) met nieuwe instellingen.
"""
import http.server
import urllib.request
import urllib.parse
import urllib.error
import http.cookiejar
import threading
import time
import re
import json
import os
import sys
import io
from PIL import Image

HD_SIZE = (1920, 1080)

def fit_to_hd(raw):
    """Past een geüploade afbeelding aan naar HD-formaat (1920x1080):
    schaalt passend binnen het kader en vult de rand op met zwart (letterbox)."""
    img = Image.open(io.BytesIO(raw))
    img = img.convert("RGB")
    src_w, src_h = img.size
    scale = min(HD_SIZE[0] / src_w, HD_SIZE[1] / src_h)
    new_w, new_h = max(1, round(src_w * scale)), max(1, round(src_h * scale))
    resized = img.resize((new_w, new_h), Image.LANCZOS)
    canvas = Image.new("RGB", HD_SIZE, (0, 0, 0))
    canvas.paste(resized, ((HD_SIZE[0] - new_w) // 2, (HD_SIZE[1] - new_h) // 2))
    out = io.BytesIO()
    canvas.save(out, format="JPEG", quality=88)
    return out.getvalue()

CONFIG_FILE   = "/opt/camping-tv/config.json"
LISTEN_HOST   = "127.0.0.1"
LISTEN_PORT   = 8888
MEDIAMTX_HLS  = "http://127.0.0.1:8889"
MEDIAMTX_API  = "http://127.0.0.1:9997"
START_TIME    = time.time()

DEFAULT_GLOBAL = {
    "seg_cache_max":    60,
    "poll_interval":    6,
    "active_ttl":       120,
    "upstream_timeout": 15,
    "fetch_timeout":    20,
    "user_agent":       "VLC/3.0 LibVLC/3.0",
}

def load_config():
    try:
        with open(CONFIG_FILE) as f:
            cfg = json.load(f)
    except Exception as e:
        print(f"Fout bij laden config: {e} — lege config", flush=True)
        cfg = {"global": {}, "channels": []}
    g = dict(DEFAULT_GLOBAL)
    g.update(cfg.get("global") or {})
    cfg["global"] = g
    chs = {}
    for ch in cfg.get("channels", []):
        try:
            num = int(ch["num"])
            if ch.get("type") == "local":
                key = ch.get("stream_key") or f"local{num}"
                ch["stream_key"] = key
                ch["url"] = f"{MEDIAMTX_HLS}/{key}/index.m3u8"
            chs[num] = ch
        except Exception:
            pass
    cfg["channels"] = chs
    return cfg

config   = load_config()
GLOBAL   = config["global"]
channels = config["channels"]

def ch_setting(ch_num, key):
    """Per-kanaal override, anders globale instelling."""
    ch = channels.get(ch_num, {})
    val = ch.get(key)
    return val if val not in (None, "") else GLOBAL[key]

# ── Live-status van lokale (OBS) kanalen via MediaMTX API ─────────────────────
_live_cache      = {}   # stream_key → (timestamp, bool)
_live_cache_lock = threading.Lock()
LIVE_CACHE_TTL   = 2.0

def is_local_live(stream_key):
    now = time.time()
    with _live_cache_lock:
        cached = _live_cache.get(stream_key)
        if cached and (now - cached[0]) < LIVE_CACHE_TTL:
            return cached[1]
    live = False
    try:
        req = urllib.request.Request(f"{MEDIAMTX_API}/v3/paths/get/{stream_key}")
        with urllib.request.urlopen(req, timeout=2) as r:
            info = json.loads(r.read().decode())
        live = bool(info.get("ready"))
    except Exception:
        live = False
    with _live_cache_lock:
        _live_cache[stream_key] = (now, live)
    return live

def ext_for_content_type(ctype):
    if "jpeg" in ctype or "jpg" in ctype: return ".jpg"
    if "svg"  in ctype: return ".svg"
    if "webp" in ctype: return ".webp"
    if "gif"  in ctype: return ".gif"
    return ".png"

def make_opener(ua=None):
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    opener.addheaders = [("User-Agent", ua or GLOBAL["user_agent"]), ("Accept", "*/*")]
    return opener

# ── Statistieken ──────────────────────────────────────────────────────────────
stats_lock = threading.Lock()
stats = {
    "bytes_in":         0,
    "bytes_out":        0,
    "seg_cache_hits":   0,
    "seg_cache_misses": 0,
    "req_m3u8":         0,
    "req_seg":          0,
    "active_conns":     0,
    "ch_requests":      {},
    "ch_bytes_out":     {},
    "ch_bytes_in":      {},
    "ch_last_seen":     {},
}

def stat_inc(key, val=1):
    with stats_lock:
        stats[key] = stats.get(key, 0) + val

def stat_ch(key, ch_num, val=1):
    with stats_lock:
        d = stats[key]
        d[ch_num] = d.get(ch_num, 0) + val

# Per-IP viewer tracking
VIEWER_TTL   = 45
viewers      = {}
viewers_lock = threading.Lock()

def viewer_update(ip, ch_num, bytes_out=0):
    with viewers_lock:
        if ip not in viewers:
            viewers[ip] = {"ch": ch_num, "last_seen": time.time(),
                           "bytes_out": 0, "seg_requests": 0}
        v = viewers[ip]
        v["last_seen"]    = time.time()
        v["ch"]           = ch_num
        v["bytes_out"]   += bytes_out
        v["seg_requests"] += 1

def viewers_active():
    now = time.time()
    with viewers_lock:
        active = {ip: dict(v) for ip, v in viewers.items() if now - v["last_seen"] < VIEWER_TTL}
        for ip in list(viewers.keys()):
            if now - viewers[ip]["last_seen"] >= VIEWER_TTL:
                del viewers[ip]
    return active

# ── Segment cache ─────────────────────────────────────────────────────────────
seg_cache      = {}
seg_cache_keys = []
seg_cache_lock = threading.Lock()

def cache_put(url, data):
    with seg_cache_lock:
        if url in seg_cache:
            return
        seg_cache[url] = data
        seg_cache_keys.append(url)
        cache_max = GLOBAL["seg_cache_max"]
        while len(seg_cache_keys) > cache_max:
            old = seg_cache_keys.pop(0)
            seg_cache.pop(old, None)

def cache_get(url):
    with seg_cache_lock:
        return seg_cache.get(url)

def cache_bytes():
    with seg_cache_lock:
        return sum(len(v) for v in seg_cache.values()), len(seg_cache_keys)

# ── Actieve kanaal tracking ───────────────────────────────────────────────────
last_requested = {}
req_lock       = threading.Lock()

def mark_active(ch_num):
    with req_lock:
        last_requested[ch_num] = time.time()
    with stats_lock:
        stats["ch_last_seen"][ch_num] = time.time()

def is_active(ch_num):
    with req_lock:
        t = last_requested.get(ch_num, 0)
    ttl = ch_setting(ch_num, "active_ttl")
    return (time.time() - t) < ttl

# ── Systeemstats ──────────────────────────────────────────────────────────────
_prev_cpu = None
_prev_net = None

def read_cpu():
    global _prev_cpu
    try:
        with open("/proc/stat") as f:
            line = f.readline()
        vals = list(map(int, line.split()[1:]))
        idle, total = vals[3], sum(vals)
        if _prev_cpu:
            d_total = total - _prev_cpu[1]
            d_idle  = idle  - _prev_cpu[0]
            pct = 100.0 * (1 - d_idle / max(d_total, 1))
        else:
            pct = 0.0
        _prev_cpu = (idle, total)
        return round(pct, 1)
    except Exception:
        return 0.0

def read_mem():
    try:
        mem = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, v = line.split(":")
                mem[k.strip()] = int(v.strip().split()[0]) * 1024
        total, avail = mem.get("MemTotal", 0), mem.get("MemAvailable", 0)
        used = total - avail
        return {"total": total, "used": used, "pct": round(100*used/max(total,1), 1)}
    except Exception:
        return {"total": 0, "used": 0, "pct": 0}

def read_disk():
    try:
        st = os.statvfs("/")
        total = st.f_blocks * st.f_frsize
        free  = st.f_bavail * st.f_frsize
        used  = total - free
        return {"total": total, "used": used, "pct": round(100*used/max(total,1), 1)}
    except Exception:
        return {"total": 0, "used": 0, "pct": 0}

def read_net():
    global _prev_net
    try:
        iface = None
        with open("/proc/net/dev") as f:
            for line in f:
                parts = line.split(":")
                if len(parts) == 2:
                    name = parts[0].strip()
                    if name and name != "lo":
                        nums = list(map(int, parts[1].split()))
                        rx, tx = nums[0], nums[8]
                        if rx > 0:
                            iface = (name, rx, tx)
                            break
        if not iface:
            return {"rx_bps": 0, "tx_bps": 0, "iface": "?"}
        now = time.time()
        name, rx, tx = iface
        if _prev_net:
            dt = now - _prev_net[0]
            rx_bps = (rx - _prev_net[1]) / max(dt, 0.001)
            tx_bps = (tx - _prev_net[2]) / max(dt, 0.001)
        else:
            rx_bps = tx_bps = 0
        _prev_net = (now, rx, tx)
        return {"rx_bps": int(rx_bps), "tx_bps": int(tx_bps), "iface": name}
    except Exception:
        return {"rx_bps": 0, "tx_bps": 0, "iface": "?"}

def get_stats_json():
    cache_bytes_total, cache_count = cache_bytes()
    with stats_lock:
        s = dict(stats)
        s["ch_requests"]  = dict(s["ch_requests"])
        s["ch_bytes_out"] = dict(s["ch_bytes_out"])
        s["ch_bytes_in"]  = dict(s["ch_bytes_in"])
        s["ch_last_seen"] = dict(s["ch_last_seen"])

    now = time.time()
    active_chs = []
    for num, ch in channels.items():
        last = s["ch_last_seen"].get(num, 0)
        active_chs.append({
            "num": num, "name": ch["name"], "active": is_active(num),
            "last_seen": round(now - last) if last else None,
            "requests":  s["ch_requests"].get(num, 0),
            "bytes_out": s["ch_bytes_out"].get(num, 0),
            "bytes_in":  s["ch_bytes_in"].get(num, 0),
        })
    active_chs.sort(key=lambda x: x["requests"], reverse=True)

    va = viewers_active()
    viewer_list = []
    for ip, v in va.items():
        ch_name = channels.get(v["ch"], {}).get("name", f"ch{v['ch']}") if v["ch"] else "?"
        viewer_list.append({
            "ip": ip, "ch": v["ch"], "ch_name": ch_name,
            "last_seen_s": round(now - v["last_seen"]),
            "bytes_out": v["bytes_out"], "seg_requests": v["seg_requests"],
        })
    viewer_list.sort(key=lambda x: x["last_seen_s"])

    hit_total = s["seg_cache_hits"] + s["seg_cache_misses"]
    return {
        "uptime_s": round(now - START_TIME),
        "cpu_pct": read_cpu(), "mem": read_mem(), "disk": read_disk(), "net": read_net(),
        "cache_segments": cache_count, "cache_bytes": cache_bytes_total,
        "cache_max": GLOBAL["seg_cache_max"],
        "cache_hit_pct": round(100 * s["seg_cache_hits"] / max(hit_total, 1), 1),
        "bytes_in_total": s["bytes_in"], "bytes_out_total": s["bytes_out"],
        "req_m3u8": s["req_m3u8"], "req_seg": s["req_seg"],
        "active_conns": s["active_conns"],
        "channels": active_chs,
        "viewers": viewer_list, "viewer_count": len(viewer_list),
    }

# ── URL helpers ───────────────────────────────────────────────────────────────
def to_abs(line, final_url):
    s = line.strip()
    p = urllib.parse.urlparse(final_url)
    origin = f"{p.scheme}://{p.netloc}"
    base   = final_url.rsplit("/", 1)[0] + "/"
    if s.startswith("http://") or s.startswith("https://"):
        return s
    if s.startswith("/"):
        return origin + s
    return base + s

def rewrite_m3u8(text, final_url, proxy_base, ch_num):
    lines = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("#"):
            lines.append(line)
        elif s:
            enc = urllib.parse.quote(to_abs(s, final_url), safe="")
            lines.append(f"{proxy_base}/seg?url={enc}&ch={ch_num}")
        else:
            lines.append(line)
    return "\n".join(lines).replace("#EXT-X-ENDLIST", "")

def resolve_media_playlist(opener, text, final_url, timeout):
    """Als de playlist een master-playlist is (#EXT-X-STREAM-INF), volg de eerste
    variant-playlist via dezelfde opener (cookies blijven behouden) en geef de
    media-playlist terug. Anders ongewijzigd."""
    for _ in range(3):
        if "#EXT-X-STREAM-INF" not in text:
            return text, final_url
        variant = None
        take_next = False
        for line in text.splitlines():
            s = line.strip()
            if not s:
                continue
            if s.startswith("#EXT-X-STREAM-INF"):
                take_next = True
                continue
            if take_next and not s.startswith("#"):
                variant = to_abs(s, final_url)
                break
        if not variant:
            return text, final_url
        with opener.open(variant, timeout=timeout) as r:
            text, final_url = r.read().decode("utf-8", errors="replace"), r.url
    return text, final_url

def seg_urls_from_text(text, final_url):
    return [to_abs(l, final_url) for l in text.splitlines()
            if l.strip() and not l.strip().startswith("#")]

# ── Per-kanaal poller ─────────────────────────────────────────────────────────
class ChannelPoller(threading.Thread):
    def __init__(self, ch_num, ch_url):
        super().__init__(daemon=True)
        self.ch_num  = ch_num
        self.ch_url  = ch_url
        self._lock   = threading.Lock()
        self._text   = ""
        self._final  = ""
        self._opener = None

    def get_m3u8(self, proxy_base):
        with self._lock:
            if not self._text:
                return None
            return rewrite_m3u8(self._text, self._final, proxy_base, self.ch_num).encode()

    def run(self):
        while not is_active(self.ch_num):
            time.sleep(1)
        print(f"[ch{self.ch_num}] poller start", flush=True)
        while True:
            if is_active(self.ch_num):
                try:
                    self._poll()
                except Exception as e:
                    print(f"[ch{self.ch_num}] fout: {e}", flush=True)
                    self._opener = None
            time.sleep(ch_setting(self.ch_num, "poll_interval"))

    def _poll(self):
        timeout = ch_setting(self.ch_num, "upstream_timeout")
        if self._opener is None:
            self._opener = make_opener(channels.get(self.ch_num, {}).get("user_agent"))
        with self._opener.open(self.ch_url, timeout=timeout) as r:
            data, final = r.read(), r.url
        stat_inc("bytes_in", len(data))
        stat_ch("ch_bytes_in", self.ch_num, len(data))
        text = data.decode("utf-8", errors="replace")
        text, final = resolve_media_playlist(self._opener, text, final, timeout)
        segs = seg_urls_from_text(text, final)
        new_segs = [u for u in segs if cache_get(u) is None]
        with self._lock:
            self._text, self._final = text, final

        fetch_timeout = ch_setting(self.ch_num, "fetch_timeout")
        def fetch(url):
            try:
                op = self._opener
                with op.open(url, timeout=fetch_timeout) as r:
                    d = r.read()
                if d and d[:1] == b'\x47':
                    stat_inc("bytes_in", len(d))
                    stat_ch("ch_bytes_in", self.ch_num, len(d))
                    cache_put(url, d)
            except Exception:
                pass

        ts = [threading.Thread(target=fetch, args=(u,), daemon=True) for u in new_segs]
        for t in ts: t.start()
        for t in ts: t.join(timeout=fetch_timeout + 2)

pollers = {num: ChannelPoller(num, ch["url"]) for num, ch in channels.items()}
for p in pollers.values():
    p.start()
print(f"Proxy klaar — {len(pollers)} lazy pollers actief", flush=True)

# ── HTTP handler ─────────────────────────────────────────────────────────────
ADMIN_API_PREFIX = "/admin-api"

class ProxyHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache, no-store")

    def json_response(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.cors()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.cors()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        stat_inc("active_conns")
        try:
            self._handle_get()
        finally:
            stat_inc("active_conns", -1)

    def do_POST(self):
        try:
            self._handle_post()
        except Exception as e:
            try:
                self.json_response({"error": str(e)}, 500)
            except Exception:
                pass

    # ── POST endpoints (config-API voor admin-editor) ────────────────────────
    def _handle_post(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""

        if path == f"{ADMIN_API_PREFIX}/config":
            try:
                new_cfg = json.loads(raw.decode("utf-8"))
            except Exception as e:
                self.json_response({"error": f"Ongeldige JSON: {e}"}, 400); return
            ok, err = validate_config(new_cfg)
            if not ok:
                self.json_response({"error": err}, 400); return
            try:
                with open(CONFIG_FILE, "w") as f:
                    json.dump(new_cfg, f, indent=2, ensure_ascii=False)
                write_channels_json(new_cfg)
            except Exception as e:
                self.json_response({"error": f"Schrijven mislukt: {e}"}, 500); return
            self.json_response({"ok": True, "restarting": True})
            print("Config bijgewerkt via admin-API — herstart proces…", flush=True)
            threading.Thread(target=restart_process, daemon=True).start()
            return

        if path == f"{ADMIN_API_PREFIX}/logo":
            params = urllib.parse.parse_qs(parsed.query)
            num = params.get("ch", [None])[0]
            if not num or not num.isdigit():
                self.json_response({"error": "Ontbrekend of ongeldig kanaalnummer"}, 400); return
            ext = ext_for_content_type(self.headers.get("Content-Type", ""))
            dest_dir = "/var/www/html/logos"
            os.makedirs(dest_dir, exist_ok=True)
            for old_ext in (".png", ".jpg", ".jpeg", ".svg", ".webp", ".gif", ".ico"):
                p = os.path.join(dest_dir, num + old_ext)
                if os.path.exists(p):
                    os.remove(p)
            dest = os.path.join(dest_dir, num + ext)
            with open(dest, "wb") as f:
                f.write(raw)
            self.json_response({"ok": True, "path": f"/logos/{num}{ext}"})
            return

        # Splash-afbeelding uploaden voor lokaal kanaal: /admin-api/splash?ch=N
        if path == f"{ADMIN_API_PREFIX}/splash":
            params = urllib.parse.parse_qs(parsed.query)
            num = params.get("ch", [None])[0]
            if not num or not num.isdigit():
                self.json_response({"error": "Ontbrekend of ongeldig kanaalnummer"}, 400); return
            dest_dir = f"/var/www/html/splash/{num}"
            os.makedirs(dest_dir, exist_ok=True)
            try:
                processed = fit_to_hd(raw)
            except Exception as e:
                self.json_response({"error": f"Afbeelding kon niet worden verwerkt: {e}"}, 400); return
            existing = [f for f in os.listdir(dest_dir) if not f.startswith(".")]
            idx = len(existing) + 1
            fname = f"{idx:02d}.jpg"
            with open(os.path.join(dest_dir, fname), "wb") as f:
                f.write(processed)
            self.json_response({"ok": True, "path": f"/splash/{num}/{fname}"})
            return

        self.send_error(404)

    # ── GET endpoints ─────────────────────────────────────────────────────────
    def _handle_get(self):
        parsed = urllib.parse.urlparse(self.path)
        path   = parsed.path
        params = urllib.parse.parse_qs(parsed.query)

        if path == "/stats":
            self.json_response(get_stats_json())
            return

        if path == f"{ADMIN_API_PREFIX}/config":
            self.json_response({
                "global":   GLOBAL,
                "channels": [channels[n] for n in sorted(channels)],
                "defaults": DEFAULT_GLOBAL,
            })
            return

        # Lijst van geüploade splash-afbeeldingen voor een lokaal kanaal
        if path == f"{ADMIN_API_PREFIX}/splash":
            num = params.get("ch", [None])[0]
            if not num or not num.isdigit():
                self.json_response({"error": "Ontbrekend kanaalnummer"}, 400); return
            d = f"/var/www/html/splash/{num}"
            files = []
            if os.path.isdir(d):
                files = sorted(f for f in os.listdir(d) if not f.startswith("."))
            self.json_response({"images": [f"/splash/{num}/{f}" for f in files]})
            return

        # Live-status van een kanaal (vooral relevant voor lokale OBS-kanalen)
        m = re.match(r"^/ch/(\d+)/status$", path)
        if m:
            num = int(m.group(1))
            ch  = channels.get(num)
            if not ch:
                self.send_error(404); return
            if ch.get("type") == "local":
                live = is_local_live(ch["stream_key"])
            else:
                live = True
            self.json_response({"num": num, "type": ch.get("type", "remote"), "live": live})
            return

        m = re.match(r"^/ch/(\d+)/live\.m3u8$", path)
        if m:
            num    = int(m.group(1))
            poller = pollers.get(num)
            if not poller:
                self.send_error(404); return
            mark_active(num)
            stat_inc("req_m3u8")
            stat_ch("ch_requests", num)
            scheme         = self.headers.get("X-Forwarded-Proto", "http")
            proxy_host     = self.headers.get("Host", f"{LISTEN_HOST}:{LISTEN_PORT}")
            proxy_base_url = f"{scheme}://{proxy_host}"
            body = poller.get_m3u8(proxy_base_url)
            if not body:
                try:
                    timeout = ch_setting(num, "upstream_timeout")
                    op = make_opener(channels.get(num, {}).get("user_agent"))
                    with op.open(channels[num]["url"], timeout=timeout) as r:
                        text, final = r.read().decode("utf-8", errors="replace"), r.url
                    with poller._lock:
                        poller._text, poller._final = text, final
                    body = rewrite_m3u8(text, final, proxy_base_url, num).encode()
                    segs = seg_urls_from_text(text, final)
                    fetch_timeout = ch_setting(num, "fetch_timeout")
                    def _pre(url):
                        if cache_get(url): return
                        try:
                            o = make_opener(channels.get(num, {}).get("user_agent"))
                            with o.open(url, timeout=fetch_timeout) as r2:
                                d = r2.read()
                            if d and d[:1] == b'\x47':
                                stat_inc("bytes_in", len(d))
                                stat_ch("ch_bytes_in", num, len(d))
                                cache_put(url, d)
                        except Exception:
                            pass
                    for s in segs:
                        threading.Thread(target=_pre, args=(s,), daemon=True).start()
                except Exception as e:
                    self.send_error(503, f"Stream niet beschikbaar: {e}"); return
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.apple.mpegurl")
            self.cors()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            stat_inc("bytes_out", len(body))
            return

        if path == "/seg":
            url_list = params.get("url", [])
            if not url_list:
                self.send_error(400); return
            seg_url   = urllib.parse.unquote(url_list[0])
            ch_param  = params.get("ch", [None])[0]
            ch_num    = int(ch_param) if ch_param else None
            client_ip = (self.headers.get("X-Real-IP") or
                         self.headers.get("X-Forwarded-For", "").split(",")[0].strip() or
                         self.client_address[0])
            if ch_num:
                mark_active(ch_num)
            stat_inc("req_seg")
            cached = cache_get(seg_url)
            if cached:
                stat_inc("seg_cache_hits")
                self.send_response(200)
                self.send_header("Content-Type", "video/mp2t")
                self.cors()
                self.send_header("Content-Length", str(len(cached)))
                self.end_headers()
                self.wfile.write(cached)
                stat_inc("bytes_out", len(cached))
                if ch_num:
                    stat_ch("ch_bytes_out", ch_num, len(cached))
                    viewer_update(client_ip, ch_num, len(cached))
                return
            stat_inc("seg_cache_misses")
            try:
                fetch_timeout = ch_setting(ch_num, "fetch_timeout") if ch_num else GLOBAL["fetch_timeout"]
                op = make_opener(channels.get(ch_num, {}).get("user_agent") if ch_num else None)
                with op.open(seg_url, timeout=fetch_timeout) as r:
                    data = r.read()
                if not data or data[:1] != b'\x47':
                    self.send_error(502, "Geen TS"); return
                self.send_response(200)
                self.send_header("Content-Type", "video/mp2t")
                self.cors()
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                stat_inc("bytes_in",  len(data))
                stat_inc("bytes_out", len(data))
                if ch_num:
                    stat_ch("ch_bytes_in",  ch_num, len(data))
                    stat_ch("ch_bytes_out", ch_num, len(data))
                    viewer_update(client_ip, ch_num, len(data))
                cache_put(seg_url, data)
            except Exception as e:
                try:
                    self.send_error(502, f"Segment fout: {e}")
                except Exception:
                    pass
            return

        self.send_error(404)

# ── Config validatie & herstart ───────────────────────────────────────────────
def validate_config(cfg):
    if not isinstance(cfg, dict):
        return False, "Config moet een object zijn"
    if "channels" not in cfg or not isinstance(cfg["channels"], list):
        return False, "Config mist 'channels' lijst"
    seen = set()
    start_channels = []
    for ch in cfg["channels"]:
        if not isinstance(ch, dict):
            return False, "Elk kanaal moet een object zijn"
        for req in ("num", "name"):
            if req not in ch or ch[req] in (None, ""):
                return False, f"Kanaal mist verplicht veld '{req}'"
        try:
            n = int(ch["num"])
        except Exception:
            return False, f"Ongeldig kanaalnummer: {ch.get('num')}"
        if n in seen:
            return False, f"Kanaalnummer {n} komt dubbel voor"
        seen.add(n)
        ch_type = ch.get("type", "remote")
        if ch_type not in ("remote", "local"):
            return False, f"Kanaal {n}: ongeldig type '{ch_type}'"
        if ch_type == "remote":
            url = ch.get("url", "")
            if not (url.startswith("http://") or url.startswith("https://")):
                return False, f"Kanaal {n}: URL moet met http(s):// beginnen"
        else:
            if not ch.get("stream_key"):
                return False, f"Kanaal {n}: stream-key is verplicht voor lokale kanalen"
            if not re.match(r"^[a-zA-Z0-9_-]+$", ch["stream_key"]):
                return False, f"Kanaal {n}: stream-key mag alleen letters, cijfers, - en _ bevatten"
        if ch.get("is_start_channel"):
            start_channels.append(n)
    if len(start_channels) > 1:
        return False, f"Slechts één kanaal mag het startkanaal zijn (nu: {start_channels})"
    g = cfg.get("global", {})
    if not isinstance(g, dict):
        return False, "'global' moet een object zijn"
    numeric = ("seg_cache_max", "poll_interval", "active_ttl", "upstream_timeout", "fetch_timeout")
    for key in numeric:
        if key in g and g[key] not in (None, ""):
            try:
                v = float(g[key])
                if v <= 0:
                    return False, f"'{key}' moet positief zijn"
            except Exception:
                return False, f"'{key}' moet numeriek zijn"
    return True, None

CHANNELS_JSON = "/var/www/html/channels.json"

def write_channels_json(cfg):
    """Schrijft channels.json voor de speler op basis van de actuele config."""
    out = []
    for ch in sorted(cfg.get("channels", []), key=lambda c: int(c["num"])):
        entry = {
            "num":  int(ch["num"]),
            "name": ch.get("name", ""),
            "url":  ch.get("url", ""),
            "type": ch.get("type", "remote"),
        }
        if ch.get("logo"):
            entry["logo"] = ch["logo"]
        if ch.get("tvg_id"):
            entry["tvg_id"] = ch["tvg_id"]
        if entry["type"] == "local":
            entry["is_start_channel"] = bool(ch.get("is_start_channel"))
            entry["splash_interval"]  = ch.get("splash_interval") or 8
        out.append(entry)
    tmp = CHANNELS_JSON + ".tmp"
    with open(tmp, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CHANNELS_JSON)

def restart_process():
    time.sleep(0.5)
    print("Herstart proxy-proces met nieuwe configuratie…", flush=True)
    os.execv(sys.executable, [sys.executable] + sys.argv)

class ThreadedServer(http.server.ThreadingHTTPServer):
    daemon_threads = True

if __name__ == "__main__":
    server = ThreadedServer((LISTEN_HOST, LISTEN_PORT), ProxyHandler)
    print(f"HLS Proxy luistert op {LISTEN_HOST}:{LISTEN_PORT}", flush=True)
    server.serve_forever()
