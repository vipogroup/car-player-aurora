import email.utils
import hashlib
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import List, Optional, Tuple

from yt_dlp import YoutubeDL


def _parse_bind_host() -> str:
    h = (os.environ.get("UNBLOCKED_PLAYER_HOST") or "127.0.0.1").strip()
    if not h:
        return "127.0.0.1"
    if h in ("*", "all", "any", "0.0.0.0/0"):
        return "0.0.0.0"
    return h


HOST = _parse_bind_host()


def _url_hostname() -> str:
    """Host for links opened in a browser on this machine (0.0.0.0 is not valid as a URL host)."""
    if HOST in ("0.0.0.0", "0.0.0.0/0"):
        return "127.0.0.1"
    return HOST


def _guess_lan_ipv4() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.3)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return ""


try:
    # UNBLOCKED_PLAYER_PORT — מקומי. PORT — Heroku, Render, Railway ועוד (PaaS).
    _p_raw = os.environ.get("UNBLOCKED_PLAYER_PORT") or os.environ.get("PORT", "5600")
    _p = int(_p_raw)
    PORT = _p if 1 <= _p <= 65535 else 5600
except ValueError:
    PORT = 5600
# גרסת חבילת "שרת מקומי" — משווה מול local-server-version.json (מאגר car-).
UNBLOCKED_LOCAL_SERVER_VERSION = "1.0.4"
REMOTE_LOCAL_SERVER_VERSION_URL = (
    "https://raw.githubusercontent.com/vipogroup/car-/main/local-server-version.json"
)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPT_FILE = os.path.abspath(__file__)
# זמן שינוי הקובץ ברגע עליית התהליך — משמש לזיהוי "שרת לא הופעל מחדש אחרי עריכה"
SERVER_LOADED_MTIME = int(os.path.getmtime(SCRIPT_FILE))
# שירים שהורדו לדיסק ליד השרת — ניגון בלי YouTube (כל עוד תהליך השרת רץ).
OFFLINE_DIR = os.path.join(SCRIPT_DIR, "offline_library")
_offline_lock = threading.Lock()

# Aurora — חבילת UI חדשה (ראי aurora/index.html, aurora/styles.css, aurora/app.js)
AURORA_DIR = os.path.join(SCRIPT_DIR, "aurora")
AURORA_MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".mjs": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".woff2": "font/woff2",
}
# כשהדף נטען מ־/ , יחסי ./styles.css הופך ל־/styles.css — כינויים לאחור למטמון HTML ישן.
AURORA_ROOT_STATIC_NAMES = frozenset(
    {
        "styles.css",
        "app.js",
        "icons.js",
        "color.js",
        "visualizer.js",
        "device-offline.js",
        "client-version.json",
        "bundle-fingerprint.json",
    }
)


def aurora_template_path() -> str:
    return os.path.join(AURORA_DIR, "index.html")


def aurora_template_available() -> bool:
    return os.path.isfile(aurora_template_path())


def build_html_aurora() -> str:
    """Render the Aurora shell. Substitutes only data placeholders; the heavy
    HTML/CSS/JS lives on disk in the aurora/ folder so it can be edited freely."""
    with open(aurora_template_path(), "r", encoding="utf-8") as f:
        tpl = f.read()
    lan_url = ""
    if HOST == "0.0.0.0":
        lan = _guess_lan_ipv4()
        if lan:
            lan_url = f"http://{lan}:{PORT}/"
    # הדף נטען מ־/ אבל נכסי Aurora מוגשים רק תחת /aurora/ — יחסי ./styles.css הופך ל־/styles.css (404).
    # ב־GitHub Pages הקובץ יושב תחת נתיב תיקייה אז ./ נשאר נכון; כאן מתקנים רק בפלט השרת.
    for _old, _new in (
        ('href="./styles.css"', 'href="/aurora/styles.css"'),
        ("href='./styles.css'", "href='/aurora/styles.css'"),
        ('src="./app.js"', 'src="/aurora/app.js"'),
        ("src='./app.js'", "src='/aurora/app.js'"),
    ):
        tpl = tpl.replace(_old, _new)
    return (
        tpl
        .replace("{{PLAYLIST_JSON}}", json.dumps(PLAYLIST, ensure_ascii=False))
        .replace("{{LAN_URL_JSON}}", json.dumps(lan_url))
        .replace("{{BUILD}}", str(SERVER_LOADED_MTIME))
        .replace("{{VERSION}}", UNBLOCKED_LOCAL_SERVER_VERSION)
    )


def _safe_aurora_path(rel: str) -> Optional[str]:
    """Resolve a /aurora/<rel> request safely — must stay inside AURORA_DIR."""
    rel = rel.lstrip("/").replace("\\", "/")
    if not rel:
        return None
    if ".." in rel.split("/"):
        return None
    target = os.path.normpath(os.path.join(AURORA_DIR, rel))
    if not target.startswith(os.path.normpath(AURORA_DIR)):
        return None
    if not os.path.isfile(target):
        return None
    return target

# Service Worker (PWA — "התקנה" למסך הבית). עדכן מספר אם משנים לוגיקת מטמון.
UNBLOCKED_PWA_VERSION = 7
UNBLOCKED_SW_SOURCE = """
const UNBLOCKED_PWA_VERSION = %d;
const CACHE = 'unblocked-pwa-v' + UNBLOCKED_PWA_VERSION;
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) =>
      cache.addAll([
        new URL('manifest.json', self.location).href,
        new URL('car-music-icon.png', self.location).href,
        new URL('unblocked-sw.js', self.location).href,
      ])
    )
  );
  self.skipWaiting();
});
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((names) =>
      Promise.all(
        names.map((n) => {
          if (n !== CACHE) {
            return caches.delete(n);
          }
          return Promise.resolve();
        })
      )
    )
  );
  return self.clients.claim();
});
self.addEventListener('fetch', (event) => {
  if (event.request.method !== 'GET') {
    return;
  }
  const u = new URL(event.request.url);
  if (u.origin !== self.location.origin) {
    return;
  }
  if (event.request.mode === 'navigate') {
    event.respondWith(
      fetch(event.request, { cache: 'no-store' })
    );
    return;
  }
  if (u.pathname.includes('/api/')) {
    event.respondWith(fetch(event.request, { cache: 'no-store' }));
    return;
  }
  if (u.pathname.includes('/aurora/')) {
    event.respondWith(
      fetch(event.request, { cache: 'no-store' })
        .then((res) => {
          if (res && res.status === 200) {
            const c = res.clone();
            caches.open(CACHE).then((cache) => {
              try {
                cache.put(event.request, c);
              } catch (e) {}
            });
          }
          return res;
        })
        .catch(() => caches.match(event.request))
    );
    return;
  }
  event.respondWith(
    fetch(event.request)
      .then((res) => {
        if (res && res.status === 200) {
          const c = res.clone();
          caches.open(CACHE).then((cache) => {
            try {
              cache.put(event.request, c);
            } catch (e) {}
          });
        }
        return res;
      })
      .catch(() => caches.match(event.request))
  );
});
""".strip() % (UNBLOCKED_PWA_VERSION,)


def _parse_semver_tuple(s: str) -> Optional[Tuple[int, int, int]]:
    s = (s or "").strip()
    if not s:
        return None
    parts: List[int] = []
    for p in s.split("."):
        if p.isdigit():
            parts.append(int(p))
        else:
            return None
    if not parts:
        return None
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def _print_remote_version_hint() -> None:
    try:
        req = urllib.request.Request(
            REMOTE_LOCAL_SERVER_VERSION_URL,
            headers={"User-Agent": f"unblocked_player/{UNBLOCKED_LOCAL_SERVER_VERSION}"},
        )
        with urllib.request.urlopen(req, timeout=4) as r:
            data = json.loads(r.read().decode("utf-8", errors="replace"))
    except Exception:
        return
    if not isinstance(data, dict):
        return
    remote = data.get("unblockedLocalServer")
    rt = _parse_semver_tuple(str(remote) if remote is not None else "")
    lt = _parse_semver_tuple(UNBLOCKED_LOCAL_SERVER_VERSION)
    if not rt or not lt or rt <= lt:
        return
    print(
        f"\n*** [עדכון] יש חבילת שרת מקומי חדשה יותר: {remote} (מקומי: {UNBLOCKED_LOCAL_SERVER_VERSION})"
    )
    print(
        "    הורידו: https://raw.githubusercontent.com/vipogroup/car-/main/local-server-unblocked.zip\n"
    )


def unblocked_pwa_manifest_json() -> str:
    return json.dumps(
        {
            "name": "מוזיקה Unblocked",
            "short_name": "מוזיקה",
            "description": "נגן YouTube (ספרייה, אהובים, פלייליסטים)",
            "start_url": "./",
            "scope": "./",
            "id": "unblocked-player",
            "display": "standalone",
            "display_override": ["standalone", "fullscreen", "minimal-ui"],
            "background_color": "#121212",
            "theme_color": "#5edfff",
            "dir": "rtl",
            "lang": "he",
            "icons": [
                {
                    "src": "car-music-icon.png",
                    "sizes": "192x192",
                    "type": "image/png",
                    "purpose": "any",
                },
                {
                    "src": "car-music-icon.png",
                    "sizes": "512x512",
                    "type": "image/png",
                    "purpose": "maskable",
                },
            ],
            "categories": ["music", "entertainment"],
            "prefer_related_applications": False,
        },
        ensure_ascii=False,
    )


def apply_youtube_auth(ydl_opts: dict) -> None:
    """
    YouTube often returns 'Sign in to confirm you are not a bot' for anonymous
    clients. The reliable fix is passing cookies (logged-in or even export-only).
    - Place yt_cookies.txt / cookies.txt / youtube_cookies.txt next to this script, or
    - Set environment variable YTDLP_COOKIEFILE to the full path of a Netscape cookies file, or
    - Set YTDLP_COOKIES_BROWSER to 'edge' or 'chrome' (browser must be closed on Windows
      so the cookie database can be copied; see yt-dlp FAQ).
    """
    cookiefile = (os.environ.get("YTDLP_COOKIEFILE") or "").strip()
    if not cookiefile:
        for name in ("yt_cookies.txt", "cookies.txt", "youtube_cookies.txt"):
            candidate = os.path.join(SCRIPT_DIR, name)
            if os.path.isfile(candidate) and os.path.getsize(candidate) > 0:
                cookiefile = candidate
                break
    if cookiefile and os.path.isfile(cookiefile) and os.path.getsize(cookiefile) > 0:
        ydl_opts["cookiefile"] = cookiefile
        return
    browser = (os.environ.get("YTDLP_COOKIES_BROWSER") or "").strip().lower()
    if browser in ("edge", "chrome", "chromium", "opera", "brave", "firefox", "vivaldi"):
        ydl_opts["cookiesfrombrowser"] = (browser,)


YOUTUBE_EXTRACTOR_ARGS = {
    "youtube": {
        "player_client": ["android", "web", "web_embedded", "mweb", "ios"],
    },
}

PLAYLIST = [
    {"name": "קישור 1", "url": "https://www.youtube.com/watch?v=5CUKHKrZe2s"},
    {"name": "קישור 2", "url": "https://www.youtube.com/watch?v=-NV3DA7yokQ"},
    {"name": "קישור 3", "url": "https://www.youtube.com/watch?v=pFe1_DNsGpg"},
    {"name": "קישור 4", "url": "https://www.youtube.com/watch?v=LWbiOwwiVYU"},
    {"name": "קישור 5", "url": "https://www.youtube.com/watch?v=mvhDICxDppQ"},
    {"name": "קישור 6", "url": "https://www.youtube.com/watch?v=zOigxsiVKfw"},
    {"name": "קישור 7", "url": "https://www.youtube.com/watch?v=1zjVS9DINAA"},
    {"name": "קישור 8", "url": "https://www.youtube.com/watch?v=6Jdbu-RVuzI"},
]

# Simple in-memory cache for stream URLs.
STREAM_CACHE = {}
CACHE_TTL_SECONDS = 600


def build_html():
    items_json = json.dumps(PLAYLIST, ensure_ascii=False)
    disk_mtime = int(os.path.getmtime(SCRIPT_FILE))
    code_stale = disk_mtime != SERVER_LOADED_MTIME
    stale_display = "block" if code_stale else "none"
    lan_guess = _guess_lan_ipv4() if HOST == "0.0.0.0" else ""
    lan_url = f"http://{lan_guess}:{PORT}/" if lan_guess else ""
    lan_url_json = json.dumps(lan_url)
    lan_btn_display = "inline-flex" if lan_url else "none"
    return f"""<!doctype html>
<html lang="he">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
  <meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate" />
  <meta http-equiv="Pragma" content="no-cache" />
  <meta name="unblocked-player" content="1" />
  <meta name="player-build" content="{SERVER_LOADED_MTIME}" />
  <meta name="player-disk-mtime" content="{disk_mtime}" />
  <title>מוזיקה v5 · נגן YouTube</title>
  <link rel="manifest" href="manifest.json" />
  <meta name="theme-color" content="#5edfff" />
  <meta name="color-scheme" content="dark" />
  <meta name="mobile-web-app-capable" content="yes" />
  <meta name="apple-mobile-web-app-capable" content="yes" />
  <meta name="apple-mobile-web-app-title" content="מוזיקה" />
  <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent" />
  <link rel="icon" type="image/png" href="car-music-icon.png" />
  <link rel="apple-touch-icon" href="car-music-icon.png" />
  <meta name="app-ui" content="v5-nav-home-playlists" />
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link href="https://fonts.googleapis.com/css2?family=Heebo:wght@400;500;600;700;800&display=swap" rel="stylesheet" />
  <style>
    :root {{
      --spot-black: #000000;
      --spot-base: #121212;
      --spot-card: #151a1f;
      --spot-elevated: #1f252c;
      --spot-border: rgba(255, 255, 255, 0.1);
      --spot-text: #ffffff;
      --spot-sub: #b3b3b3;
      /* צבע מוביל — תכלת נקי (ללא כחול מודגש) */
      --accent: #5edfff;
      --accent-2: #67e5ff;
      --accent-glow: rgba(94, 223, 255, 0.34);
      --accent-soft: rgba(94, 223, 255, 0.14);
      --apple-blur: saturate(180%) blur(20px);
      --radius: 10px;
      --radius-lg: 16px;
      --font: "Heebo", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}

    * {{ box-sizing: border-box; }}

    html {{
      scroll-behavior: smooth;
      -webkit-text-size-adjust: 100%;
      text-size-adjust: 100%;
    }}

    body {{
      margin: 0;
      min-height: 100vh;
      direction: rtl;
      font-family: var(--font);
      color: var(--spot-text);
      background: var(--spot-base);
      -webkit-font-smoothing: antialiased;
      overflow-x: hidden;
    }}

    .app-root {{
      display: flex;
      min-height: 100vh;
      max-width: 1600px;
      margin: 0 auto;
    }}

    /* —— Spotify-style library sidebar —— */
    .spot-sidebar {{
      width: min(340px, 100vw);
      flex-shrink: 0;
      background: var(--spot-black);
      border-left: 1px solid var(--spot-border);
      display: flex;
      flex-direction: column;
      padding: 20px 16px 24px;
    }}

    .brand {{
      display: flex;
      align-items: center;
      gap: 12px;
      margin-bottom: 24px;
      padding-bottom: 20px;
      border-bottom: 1px solid var(--spot-border);
    }}

    .brand-icon {{
      width: 44px;
      height: 44px;
      border-radius: 12px;
      background: linear-gradient(145deg, #2d2d2d, #1a1a1a);
      display: none;
      place-items: center;
      font-size: 22px;
      box-shadow: 0 4px 16px rgba(0, 0, 0, 0.45);
    }}

    .brand-name {{ font-weight: 800; font-size: 1.15rem; letter-spacing: -0.02em; }}
    .brand-tag {{ font-size: 0.72rem; color: var(--spot-sub); font-weight: 600; margin-top: 2px; }}

    .sidebar-head {{
      font-size: 0.7rem;
      font-weight: 800;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      color: var(--spot-sub);
      margin-bottom: 12px;
    }}

    .queue-wrap {{
      flex: 1;
      min-height: 120px;
      border-radius: var(--radius);
      overflow: hidden;
    }}

    .quick-results {{
      max-height: calc(100vh - 200px);
      overflow-y: auto;
      padding: 4px;
      scrollbar-width: thin;
      scrollbar-color: #444 transparent;
    }}
    .quick-results::-webkit-scrollbar {{ width: 6px; }}
    .quick-results::-webkit-scrollbar-thumb {{ background: #444; border-radius: 4px; }}

    .queue-row {{
      display: grid;
      grid-template-columns: 48px 1fr auto;
      gap: 10px;
      align-items: center;
      padding: 8px 10px;
      border-radius: var(--radius);
      cursor: pointer;
      transition: background 0.15s ease;
    }}
    .queue-row:hover {{ background: var(--spot-elevated); }}
    .queue-row.is-active {{
      background: var(--accent-soft);
      box-shadow: inset 0 0 0 1px var(--accent-glow);
    }}

    .queue-thumb {{
      width: 48px;
      height: 48px;
      border-radius: 6px;
      object-fit: cover;
      background: #333;
    }}

    .queue-body {{ min-width: 0; text-align: right; }}
    .queue-title {{
      font-size: 0.88rem;
      font-weight: 600;
      color: var(--spot-text);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}
    .queue-sub {{ font-size: 0.72rem; color: var(--spot-sub); margin-top: 2px; }}

    .queue-actions {{
      display: flex;
      gap: 6px;
      align-items: center;
    }}
    .queue-actions button {{
      padding: 6px 10px;
      font-size: 0.75rem;
      min-height: 32px;
    }}

    .queue-empty {{
      padding: 16px;
      border-radius: 12px;
      border: 1px dashed rgba(255, 255, 255, 0.2);
      background: rgba(255, 255, 255, 0.02);
      text-align: right;
      color: #d6e2ed;
      font-size: 0.88rem;
      line-height: 1.5;
    }}

    /* —— Main (Apple Music–style glass + hero) —— */
    .main-column {{
      flex: 1;
      min-width: 0;
      padding: 20px 24px 40px;
      background: linear-gradient(180deg, #1e1e1e 0%, var(--spot-base) 28%);
    }}
    .glass-top,
    .content-views,
    .now-playing-card,
    .up-next-card,
    .player-card-inner {{
      max-width: 1180px;
      margin-inline: auto;
    }}

    .glass-top {{
      backdrop-filter: var(--apple-blur);
      -webkit-backdrop-filter: var(--apple-blur);
      background: rgba(30, 30, 30, 0.72);
      border: 1px solid var(--spot-border);
      border-radius: var(--radius-lg);
      padding: 10px 14px;
      margin-bottom: 12px;
    }}
    .glass-top-row {{
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 14px;
      flex-wrap: wrap;
    }}
    .glass-top-text {{ flex: 1; min-width: 200px; }}
    .hard-refresh-btn {{
      flex-shrink: 0;
      padding: 8px 14px;
      border-radius: 10px;
      border: 1px solid var(--spot-border);
      background: var(--spot-elevated);
      color: var(--spot-text);
      font-family: var(--font);
      font-size: 0.78rem;
      font-weight: 600;
      cursor: pointer;
      white-space: nowrap;
      transition: border-color 0.15s, color 0.15s, background 0.15s;
    }}
    .btn-leading-icon {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      justify-content: center;
    }}
    .btn-leading-icon svg {{
      width: 14px;
      height: 14px;
      stroke: currentColor;
      fill: none;
      stroke-width: 2;
      stroke-linecap: round;
      stroke-linejoin: round;
      flex-shrink: 0;
    }}
    .hard-refresh-btn:hover {{
      border-color: var(--accent);
      color: var(--accent);
      background: rgba(255, 255, 255, 0.06);
    }}
    .lan-phone-btn {{
      display: inline-flex;
      align-items: center;
      padding: 8px 14px;
      border-radius: 10px;
      border: 1px solid var(--accent-glow);
      background: rgba(94, 223, 255, 0.1);
      color: var(--accent-2);
      font-family: var(--font);
      font-size: 0.78rem;
      font-weight: 700;
      cursor: pointer;
      white-space: nowrap;
      transition: border-color 0.15s, color 0.15s, background 0.15s;
    }}
    .lan-phone-btn:hover {{
      border-color: var(--accent);
      color: #fff;
      background: rgba(94, 223, 255, 0.2);
    }}
    .ts-remote-btn {{
      display: inline-flex;
      align-items: center;
      padding: 8px 14px;
      border-radius: 10px;
      border: 1px solid rgba(196, 181, 253, 0.55);
      background: rgba(139, 92, 246, 0.12);
      color: #ddd6fe;
      font-family: var(--font);
      font-size: 0.78rem;
      font-weight: 700;
      cursor: pointer;
      white-space: nowrap;
      transition: border-color 0.15s, color 0.15s, background 0.15s;
    }}
    .ts-remote-btn:hover {{
      border-color: #c4b5fd;
      color: #fff;
      background: rgba(139, 92, 246, 0.22);
    }}
    .ts-help-steps {{
      margin: 10px 0 0;
      padding: 0 1.1rem 0 0;
      font-size: 0.88rem;
      line-height: 1.55;
      color: #c3d0d8;
    }}
    .ts-help-steps li {{ margin-bottom: 8px; }}
    .ts-help-links {{ margin: 8px 0 0; padding: 0 1rem 0 0; font-size: 0.86rem; }}
    .ts-help-links a {{ color: #7ee0ff; font-weight: 700; }}
    .ts-help-note {{ font-size: 0.8rem; color: #9fb0ba; margin-top: 10px; line-height: 1.45; }}
    .lan-qr-box {{
      display: flex;
      justify-content: center;
      padding: 12px;
      background: #fff;
      border-radius: 12px;
      margin: 12px 0 8px;
    }}
    .lan-qr-hint {{ font-size: 0.85rem; line-height: 1.5; color: #c3d0d8; margin: 0 0 6px; }}
    .lan-url-row {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      align-items: center;
      margin-top: 4px;
    }}
    .lan-url-row code {{
      flex: 1;
      min-width: 0;
      font-size: 0.8rem;
      background: var(--spot-base);
      border: 1px solid var(--spot-border);
      border-radius: 8px;
      padding: 8px 10px;
      color: #7ee0ff;
      word-break: break-all;
    }}
    .lan-url-copy-btn {{
      padding: 8px 12px;
      border-radius: 8px;
      border: 1px solid var(--spot-border);
      background: var(--spot-elevated);
      color: var(--spot-text);
      font-family: var(--font);
      font-size: 0.8rem;
      font-weight: 700;
      cursor: pointer;
    }}
    .lan-url-copy-btn:hover {{ border-color: var(--accent); color: var(--accent); }}
    body.car-mode .lan-phone-btn,
    body.car-mode .ts-remote-btn,
    body.car-mode #settingsLanQrBtn,
    body.car-mode #settingsTailscaleBtn {{ display: none !important; }}
    .sub-inline {{
      margin: 0;
      font-size: 0.82rem;
      color: var(--spot-sub);
      text-align: right;
      line-height: 1.5;
    }}
    .build-line {{ display: none; }}
    .build-line a {{ color: var(--accent); font-weight: 600; }}
    .build-line code {{ font-size: 0.68rem; background: rgba(255, 255, 255, 0.06); padding: 2px 6px; border-radius: 4px; }}

    .now-playing-card {{
      background: linear-gradient(180deg, rgba(28, 32, 38, 0.95) 0%, rgba(18, 21, 25, 0.98) 100%);
      border: 1px solid var(--spot-border);
      border-radius: 20px;
      padding: 24px;
      margin-bottom: 20px;
      box-shadow: 0 24px 48px rgba(0, 0, 0, 0.45);
    }}

    .np-layout {{
      display: flex;
      flex-direction: row-reverse;
      gap: 28px;
      align-items: center;
      flex-wrap: wrap;
      justify-content: flex-start;
    }}

    .np-art-wrap {{
      flex-shrink: 0;
      border-radius: 14px;
      overflow: hidden;
      box-shadow: 0 16px 40px rgba(0, 0, 0, 0.55);
    }}

    .np-artwork {{
      display: block;
      width: min(220px, 42vw);
      height: min(220px, 42vw);
      object-fit: cover;
      background: #222;
    }}

    .np-text-col {{
      flex: 1;
      min-width: 200px;
      text-align: right;
    }}

    .np-label {{
      font-size: 0.7rem;
      font-weight: 800;
      letter-spacing: 0.14em;
      text-transform: uppercase;
      color: var(--spot-sub);
      margin-bottom: 8px;
    }}

    .meta.np-title {{
      margin: 0 0 20px;
      font-size: clamp(1.15rem, 2.8vw, 1.65rem);
      font-weight: 700;
      line-height: 1.35;
      color: var(--spot-text);
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
      overflow: hidden;
    }}

    .controls.transport-bar {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      justify-content: center;
      gap: 14px;
      margin-bottom: 18px;
    }}

    .icon-btn {{
      width: 44px;
      height: 44px;
      padding: 0;
      border-radius: 50%;
      display: grid;
      place-items: center;
      font-size: 1rem;
    }}
    .icon-btn svg {{
      width: 19px;
      height: 19px;
      stroke: currentColor;
      fill: none;
      stroke-width: 2;
      stroke-linecap: round;
      stroke-linejoin: round;
    }}
    .icon-btn .ico-exit-fullscreen {{
      display: none;
    }}
    .icon-btn.is-fullscreen .ico-enter-fullscreen {{
      display: none;
    }}
    .icon-btn.is-fullscreen .ico-exit-fullscreen {{
      display: block;
    }}

    .play-fab {{
      width: 64px;
      height: 64px;
      border-radius: 50%;
      border: none;
      background: var(--accent);
      color: #0a1118;
      font-size: 1.5rem;
      font-weight: 800;
      cursor: pointer;
      display: grid;
      place-items: center;
      box-shadow: 0 8px 22px var(--accent-glow);
      transition: transform 0.15s ease, filter 0.15s ease;
    }}
    .play-fab svg {{
      width: 24px;
      height: 24px;
      stroke: currentColor;
      fill: currentColor;
      stroke-width: 0;
    }}
    .play-fab .ico-pause {{ display: none; }}
    .play-fab:not(.is-paused) .ico-play {{ display: none; }}
    .play-fab:not(.is-paused) .ico-pause {{ display: block; }}
    .play-fab:hover {{ filter: brightness(1.06); }}

    .scrobble-row {{
      display: flex;
      align-items: center;
      gap: 12px;
      margin-bottom: 12px;
      direction: ltr;
    }}
    .time-tag {{
      font-size: 0.72rem;
      font-weight: 600;
      color: var(--spot-sub);
      font-variant-numeric: tabular-nums;
      min-width: 36px;
    }}

    input[type="range"].range-progress {{
      flex: 1;
      height: 4px;
      -webkit-appearance: none;
      appearance: none;
      background: linear-gradient(90deg, var(--accent) 0%, #444 0%);
      border-radius: 2px;
      cursor: pointer;
      direction: ltr;
    }}
    input[type="range"].range-progress::-webkit-slider-thumb {{
      -webkit-appearance: none;
      width: 12px;
      height: 12px;
      border-radius: 50%;
      background: #fff;
      box-shadow: 0 0 8px rgba(0, 0, 0, 0.5);
      margin-top: -4px;
    }}
    input[type="range"].range-progress::-moz-range-thumb {{
      width: 12px;
      height: 12px;
      border-radius: 50%;
      background: #fff;
      border: none;
    }}

    .vol-row {{
      display: flex;
      align-items: center;
      gap: 10px;
      max-width: 280px;
      margin-right: auto;
      direction: ltr;
    }}
    .eq-toggle-row {{
      margin-top: 10px;
      display: flex;
      justify-content: flex-start;
    }}
    .eq-toggle-btn {{
      min-height: 38px;
      padding: 0 12px;
      border-radius: 10px;
      border: 1px solid var(--spot-border);
      background: rgba(255, 255, 255, 0.04);
      color: #dff7ff;
      font-family: var(--font);
      font-size: 0.8rem;
      font-weight: 700;
      cursor: pointer;
    }}
    .eq-drawer {{
      margin-top: 10px;
      border: 1px solid var(--spot-border);
      border-radius: 12px;
      background: rgba(0, 0, 0, 0.18);
      padding: 10px;
      display: none;
    }}
    .eq-drawer.is-open {{
      display: block;
    }}
    .eq-preamp-row {{
      display: grid;
      grid-template-columns: 72px 1fr 50px;
      gap: 8px;
      align-items: center;
      margin-bottom: 8px;
    }}
    .eq-bands {{
      display: grid;
      grid-template-columns: repeat(10, minmax(0, 1fr));
      gap: 6px;
      margin-bottom: 10px;
    }}
    .eq-band {{
      text-align: center;
    }}
    .eq-band input {{
      width: 100%;
    }}
    .eq-band-label {{
      font-size: 0.66rem;
      color: var(--spot-sub);
      margin-top: 2px;
    }}
    .eq-actions {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
    }}
    .eq-top-actions,
    .eq-compressor-row,
    .eq-slot-row {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      align-items: center;
      margin-bottom: 8px;
    }}
    .eq-mini-label {{
      font-size: 0.72rem;
      color: var(--spot-sub);
      min-width: 58px;
    }}
    .eq-mini-value {{
      font-size: 0.74rem;
      color: #d8e6f2;
      min-width: 42px;
      text-align: left;
    }}
    .eq-switch-btn {{
      min-height: 34px;
      padding: 0 10px;
      border-radius: 8px;
      border: 1px solid var(--spot-border);
      background: rgba(255,255,255,0.04);
      color: #dff7ff;
      font-size: 0.75rem;
      font-weight: 700;
      cursor: pointer;
    }}
    .eq-switch-btn.is-on {{
      border-color: var(--accent);
      background: rgba(88, 213, 255, 0.16);
      color: #fff;
    }}
    .vol-ico {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 18px;
      height: 18px;
      opacity: 0.78;
    }}
    .vol-ico svg {{
      width: 18px;
      height: 18px;
      stroke: currentColor;
      fill: none;
      stroke-width: 2;
      stroke-linecap: round;
      stroke-linejoin: round;
    }}
    input[type="range"].range-vol {{
      flex: 1;
      height: 4px;
      -webkit-appearance: none;
      appearance: none;
      background: #444;
      border-radius: 2px;
      direction: ltr;
    }}
    input[type="range"].range-vol::-webkit-slider-thumb {{
      -webkit-appearance: none;
      width: 10px;
      height: 10px;
      border-radius: 50%;
      background: #fff;
      margin-top: -3px;
    }}

    .player-card-inner {{
      background: var(--spot-card);
      border: 1px solid var(--spot-border);
      border-radius: var(--radius-lg);
      padding: 20px;
    }}

    .toolbar {{
      display: grid;
      grid-template-columns: repeat(5, minmax(0, 1fr));
      gap: 10px;
      margin-bottom: 14px;
    }}

    .toolbar input,
    .toolbar select {{
      width: 100%;
      height: 42px;
      background: var(--spot-elevated);
      color: var(--spot-text);
      border: 1px solid var(--spot-border);
      border-radius: var(--radius);
      padding: 0 12px;
      font-size: 0.88rem;
      font-family: inherit;
    }}
    .toolbar input:focus,
    .toolbar select:focus {{
      outline: none;
      border-color: var(--accent);
      box-shadow: 0 0 0 2px var(--accent-soft);
    }}
    .toolbar input::placeholder {{ color: #777; }}

    .section-label {{
      font-size: 0.68rem;
      font-weight: 800;
      letter-spacing: 0.1em;
      text-transform: uppercase;
      color: var(--spot-sub);
      margin: 4px 0 8px;
      text-align: right;
    }}

    .yt-search {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 10px;
      margin-bottom: 14px;
    }}

    .yt-search input {{
      height: 42px;
      background: var(--spot-elevated);
      border: 1px solid var(--spot-border);
      border-radius: var(--radius);
      color: #fff;
      padding: 0 14px;
      font-family: inherit;
      font-size: 0.88rem;
    }}
    .yt-search input:focus {{
      outline: none;
      border-color: var(--accent);
    }}

    .video-shell {{
      width: 100%;
      aspect-ratio: 16 / 9;
      max-height: min(52vh, 520px);
      background: #000;
      border-radius: var(--radius-lg);
      overflow: hidden;
      position: relative;
      margin-bottom: 12px;
      box-shadow: 0 12px 40px rgba(0, 0, 0, 0.5);
    }}

    .video-shell .media-layer {{
      position: absolute;
      inset: 0;
      width: 100%;
      height: 100%;
    }}
    video.media-layer {{ object-fit: contain; background: #000; }}
    iframe.media-layer {{ border: 0; display: none; }}
    .video-shell.use-embed #video {{ display: none; }}
    .video-shell.use-embed #ytEmbed {{ display: block; }}

    button {{
      font-family: inherit;
      border-radius: 10px;
      padding: 10px 18px;
      font-weight: 700;
      font-size: 0.82rem;
      cursor: pointer;
      border: none;
      transition: transform 0.12s ease, filter 0.15s ease;
    }}
    button:active {{ transform: scale(0.99); }}

    button.secondary {{
      background: var(--spot-elevated);
      color: var(--spot-text);
      border: 1px solid var(--spot-border);
    }}
    button.secondary:hover {{ background: #2a313a; }}

    button.danger {{
      background: #262c33;
      color: #c9d2dc;
      border: 1px solid #38414b;
    }}

    button.active {{
      color: #b6dcff !important;
      border-color: var(--accent) !important;
      background: var(--accent-soft) !important;
    }}

    #repeatBtn[data-mode="one"] {{
      color: #b6dcff;
      border-color: var(--accent-glow);
    }}

    .yt-results {{
      max-height: 220px;
      overflow-y: auto;
      border: 1px solid var(--spot-border);
      border-radius: var(--radius);
      background: rgba(0, 0, 0, 0.25);
      margin-bottom: 8px;
    }}

    .yt-item {{
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 10px;
      padding: 10px 12px;
      border-bottom: 1px solid var(--spot-border);
      align-items: center;
    }}
    .yt-item:last-child {{ border-bottom: none; }}
    .yt-title {{ font-size: 0.85rem; color: #eee; text-align: right; }}

    .status {{
      margin-top: 12px;
      padding-top: 12px;
      border-top: 1px solid var(--spot-border);
      color: #a8efff;
      font-size: 0.8rem;
      text-align: right;
      line-height: 1.45;
      word-break: break-word;
    }}

    .car-hint {{
      display: none;
      margin-bottom: 12px;
      padding: 12px 14px;
      border-radius: var(--radius);
      background: rgba(94, 223, 255, 0.08);
      border: 1px solid rgba(94, 223, 255, 0.22);
      color: #b8f2ff;
      font-size: 0.85rem;
      font-weight: 600;
    }}

    @media (max-width: 1024px) {{
      .app-root {{ display: block; }}
      .spot-sidebar {{
        display: none;
      }}
      .bottom-nav {{ display: grid; }}
      .quick-results {{ max-height: 220px; }}
      .toolbar {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .main-column {{ padding-bottom: 112px; }}
    }}

    @media (max-width: 768px) {{
      .main-column {{ padding: 16px 14px 122px; }}
      .np-layout {{ flex-direction: column; align-items: stretch; }}
      .np-art-wrap {{ align-self: center; }}
      .vol-row {{ max-width: none; }}
      .toolbar {{ grid-template-columns: 1fr; }}
      .yt-search {{ grid-template-columns: 1fr; }}
      .controls.transport-bar .icon-btn {{ width: 40px; height: 40px; }}
      .play-fab {{ width: 56px; height: 56px; font-size: 1.25rem; }}
      .bottom-nav-btn {{ min-height: 50px; font-size: 0.7rem; }}
      .bottom-nav-ico,
      .bottom-nav-ico svg {{ width: 18px; height: 18px; }}
    }}
    @media (max-width: 560px) {{
      .now-playing-card {{ padding: 16px; border-radius: 14px; }}
      .player-card-inner {{ padding: 14px; border-radius: 14px; }}
      .up-next-card {{ padding: 12px 12px; border-radius: 14px; }}
      .np-artwork {{ width: min(160px, 52vw); height: min(160px, 52vw); }}
      .meta.np-title {{ font-size: 1.06rem; }}
      .controls.transport-bar {{ gap: 10px; }}
      .toolbar {{ grid-template-columns: 1fr; }}
      .bottom-nav {{ gap: 5px; padding: 7px 8px calc(7px + env(safe-area-inset-bottom, 0px)); }}
      .bottom-nav-btn {{ min-height: 46px; font-size: 0.68rem; padding: 4px 6px; }}
      .bottom-nav-label {{ font-size: 0.67rem; }}
      .home-tiles {{ grid-template-columns: 1fr; }}
      .quick-results .queue-actions button {{ min-width: 34px; }}
    }}

    body.car-mode .spot-sidebar,
    body.car-mode .glass-top,
    body.car-mode .content-views,
    body.car-mode .yt-search,
    body.car-mode .yt-results,
    body.car-mode #downloadTrackBtn,
    body.car-mode #saveOfflineBtn,
    body.car-mode .section-label,
    body.car-mode .toolbar {{
      display: none !important;
    }}

    body.car-mode .now-playing-card {{
      background: transparent;
      border: none;
      box-shadow: none;
      padding: 12px 8px;
    }}

    body.car-mode .np-art-wrap {{ display: none; }}

    body.car-mode .car-hint {{
      display: flex;
      flex-direction: column;
      gap: 10px;
      align-items: stretch;
      text-align: right;
    }}
    @media (min-width: 520px) {{
      body.car-mode .car-hint {{
        flex-direction: row;
        flex-wrap: wrap;
        align-items: center;
        justify-content: space-between;
      }}
    }}
    .car-exit-btn {{
      flex-shrink: 0;
      min-height: 44px;
      padding: 10px 16px;
      border-radius: 12px;
      border: 1px solid rgba(94, 223, 255, 0.45);
      background: rgba(94, 223, 255, 0.18);
      color: #e8fbff;
      font-size: 0.9rem;
      font-weight: 700;
      cursor: pointer;
      font-family: inherit;
    }}
    .car-exit-btn:hover {{
      background: rgba(94, 223, 255, 0.28);
    }}
    .car-exit-btn:focus-visible {{
      outline: 2px solid var(--accent, #5edfff);
      outline-offset: 2px;
    }}

    body.car-mode .controls.transport-bar {{
      gap: 12px;
      margin-top: 8px;
    }}

    body.car-mode .controls.transport-bar .icon-btn {{
      min-width: 72px;
      min-height: 64px;
      width: auto;
      height: auto;
      border-radius: 16px;
      font-size: 1.1rem;
    }}

    body.car-mode .play-fab {{
      width: 88px;
      height: 88px;
      font-size: 1.75rem;
    }}

    body.car-mode .meta.np-title {{
      text-align: center;
      font-size: 1.25rem;
    }}

    body.car-mode .scrobble-row,
    body.car-mode .vol-row {{
      display: none;
    }}

    body.car-mode .status {{ text-align: center; }}

    body.car-mode .player-card-inner {{
      background: transparent;
      border: none;
      padding: 0;
    }}

    body.car-mode .video-shell {{
      max-height: 40vh;
    }}

    body.car-mode .up-next-card {{
      display: none !important;
    }}

    .top-nav {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-bottom: 16px;
      justify-content: flex-end;
    }}
    .nav-pill {{
      display: inline-block;
      padding: 8px 16px;
      border-radius: 999px;
      font-size: 0.82rem;
      font-weight: 700;
      color: var(--spot-sub);
      background: var(--spot-elevated);
      border: 1px solid var(--spot-border);
      text-decoration: none;
      transition: background 0.15s, color 0.15s, border-color 0.15s;
    }}
    .nav-pill:hover {{
      color: var(--spot-text);
      border-color: var(--accent-glow);
      background: rgba(255, 255, 255, 0.08);
    }}

    .np-summary-row {{
      display: flex;
      flex-direction: row-reverse;
      align-items: center;
      gap: 10px;
      margin-bottom: 10px;
      cursor: pointer;
      user-select: none;
    }}
    .np-summary-text {{
      flex: 1;
      min-width: 0;
      text-align: right;
    }}
    .np-summary-text .np-label {{
      margin-bottom: 4px;
    }}
    .np-summary-text .meta.np-title {{
      margin: 0;
      font-size: 1.03rem;
      display: -webkit-box;
      -webkit-box-orient: vertical;
      -webkit-line-clamp: 1;
      overflow: hidden;
      text-overflow: ellipsis;
    }}
    .np-expand-indicator {{
      width: 24px;
      height: 24px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      color: var(--spot-sub);
      transition: transform 0.16s ease, color 0.16s ease;
    }}
    .np-expand-indicator svg {{
      width: 18px;
      height: 18px;
      stroke: currentColor;
      fill: none;
      stroke-width: 2;
      stroke-linecap: round;
      stroke-linejoin: round;
    }}
    .np-expanded-content {{
      margin-top: 6px;
    }}
    .now-playing-card.is-collapsed .np-expanded-content {{
      display: none;
    }}
    .now-playing-card.is-collapsed .np-summary-row {{
      margin-bottom: 0;
    }}
    .now-playing-card:not(.is-collapsed) .np-expand-indicator {{
      transform: rotate(180deg);
      color: #dff7ff;
    }}
    .now-playing-card.is-page-open {{
      inset: 0;
      width: 100%;
      max-width: none;
      transform: none;
      bottom: 0;
      border-radius: 0;
      padding: 14px 14px calc(96px + env(safe-area-inset-bottom, 0px));
      background: linear-gradient(180deg, #05070a 0%, #0a1118 100%);
      z-index: 23000;
      overflow-y: auto;
      box-shadow: none;
    }}
    .np-page-open .glass-top,
    .np-page-open .content-views {{
      opacity: 0;
      pointer-events: none;
    }}
    .now-playing-card.is-page-open .np-layout {{
      align-items: flex-start;
      gap: 16px;
    }}
    .now-playing-card.is-page-open .np-art-wrap {{
      display: none;
    }}
    .now-playing-card.is-page-open .np-text-col {{
      width: min(760px, 100%);
      /* Keep metadata/controls always below the fixed video card */
      margin: calc(min(56vw, 460px) + 86px) auto 0;
    }}
    .now-playing-card.is-page-open .np-summary-row {{
      position: sticky;
      top: 0;
      z-index: 2;
      padding: 8px 0;
      background: linear-gradient(180deg, rgba(5, 7, 10, 0.96) 60%, rgba(5, 7, 10, 0));
    }}
    .now-playing-card.is-page-open .np-summary-text .meta.np-title {{
      -webkit-line-clamp: 2;
      font-size: 1.2rem;
    }}
    .np-page-close-btn {{
      display: none;
      width: 34px;
      height: 34px;
      border-radius: 999px;
      border: 1px solid rgba(255,255,255,0.16);
      background: rgba(255,255,255,0.04);
      color: #dbe8f2;
      align-items: center;
      justify-content: center;
      padding: 0;
      cursor: pointer;
      flex-shrink: 0;
    }}
    .np-page-close-btn svg {{
      width: 18px;
      height: 18px;
      stroke: currentColor;
      fill: none;
      stroke-width: 2;
      stroke-linecap: round;
      stroke-linejoin: round;
    }}
    .now-playing-card.is-page-open .np-page-close-btn {{
      display: inline-flex;
    }}
    .now-playing-card.is-page-open .np-expand-indicator {{
      display: none;
    }}
    .now-playing-card.is-page-open ~ .bottom-nav,
    .now-playing-card.is-page-open ~ .nav-back-fab {{
      opacity: 0;
      pointer-events: none;
    }}
    .np-page-open .player-card-inner {{
      display: block !important;
      position: fixed;
      left: 50%;
      transform: translateX(-50%);
      top: 72px;
      z-index: 23150;
      width: min(760px, calc(100% - 20px));
      padding: 0;
      border: 0;
      background: transparent;
      box-shadow: none;
      pointer-events: auto;
    }}
    .np-page-open .player-card-inner #videoSection {{
      display: block;
    }}
    .np-page-open .player-card-inner .video-shell {{
      margin-bottom: 0;
      border-radius: 14px;
      max-height: none;
      aspect-ratio: 16 / 9;
    }}
    @media (min-width: 769px) {{
      /* Desktop compact mode: a clean, small player window + fullscreen option. */
      .np-page-open .player-card-inner {{
        position: fixed;
        left: 50%;
        transform: translateX(-50%);
        top: 76px;
        width: min(520px, calc(100% - 24px));
        margin: 0;
        border: 1px solid var(--spot-border);
        border-radius: 16px;
        background: rgba(10, 16, 22, 0.72);
        backdrop-filter: blur(6px);
        -webkit-backdrop-filter: blur(6px);
        z-index: 23150;
      }}
      .now-playing-card.is-page-open .np-text-col {{
        width: min(940px, 100%);
        margin: 430px auto 0;
      }}
      .now-playing-card.is-page-open .np-expanded-content {{
        max-width: 940px;
        margin: 0 auto;
      }}
      .np-page-open .player-card-inner .video-shell {{
        border: 1px solid rgba(255,255,255,0.08);
      }}
      .now-playing-card.is-page-open .eq-drawer {{
        max-width: 940px;
        margin: 10px auto 0;
      }}
      .now-playing-card.is-page-open .eq-bands {{
        grid-template-columns: repeat(5, minmax(0, 1fr));
        row-gap: 10px;
      }}
    }}
    .np-page-open .player-card-inner .toolbar,
    .np-page-open .player-card-inner #discover,
    .np-page-open .player-card-inner .status,
    .np-page-open .player-card-inner .car-hint {{
      display: none !important;
    }}
    @media (max-width: 768px) {{
      .np-page-open .player-card-inner {{
        top: 66px;
        width: calc(100% - 14px);
      }}
      .now-playing-card.is-page-open .np-text-col {{
        margin-top: calc(min(60vw, 340px) + 72px);
      }}
    }}
    .like-btn {{
      flex-shrink: 0;
      width: 38px;
      height: 38px;
      border-radius: 0;
      border: 0;
      background: transparent;
      color: #ffffff;
      cursor: pointer;
      line-height: 1;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      transition: color 0.15s, transform 0.12s ease;
    }}
    .like-btn svg {{
      width: 30px;
      height: 30px;
      stroke: currentColor;
      fill: none;
      stroke-width: 2.2;
      stroke-linecap: round;
      stroke-linejoin: round;
      filter: drop-shadow(0 0 3px rgba(0,0,0,0.65));
    }}
    .like-btn .ico-heart-fill {{
      display: none;
      fill: currentColor;
      stroke: none;
    }}
    .like-btn:hover {{ color: #fff; transform: scale(1.04); }}
    .like-btn.liked {{
      color: var(--accent);
    }}
    .like-btn.liked .ico-heart-stroke {{ display: none; }}
    .like-btn.liked .ico-heart-fill {{ display: block; }}
    .np-quick-play-btn {{
      flex-shrink: 0;
      width: 38px;
      height: 38px;
      border: 0;
      background: transparent;
      color: #ffffff;
      cursor: pointer;
      line-height: 1;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      padding: 0;
      transition: color 0.15s, transform 0.12s ease;
    }}
    .np-quick-play-btn:hover {{ color: #fff; transform: scale(1.04); }}
    .np-quick-play-btn svg {{
      width: 30px;
      height: 30px;
      stroke: currentColor;
      fill: currentColor;
      stroke-width: 0;
      stroke-linecap: round;
      stroke-linejoin: round;
      filter: drop-shadow(0 0 3px rgba(0,0,0,0.65));
    }}
    .np-quick-play-btn .ico-quick-pause {{ display: none; }}
    .np-quick-play-btn.is-playing .ico-quick-play {{ display: none; }}
    .np-quick-play-btn.is-playing .ico-quick-pause {{
      display: block;
    }}
    .icon-btn .repeat-one-mark {{
      display: none;
      position: absolute;
      top: 8px;
      right: 8px;
      min-width: 12px;
      height: 12px;
      border-radius: 999px;
      background: rgba(88, 213, 255, 0.22);
      color: #c7f3ff;
      font-size: 0.58rem;
      line-height: 12px;
      text-align: center;
      font-weight: 800;
    }}
    #repeatBtn {{
      position: relative;
    }}
    #repeatBtn[data-mode="one"] .repeat-one-mark {{
      display: block;
    }}

    .up-next-card {{
      background: var(--spot-card);
      border: 1px solid var(--spot-border);
      border-radius: var(--radius-lg);
      padding: 16px 18px;
      margin-bottom: 18px;
    }}
    .section-heading {{
      margin: 0 0 12px;
      font-size: 0.78rem;
      font-weight: 800;
      letter-spacing: 0.1em;
      text-transform: uppercase;
      color: var(--spot-sub);
      text-align: right;
    }}
    .up-next-list {{ min-height: 24px; }}
    .up-next-hint {{
      font-size: 0.85rem;
      color: var(--spot-sub);
      text-align: right;
      padding: 8px 0;
    }}
    .up-next-row {{
      display: flex;
      flex-direction: row-reverse;
      align-items: center;
      gap: 12px;
      padding: 10px 12px;
      border-radius: var(--radius);
      cursor: pointer;
      text-align: right;
      transition: background 0.12s;
    }}
    .up-next-row:hover {{ background: var(--spot-elevated); }}
    .up-next-row .up-num {{
      font-size: 0.75rem;
      font-weight: 800;
      color: var(--accent);
      min-width: 22px;
    }}
    .up-next-row .up-title {{
      flex: 1;
      font-size: 0.88rem;
      color: var(--spot-text);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}

    .sidebar-recent-wrap {{ margin-bottom: 16px; }}
    .recent-list {{
      max-height: 190px;
      overflow-y: auto;
      border-radius: var(--radius);
      border: 1px solid var(--spot-border);
      background: rgba(0, 0, 0, 0.2);
    }}
    .recent-row {{
      padding: 8px 10px;
      font-size: 0.8rem;
      color: #ddd;
      cursor: pointer;
      border-bottom: 1px solid var(--spot-border);
      text-align: right;
      white-space: normal;
      overflow: visible;
      text-overflow: clip;
      line-height: 1.35;
      word-break: break-word;
    }}
    .recent-row:last-child {{ border-bottom: none; }}
    .recent-row:hover {{ background: var(--spot-elevated); }}

    .like-inline {{
      width: 32px;
      height: 32px;
      padding: 0;
      border-radius: 50%;
      border: none;
      background: transparent;
      color: var(--spot-sub);
      cursor: pointer;
      line-height: 1;
      flex-shrink: 0;
      display: inline-flex;
      align-items: center;
      justify-content: center;
    }}
    .like-inline svg {{
      width: 16px;
      height: 16px;
      stroke: currentColor;
      fill: none;
      stroke-width: 2;
      stroke-linecap: round;
      stroke-linejoin: round;
    }}
    .like-inline .ico-heart-fill {{
      display: none;
      fill: currentColor;
      stroke: none;
    }}
    .like-inline.liked {{ color: var(--accent); }}
    .like-inline.liked .ico-heart-stroke {{ display: none; }}
    .like-inline.liked .ico-heart-fill {{ display: block; }}
    .queue-play-btn {{
      width: 34px;
      min-width: 34px;
      padding: 0;
      display: inline-flex;
      align-items: center;
      justify-content: center;
    }}
    .queue-play-btn svg {{
      width: 15px;
      height: 15px;
      fill: currentColor;
      stroke: none;
    }}

    .am-nav {{
      display: flex;
      flex-direction: column;
      gap: 4px;
      margin-bottom: 20px;
    }}
    .am-nav-item {{
      width: 100%;
      text-align: right;
      padding: 10px 12px;
      border-radius: 8px;
      border: none;
      background: transparent;
      color: var(--spot-sub);
      font-family: var(--font);
      font-size: 0.92rem;
      font-weight: 600;
      cursor: pointer;
      transition: background 0.15s, color 0.15s;
    }}
    .am-nav-item:hover {{
      background: var(--spot-elevated);
      color: var(--spot-text);
    }}
    .am-nav-item:focus-visible {{
      outline: 2px solid var(--accent);
      outline-offset: 2px;
    }}
    .am-nav-item.is-active {{
      background: var(--accent-soft);
      color: var(--spot-text);
      box-shadow: inset 0 0 0 1px var(--accent-glow);
    }}
    .bottom-nav {{
      position: fixed;
      left: 0;
      right: 0;
      bottom: 0;
      z-index: 15000;
      display: none;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 2px;
      padding: 8px 10px calc(8px + env(safe-area-inset-bottom, 0px));
      background: linear-gradient(180deg, rgba(7, 9, 12, 0.84) 0%, rgba(8, 10, 14, 0.98) 45%);
      border-top: 1px solid rgba(255, 255, 255, 0.08);
      backdrop-filter: saturate(135%) blur(14px);
      -webkit-backdrop-filter: saturate(135%) blur(14px);
    }}
    .bottom-nav-btn {{
      border: 0;
      border-radius: 12px;
      background: transparent;
      color: #c8d2dd;
      min-height: 56px;
      padding: 6px 4px;
      font-family: var(--font);
      font-size: 0.72rem;
      font-weight: 700;
      cursor: pointer;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      gap: 4px;
      transition: background 0.15s, color 0.15s, transform 0.12s;
    }}
    .bottom-nav-btn:hover {{
      color: #fff;
      background: rgba(255, 255, 255, 0.06);
    }}
    .bottom-nav-btn:focus-visible {{
      outline: 2px solid var(--accent);
      outline-offset: 2px;
    }}
    .bottom-nav-btn.is-active {{
      color: #ff7a00;
      background: rgba(255, 122, 0, 0.12);
      transform: translateY(-1px);
    }}
    .bottom-nav-ico {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 20px;
      height: 20px;
      line-height: 1;
    }}
    .bottom-nav-ico svg {{
      width: 20px;
      height: 20px;
      stroke: currentColor;
      fill: none;
      stroke-width: 2;
      stroke-linecap: round;
      stroke-linejoin: round;
    }}
    .bottom-nav-label {{
      display: block;
      line-height: 1;
      font-size: 0.73rem;
      letter-spacing: 0.01em;
    }}
    .playlist-nav-list {{
      display: flex;
      flex-direction: column;
      gap: 2px;
      max-height: 200px;
      overflow-y: auto;
      margin-bottom: 16px;
    }}
    .playlist-nav-btn {{
      text-align: right;
      padding: 8px 10px;
      border-radius: 6px;
      border: none;
      background: transparent;
      color: var(--spot-sub);
      font-size: 0.82rem;
      font-weight: 600;
      cursor: pointer;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}
    .playlist-nav-btn:hover {{ background: var(--spot-elevated); color: var(--spot-text); }}

    .content-views {{
      min-height: 220px;
      margin-bottom: 20px;
    }}
    .view-panel {{
      display: none;
      animation: viewFade 0.22s ease;
    }}
    .view-panel.is-active {{ display: block; }}
    @keyframes viewFade {{
      from {{
        opacity: 0;
        transform: translateY(4px);
      }}
      to {{
        opacity: 1;
        transform: translateY(0);
      }}
    }}

    .view-head {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 12px;
      margin-bottom: 16px;
    }}
    .view-title {{
      margin: 0;
      font-size: 1.6rem;
      font-weight: 800;
      letter-spacing: -0.02em;
    }}
    .global-search-wrap {{
      margin-top: 10px;
      max-width: 560px;
      position: relative;
    }}
    .global-search-wrap::before {{
      content: "⌕";
      position: absolute;
      right: 12px;
      top: 50%;
      transform: translateY(-50%);
      color: var(--spot-sub);
      font-size: 0.95rem;
      pointer-events: none;
    }}
    .global-search-wrap #search {{
      width: 100%;
      padding-right: 34px;
    }}
    /* רשימת חיפוש צפה הוסרה — תוצאות יוטיוב רק בעמוד הספרייה */
    .global-search-results {{
      display: none !important;
      visibility: hidden;
      pointer-events: none;
      position: absolute;
      width: 0;
      height: 0;
      overflow: hidden;
    }}
    .gs-row {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto auto;
      gap: 8px;
      align-items: center;
      padding: 10px 10px;
      border-bottom: 1px solid var(--spot-border);
    }}
    .gs-row:last-child {{ border-bottom: none; }}
    .gs-title {{
      font-size: 0.83rem;
      color: #eaf3fb;
      text-align: right;
      line-height: 1.35;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    .gs-empty {{
      padding: 10px 12px;
      font-size: 0.8rem;
      color: var(--spot-sub);
      text-align: right;
    }}
    .yt-search-page {{
      margin-bottom: 14px;
      border: 1px solid var(--spot-border);
      border-radius: 12px;
      background: rgba(0, 0, 0, 0.2);
      overflow: hidden;
    }}
    .yt-search-page-head {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      flex-wrap: wrap;
      padding: 10px 12px;
      border-bottom: 1px solid var(--spot-border);
      background: rgba(255,255,255,0.03);
    }}
    .yt-search-page-head-text {{
      min-width: 0;
      flex: 1 1 200px;
    }}
    .yt-search-toolbar-search-host {{
      flex: 1 1 260px;
      min-width: 0;
      max-width: 100%;
    }}
    .yt-search-toolbar-search-host .global-search-wrap {{
      width: 100%;
      max-width: 100%;
    }}
    .glass-top-search-host {{
      width: 100%;
      margin-top: 6px;
    }}
    .home-settings-bar {{
      display: flex;
      justify-content: flex-end;
      align-items: center;
      gap: 10px;
      margin-bottom: 12px;
    }}
    .home-settings-actions {{
      display: flex;
      flex-direction: column;
      gap: 10px;
      margin: 12px 0 6px;
    }}
    .home-settings-actions > button {{
      width: 100%;
      justify-content: center;
    }}
    .yt-search-page-title {{
      margin: 0;
      font-size: 0.9rem;
      color: #eaf3fb;
      text-align: right;
      font-weight: 700;
    }}
    .yt-search-page-sub {{
      margin: 0;
      font-size: 0.75rem;
      color: var(--spot-sub);
      text-align: right;
    }}
    .yt-search-page-list {{
      max-height: min(58vh, 520px);
      overflow-y: auto;
    }}
    .yt-sp-row {{
      display: grid;
      grid-template-columns: 58px minmax(0, 1fr) auto;
      gap: 10px;
      align-items: center;
      padding: 8px 10px;
      border-bottom: 1px solid var(--spot-border);
      direction: ltr;
      cursor: pointer;
    }}
    .yt-sp-row:last-child {{ border-bottom: none; }}
    .yt-sp-thumb {{
      width: 58px;
      height: 42px;
      border-radius: 8px;
      object-fit: cover;
      background: #111;
      border: 1px solid rgba(255,255,255,0.1);
    }}
    .yt-sp-meta {{
      min-width: 0;
      text-align: right;
      direction: rtl;
    }}
    .yt-sp-title {{
      font-size: 0.88rem;
      color: #f2f6fb;
      line-height: 1.3;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      margin-bottom: 2px;
    }}
    .yt-sp-sub {{
      font-size: 0.74rem;
      color: var(--spot-sub);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}
    .yt-sp-plus {{
      width: 34px;
      height: 34px;
      border-radius: 10px;
      border: 1px solid rgba(255, 140, 0, 0.55);
      background: rgba(255, 140, 0, 0.08);
      color: #ff9a2f;
      font-size: 1.35rem;
      font-weight: 700;
      line-height: 1;
      padding: 0;
      display: inline-flex;
      align-items: center;
      justify-content: center;
    }}
    .yt-sp-plus:hover {{
      background: rgba(255, 140, 0, 0.18);
      color: #ffb35e;
    }}
    .home-tiles {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
      gap: 12px;
      margin-bottom: 24px;
    }}
    .home-tile {{
      padding: 18px 16px;
      border-radius: 14px;
      background: var(--spot-card);
      border: 1px solid var(--spot-border);
      cursor: pointer;
      text-align: right;
      transition: transform 0.12s, border-color 0.12s, box-shadow 0.12s;
    }}
    .home-tile:hover {{
      border-color: var(--accent-glow);
      transform: translateY(-2px);
      box-shadow: 0 14px 28px rgba(0, 0, 0, 0.26);
    }}
    .home-tile .ht-label {{ font-weight: 700; font-size: 0.95rem; margin-bottom: 6px; }}
    .home-tile .ht-meta {{ font-size: 0.78rem; color: var(--spot-sub); font-weight: 600; }}
    .home-recent h3 {{
      margin: 0 0 12px;
      font-size: 0.85rem;
      font-weight: 800;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--spot-sub);
    }}

    .library-scroll .quick-results {{
      max-height: min(52vh, 520px);
    }}

    .liked-panel-list .queue-row,
    .playlist-detail-list .queue-row {{
      grid-template-columns: 48px 1fr auto;
    }}

    .playlists-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
      gap: 14px;
    }}
    .pl-card {{
      padding: 20px 16px;
      border-radius: 14px;
      background: var(--spot-card);
      border: 1px solid var(--spot-border);
      cursor: pointer;
      text-align: right;
      transition: transform 0.12s, border-color 0.12s, box-shadow 0.12s;
    }}
    .pl-card:hover {{
      border-color: var(--accent);
      transform: translateY(-2px);
      box-shadow: 0 14px 28px rgba(0, 0, 0, 0.26);
    }}
    .pl-card h3 {{ margin: 0 0 8px; font-size: 1.05rem; font-weight: 800; }}
    .pl-card .pl-count {{ font-size: 0.8rem; color: var(--spot-sub); font-weight: 600; }}
    .pl-head-row {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      flex-wrap: wrap;
      margin-bottom: 18px;
    }}
    .yt-rec-wrap {{
      margin-top: 16px;
    }}
    .yt-rec-head {{
      margin: 0 0 10px;
      font-size: 0.92rem;
      font-weight: 800;
      color: #d9e8f3;
    }}
    .yt-rec-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
      gap: 10px;
    }}
    .yt-rec-card {{
      border: 1px solid var(--spot-border);
      border-radius: 12px;
      background: rgba(255, 255, 255, 0.03);
      padding: 12px;
      text-align: right;
    }}
    .yt-rec-title {{
      font-size: 0.86rem;
      font-weight: 700;
      margin: 0 0 6px;
      color: #ecf4fb;
    }}
    .yt-rec-sub {{
      font-size: 0.75rem;
      color: var(--spot-sub);
      margin: 0 0 10px;
      min-height: 32px;
    }}
    .yt-rec-open {{
      width: 100%;
      min-height: 36px;
      border-radius: 10px;
      border: 1px solid rgba(88, 213, 255, 0.45);
      background: rgba(88, 213, 255, 0.1);
      color: #d9f7ff;
      font-family: var(--font);
      font-size: 0.78rem;
      font-weight: 700;
      cursor: pointer;
    }}
    .yt-rec-open:hover {{
      border-color: var(--accent);
      background: rgba(88, 213, 255, 0.18);
      color: #fff;
    }}
    .pl-detail-actions {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-bottom: 16px;
    }}

    .pl-add-btn {{
      width: 36px;
      height: 36px;
      padding: 0;
      border-radius: 8px;
      border: 1px solid var(--spot-border);
      background: var(--spot-elevated);
      color: var(--spot-text);
      font-size: 1.1rem;
      cursor: pointer;
      line-height: 1;
    }}
    .pl-add-btn:hover {{ border-color: var(--accent); color: var(--accent); }}

    .modal-overlay {{
      position: fixed;
      inset: 0;
      background: rgba(0, 0, 0, 0.65);
      display: none;
      align-items: center;
      justify-content: center;
      z-index: 20000;
      padding: 16px;
    }}
    .modal-overlay.is-open {{ display: flex; }}
    .modal-card {{
      width: min(400px, 100%);
      padding: 22px;
      border-radius: 16px;
      background: var(--spot-card);
      border: 1px solid var(--spot-border);
      box-shadow: 0 24px 80px rgba(0, 0, 0, 0.55);
    }}
    .modal-card h3 {{ margin: 0 0 14px; font-size: 1.1rem; }}
    .modal-card select {{
      width: 100%;
      padding: 10px 12px;
      margin-bottom: 14px;
      border-radius: 10px;
      border: 1px solid var(--spot-border);
      background: var(--spot-base);
      color: var(--spot-text);
      font-family: var(--font);
    }}
    .modal-actions {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      justify-content: flex-end;
    }}

    .ui-version-strip {{
      display: none;
    }}
    .ui-version-strip strong {{ color: #b6dcff; letter-spacing: 0.04em; }}
    .ui-version-strip-mid {{ color: rgba(255, 255, 255, 0.88); max-width: 52ch; }}
    .ui-version-strip-btn {{
      padding: 6px 14px;
      border-radius: 8px;
      border: 1px solid rgba(255, 255, 255, 0.35);
      background: rgba(0, 0, 0, 0.35);
      color: #fff;
      font-family: var(--font);
      font-size: 0.78rem;
      font-weight: 700;
      cursor: pointer;
    }}
    .ui-version-strip-btn:hover {{ background: rgba(46, 168, 255, 0.35); border-color: var(--accent); }}
    .ui-version-strip-link {{
      color: #b6dcff;
      font-weight: 700;
      text-decoration: underline;
      font-size: 0.78rem;
    }}
    .ui-version-strip-link:hover {{ color: #fff; }}

    body.car-mode .ui-version-strip {{ display: none; }}
    body.car-mode .pwa-install-modal {{ display: none !important; }}
    body.car-mode .bottom-nav {{ display: none; }}

    .code-stale-banner {{
      display: none;
      background: linear-gradient(90deg, #13222b, #0f1d26);
      color: #dff8ff;
      padding: 12px 16px;
      font-size: 0.88rem;
      font-weight: 600;
      text-align: center;
      border-bottom: 2px solid var(--accent);
      line-height: 1.45;
    }}

    .pwa-install-modal {{
      position: fixed;
      inset: 0;
      z-index: 25000;
      display: none;
      align-items: center;
      justify-content: center;
      padding: 20px 16px;
      box-sizing: border-box;
    }}
    .pwa-install-modal.is-open {{ display: flex; }}
    .pwa-install-modal-backdrop {{
      position: absolute;
      inset: 0;
      background: rgba(0, 0, 0, 0.6);
      backdrop-filter: blur(4px);
    }}
    .pwa-install-card {{
      position: relative;
      z-index: 1;
      width: min(400px, 100%);
      max-height: 90vh;
      overflow: auto;
      border-radius: 16px;
      background: #151a1f;
      border: 1px solid var(--spot-border);
      box-shadow: 0 24px 64px rgba(0,0,0,0.5);
      padding: 1.1rem 1.15rem 1.05rem;
      text-align: right;
    }}
    .pwa-install-card h2 {{ margin: 0 0 10px; font-size: 1.12rem; font-weight: 800; color: #fff; }}
    .pwa-install-card p {{ margin: 0; font-size: 0.9rem; line-height: 1.5; color: #c8d6df; font-weight: 500; }}
    .pwa-install-card code {{ font-size: 0.8em; color: #7ee0ff; }}
    .pwa-install-actions {{ display: flex; flex-wrap: wrap; gap: 8px; justify-content: flex-end; margin-top: 16px; }}
    .pwa-install-actions button {{
      min-height: 44px;
      padding: 10px 16px;
      border-radius: 10px;
      font-family: var(--font);
      font-size: 0.9rem;
      font-weight: 700;
      cursor: pointer;
      border: 1px solid var(--spot-border);
    }}
    .pwa-install-primary {{
      background: var(--accent);
      border-color: var(--accent);
      color: #0a0e12;
    }}
    .pwa-install-secondary {{
      background: transparent;
      color: #e0eef4;
    }}

    /* --- UI cleanup pass: clarity + comfort --- */
    :root {{
      --spot-base: #0f1318;
      --spot-card: #161d25;
      --spot-elevated: #202a34;
      --spot-border: rgba(255, 255, 255, 0.12);
      --spot-sub: #a9b7c6;
      --accent: #58d5ff;
      --accent-2: #b5efff;
      --accent-glow: rgba(88, 213, 255, 0.38);
      --accent-soft: rgba(88, 213, 255, 0.16);
      --radius: 12px;
      --radius-lg: 18px;
    }}

    body {{
      line-height: 1.45;
      overflow: hidden;
    }}

    .main-column {{
      padding: 22px 24px 92px;
      background: linear-gradient(180deg, #1b2530 0%, var(--spot-base) 32%);
    }}

    .glass-top,
    .content-views,
    .now-playing-card,
    .up-next-card,
    .player-card-inner,
    .modal-card {{
      border-radius: var(--radius-lg);
      border-color: var(--spot-border);
      box-shadow: 0 14px 34px rgba(0, 0, 0, 0.28);
    }}

    .view-title {{
      font-size: 1.25rem;
      letter-spacing: -0.01em;
    }}
    .view-head {{
      gap: 10px;
      margin-bottom: 14px;
    }}
    .sub-help {{
      margin: 0 0 12px;
      color: var(--spot-sub);
      font-size: 0.84rem;
      font-weight: 500;
    }}

    #search,
    #ytQuery,
    .toolbar select {{
      min-height: 44px;
      border-radius: 10px;
      border: 1px solid var(--spot-border);
      background: rgba(255, 255, 255, 0.04);
      color: var(--spot-text);
      font-family: var(--font);
      font-size: 0.92rem;
      padding-inline: 12px;
      outline: none;
      transition: border-color 0.15s ease, box-shadow 0.15s ease, background 0.15s ease;
    }}
    #search:focus,
    #ytQuery:focus,
    .toolbar select:focus {{
      border-color: var(--accent);
      box-shadow: 0 0 0 3px rgba(88, 213, 255, 0.16);
      background: rgba(255, 255, 255, 0.07);
    }}

    @media (max-width: 820px) {{
      .global-search-wrap {{
        max-width: 100%;
      }}
    }}

    button,
    .secondary,
    .bottom-nav-btn,
    .playlist-nav-btn,
    .icon-btn,
    .play-fab {{
      min-height: 42px;
      border-radius: 10px;
      font-weight: 700;
    }}
    .secondary {{
      border-color: var(--spot-border);
      background: rgba(255, 255, 255, 0.04);
    }}
    .secondary:hover {{
      border-color: var(--accent);
      background: rgba(88, 213, 255, 0.12);
      color: #eaffff;
    }}

    .queue-row {{
      grid-template-columns: 54px 1fr auto;
      gap: 12px;
      padding: 10px 12px;
      border: 1px solid transparent;
      background: rgba(255, 255, 255, 0.02);
    }}
    .queue-row:hover {{
      background: rgba(255, 255, 255, 0.06);
      border-color: var(--spot-border);
    }}
    .queue-thumb {{
      width: 54px;
      height: 54px;
      border-radius: 8px;
    }}
    .queue-title {{
      font-size: 0.92rem;
    }}
    .queue-sub {{
      font-size: 0.78rem;
      margin-top: 4px;
    }}

    .np-title {{
      font-size: 1.05rem;
      font-weight: 800;
    }}
    .transport-bar {{
      gap: 8px;
      flex-wrap: wrap;
    }}
    .play-fab {{
      min-width: 52px;
      min-height: 52px;
      box-shadow: 0 10px 28px rgba(88, 213, 255, 0.28);
    }}
    .time-tag {{
      min-width: 46px;
      text-align: center;
      font-variant-numeric: tabular-nums;
    }}

    .bottom-nav {{
      border-top: 1px solid var(--spot-border);
      backdrop-filter: blur(12px);
      -webkit-backdrop-filter: blur(12px);
      background: rgba(12, 16, 21, 0.92);
    }}
    .bottom-nav-btn {{
      color: #c8d2dd;
    }}
    .bottom-nav-btn.is-active {{
      color: #ff7a00;
      background: rgba(255, 122, 0, 0.12);
      border-color: transparent;
    }}
    .app-root {{
      display: block;
      max-width: 980px;
      margin: 0 auto;
    }}
    .spot-sidebar {{
      display: none !important;
    }}
    .main-column {{
      padding: 14px 14px 128px;
      background: radial-gradient(circle at 50% -20%, #2c3d50 0%, #121820 45%, #0b1015 100%);
      min-height: 100vh;
      height: 100vh;
      display: flex;
      flex-direction: column;
      overflow: hidden;
    }}
    .glass-top {{
      position: sticky;
      top: 8px;
      z-index: 12000;
      background: rgba(18, 24, 31, 0.88);
      flex-shrink: 0;
    }}
    .content-views {{
      flex: 1 1 auto;
      min-height: 0;
      overflow-y: auto;
      padding-bottom: 12px;
      margin-bottom: 10px;
    }}
    .now-playing-card {{
      flex-shrink: 0;
      position: fixed;
      width: min(560px, calc(100% - 18px));
      left: 50%;
      transform: translateX(-50%);
      bottom: calc(88px + env(safe-area-inset-bottom, 0px));
      z-index: 14000;
      margin: 0;
      border-radius: 18px;
      padding: 12px 14px;
      box-shadow: 0 18px 40px rgba(0, 0, 0, 0.45);
    }}
    .np-layout {{
      gap: 10px;
    }}
    .np-art-wrap {{
      display: none;
    }}
    .up-next-card,
    .player-card-inner {{
      display: none !important;
    }}
    .bottom-nav {{
      display: grid !important;
      width: min(560px, calc(100% - 18px));
      left: 50%;
      right: auto;
      transform: translateX(-50%);
      bottom: 10px;
      border: 1px solid rgba(255, 255, 255, 0.1);
      border-radius: 18px;
      box-shadow: 0 18px 44px rgba(0, 0, 0, 0.5);
    }}
    .nav-back-fab {{
      position: fixed;
      left: 14px;
      top: calc(12px + env(safe-area-inset-top, 0px));
      bottom: auto;
      z-index: 15010;
      min-width: 46px;
      min-height: 46px;
      border-radius: 999px;
      border: 1px solid rgba(255, 255, 255, 0.14);
      background: rgba(14, 19, 24, 0.92);
      color: #f2f7fb;
      font-family: var(--font);
      font-size: 1.08rem;
      font-weight: 800;
      cursor: pointer;
      box-shadow: 0 10px 26px rgba(0, 0, 0, 0.38);
    }}
    .nav-back-fab svg {{
      width: 20px;
      height: 20px;
      stroke: currentColor;
      fill: none;
      stroke-width: 2;
      stroke-linecap: round;
      stroke-linejoin: round;
    }}
    .nav-back-fab[hidden] {{
      display: none !important;
    }}
    @media (max-width: 560px) {{
      .bottom-nav {{
        width: calc(100% - 12px);
        bottom: 6px;
      }}
      .now-playing-card {{
        width: calc(100% - 12px);
        bottom: calc(82px + env(safe-area-inset-bottom, 0px));
        padding: 10px 10px;
      }}
      .nav-back-fab {{
        left: 10px;
        top: calc(10px + env(safe-area-inset-top, 0px));
        bottom: auto;
      }}
    }}
    @media (max-width: 430px) {{
      .main-column {{
        padding: calc(8px + env(safe-area-inset-top, 0px)) 10px calc(134px + env(safe-area-inset-bottom, 0px));
      }}
      .glass-top {{
        top: calc(2px + env(safe-area-inset-top, 0px));
        padding: 10px 10px;
      }}
      .bottom-nav {{
        padding: 8px 8px calc(12px + env(safe-area-inset-bottom, 0px));
      }}
      .now-playing-card {{
        bottom: calc(94px + env(safe-area-inset-bottom, 0px));
      }}
      .recent-row {{
        font-size: 0.84rem;
        padding: 9px 10px;
      }}
      .queue-title {{
        white-space: normal;
        overflow: visible;
        text-overflow: clip;
        display: -webkit-box;
        -webkit-line-clamp: 2;
        -webkit-box-orient: vertical;
        line-height: 1.28;
      }}
      .home-tiles {{
        grid-template-columns: 1fr;
        gap: 8px;
      }}
      .home-tile {{
        padding: 14px 12px;
      }}
      .content-views {{
        padding-bottom: calc(10px + env(safe-area-inset-bottom, 0px));
      }}
    }}
  </style>
</head>
<body>
  <!-- ui-build: premium-cyan-v7 -->
  <div class="ui-version-strip" role="status">
    <strong>ממשק v5</strong>
    <span class="ui-version-strip-mid">דף הבית · הספרייה שלי · אהובים · פלייליסטים בסרגל · אם לא רואים — לחצי ״בדיקת שרת״</span>
    <a class="ui-version-strip-link" href="__player_check" target="_blank" rel="noopener">בדיקת שרת</a>
    <button type="button" class="ui-version-strip-btn btn-leading-icon" id="stripReloadBtn" title="טעינה מחדש מהשרת">
      <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M21 12a9 9 0 1 1-2.64-6.36"/><path d="M21 3v6h-6"/></svg>
      <span>רענון</span>
    </button>
  </div>
  <div class="code-stale-banner" id="codeStaleBanner" style="display: {stale_display};" role="alert">
    עדכנת את <code>unblocked_player.py</code> בדיסק, אבל השרת Python עדיין רץ מהזיכרון הישן.
    עצורי את השרת (Ctrl+C בחלון הטרמינל) והפעילי שוב <strong>מתוך אותה תיקייה</strong> (למשל
    <code dir="ltr">restart-player-lan.ps1</code> או <code>python unblocked_player.py</code>).
    אם כבר הפעלת מחדש ועדיין ממשק ישן: דף הבית, ״הגדרות״, ״ניקוי מטמון ו־Service Worker״.
  </div>
  <div id="pwaInstallModal" class="pwa-install-modal" role="dialog" aria-modal="true" aria-labelledby="pwaModalTitle" hidden>
    <div class="pwa-install-modal-backdrop" data-pwa-dismiss="1" aria-hidden="true"></div>
    <div class="pwa-install-card" role="document">
      <h2 id="pwaModalTitle">להתקין את הנגן על הטלפון?</h2>
      <p id="pwaModalBody">אפשר להוסיף קיצור דרך על מסך הבית ולפתוח את המערכת כמו אפליקציה.</p>
      <div class="pwa-install-actions">
        <button type="button" class="pwa-install-secondary" id="pwaModalDecline" data-pwa-dismiss="1">לא עכשיו</button>
        <button type="button" class="pwa-install-primary" id="pwaModalConfirm">התקן / הוסף</button>
      </div>
    </div>
  </div>
  <div class="app-root">
    <aside class="spot-sidebar" id="appSidebar" aria-label="ניווט ראשי">
      <div class="brand">
        <div class="brand-icon">♪</div>
        <div>
          <div class="brand-name">מוזיקה</div>
          <div class="brand-tag">YouTube · מקומי</div>
        </div>
      </div>
      <nav class="am-nav" aria-label="אזורים">
        <button type="button" class="am-nav-item is-active" data-nav-target="home">דף הבית</button>
        <button type="button" class="am-nav-item" data-nav-target="library">הספרייה שלי</button>
        <button type="button" class="am-nav-item" data-nav-target="liked">שירים שאהבתי</button>
        <button type="button" class="am-nav-item" data-nav-target="playlists">פלייליסטים</button>
        <button type="button" class="am-nav-item" data-nav-target="offline">שמורים אצל השרת</button>
      </nav>
      <div class="sidebar-head">קיצור לפלייליסט</div>
      <div id="playlistSidebarList" class="playlist-nav-list"></div>
    </aside>

    <main class="main-column">
      <div class="glass-top">
        <div class="glass-top-row">
          <div class="glass-top-text">
            <p class="sub-inline build-line">גרסת שרת: <strong>{SERVER_LOADED_MTIME}</strong>
              · <a href="?v={SERVER_LOADED_MTIME}">קישור לרענון</a>
              · אימות: כותרת <code>X-Unblocked-Player</code> בכלי רשת.</p>
            <div id="glassTopSearchHost" class="glass-top-search-host">
              <div id="globalSearchWrap" class="global-search-wrap">
                <input id="search" type="search" placeholder="חיפוש מהיר לפי שם שיר או אמן..." autocomplete="off" />
                <div id="globalSearchResults" class="global-search-results" role="listbox" aria-label="תוצאות חיפוש מיוטיוב"></div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div class="content-views" id="contentViews">
        <section id="viewHome" class="view-panel is-active" aria-label="דף הבית">
          <div class="home-settings-bar">
            <button type="button" class="secondary btn-leading-icon" id="homeSettingsBtn" aria-haspopup="dialog" aria-controls="homeSettingsOverlay" title="רשת, מרחוק ורענון מלא">
              <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><circle cx="12" cy="12" r="3"/><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/></svg>
              <span>הגדרות</span>
            </button>
          </div>
          <div class="home-tiles" id="homeTiles"></div>
          <div class="home-recent">
            <h3>נוגנו לאחרונה</h3>
            <div id="recentList" class="recent-list"></div>
          </div>
        </section>

        <section id="viewOffline" class="view-panel" aria-label="שמורים אצל השרת">
          <h2 class="view-title" style="margin-bottom:8px;">שמורים אצל השרת</h2>
          <p class="sub-help">קבצים בתיקייה <code>offline_library</code> ליד קובץ השרת. אחרי השמירה אפשר לנגן כאן בלי אינטרנט — כל עוד תהליך השרת רץ (גם מהטלפון באותה רשת מקומית).</p>
          <div id="offlinePanelList" class="liked-panel-list"></div>
        </section>

        <section id="viewLibrary" class="view-panel" aria-label="הספרייה שלי">
          <div class="yt-search-page" id="ytSearchPage">
            <div class="yt-search-page-head">
              <div class="yt-search-page-head-text">
                <p class="yt-search-page-title" id="ytSearchPageTitle">תוצאות חיפוש מיוטיוב</p>
                <p class="yt-search-page-sub" id="ytSearchPageSub">כתבי בשורת החיפוש ליד הכותרת</p>
              </div>
              <div id="ytSearchToolbarSearchHost" class="yt-search-toolbar-search-host" aria-label="חיפוש יוטיוב"></div>
            </div>
            <div class="yt-search-page-list" id="ytSearchPageList"></div>
          </div>
          <p class="sub-help">טיפ: לחצי על שורה כדי לנגן, ועל כפתור המועדפים כדי לשמור שיר.</p>
          <div class="library-scroll">
            <div class="queue-wrap">
              <div class="quick-results" id="quickResults"></div>
            </div>
          </div>
        </section>

        <section id="viewLiked" class="view-panel" aria-label="שירים שאהבתי">
          <h2 class="view-title" style="margin-bottom:16px;">שירים שאהבתי</h2>
          <div id="likedPanelList" class="liked-panel-list"></div>
        </section>

        <section id="viewPlaylists" class="view-panel" aria-label="פלייליסטים">
          <div class="pl-head-row">
            <h2 class="view-title" style="margin:0;">פלייליסטים</h2>
            <button type="button" class="secondary" id="newPlaylistBtn">+ פלייליסט חדש</button>
          </div>
          <div id="playlistsGrid" class="playlists-grid"></div>
          <div class="yt-rec-wrap">
            <h3 class="yt-rec-head">המלצות מיוטיוב</h3>
            <div id="ytRecommendedPlaylists" class="yt-rec-grid"></div>
          </div>
        </section>

        <section id="viewPlaylistDetail" class="view-panel" aria-label="פלייליסט">
          <div class="pl-detail-actions">
            <button type="button" class="secondary btn-leading-icon" id="backFromPlaylist">
              <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="m15 6-6 6 6 6"/><path d="M9 12h10"/></svg>
              <span>חזרה</span>
            </button>
            <button type="button" class="secondary" id="playPlaylistBtn">נגן הכל</button>
          </div>
          <h2 class="view-title" id="playlistDetailTitle" style="margin-bottom:14px;">פלייליסט</h2>
          <div id="playlistDetailTracks" class="playlist-detail-list"></div>
        </section>
      </div>

      <section class="now-playing-card is-collapsed" id="nowPlaying" aria-label="ניגון כעת">
        <div class="np-layout">
          <div class="np-art-wrap">
            <img id="npArtwork" class="np-artwork" alt="" width="220" height="220" />
          </div>
          <div class="np-text-col">
            <div class="np-summary-row" id="npSummaryRow" role="button" tabindex="0" aria-expanded="false">
              <button type="button" id="npPageCloseBtn" class="np-page-close-btn" aria-label="סגירת עמוד ניגון">
                <svg viewBox="0 0 24 24" focusable="false"><path d="m6 9 6 6 6-6"/></svg>
              </button>
              <div class="np-summary-text">
                <div class="np-label">ניגון כעת</div>
                <div class="meta np-title" id="meta">טוען...</div>
              </div>
              <button type="button" id="likeBtn" class="like-btn" title="הוסף למועדפים" aria-pressed="false">
                <svg class="ico-heart-stroke" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M12 20.5s-7-4.6-7-10a4 4 0 0 1 7-2.5A4 4 0 0 1 19 10.5c0 5.4-7 10-7 10z"/></svg>
                <svg class="ico-heart-fill" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M12 20.5s-7-4.6-7-10a4 4 0 0 1 7-2.5A4 4 0 0 1 19 10.5c0 5.4-7 10-7 10z"/></svg>
              </button>
              <button type="button" id="npQuickPlayBtn" class="np-quick-play-btn" title="ניגון מהיר" aria-label="ניגון מהיר" aria-pressed="false">
                <svg class="ico-quick-play" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="m8 5 11 7-11 7z"/></svg>
                <svg class="ico-quick-pause" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
                  <rect x="7" y="5" width="4.2" height="14" rx="1.2"></rect>
                  <rect x="12.8" y="5" width="4.2" height="14" rx="1.2"></rect>
                </svg>
              </button>
              <span class="np-expand-indicator" aria-hidden="true">
                <svg viewBox="0 0 24 24" focusable="false"><path d="m6 9 6 6 6-6"/></svg>
              </span>
            </div>
            <div class="np-expanded-content">
            <div class="controls transport-bar">
              <button type="button" class="secondary icon-btn" id="shuffle" title="ערבוב">
                <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M16 4h4v4"/><path d="M20 4 8 16"/><path d="M4 8h2c2 0 3 1 4 3l1 2c1 2 2 3 4 3h5"/><path d="M16 20h4v-4"/><path d="M20 20 8 8"/></svg>
              </button>
              <button type="button" class="secondary icon-btn" id="repeatBtn" title="חזרה">
                <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M17 1l4 4-4 4"/><path d="M3 11V9a4 4 0 0 1 4-4h14"/><path d="M7 23l-4-4 4-4"/><path d="M21 13v2a4 4 0 0 1-4 4H3"/></svg>
                <span class="repeat-one-mark">1</span>
              </button>
              <button type="button" class="secondary icon-btn" id="prev" title="קודם">
                <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M6 5v14"/><path d="m18 6-8 6 8 6z"/></svg>
              </button>
              <button type="button" class="play-fab is-paused" id="play" title="נגן">
                <svg class="ico-play" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="m8 5 11 7-11 7z"/></svg>
                <svg class="ico-pause" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M8 5h3v14H8zM13 5h3v14h-3z"/></svg>
              </button>
              <button type="button" class="secondary icon-btn" id="next" title="הבא">
                <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M18 5v14"/><path d="m6 6 8 6-8 6z"/></svg>
              </button>
              <button type="button" class="secondary icon-btn" id="downloadTrackBtn" title="הורדת השיר למכשיר">
                <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M12 3v12"/><path d="m8 11 4 4 4-4"/><path d="M5 21h14"/></svg>
              </button>
              <button type="button" class="secondary icon-btn" id="saveOfflineBtn" title="שמירה אצל השרת (ניגון בלי רשת)">
                <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M4 20h16v-8H4z"/><path d="M8 12V4h8v8"/><path d="M12 8v4"/></svg>
              </button>
              <button type="button" class="secondary icon-btn" id="fullscreenBtn" title="מסך מלא" aria-pressed="false">
                <svg class="ico-enter-fullscreen" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M8 3H3v5"/><path d="M3 3l6 6"/><path d="M16 3h5v5"/><path d="m21 3-6 6"/><path d="M8 21H3v-5"/><path d="m3 21 6-6"/><path d="M16 21h5v-5"/><path d="m21 21-6-6"/></svg>
                <svg class="ico-exit-fullscreen" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M9 9H4V4"/><path d="m4 9 6-6"/><path d="M15 9h5V4"/><path d="m20 9-6-6"/><path d="M9 15H4v5"/><path d="m4 15 6 6"/><path d="M15 15h5v5"/><path d="m20 15-6 6"/></svg>
              </button>
            </div>
            <div class="scrobble-row">
              <span class="time-tag" id="timeCurrent">0:00</span>
              <input type="range" id="progressBar" class="range-progress" min="0" max="1000" value="0" step="1" aria-label="התקדמות" />
              <span class="time-tag" id="timeTotal">0:00</span>
            </div>
            <div class="vol-row">
              <span class="vol-ico" aria-hidden="true">
                <svg viewBox="0 0 24 24" focusable="false"><path d="M4 15h4l5 4V5L8 9H4z"/><path d="M16 9a5 5 0 0 1 0 6"/><path d="M18.5 6.5a8.5 8.5 0 0 1 0 11"/></svg>
              </span>
              <input type="range" id="volumeBar" class="range-vol" min="0" max="100" value="100" step="1" aria-label="עוצמה" />
            </div>
            <div class="eq-toggle-row">
              <button type="button" id="eqToggleBtn" class="eq-toggle-btn" aria-expanded="false">EQ מקצועי</button>
            </div>
            <div id="eqDrawer" class="eq-drawer" aria-label="אקולייזר מקצועי">
              <div class="eq-top-actions">
                <button type="button" id="eqEnableBtn" class="eq-switch-btn is-on">EQ פעיל</button>
                <button type="button" id="eqClipBtn" class="eq-switch-btn is-on">Prevent Clipping</button>
                <button type="button" id="eqCompEnableBtn" class="eq-switch-btn">Compressor כבוי</button>
              </div>
              <div class="eq-preamp-row">
                <span>Preamp</span>
                <input type="range" id="eqPreamp" min="-12" max="12" step="0.5" value="0" />
                <strong id="eqPreampValue">0dB</strong>
              </div>
              <div class="eq-compressor-row">
                <span class="eq-mini-label">Threshold</span>
                <input type="range" id="eqCompThreshold" min="-48" max="0" step="1" value="-18" />
                <span id="eqCompThresholdValue" class="eq-mini-value">-18dB</span>
              </div>
              <div class="eq-compressor-row">
                <span class="eq-mini-label">Ratio</span>
                <input type="range" id="eqCompRatio" min="1" max="12" step="0.5" value="3" />
                <span id="eqCompRatioValue" class="eq-mini-value">3:1</span>
              </div>
              <div id="eqBands" class="eq-bands"></div>
              <div class="eq-slot-row">
                <button type="button" class="secondary" id="eqSaveSet1">שמור Set 1</button>
                <button type="button" class="secondary" id="eqSaveSet2">שמור Set 2</button>
                <button type="button" class="secondary" id="eqLoadSet1">טען Set 1</button>
                <button type="button" class="secondary" id="eqLoadSet2">טען Set 2</button>
              </div>
              <div class="eq-actions">
                <button type="button" class="secondary" id="eqResetBtn">איפוס</button>
                <button type="button" class="secondary" id="eqPresetVoiceBtn">Preset: Vocal</button>
                <button type="button" class="secondary" id="eqPresetBassBtn">Preset: Bass</button>
              </div>
            </div>
            </div>
          </div>
        </div>
      </section>

      <section class="up-next-card" id="upNextSection" aria-labelledby="upNextHeading">
        <h2 class="section-heading" id="upNextHeading">הבא בתור</h2>
        <div id="upNextList" class="up-next-list"></div>
      </section>

      <div class="player-card-inner">
        <div class="car-hint" role="status">
          <span class="car-hint-text">מצב רכב: כפתורים גדולים וממשק מצומצם לבטיחות בנהיגה.</span>
          <button type="button" class="car-exit-btn" id="exitCarModeBtn">חזרה למצב רגיל</button>
        </div>
        <div id="videoSection" class="video-block">
          <div class="video-shell" id="mediaShell">
            <video
              class="media-layer"
              id="video"
              autoplay
              playsinline
              webkit-playsinline="true"
              x5-playsinline="true"
              controlslist="nofullscreen nodownload noplaybackrate"
              disablepictureinpicture
            ></video>
            <iframe
              class="media-layer"
              id="ytEmbed"
              title="YouTube"
              allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
              allowfullscreen
              referrerpolicy="strict-origin-when-cross-origin"
            ></iframe>
          </div>
        </div>

        <div class="toolbar">
          <select id="mode" aria-label="מצב ממשק">
            <option value="normal">מצב רגיל</option>
            <option value="car">מצב רכב</option>
          </select>
          <select id="quality" aria-label="איכות סטרים">
            <option value="auto">איכות אוטומטית</option>
            <option value="high">איכות גבוהה</option>
            <option value="normal">איכות רגילה</option>
          </select>
          <button type="button" class="secondary" id="favoritesOnlyBtn" title="הצג רק שירים מועדפים">מועדפים</button>
          <button type="button" class="secondary" id="showAllBtn">הצג הכל</button>
          <button type="button" class="secondary" id="repairNamesBtn">נקה שמות</button>
        </div>

        <div id="discover">
          <p class="section-label">גילוי — חיפוש ביוטיוב</p>
          <div class="yt-search">
            <input id="ytQuery" type="text" placeholder="חפש שירים ביוטיוב..." autocomplete="off" />
            <button type="button" class="secondary" id="ytSearchBtn">חפש</button>
          </div>
          <div class="yt-results" id="ytResults" style="display:none;"></div>
        </div>
        <div class="status" id="status"></div>
      </div>
      <nav class="bottom-nav" aria-label="ניווט תחתון">
        <button type="button" class="bottom-nav-btn is-active" data-nav-target="home">
          <span class="bottom-nav-ico" aria-hidden="true">
            <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M3 10.5 12 3l9 7.5"/><path d="M5 9.5V21h14V9.5"/><path d="M10 21v-6h4v6"/></svg>
          </span>
          <span class="bottom-nav-label">בית</span>
        </button>
        <button type="button" class="bottom-nav-btn" data-nav-target="library">
          <span class="bottom-nav-ico" aria-hidden="true">
            <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>
          </span>
          <span class="bottom-nav-label">חיפוש</span>
        </button>
        <button type="button" class="bottom-nav-btn" data-nav-target="liked">
          <span class="bottom-nav-ico" aria-hidden="true">
            <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M12 20.5s-7-4.6-7-10a4 4 0 0 1 7-2.5A4 4 0 0 1 19 10.5c0 5.4-7 10-7 10z"/></svg>
          </span>
          <span class="bottom-nav-label">אהובים</span>
        </button>
        <button type="button" class="bottom-nav-btn" data-nav-target="playlists">
          <span class="bottom-nav-ico" aria-hidden="true">
            <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M9 18a2.5 2.5 0 1 1-2.5-2.5c.9 0 1.7.3 2.5.8V6l10-2v9"/><path d="M19 15a2.5 2.5 0 1 1-2.5-2.5c.9 0 1.7.3 2.5.8"/></svg>
          </span>
          <span class="bottom-nav-label">פלייליסטים</span>
        </button>
      </nav>
      <button type="button" id="navBackFab" class="nav-back-fab" aria-label="חזרה לדף קודם" title="חזרה" hidden>
        <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="m15 6-6 6 6 6"/><path d="M9 12h10"/></svg>
      </button>
    </main>
  </div>

  <div id="plOverlay" class="modal-overlay" role="dialog" aria-modal="true" aria-labelledby="plOverlayTitle">
    <div class="modal-card">
      <h3 id="plOverlayTitle">הוספה לפלייליסט</h3>
      <select id="plOverlaySelect" aria-label="בחירת פלייליסט"></select>
      <div class="modal-actions">
        <button type="button" class="secondary" id="plOverlayClose">סגור</button>
        <button type="button" class="secondary" id="plOverlayNew">פלייליסט חדש</button>
        <button type="button" class="secondary" id="plOverlayConfirm" style="background:var(--accent);border-color:var(--accent);color:#fff;font-weight:700;">הוסף</button>
      </div>
    </div>
  </div>

  <div id="searchActionOverlay" class="modal-overlay" role="dialog" aria-modal="true" aria-labelledby="searchActionTitle">
    <div class="modal-card" style="max-width: 420px;">
      <h3 id="searchActionTitle">מה תרצי לעשות עם השיר?</h3>
      <div class="modal-actions" style="justify-content:flex-start; flex-wrap:wrap;">
        <button type="button" class="secondary" id="searchActPlayNow">נגן עכשיו</button>
        <button type="button" class="secondary" id="searchActQueue">הוסף לרשימת השמעה</button>
        <button type="button" class="secondary" id="searchActFav">הוסף למועדפים</button>
        <button type="button" class="secondary" id="searchActPlaylist">הוסף לפלייליסט</button>
        <button type="button" class="secondary" id="searchActClose">סגור</button>
      </div>
    </div>
  </div>

  <div id="homeSettingsOverlay" class="modal-overlay" role="dialog" aria-modal="true" aria-labelledby="homeSettingsTitle">
    <div class="modal-card" style="max-width: 420px;">
      <h3 id="homeSettingsTitle">הגדרות</h3>
      <p class="sub-inline" style="margin:0 0 8px;">רשת, מרחוק ורענון מלא של הממשק</p>
      <div class="home-settings-actions">
        <button type="button" class="hard-refresh-btn btn-leading-icon" id="settingsHardRefreshBtn" title="טעינה מחדש של הממשק מהשרת">
          <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M21 12a9 9 0 1 1-2.64-6.36"/><path d="M21 3v6h-6"/></svg>
          <span>רענון מלא</span>
        </button>
        <button type="button" class="lan-phone-btn" id="settingsLanQrBtn" style="display: {lan_btn_display};" title="קוד QR לפתיחה מהטלפון באותה רשת Wi‑Fi">מובייל · סריקת QR</button>
        <button type="button" class="ts-remote-btn" id="settingsTailscaleBtn" title="הדרכה לחיבור מהטלפון מחוץ לבית דרך Tailscale">מרחוק · Tailscale</button>
        <button type="button" class="secondary" id="settingsClearCacheSwBtn" title="מסיר Service Worker ומטמון PWA, מומלץ אם המסך לא מתעדכן אחרי ריסטארט לשרת">ניקוי מטמון ו־Service Worker</button>
      </div>
      <div class="modal-actions">
        <button type="button" class="secondary" id="homeSettingsCloseBtn">סגור</button>
      </div>
    </div>
  </div>

  <button type="button" id="lanPhoneBtn" hidden aria-hidden="true" tabindex="-1"></button>
  <button type="button" id="tailscaleHelpBtn" hidden aria-hidden="true" tabindex="-1"></button>

  <div id="lanPhoneOverlay" class="modal-overlay" role="dialog" aria-modal="true" aria-labelledby="lanPhoneTitle">
    <div class="modal-card" style="max-width: 420px;">
      <h3 id="lanPhoneTitle" style="margin: 0 0 8px;">חיבור מהטלפון</h3>
      <p class="lan-qr-hint">וודאו שטלפון ומחשב על <strong>אותה רשת Wi‑Fi</strong>. בטלפון: פתחו את המצלמה או אפליקציית QR וסרקו. או לחצו ״העתק״ ושלחו לעצמכם.</p>
      <div id="lanQrMount" class="lan-qr-box" aria-label="קוד QR"></div>
      <div class="lan-url-row">
        <code id="lanUrlText" dir="ltr"></code>
        <button type="button" class="lan-url-copy-btn" id="lanUrlCopyBtn">העתק</button>
      </div>
      <div class="modal-actions" style="margin-top: 14px; justify-content: flex-start;">
        <button type="button" class="secondary" id="lanPhoneClose">סגור</button>
      </div>
    </div>
  </div>

  <div id="tailscaleOverlay" class="modal-overlay" role="dialog" aria-modal="true" aria-labelledby="tailscaleTitle">
    <div class="modal-card" style="max-width: 480px;">
      <h3 id="tailscaleTitle" style="margin: 0 0 10px;">מרחוק מהבית (Tailscale)</h3>
      <p class="lan-qr-hint" style="margin-bottom: 8px;">
        כדי לפתוח את <strong>אותו שרת נגן</strong> מהטלפון <strong>מחוץ ל־Wi‑Fi הביתי</strong> (בלי לפתוח פורט בראוטר), משתמשים ב־<strong>Tailscale</strong>:
        רשת פרטית מוצפנת בין המחשב לטלפון. הנגן נשאר על המחשב שלך — בדרך־כלל <strong>לא דרך נגן יוטיוב הרגיל</strong> בדפדפן.
      </p>
      <ol class="ts-help-steps">
        <li>התקינו <strong>Tailscale</strong> במחשב ובטלפון, והתחברו <strong>לאותו חשבון</strong>.</li>
        <li>במחשב השאירו רץ את השרת: <code>start-server-lan.bat</code> (חלון שחור פתוח).</li>
        <li>באפליקציית Tailscale בטלפון — מצאו את <strong>שם המחשב</strong> והעתיקו את כתובת ה־IPv4 (לרוב מתחילה ב־<code dir="ltr">100.</code>).</li>
        <li>בטלפון, בדפדפן, פתחו: <code dir="ltr">http://&lt;כתובת-Tailscale-של-המחשב&gt;:{PORT}/</code></li>
      </ol>
      <p class="lan-qr-hint" style="margin-top: 10px;">פורט השרת <strong>כאן</strong>: <code dir="ltr">{PORT}</code> · דוגמה להדבקה אחרי שיש לכם IP: <code id="tsUrlTemplate" dir="ltr">http://100.x.x.x:{PORT}/</code>
        <button type="button" class="lan-url-copy-btn" id="tsTemplateCopyBtn" style="margin-inline-start: 8px;">העתק דוגמה</button>
      </p>
      <p style="margin: 10px 0 4px; font-weight: 700; font-size: 0.88rem;">הורדות</p>
      <ul class="ts-help-links">
        <li><a href="https://tailscale.com/download/windows" target="_blank" rel="noopener">Windows</a></li>
        <li><a href="https://tailscale.com/download/mac" target="_blank" rel="noopener">macOS</a></li>
        <li><a href="https://play.google.com/store/apps/details?id=com.tailscale.ipn" target="_blank" rel="noopener">Android (Play)</a></li>
        <li><a href="https://apps.apple.com/app/tailscale/id1470499037" target="_blank" rel="noopener">iPhone / iPad</a></li>
        <li><a href="https://tailscale.com/download" target="_blank" rel="noopener">כל הפלטפורמות</a></li>
      </ul>
      <p class="ts-help-note">שימו לב: Tailscale הוא שירות חיצוני (חברה נפרדת). אם אינכם רוצים — המשיכו עם QR / כתובת <code dir="ltr">192.168…</code> באותה Wi‑Fi בלבד.</p>
      <div class="modal-actions" style="margin-top: 14px; justify-content: flex-start;">
        <button type="button" class="secondary" id="tailscaleCloseBtn">סגור</button>
      </div>
    </div>
  </div>

  <script>
    const API_BASE = (() => {{
      try {{
        const p = window.location.pathname || '';
        const i = p.lastIndexOf('/');
        return i > 0 ? p.slice(0, i) : '';
      }} catch (e) {{
        return '';
      }}
    }})();
    function apiUrl(path) {{
      const tail = String(path || '');
      const p = tail.startsWith('/') ? tail : '/' + tail;
      return (API_BASE || '') + p;
    }}
    const UNBLOCKED_LAN_URL = {lan_url_json};
    const baseItems = {items_json};
    const video = document.getElementById('video');
    if (video) {{
      // Keep playback inline on mobile; avoid native fullscreen takeover.
      video.controls = false;
      video.setAttribute('playsinline', '');
      video.setAttribute('webkit-playsinline', 'true');
    }}
    const mediaShell = document.getElementById('mediaShell');
    const ytEmbed = document.getElementById('ytEmbed');
    const meta = document.getElementById('meta');
    const statusEl = document.getElementById('status');
    const qualityEl = document.getElementById('quality');
    const modeEl = document.getElementById('mode');
    const exitCarModeBtn = document.getElementById('exitCarModeBtn');
    const searchEl = document.getElementById('search');
    const globalSearchResultsEl = document.getElementById('globalSearchResults');
    const ytSearchPageTitleEl = document.getElementById('ytSearchPageTitle');
    const ytSearchPageSubEl = document.getElementById('ytSearchPageSub');
    const ytSearchPageListEl = document.getElementById('ytSearchPageList');
    const showAllBtn = document.getElementById('showAllBtn');
    const repairNamesBtn = document.getElementById('repairNamesBtn');
    const quickResultsEl = document.getElementById('quickResults');
    const shuffleBtn = document.getElementById('shuffle');
    const fullscreenBtn = document.getElementById('fullscreenBtn');
    const ytQueryEl = document.getElementById('ytQuery');
    const ytSearchBtn = document.getElementById('ytSearchBtn');
    const ytResultsEl = document.getElementById('ytResults');
    const STORAGE_KEY = 'unblockedPlayerItems';
    const MODE_KEY = 'playerUiMode';
    const SHUFFLE_KEY = 'playerShuffleEnabled';
    const LAST_SONG_KEY = 'unblockedPlayerLastIndex';
    const REPEAT_KEY = 'playerRepeatMode';

    const repeatBtn = document.getElementById('repeatBtn');
    const progressBar = document.getElementById('progressBar');
    const volumeBar = document.getElementById('volumeBar');
    const timeCurrentEl = document.getElementById('timeCurrent');
    const timeTotalEl = document.getElementById('timeTotal');
    const npArtwork = document.getElementById('npArtwork');
    const playBtn = document.getElementById('play');
    const likeBtn = document.getElementById('likeBtn');
    const npQuickPlayBtn = document.getElementById('npQuickPlayBtn');
    const nowPlayingCard = document.getElementById('nowPlaying');
    const npSummaryRow = document.getElementById('npSummaryRow');
    const npPageCloseBtn = document.getElementById('npPageCloseBtn');
    const favoritesOnlyBtn = document.getElementById('favoritesOnlyBtn');
    const eqToggleBtn = document.getElementById('eqToggleBtn');
    const eqDrawer = document.getElementById('eqDrawer');
    const eqBandsEl = document.getElementById('eqBands');
    const eqPreampEl = document.getElementById('eqPreamp');
    const eqPreampValueEl = document.getElementById('eqPreampValue');
    const eqEnableBtn = document.getElementById('eqEnableBtn');
    const eqClipBtn = document.getElementById('eqClipBtn');
    const eqCompEnableBtn = document.getElementById('eqCompEnableBtn');
    const eqCompThresholdEl = document.getElementById('eqCompThreshold');
    const eqCompRatioEl = document.getElementById('eqCompRatio');
    const eqCompThresholdValueEl = document.getElementById('eqCompThresholdValue');
    const eqCompRatioValueEl = document.getElementById('eqCompRatioValue');
    const eqSaveSet1 = document.getElementById('eqSaveSet1');
    const eqSaveSet2 = document.getElementById('eqSaveSet2');
    const eqLoadSet1 = document.getElementById('eqLoadSet1');
    const eqLoadSet2 = document.getElementById('eqLoadSet2');
    const eqResetBtn = document.getElementById('eqResetBtn');
    const eqPresetVoiceBtn = document.getElementById('eqPresetVoiceBtn');
    const eqPresetBassBtn = document.getElementById('eqPresetBassBtn');
    const upNextListEl = document.getElementById('upNextList');
    const recentListEl = document.getElementById('recentList');
    const homeTilesEl = document.getElementById('homeTiles');
    const likedPanelListEl = document.getElementById('likedPanelList');
    const playlistsGridEl = document.getElementById('playlistsGrid');
    const ytRecommendedPlaylistsEl = document.getElementById('ytRecommendedPlaylists');
    const playlistSidebarListEl = document.getElementById('playlistSidebarList');
    const glassTopSearchHost = document.getElementById('glassTopSearchHost');
    const ytSearchToolbarSearchHost = document.getElementById('ytSearchToolbarSearchHost');
    const globalSearchWrapEl = document.getElementById('globalSearchWrap');
    const viewHome = document.getElementById('viewHome');
    const viewOffline = document.getElementById('viewOffline');
    const offlinePanelListEl = document.getElementById('offlinePanelList');
    const saveOfflineBtn = document.getElementById('saveOfflineBtn');
    const viewLibrary = document.getElementById('viewLibrary');
    const viewLiked = document.getElementById('viewLiked');
    const viewPlaylists = document.getElementById('viewPlaylists');
    const viewPlaylistDetail = document.getElementById('viewPlaylistDetail');
    const newPlaylistBtn = document.getElementById('newPlaylistBtn');
    const backFromPlaylist = document.getElementById('backFromPlaylist');
    const navBackFab = document.getElementById('navBackFab');
    const playPlaylistBtn = document.getElementById('playPlaylistBtn');
    const playlistDetailTitle = document.getElementById('playlistDetailTitle');
    const playlistDetailTracks = document.getElementById('playlistDetailTracks');
    const plOverlay = document.getElementById('plOverlay');
    const plOverlaySelect = document.getElementById('plOverlaySelect');
    const plOverlayClose = document.getElementById('plOverlayClose');
    const plOverlayNew = document.getElementById('plOverlayNew');
    const plOverlayConfirm = document.getElementById('plOverlayConfirm');
    const searchActionOverlay = document.getElementById('searchActionOverlay');
    const searchActPlayNow = document.getElementById('searchActPlayNow');
    const searchActQueue = document.getElementById('searchActQueue');
    const searchActFav = document.getElementById('searchActFav');
    const searchActPlaylist = document.getElementById('searchActPlaylist');
    const searchActClose = document.getElementById('searchActClose');

    const LIKES_KEY = 'playerLikeKeys';
    const RECENT_KEY = 'playerRecentKeys';
    const PLAYLISTS_KEY = 'playerPlaylistsV2';
    const EQ_STATE_KEY = 'playerEqStateV1';
    const EQ_SETS_KEY = 'playerEqSetsV1';

    let repeatMode = localStorage.getItem(REPEAT_KEY) || 'all';
    if (!['off', 'all', 'one'].includes(repeatMode)) repeatMode = 'all';
    let isSeekingProgress = false;
    let favoritesOnly = localStorage.getItem('playerFavFilter') === '1';

    let items = [];
    let quality = localStorage.getItem('playerQuality') || 'auto';
    if (!['auto', 'high', 'normal'].includes(quality)) {{
      quality = 'auto';
    }}
    qualityEl.value = quality;
    let uiMode = localStorage.getItem(MODE_KEY) || 'normal';
    if (!['normal', 'car'].includes(uiMode)) {{
      uiMode = 'normal';
    }}
    modeEl.value = uiMode;
    let filteredIndices = items.map((_, i) => i);
    let pos = 0;
    let shuffleEnabled = localStorage.getItem(SHUFFLE_KEY) === 'true';
    let shuffleQueue = [];
    let playbackHistory = [];
    let loadFailStreak = 0;
    let autoDjBusy = false;
    let audioCtx = null;
    let mediaSourceNode = null;
    let preGainNode = null;
    let compNode = null;
    let limiterNode = null;
    let eqFilters = [];
    let playlists = [];
    const youtubeRecommendedPlaylists = [
      {{ title: 'Top Hits 2026', sub: 'להיטים בינלאומיים חמים', url: 'https://www.youtube.com/playlist?list=PL4fGSI1pDJn5rWitrRWFKdm-ulaFiIyoK' }},
      {{ title: 'Chill & Relax', sub: 'מוזיקת צ׳יל ללימוד/עבודה', url: 'https://www.youtube.com/playlist?list=PLw-VjHDlEOgs6588-7UFiCVJduFhQ4YV0' }},
      {{ title: 'Workout Motivation', sub: 'קצב גבוה לאימון', url: 'https://www.youtube.com/playlist?list=PLGBuKfnErZlA6h5F4cA0Of7v8Rj3G8Q8P' }},
      {{ title: 'Hebrew Pop', sub: 'פופ ישראלי עדכני', url: 'https://www.youtube.com/results?search_query=hebrew+pop+playlist' }},
      {{ title: 'Throwback 2000s', sub: 'נוסטלגיה מהאלפיים', url: 'https://www.youtube.com/results?search_query=2000s+hits+playlist' }},
      {{ title: 'Lo-fi Beats', sub: 'ריכוז ולילה רגוע', url: 'https://www.youtube.com/results?search_query=lofi+beats+playlist' }},
    ];
    let activePlaylistDetailId = null;
    let plOverlaySongIndex = null;
    let currentNavView = 'home';
    let offlinePlayingVid = null;
    let globalSearchTimer = null;
    let globalSearchReqSeq = 0;
    let searchPageResults = [];
    let searchActionTargetId = '';
    const STREAM_TIMEOUT_MS = 60000;

    function getYoutubeIdFromItem(item) {{
      if (!item) return '';
      if (item.id) return String(item.id);
      const u = String(item.url || '');
      if (!u) return '';
      try {{
        const p = new URL(u);
        const h = p.hostname.replace(/^www[.]/, '');
        if (h === 'youtu.be') {{
          return (p.pathname || '').split('/').filter(Boolean)[0] || '';
        }}
        const v = p.searchParams.get('v');
        if (v) return v;
      }} catch (err) {{}}
      const m = u.match(/(?:[?&]v=|youtu\\.be\\/)([A-Za-z0-9_-]{{6,}})/);
      return m ? m[1] : '';
    }}

    function playEmbedFallback(id, autoPlay) {{
      if (!id) return false;
      try {{ video.pause(); }} catch (e) {{}}
      video.removeAttribute('src');
      try {{ video.load(); }} catch (e) {{}}
      const ap = autoPlay ? '1' : '0';
      const origin = encodeURIComponent(String(window.location.origin || ''));
      ytEmbed.src = 'https://www.youtube.com/embed/' + encodeURIComponent(id) + '?autoplay=' + ap + '&rel=0&modestbranding=1&playsinline=1&origin=' + origin;
      mediaShell.classList.add('use-embed');
      return true;
    }}

    async function canEmbedYoutubeVideo(id) {{
      if (!id) return false;
      const ctrl = new AbortController();
      const t = setTimeout(() => ctrl.abort(), 5500);
      try {{
        const u = 'https://www.youtube.com/oembed?url=' + encodeURIComponent('https://www.youtube.com/watch?v=' + id) + '&format=json';
        const r = await fetch(u, {{ cache: 'no-store', signal: ctrl.signal }});
        return r.ok;
      }} catch (e) {{
        // If browser/network blocks this probe, don't hard-block playback flow.
        return true;
      }} finally {{
        clearTimeout(t);
      }}
    }}

    async function tryPlayEmbedFallback(id, autoPlay, statusText = '') {{
      if (!id) return false;
      const embeddable = await canEmbedYoutubeVideo(id);
      if (!embeddable) return false;
      const ok = playEmbedFallback(id, autoPlay);
      if (!ok) return false;
      if (statusText) setStatus(statusText);
      updatePlayPauseUi();
      return true;
    }}

    function setStatus(t) {{
      statusEl.textContent = t || '';
    }}

    function setGlobalSearchOpen(open) {{
      if (!globalSearchResultsEl) return;
      globalSearchResultsEl.classList.toggle('is-open', !!open);
    }}

    function clearGlobalSearchDropdown() {{
      if (!globalSearchResultsEl) return;
      globalSearchResultsEl.innerHTML = '';
      setGlobalSearchOpen(false);
    }}

    function getSearchResultById(id) {{
      return searchPageResults.find((x) => x && String(x.id) === String(id)) || null;
    }}

    function formatViews(v) {{
      const n = Number(v);
      if (!Number.isFinite(n) || n <= 0) return '';
      if (n >= 1000000) return (n / 1000000).toFixed(1).replace(/\\.0$/, '') + 'M';
      if (n >= 1000) return Math.round(n / 1000) + 'K';
      return String(Math.round(n));
    }}

    function renderSearchPageResults(results, query) {{
      if (!ytSearchPageListEl) return;
      const q = String(query || '').trim();
      searchPageResults = Array.isArray(results) ? results.slice() : [];
      if (ytSearchPageTitleEl) {{
        ytSearchPageTitleEl.textContent = q ? `תוצאות YouTube: ${{q}}` : 'תוצאות חיפוש מיוטיוב';
      }}
      if (ytSearchPageSubEl) {{
        ytSearchPageSubEl.textContent = q ? `נמצאו ${{searchPageResults.length}} תוצאות` : 'כתבי בשורת החיפוש ליד הכותרת';
      }}
      if (!q) {{
        ytSearchPageListEl.innerHTML = '<div class="gs-empty">הקלידי שם שיר או אמן בשורת החיפוש</div>';
        return;
      }}
      if (!searchPageResults.length) {{
        ytSearchPageListEl.innerHTML = '<div class="gs-empty">לא נמצאו תוצאות ביוטיוב</div>';
        return;
      }}
      ytSearchPageListEl.innerHTML = searchPageResults.map((row, i) => {{
        const t = esc(row.title || ('תוצאה ' + (i + 1)));
        const up = esc(row.uploader || 'YouTube');
        const vw = formatViews(row.views);
        const sub = esc(vw ? `${{up}} · ${{vw}}` : up);
        const thumb = esc(row.thumb || `https://i.ytimg.com/vi/${{row.id}}/mqdefault.jpg`);
        return `
          <div class="yt-sp-row" data-sp-row="${{esc(row.id)}}" role="button" tabindex="0" title="נגן">
            <img class="yt-sp-thumb" src="${{thumb}}" alt="" loading="lazy" />
            <div class="yt-sp-meta">
              <div class="yt-sp-title">${{t}}</div>
              <div class="yt-sp-sub">${{sub}}</div>
            </div>
            <button type="button" class="yt-sp-plus" data-sp-more="${{esc(row.id)}}" title="אפשרויות">＋</button>
          </div>
        `;
      }}).join('');
    }}

    function openSearchActionOverlay(id) {{
      searchActionTargetId = String(id || '');
      if (!searchActionOverlay || !searchActionTargetId) return;
      searchActionOverlay.classList.add('is-open');
    }}

    function closeSearchActionOverlay() {{
      if (searchActionOverlay) searchActionOverlay.classList.remove('is-open');
      searchActionTargetId = '';
    }}

    async function searchYouTubeFromTop(query) {{
      const q = String(query || '').trim();
      if (!q) {{
        clearGlobalSearchDropdown();
        return;
      }}
      const reqId = ++globalSearchReqSeq;
      clearGlobalSearchDropdown();
      setNav('library');
      if (ytSearchPageTitleEl) ytSearchPageTitleEl.textContent = `תוצאות YouTube: ${{q}}`;
      if (ytSearchPageSubEl) ytSearchPageSubEl.textContent = 'מחפש ביוטיוב...';
      if (ytSearchPageListEl) ytSearchPageListEl.innerHTML = '<div class="gs-empty">מחפש ביוטיוב...</div>';
      try {{
        const r = await fetch(apiUrl(`/api/search?q=${{encodeURIComponent(q)}}`), {{ cache: 'no-store' }});
        if (!r.ok) throw new Error(`HTTP ${{r.status}}`);
        const data = await r.json();
        if (reqId !== globalSearchReqSeq) return;
        const results = Array.isArray(data.results) ? data.results.slice() : [];
        renderSearchPageResults(results, q);
      }} catch (e) {{
        if (reqId !== globalSearchReqSeq) return;
        if (ytSearchPageListEl) ytSearchPageListEl.innerHTML = '<div class="gs-empty">שגיאה בחיפוש יוטיוב</div>';
        renderSearchPageResults([], q);
      }}
    }}

    function loadEqState() {{
      const defaults = {{
        enabled: true,
        preventClipping: true,
        preamp: 0,
        bands: [0,0,0,0,0,0,0,0,0,0],
        compressor: {{ enabled: false, threshold: -18, ratio: 3 }},
      }};
      try {{
        const raw = JSON.parse(localStorage.getItem(EQ_STATE_KEY) || '{{}}');
        const bands = Array.isArray(raw.bands) ? raw.bands.slice(0, 10).map((v) => Number(v) || 0) : defaults.bands.slice();
        while (bands.length < 10) bands.push(0);
        const comp = raw.compressor && typeof raw.compressor === 'object' ? raw.compressor : {{}};
        return {{
          enabled: raw.enabled !== false,
          preventClipping: raw.preventClipping !== false,
          preamp: Number(raw.preamp) || 0,
          bands,
          compressor: {{
            enabled: !!comp.enabled,
            threshold: Number.isFinite(Number(comp.threshold)) ? Number(comp.threshold) : -18,
            ratio: Number.isFinite(Number(comp.ratio)) ? Number(comp.ratio) : 3,
          }},
        }};
      }} catch (e) {{
        return defaults;
      }}
    }}

    function saveEqState(state) {{
      try {{ localStorage.setItem(EQ_STATE_KEY, JSON.stringify(state)); }} catch (e) {{}}
    }}

    function dbToGain(db) {{
      return Math.pow(10, db / 20);
    }}

    function ensureEqAudioGraph() {{
      if (audioCtx && mediaSourceNode && preGainNode && eqFilters.length) return true;
      const AC = window.AudioContext || window.webkitAudioContext;
      if (!AC || !video) return false;
      try {{
        audioCtx = audioCtx || new AC();
        mediaSourceNode = mediaSourceNode || audioCtx.createMediaElementSource(video);
        preGainNode = preGainNode || audioCtx.createGain();
        compNode = compNode || audioCtx.createDynamicsCompressor();
        limiterNode = limiterNode || audioCtx.createDynamicsCompressor();
        limiterNode.threshold.value = -1;
        limiterNode.knee.value = 0;
        limiterNode.ratio.value = 20;
        limiterNode.attack.value = 0.003;
        limiterNode.release.value = 0.1;
        if (!eqFilters.length) {{
          const freqs = [32, 64, 125, 250, 500, 1000, 2000, 4000, 8000, 16000];
          eqFilters = freqs.map((f) => {{
            const bf = audioCtx.createBiquadFilter();
            bf.type = 'peaking';
            bf.frequency.value = f;
            bf.Q.value = 1.2;
            bf.gain.value = 0;
            return bf;
          }});
        }}
        mediaSourceNode.disconnect();
        mediaSourceNode.connect(preGainNode);
        let chain = preGainNode;
        for (const f of eqFilters) {{
          chain.connect(f);
          chain = f;
        }}
        chain.connect(compNode);
        compNode.connect(limiterNode);
        limiterNode.connect(audioCtx.destination);
        return true;
      }} catch (e) {{
        return false;
      }}
    }}

    function applyEqStateToAudio(state) {{
      if (!ensureEqAudioGraph()) return false;
      const enabled = state.enabled !== false;
      preGainNode.gain.value = dbToGain(enabled ? state.preamp : 0);
      for (let i = 0; i < eqFilters.length; i++) {{
        eqFilters[i].gain.value = enabled ? Number(state.bands[i] || 0) : 0;
      }}
      const compOn = enabled && state.compressor && state.compressor.enabled;
      compNode.threshold.value = compOn ? Number(state.compressor.threshold || -18) : 0;
      compNode.ratio.value = compOn ? Number(state.compressor.ratio || 3) : 1;
      compNode.knee.value = 18;
      compNode.attack.value = 0.008;
      compNode.release.value = 0.2;
      if (state.preventClipping === false) {{
        limiterNode.threshold.value = 0;
        limiterNode.ratio.value = 1;
      }} else {{
        limiterNode.threshold.value = -1;
        limiterNode.ratio.value = 20;
      }}
      return true;
    }}

    function initEqUi() {{
      if (!eqBandsEl || !eqPreampEl || !eqPreampValueEl) return;
      const labels = ['32','64','125','250','500','1k','2k','4k','8k','16k'];
      const st = loadEqState();
      st.enabled = false;
      saveEqState(st);
      eqBandsEl.innerHTML = labels.map((lb, i) => `
        <div class="eq-band">
          <input type="range" data-eq-band="${{i}}" min="-12" max="12" step="0.5" value="${{st.bands[i]}}" />
          <div class="eq-band-label">${{lb}}</div>
        </div>
      `).join('');
      eqPreampEl.value = String(st.preamp);
      eqPreampValueEl.textContent = `${{st.preamp}}dB`;
      if (eqCompThresholdEl) eqCompThresholdEl.value = String(st.compressor.threshold);
      if (eqCompRatioEl) eqCompRatioEl.value = String(st.compressor.ratio);
      if (eqCompThresholdValueEl) eqCompThresholdValueEl.textContent = `${{st.compressor.threshold}}dB`;
      if (eqCompRatioValueEl) eqCompRatioValueEl.textContent = `${{st.compressor.ratio}}:1`;
      if (eqEnableBtn) {{
        eqEnableBtn.classList.toggle('is-on', !!st.enabled);
        eqEnableBtn.textContent = st.enabled ? 'EQ פעיל' : 'EQ כבוי';
      }}
      if (eqClipBtn) {{
        eqClipBtn.classList.toggle('is-on', !!st.preventClipping);
        eqClipBtn.textContent = st.preventClipping ? 'Prevent Clipping' : 'Clipping מותר';
      }}
      if (eqCompEnableBtn) {{
        eqCompEnableBtn.classList.toggle('is-on', !!st.compressor.enabled);
        eqCompEnableBtn.textContent = st.compressor.enabled ? 'Compressor פעיל' : 'Compressor כבוי';
      }}
      // Do not force-create audio graph on startup:
      // some direct media streams fail under CORS-mode media element setup.

      const onChange = () => {{
        const next = {{
          enabled: !!(eqEnableBtn && eqEnableBtn.classList.contains('is-on')),
          preventClipping: !!(eqClipBtn && eqClipBtn.classList.contains('is-on')),
          preamp: Number(eqPreampEl.value) || 0,
          bands: [...eqBandsEl.querySelectorAll('input[data-eq-band]')].map((el) => Number(el.value) || 0),
          compressor: {{
            enabled: !!(eqCompEnableBtn && eqCompEnableBtn.classList.contains('is-on')),
            threshold: Number(eqCompThresholdEl ? eqCompThresholdEl.value : -18) || -18,
            ratio: Number(eqCompRatioEl ? eqCompRatioEl.value : 3) || 3,
          }},
        }};
        eqPreampValueEl.textContent = `${{next.preamp}}dB`;
        if (eqCompThresholdValueEl) eqCompThresholdValueEl.textContent = `${{next.compressor.threshold}}dB`;
        if (eqCompRatioValueEl) eqCompRatioValueEl.textContent = `${{next.compressor.ratio}}:1`;
        saveEqState(next);
        applyEqStateToAudio(next);
      }};
      eqPreampEl.addEventListener('input', onChange);
      eqBandsEl.addEventListener('input', (e) => {{
        if (!e.target.matches('input[data-eq-band]')) return;
        onChange();
      }});
      if (eqResetBtn) {{
        eqResetBtn.addEventListener('click', () => {{
          if (eqEnableBtn) {{
            eqEnableBtn.classList.add('is-on');
            eqEnableBtn.textContent = 'EQ פעיל';
          }}
          if (eqClipBtn) {{
            eqClipBtn.classList.add('is-on');
            eqClipBtn.textContent = 'Prevent Clipping';
          }}
          if (eqCompEnableBtn) {{
            eqCompEnableBtn.classList.remove('is-on');
            eqCompEnableBtn.textContent = 'Compressor כבוי';
          }}
          eqPreampEl.value = '0';
          if (eqCompThresholdEl) eqCompThresholdEl.value = '-18';
          if (eqCompRatioEl) eqCompRatioEl.value = '3';
          eqBandsEl.querySelectorAll('input[data-eq-band]').forEach((el) => (el.value = '0'));
          onChange();
        }});
      }}
      if (eqEnableBtn) {{
        eqEnableBtn.addEventListener('click', () => {{
          const on = !eqEnableBtn.classList.contains('is-on');
          eqEnableBtn.classList.toggle('is-on', on);
          eqEnableBtn.textContent = on ? 'EQ פעיל' : 'EQ כבוי';
          onChange();
        }});
      }}
      if (eqClipBtn) {{
        eqClipBtn.addEventListener('click', () => {{
          const on = !eqClipBtn.classList.contains('is-on');
          eqClipBtn.classList.toggle('is-on', on);
          eqClipBtn.textContent = on ? 'Prevent Clipping' : 'Clipping מותר';
          onChange();
        }});
      }}
      if (eqCompEnableBtn) {{
        eqCompEnableBtn.addEventListener('click', () => {{
          const on = !eqCompEnableBtn.classList.contains('is-on');
          eqCompEnableBtn.classList.toggle('is-on', on);
          eqCompEnableBtn.textContent = on ? 'Compressor פעיל' : 'Compressor כבוי';
          onChange();
        }});
      }}
      if (eqCompThresholdEl) eqCompThresholdEl.addEventListener('input', onChange);
      if (eqCompRatioEl) eqCompRatioEl.addEventListener('input', onChange);

      const saveSet = (slot) => {{
        try {{
          const sets = JSON.parse(localStorage.getItem(EQ_SETS_KEY) || '{{}}');
          sets[String(slot)] = loadEqState();
          localStorage.setItem(EQ_SETS_KEY, JSON.stringify(sets));
          setStatus(`נשמר Set ${{slot}}`);
        }} catch (e) {{
          setStatus('שמירת סט נכשלה');
        }}
      }};
      const loadSet = (slot) => {{
        try {{
          const sets = JSON.parse(localStorage.getItem(EQ_SETS_KEY) || '{{}}');
          const s = sets[String(slot)];
          if (!s) {{
            setStatus(`Set ${{slot}} ריק`);
            return;
          }}
          saveEqState(s);
          const bands = Array.isArray(s.bands) ? s.bands : [];
          if (eqEnableBtn) {{
            eqEnableBtn.classList.toggle('is-on', s.enabled !== false);
            eqEnableBtn.textContent = s.enabled !== false ? 'EQ פעיל' : 'EQ כבוי';
          }}
          if (eqClipBtn) {{
            eqClipBtn.classList.toggle('is-on', s.preventClipping !== false);
            eqClipBtn.textContent = s.preventClipping !== false ? 'Prevent Clipping' : 'Clipping מותר';
          }}
          if (eqCompEnableBtn) {{
            const compOn = !!(s.compressor && s.compressor.enabled);
            eqCompEnableBtn.classList.toggle('is-on', compOn);
            eqCompEnableBtn.textContent = compOn ? 'Compressor פעיל' : 'Compressor כבוי';
          }}
          eqPreampEl.value = String(Number(s.preamp) || 0);
          if (eqCompThresholdEl) eqCompThresholdEl.value = String((s.compressor && Number(s.compressor.threshold)) || -18);
          if (eqCompRatioEl) eqCompRatioEl.value = String((s.compressor && Number(s.compressor.ratio)) || 3);
          eqBandsEl.querySelectorAll('input[data-eq-band]').forEach((el, i) => (el.value = String(Number(bands[i]) || 0)));
          onChange();
          setStatus(`נטען Set ${{slot}}`);
        }} catch (e) {{
          setStatus('טעינת סט נכשלה');
        }}
      }};
      if (eqSaveSet1) eqSaveSet1.addEventListener('click', () => saveSet(1));
      if (eqSaveSet2) eqSaveSet2.addEventListener('click', () => saveSet(2));
      if (eqLoadSet1) eqLoadSet1.addEventListener('click', () => loadSet(1));
      if (eqLoadSet2) eqLoadSet2.addEventListener('click', () => loadSet(2));
      if (eqPresetVoiceBtn) {{
        eqPresetVoiceBtn.addEventListener('click', () => {{
          const v = [-2,-1,0,1,2,3,2,1,0,-1];
          eqBandsEl.querySelectorAll('input[data-eq-band]').forEach((el, i) => (el.value = String(v[i] || 0)));
          eqPreampEl.value = '-1';
          onChange();
        }});
      }}
      if (eqPresetBassBtn) {{
        eqPresetBassBtn.addEventListener('click', () => {{
          const v = [4,3,2,1,0,-1,-1,0,1,2];
          eqBandsEl.querySelectorAll('input[data-eq-band]').forEach((el, i) => (el.value = String(v[i] || 0)));
          eqPreampEl.value = '-2';
          onChange();
        }});
      }}
    }}

    function setNowPlayingExpanded(expanded) {{
      if (!nowPlayingCard || !npSummaryRow) return;
      nowPlayingCard.classList.toggle('is-collapsed', !expanded);
      nowPlayingCard.classList.toggle('is-page-open', !!expanded);
      document.body.classList.toggle('np-page-open', !!expanded);
      npSummaryRow.setAttribute('aria-expanded', expanded ? 'true' : 'false');
    }}

    async function ensureSongPageClipPlayback() {{
      if (!nowPlayingCard || !nowPlayingCard.classList.contains('is-page-open')) return;
      // Audio-first: opening song page should not force YouTube embed,
      // because some videos are blocked for embedding and can stall playback.
      if (!video.src || video.paused) {{
        await playPauseSafe();
      }}
    }}

    function getFullscreenElement() {{
      return document.fullscreenElement || document.webkitFullscreenElement || null;
    }}

    async function requestElementFullscreen(el) {{
      if (!el) return false;
      try {{
        if (el.requestFullscreen) {{
          await el.requestFullscreen();
          return true;
        }}
        if (el.webkitRequestFullscreen) {{
          el.webkitRequestFullscreen();
          return true;
        }}
      }} catch (e) {{
        return false;
      }}
      return false;
    }}

    async function exitAnyFullscreen() {{
      try {{
        if (document.exitFullscreen) {{
          await document.exitFullscreen();
          return true;
        }}
        if (document.webkitExitFullscreen) {{
          document.webkitExitFullscreen();
          return true;
        }}
      }} catch (e) {{
        return false;
      }}
      return false;
    }}

    function updateFullscreenButtonUi() {{
      if (!fullscreenBtn) return;
      const fs = !!getFullscreenElement();
      fullscreenBtn.classList.toggle('is-fullscreen', fs);
      fullscreenBtn.setAttribute('aria-pressed', fs ? 'true' : 'false');
      fullscreenBtn.title = fs ? 'יציאה ממסך מלא' : 'מסך מלא';
    }}

    function normalizeDisplayName(item, index) {{
      const fallback = `שיר ${{index + 1}}`;
      if (!item || typeof item !== 'object') return fallback;
      const name = String(item.name || '').trim();
      if (name) return name;
      const url = String(item.url || '').trim();
      if (url) {{
        const id = getYoutubeIdFromItem(item);
        if (id) return `YouTube ${{id}}`;
      }}
      return fallback;
    }}

    function applyUiMode() {{
      const isCar = uiMode === 'car';
      document.body.classList.toggle('car-mode', isCar);
    }}

    function saveItems() {{
      localStorage.setItem(STORAGE_KEY, JSON.stringify(items));
    }}

    function saveLastAbsoluteIndex() {{
      if (!items.length) {{
        try {{ localStorage.removeItem(LAST_SONG_KEY); }} catch (e) {{}}
        return;
      }}
      if (!filteredIndices.length) return;
      try {{ localStorage.setItem(LAST_SONG_KEY, String(currentIndex())); }} catch (e) {{}}
    }}

    function updateShuffleButton() {{
      shuffleBtn.classList.toggle('active', shuffleEnabled);
      shuffleBtn.title = shuffleEnabled ? 'ערבוב פעיל' : 'ערבוב';
    }}

    function updateRepeatButton() {{
      repeatBtn.setAttribute('data-mode', repeatMode);
      repeatBtn.classList.toggle('active', repeatMode !== 'off');
      if (repeatMode === 'one') {{
        repeatBtn.title = 'חזרה: שיר אחד';
      }} else if (repeatMode === 'all') {{
        repeatBtn.title = 'חזרה: כל הרשימה';
      }} else {{
        repeatBtn.title = 'ללא חזרה אוטומטית';
      }}
    }}

    function formatTime(sec) {{
      if (!Number.isFinite(sec) || sec < 0) return '0:00';
      const m = Math.floor(sec / 60);
      const s = Math.floor(sec % 60);
      return m + ':' + String(s).padStart(2, '0');
    }}

    function updatePlayPauseUi() {{
      const embed = mediaShell.classList.contains('use-embed');
      const quickPlaying = !!embed || !video.paused;
      if (npQuickPlayBtn) {{
        npQuickPlayBtn.classList.toggle('is-playing', quickPlaying);
        npQuickPlayBtn.setAttribute('aria-pressed', quickPlaying ? 'true' : 'false');
        npQuickPlayBtn.title = quickPlaying ? 'השהיה מהירה' : 'ניגון מהיר';
      }}
      if (embed) {{
        playBtn.classList.add('is-paused');
        return;
      }}
      if (video.paused) {{
        playBtn.classList.add('is-paused');
      }} else {{
        playBtn.classList.remove('is-paused');
      }}
    }}

    function refreshNpArtwork() {{
      if (!npArtwork) return;
      const idx = currentIndex();
      const item = items[idx];
      const yid = item ? getYoutubeIdFromItem(item) : '';
      if (yid) {{
        npArtwork.src = 'https://i.ytimg.com/vi/' + yid + '/hqdefault.jpg';
        npArtwork.alt = normalizeDisplayName(item, idx) || '';
      }} else {{
        npArtwork.removeAttribute('src');
        npArtwork.alt = '';
      }}
    }}

    function syncProgressFromVideo() {{
      if (isSeekingProgress || !video.duration) return;
      const p = video.currentTime / video.duration;
      progressBar.value = String(Math.min(1000, Math.floor(p * 1000)));
      const g = Math.round(p * 100);
      progressBar.style.background = `linear-gradient(90deg, var(--accent) ${{g}}%, #444 ${{g}}%)`;
      timeCurrentEl.textContent = formatTime(video.currentTime);
      timeTotalEl.textContent = formatTime(video.duration);
    }}

    function updateVolumeUi() {{
      if (!volumeBar) return;
      const raw = Number(volumeBar.value);
      const v = Number.isFinite(raw) ? Math.max(0, Math.min(100, raw)) : 100;
      const g = Math.round(v);
      volumeBar.style.background = `linear-gradient(90deg, var(--accent) ${{g}}%, #444 ${{g}}%)`;
      try {{
        video.muted = v <= 0;
        video.volume = Math.max(0, Math.min(1, v / 100));
      }} catch (e) {{}}
    }}

    function likeKeyForItemAt(index) {{
      const it = items[index];
      if (!it) return '';
      const id = getYoutubeIdFromItem(it);
      if (id) return 'y:' + id;
      const u = String(it.url || '').trim();
      if (u) return 'u:' + u;
      return 'i:' + index;
    }}

    function loadLikeSet() {{
      try {{
        const a = JSON.parse(localStorage.getItem(LIKES_KEY) || '[]');
        return new Set(Array.isArray(a) ? a : []);
      }} catch (e) {{
        return new Set();
      }}
    }}

    function saveLikeSet(set) {{
      try {{
        localStorage.setItem(LIKES_KEY, JSON.stringify([...set]));
      }} catch (e) {{}}
    }}

    function toggleLikeAtIndex(index) {{
      const key = likeKeyForItemAt(index);
      if (!key) return;
      const s = loadLikeSet();
      if (s.has(key)) s.delete(key);
      else s.add(key);
      saveLikeSet(s);
    }}

    function updateLikeButton() {{
      if (!likeBtn) return;
      if (offlinePlayingVid) {{
        likeBtn.hidden = true;
        return;
      }}
      likeBtn.hidden = false;
      const idx = currentIndex();
      const key = likeKeyForItemAt(idx);
      const on = key && loadLikeSet().has(key);
      likeBtn.classList.toggle('liked', !!on);
      likeBtn.setAttribute('aria-pressed', on ? 'true' : 'false');
      likeBtn.title = on ? 'הסר ממועדפים' : 'הוסף למועדפים';
    }}

    function syncFavoritesFilterBtn() {{
      if (!favoritesOnlyBtn) return;
      favoritesOnlyBtn.classList.toggle('active', favoritesOnly);
      favoritesOnlyBtn.textContent = favoritesOnly ? 'מועדפים ✓' : 'מועדפים';
    }}

    function pushRecentFromIndex(index) {{
      const key = likeKeyForItemAt(index);
      if (!key) return;
      let a = [];
      try {{
        a = JSON.parse(localStorage.getItem(RECENT_KEY) || '[]');
      }} catch (e) {{
        a = [];
      }}
      if (!Array.isArray(a)) a = [];
      a = [key, ...a.filter((k) => k !== key)].slice(0, 24);
      try {{
        localStorage.setItem(RECENT_KEY, JSON.stringify(a));
      }} catch (e) {{}}
      renderRecentList();
    }}

    function renderRecentList() {{
      if (!recentListEl) return;
      let keys = [];
      try {{
        keys = JSON.parse(localStorage.getItem(RECENT_KEY) || '[]');
      }} catch (e) {{
        keys = [];
      }}
      if (!Array.isArray(keys) || !keys.length) {{
        recentListEl.innerHTML = '<div class="queue-empty" style="padding:10px;font-size:0.8rem;">אין היסטוריה עדיין</div>';
        return;
      }}
      const rows = [];
      for (const key of keys) {{
        const idx = items.findIndex((_, i) => likeKeyForItemAt(i) === key);
        if (idx < 0) continue;
        rows.push({{ idx, title: normalizeDisplayName(items[idx], idx) }});
        if (rows.length >= 10) break;
      }}
      if (!rows.length) {{
        recentListEl.innerHTML = '<div class="queue-empty" style="padding:10px;font-size:0.8rem;">אין היסטוריה עדיין</div>';
        return;
      }}
      recentListEl.innerHTML = rows
        .map(
          (r) =>
            `<div class="recent-row" data-pick="${{r.idx}}">${{esc(r.title)}}</div>`,
        )
        .join('');
    }}

    function renderUpNext() {{
      if (!upNextListEl) return;
      if (!filteredIndices.length) {{
        upNextListEl.innerHTML = '';
        return;
      }}
      if (shuffleEnabled) {{
        upNextListEl.innerHTML =
          '<div class="up-next-hint">ערבוב פעיל — השירים הבאים ייבחרו בסדר אקראי מתוך הרשימה.</div>';
        return;
      }}
      const cur = currentIndex();
      const posIn = filteredIndices.indexOf(cur);
      if (posIn < 0) {{
        upNextListEl.innerHTML = '';
        return;
      }}
      const nextIdxs = [];
      const n = filteredIndices.length;
      if (n <= 1) {{
        upNextListEl.innerHTML = '<div class="up-next-hint">אין שיר נוסף בתור.</div>';
        return;
      }}
      for (let step = 1; step <= 5 && nextIdxs.length < 5; step++) {{
        nextIdxs.push(filteredIndices[(posIn + step) % n]);
      }}
      upNextListEl.innerHTML = nextIdxs
        .map((absIdx, i) => {{
          const t = esc(normalizeDisplayName(items[absIdx], absIdx));
          return `<div class="up-next-row" data-pick="${{absIdx}}"><span class="up-num">${{i + 1}}</span><span class="up-title">${{t}}</span></div>`;
        }})
        .join('');
    }}

    function loadItems() {{
      try {{
        const stored = JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]');
        if (Array.isArray(stored) && stored.length) {{
          items = stored
            .filter(x => x && typeof x === 'object')
            .map((x, i) => ({{
              ...x,
              name: normalizeDisplayName(x, i),
            }}));
        }} else {{
          items = baseItems.slice().map((x, i) => ({{
            ...x,
            name: normalizeDisplayName(x, i),
          }}));
        }}
      }} catch (e) {{
        items = baseItems.slice().map((x, i) => ({{
          ...x,
          name: normalizeDisplayName(x, i),
        }}));
      }}
      saveItems();
      loadFailStreak = 0;
      filteredIndices = items.map((_, i) => i);
      pos = 0;
      shuffleQueue = [];
      playbackHistory = [];
    }}

    function detectAutoQuality() {{
      // Best effort network-based estimation.
      const conn = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
      if (!conn) return 'high';

      const downlink = Number(conn.downlink || 0); // Mbps
      const effectiveType = String(conn.effectiveType || '').toLowerCase();
      const saveData = !!conn.saveData;

      if (saveData) return 'normal';
      if (effectiveType.includes('2g') || effectiveType === 'slow-2g') return 'normal';
      if (effectiveType === '3g') return 'normal';
      if (downlink > 0 && downlink < 3) return 'normal';
      return 'high';
    }}

    function activeQuality() {{
      return quality === 'auto' ? detectAutoQuality() : quality;
    }}

    function currentIndex() {{
      if (!filteredIndices.length) return 0;
      return filteredIndices[pos];
    }}

    function shuffleArray(arr) {{
      const out = arr.slice();
      for (let i = out.length - 1; i > 0; i--) {{
        const j = Math.floor(Math.random() * (i + 1));
        [out[i], out[j]] = [out[j], out[i]];
      }}
      return out;
    }}

    function rebuildShuffleQueue() {{
      if (!filteredIndices.length) {{
        shuffleQueue = [];
        return;
      }}
      const current = currentIndex();
      const pool = filteredIndices.filter(i => i !== current);
      shuffleQueue = shuffleArray(pool);
    }}

    function moveToIndex(index, fromUser = false) {{
      const nextPos = filteredIndices.indexOf(index);
      if (nextPos === -1) return false;
      if (fromUser && filteredIndices.length > 0) {{
        const current = currentIndex();
        playbackHistory.push(current);
        if (playbackHistory.length > 300) playbackHistory.shift();
      }}
      pos = nextPos;
      return true;
    }}

    function playAbsoluteIndex(pick) {{
      offlinePlayingVid = null;
      if (!Number.isFinite(pick)) return false;
      if (pick < 0 || pick >= items.length) return false;

      // Keep current filtered set when possible, otherwise fallback safely.
      let newPos = filteredIndices.indexOf(pick);
      if (newPos === -1) {{
        filteredIndices = [pick];
        newPos = 0;
      }}
      pos = newPos;
      saveLastAbsoluteIndex();
      loadCurrent(true);
      return true;
    }}

    function rebuildFilter(autoPlay = true, resetPos = true) {{
      const rawQ = (searchEl.value || '').trim().toLowerCase();
      /* בשורת הספרייה אותו שדה מפעיל חיפוש יוטיוב — לא לסנן לפיו את רשימת השירים המקומית */
      const q = currentNavView === 'library' ? '' : rawQ;
      let cand = items
        .map((item, i) => ({{ i, name: (item.name || '').toLowerCase() }}))
        .filter(row => !q || row.name.includes(q))
        .map(row => row.i);
      if (favoritesOnly) {{
        const likes = loadLikeSet();
        cand = cand.filter((i) => likes.has(likeKeyForItemAt(i)));
      }}
      filteredIndices = cand;

      if (!filteredIndices.length) {{
        meta.textContent = 'אין תוצאות לחיפוש';
        setStatus('נסה מילת חיפוש אחרת');
        renderQuickResults(q);
        renderUpNext();
        updateLikeButton();
        refreshAllBrowseUi();
        return;
      }}
      loadFailStreak = 0;
      shuffleQueue = [];
      playbackHistory = [];
      if (resetPos) {{
        pos = 0;
      }} else {{
        /* שחזור מיקום אחרון: גם כשיש חיפוש בבית — אם השיר השמור עדיין ברשימה המסוננת */
        let saved = NaN;
        try {{ saved = parseInt(String(localStorage.getItem(LAST_SONG_KEY) || ''), 10); }} catch (e) {{ saved = NaN; }}
        if (Number.isFinite(saved) && saved >= 0 && saved < items.length) {{
          const pIn = filteredIndices.indexOf(saved);
          if (pIn >= 0) pos = pIn;
        }}
      }}
      const idx = currentIndex();
      const cur = items[idx];
      meta.textContent = `#${{idx + 1}} - ${{normalizeDisplayName(cur, idx)}}`;
      renderQuickResults(q);
      refreshNpArtwork();
      if (autoPlay) {{
        loadCurrent(true);
      }} else {{
        setStatus('לחץ "נגן" להתחלת ניגון');
      }}
      refreshAllBrowseUi();
    }}

    function esc(s) {{
      return String(s || '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
    }}

    function loadPlaylistsFromStorage() {{
      try {{
        const raw = JSON.parse(localStorage.getItem(PLAYLISTS_KEY) || '[]');
        playlists = Array.isArray(raw)
          ? raw
              .filter((p) => p && typeof p === 'object' && p.id && p.name)
              .map((p) => ({{
                id: String(p.id),
                name: String(p.name || 'ללא שם'),
                keys: Array.isArray(p.keys) ? p.keys.map((k) => String(k)) : [],
              }}))
          : [];
      }} catch (e) {{
        playlists = [];
      }}
    }}

    function savePlaylistsToStorage() {{
      try {{
        localStorage.setItem(PLAYLISTS_KEY, JSON.stringify(playlists));
      }} catch (e) {{}}
    }}

    function setNav(view, plId) {{
      currentNavView = view;
      document.querySelectorAll('[data-nav-target]').forEach((btn) => {{
        const id = btn.getAttribute('data-nav-target');
        const active = (view !== 'playlistDetail' && id === view) || (view === 'playlistDetail' && id === 'playlists');
        btn.classList.toggle('is-active', active);
        btn.setAttribute('aria-current', active ? 'page' : 'false');
      }});
      const panels = [viewHome, viewOffline, viewLibrary, viewLiked, viewPlaylists, viewPlaylistDetail].filter(Boolean);
      panels.forEach((p) => p.classList.remove('is-active'));
      if (view === 'home' && viewHome) viewHome.classList.add('is-active');
      else if (view === 'offline' && viewOffline) {{
        viewOffline.classList.add('is-active');
        renderOfflinePanel();
      }}
      else if (view === 'library' && viewLibrary) viewLibrary.classList.add('is-active');
      else if (view === 'liked' && viewLiked) viewLiked.classList.add('is-active');
      else if (view === 'playlists' && viewPlaylists) viewPlaylists.classList.add('is-active');
      else if (view === 'playlistDetail' && viewPlaylistDetail) {{
        activePlaylistDetailId = plId || null;
        viewPlaylistDetail.classList.add('is-active');
        renderPlaylistDetailView();
      }}
      if (view !== 'playlistDetail') activePlaylistDetailId = null;
      if (view === 'liked') renderLikedPanel();
      if (view === 'playlists') {{
        renderPlaylistsGrid();
        renderYoutubeRecommendedPlaylists();
      }}
      if (view === 'home') {{
        renderHomeTiles();
        renderRecentList();
      }}
      syncSearchDock();
      updateBackFab();
    }}

    function syncSearchDock() {{
      if (!globalSearchWrapEl) return;
      if (currentNavView === 'library' && ytSearchToolbarSearchHost) {{
        if (globalSearchWrapEl.parentElement !== ytSearchToolbarSearchHost) {{
          ytSearchToolbarSearchHost.appendChild(globalSearchWrapEl);
        }}
      }} else if (glassTopSearchHost) {{
        if (globalSearchWrapEl.parentElement !== glassTopSearchHost) {{
          glassTopSearchHost.appendChild(globalSearchWrapEl);
        }}
      }}
    }}

    function updateBackFab() {{
      if (!navBackFab) return;
      const shouldShow = currentNavView !== 'home';
      navBackFab.hidden = !shouldShow;
      navBackFab.setAttribute('aria-hidden', shouldShow ? 'false' : 'true');
    }}

    function renderHomeTiles() {{
      if (!homeTilesEl) return;
      const n = items.length;
      const likes = loadLikeSet();
      let likedCount = 0;
      for (let i = 0; i < items.length; i++) {{
        const k = likeKeyForItemAt(i);
        if (k && likes.has(k)) likedCount++;
      }}
      const plCount = playlists.length;
      homeTilesEl.innerHTML =
        '<div class="home-tile" data-go="library">' +
        '<div class="ht-label">הספרייה שלי</div>' +
        '<div class="ht-meta">' +
        n +
        ' שירים</div></div>' +
        '<div class="home-tile" data-go="liked">' +
        '<div class="ht-label">שירים שאהבתי</div>' +
        '<div class="ht-meta">' +
        likedCount +
        ' שירים</div></div>' +
        '<div class="home-tile" data-go="playlists">' +
        '<div class="ht-label">פלייליסטים</div>' +
        '<div class="ht-meta">' +
        plCount +
        ' רשימות</div></div>' +
        '<div class="home-tile" data-go="offline">' +
        '<div class="ht-label">שמורים אצל השרת</div>' +
        '<div class="ht-meta">ניגון בלי אינטרנט</div></div>';
    }}

    function renderLikedPanel() {{
      if (!likedPanelListEl) return;
      const likes = loadLikeSet();
      const idxs = [];
      for (let i = 0; i < items.length; i++) {{
        const k = likeKeyForItemAt(i);
        if (k && likes.has(k)) idxs.push(i);
      }}
      if (!idxs.length) {{
        likedPanelListEl.innerHTML = '<div class="queue-empty">עדיין אין שירים במועדפים. עברי ל״הספרייה שלי״ ולחצי על כפתור המועדפים ליד שיר.</div>';
        return;
      }}
      const curIdx = currentIndex();
      likedPanelListEl.innerHTML = idxs.map((index) => {{
        const row = items[index] || {{}};
        const title = normalizeDisplayName(row, index);
        const yid = getYoutubeIdFromItem(row);
        const thumb = yid ? `https://i.ytimg.com/vi/${{yid}}/mqdefault.jpg` : '';
        const active = index === curIdx ? 'is-active' : '';
        return `
          <div class="queue-row ${{active}}" data-pick="${{index}}">
            <img class="queue-thumb" src="${{thumb}}" alt="" loading="lazy" width="48" height="48" />
            <div class="queue-body">
              <div class="queue-title">${{esc(title)}}</div>
              <div class="queue-sub">שיר ${{index + 1}}</div>
            </div>
            <div class="queue-actions">
              <button type="button" class="like-inline liked" data-like="${{index}}" title="הסר ממועדפים" aria-label="הסר ממועדפים">
                <svg class="ico-heart-stroke" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M12 20.5s-7-4.6-7-10a4 4 0 0 1 7-2.5A4 4 0 0 1 19 10.5c0 5.4-7 10-7 10z"/></svg>
                <svg class="ico-heart-fill" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M12 20.5s-7-4.6-7-10a4 4 0 0 1 7-2.5A4 4 0 0 1 19 10.5c0 5.4-7 10-7 10z"/></svg>
              </button>
              <button type="button" class="secondary queue-play-btn" data-pick="${{index}}" aria-label="נגן שיר">
                <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="m8 5 11 7-11 7z"/></svg>
              </button>
            </div>
          </div>`;
      }}).join('');
    }}

    async function renderOfflinePanel() {{
      if (!offlinePanelListEl) return;
      offlinePanelListEl.innerHTML = '<div class="queue-empty">טוען...</div>';
      try {{
        const r = await fetch(apiUrl('/api/offline_list'), {{ cache: 'no-store' }});
        const data = await r.json();
        const tracks = Array.isArray(data.tracks) ? data.tracks : [];
        if (!tracks.length) {{
          offlinePanelListEl.innerHTML =
            '<div class="queue-empty">אין שירים שמורים. במסך הניגון לחצי על אייקון השמירה אצל השרת (ליד ההורדה). הקבצים נשמרים בתיקייה <code>offline_library</code> ליד השרת.</div>';
          return;
        }}
        offlinePanelListEl.innerHTML = tracks
          .map((t) => {{
            const yid = String(t.video_id || '').replace(/[^A-Za-z0-9_-]/g, '');
            const title = esc(String(t.title || 'שיר'));
            const thumb = yid ? `https://i.ytimg.com/vi/${{yid}}/mqdefault.jpg` : '';
            return `
          <div class="queue-row" data-offline-vid="${{yid}}">
            <img class="queue-thumb" src="${{thumb}}" alt="" loading="lazy" width="48" height="48" />
            <div class="queue-body">
              <div class="queue-title">${{title}}</div>
              <div class="queue-sub">מקומי אצל השרת</div>
            </div>
            <div class="queue-actions">
              <button type="button" class="danger" data-offline-del="${{yid}}">מחק</button>
              <button type="button" class="secondary queue-play-btn" data-offline-play="${{yid}}" aria-label="נגן שיר">
                <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="m8 5 11 7-11 7z"/></svg>
              </button>
            </div>
          </div>`;
          }})
          .join('');
      }} catch (e) {{
        offlinePanelListEl.innerHTML =
          '<div class="queue-empty">לא ניתן לטעון את הרשימה. ודאי שהשרת רץ.</div>';
      }}
    }}

    async function playOfflineByVid(vid) {{
      const clean = String(vid || '').replace(/[^A-Za-z0-9_-]/g, '');
      if (!clean) return;
      setStatus('טוען מהאחסון המקומי של השרת...');
      mediaShell.classList.remove('use-embed');
      try {{ ytEmbed.src = 'about:blank'; }} catch (e) {{}}
      try {{ video.pause(); }} catch (e) {{}}
      video.removeAttribute('src');
      try {{ video.load(); }} catch (e) {{}}
      try {{
        const r = await fetch(apiUrl('/api/offline_stream?vid=' + encodeURIComponent(clean)), {{ cache: 'no-store' }});
        const data = await r.json();
        if (!r.ok || data.error) throw new Error(data.error || ('HTTP ' + r.status));
        offlinePlayingVid = clean;
        meta.textContent = String(data.title || 'שמור מקומית');
        if (npArtwork) {{
          npArtwork.src = 'https://i.ytimg.com/vi/' + clean + '/hqdefault.jpg';
          npArtwork.alt = String(data.title || '');
        }}
        const rel = String(data.stream_url || '');
        video.src = rel.startsWith('/')
          ? (window.location.origin + (API_BASE || '') + rel)
          : rel;
        try {{ await video.play(); }} catch (e) {{}}
        setStatus('ניגון מהשמורים אצל השרת');
        const ix = items.findIndex((it) => getYoutubeIdFromItem(it) === clean);
        if (ix >= 0) pushRecentFromIndex(ix);
        renderRecentList();
        updateLikeButton();
        updatePlayPauseUi();
      }} catch (e) {{
        offlinePlayingVid = null;
        setStatus((e && e.message) ? String(e.message) : 'שגיאת ניגון מקומי');
      }}
    }}

    function renderPlaylistsGrid() {{
      if (!playlistsGridEl) return;
      if (!playlists.length) {{
        playlistsGridEl.innerHTML = '<div class="queue-empty">אין פלייליסטים. לחצי ״+ פלייליסט חדש״ כדי להתחיל.</div>';
        return;
      }}
      playlistsGridEl.innerHTML = playlists
        .map((p) => {{
          const cnt = (p.keys || []).length;
          return (
            '<div class="pl-card" data-plopen="' +
            esc(p.id) +
            '"><h3>' +
            esc(p.name) +
            '</h3><div class="pl-count">' +
            cnt +
            ' שירים</div></div>'
          );
        }})
        .join('');
    }}

    function renderYoutubeRecommendedPlaylists() {{
      if (!ytRecommendedPlaylistsEl) return;
      ytRecommendedPlaylistsEl.innerHTML = youtubeRecommendedPlaylists
        .map((p) => (
          '<div class="yt-rec-card">' +
          '<p class="yt-rec-title">' + esc(p.title) + '</p>' +
          '<p class="yt-rec-sub">' + esc(p.sub) + '</p>' +
          '<button type="button" class="yt-rec-open" data-yt-rec="' + esc(p.url) + '">פתח ביוטיוב</button>' +
          '</div>'
        ))
        .join('');
    }}

    function renderPlaylistSidebar() {{
      if (!playlistSidebarListEl) return;
      if (!playlists.length) {{
        playlistSidebarListEl.innerHTML = '<div class="queue-empty" style="padding:8px;font-size:0.75rem;">אין עדיין</div>';
        return;
      }}
      playlistSidebarListEl.innerHTML = playlists
        .map(
          (p) =>
            `<button type="button" class="playlist-nav-btn" data-plsidebar="${{esc(p.id)}}">${{esc(p.name)}}</button>`,
        )
        .join('');
    }}

    function renderPlaylistDetailView() {{
      if (!playlistDetailTitle || !playlistDetailTracks) return;
      const pl = playlists.find((x) => x.id === activePlaylistDetailId);
      if (!pl) {{
        playlistDetailTitle.textContent = 'פלייליסט';
        playlistDetailTracks.innerHTML = '';
        return;
      }}
      playlistDetailTitle.textContent = pl.name;
      const idxs = [];
      for (const key of pl.keys || []) {{
        const ix = items.findIndex((_, i) => likeKeyForItemAt(i) === key);
        if (ix >= 0) idxs.push(ix);
      }}
      if (!idxs.length) {{
        playlistDetailTracks.innerHTML = '<div class="queue-empty">הפלייליסט ריק. הוסיפי שירים מ״הספרייה שלי״ עם כפתור ＋.</div>';
        return;
      }}
      const curIdx = currentIndex();
      playlistDetailTracks.innerHTML = idxs
        .map((index) => {{
          const row = items[index] || {{}};
          const title = normalizeDisplayName(row, index);
          const yid = getYoutubeIdFromItem(row);
          const thumb = yid ? `https://i.ytimg.com/vi/${{yid}}/mqdefault.jpg` : '';
          const active = index === curIdx ? 'is-active' : '';
          return `
          <div class="queue-row ${{active}}" data-pick="${{index}}">
            <img class="queue-thumb" src="${{thumb}}" alt="" loading="lazy" width="48" height="48" />
            <div class="queue-body">
              <div class="queue-title">${{esc(title)}}</div>
              <div class="queue-sub">שיר ${{index + 1}}</div>
            </div>
            <div class="queue-actions">
              <button type="button" class="danger" data-plremove="${{esc(pl.id)}}" data-songkey="${{esc(likeKeyForItemAt(index))}}">הסר</button>
              <button type="button" class="secondary queue-play-btn" data-pick="${{index}}" aria-label="נגן שיר">
                <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="m8 5 11 7-11 7z"/></svg>
              </button>
            </div>
          </div>`;
        }})
        .join('');
    }}

    function playPlaylistIndices(idxs) {{
      if (!Array.isArray(idxs) || !idxs.length) return;
      favoritesOnly = false;
      try {{
        localStorage.setItem('playerFavFilter', '0');
      }} catch (e) {{}}
      syncFavoritesFilterBtn();
      searchEl.value = '';
      filteredIndices = idxs.slice();
      pos = 0;
      shuffleQueue = [];
      playbackHistory = [];
      renderQuickResults('');
      renderUpNext();
      updateLikeButton();
      saveLastAbsoluteIndex();
      loadCurrent(true);
      setStatus('ניגון פלייליסט');
    }}

    function playActivePlaylistAll() {{
      const pl = playlists.find((x) => x.id === activePlaylistDetailId);
      if (!pl) return;
      const idxs = [];
      for (const key of pl.keys || []) {{
        const ix = items.findIndex((_, i) => likeKeyForItemAt(i) === key);
        if (ix >= 0) idxs.push(ix);
      }}
      playPlaylistIndices(idxs);
    }}

    function removeSongFromPlaylist(plId, songKey) {{
      const pl = playlists.find((x) => x.id === plId);
      if (!pl || !songKey) return;
      pl.keys = (pl.keys || []).filter((k) => k !== songKey);
      savePlaylistsToStorage();
      refreshPlaylistUi();
      setStatus('הוסר מהפלייליסט');
    }}

    function addKeyToPlaylist(plId, key) {{
      if (!plId || !key) return false;
      const pl = playlists.find((x) => x.id === plId);
      if (!pl) return false;
      if (!pl.keys) pl.keys = [];
      if (pl.keys.includes(key)) {{
        setStatus('השיר כבר בפלייליסט');
        return true;
      }}
      pl.keys.push(key);
      savePlaylistsToStorage();
      refreshPlaylistUi();
      setStatus('נוסף לפלייליסט');
      return true;
    }}

    function createPlaylist(name) {{
      const n = String(name || '').trim();
      if (!n) return null;
      const id = 'pl_' + Date.now();
      playlists.push({{ id, name: n, keys: [] }});
      savePlaylistsToStorage();
      refreshPlaylistUi();
      return id;
    }}

    function refreshPlaylistUi() {{
      loadPlaylistsFromStorage();
      renderPlaylistSidebar();
      renderPlaylistsGrid();
      renderYoutubeRecommendedPlaylists();
      renderPlaylistDetailView();
      renderHomeTiles();
    }}

    function openPlOverlay(songIndex) {{
      if (!Number.isFinite(songIndex)) return;
      plOverlaySongIndex = songIndex;
      loadPlaylistsFromStorage();
      if (!plOverlaySelect) return;
      if (!playlists.length) {{
        plOverlaySelect.innerHTML = '<option value="">— אין פלייליסט —</option>';
      }} else {{
        plOverlaySelect.innerHTML = playlists
          .map((p) => {{
            const cnt = (p.keys || []).length;
            return (
              '<option value="' +
              esc(p.id) +
              '">' +
              esc(p.name) +
              ' (' +
              cnt +
              ')</option>'
            );
          }})
          .join('');
      }}
      if (plOverlay) plOverlay.classList.add('is-open');
    }}

    function closePlOverlay() {{
      if (plOverlay) plOverlay.classList.remove('is-open');
      plOverlaySongIndex = null;
    }}

    function refreshAllBrowseUi() {{
      // Keep this lightweight: only refresh always-visible counters
      // and the currently active screen, not every list in the app.
      renderHomeTiles();
      if (currentNavView === 'liked') {{
        renderLikedPanel();
      }} else if (currentNavView === 'playlists') {{
        renderPlaylistsGrid();
        renderYoutubeRecommendedPlaylists();
      }} else if (currentNavView === 'playlistDetail') {{
        renderPlaylistDetailView();
      }} else if (currentNavView === 'offline') {{
        renderOfflinePanel();
      }}
    }}

    function renderQuickResults(_q) {{
      if (!items.length) {{
        quickResultsEl.innerHTML = '<div class="queue-empty">אין שירים במערכת</div>';
        renderUpNext();
        updateLikeButton();
        return;
      }}
      if (!filteredIndices.length) {{
        quickResultsEl.innerHTML = '<div class="queue-empty">לא נמצאו שירים</div>';
        renderUpNext();
        updateLikeButton();
        return;
      }}
      const curIdx = currentIndex();
      const likes = loadLikeSet();
      quickResultsEl.innerHTML = filteredIndices.map((index) => {{
        const row = items[index] || {{}};
        const title = normalizeDisplayName(row, index);
        const yid = getYoutubeIdFromItem(row);
        const thumb = yid ? `https://i.ytimg.com/vi/${{yid}}/mqdefault.jpg` : '';
        const active = index === curIdx ? 'is-active' : '';
        const lk = likeKeyForItemAt(index);
        const liked = lk && likes.has(lk);
        return `
          <div class="queue-row ${{active}}" data-pick="${{index}}">
            <img class="queue-thumb" src="${{thumb}}" alt="" loading="lazy" width="48" height="48" />
            <div class="queue-body">
              <div class="queue-title">${{esc(title)}}</div>
              <div class="queue-sub">שיר ${{index + 1}}</div>
            </div>
            <div class="queue-actions">
              <button type="button" class="like-inline ${{liked ? 'liked' : ''}}" data-like="${{index}}" title="מועדפים" aria-label="מועדפים">
                <svg class="ico-heart-stroke" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M12 20.5s-7-4.6-7-10a4 4 0 0 1 7-2.5A4 4 0 0 1 19 10.5c0 5.4-7 10-7 10z"/></svg>
                <svg class="ico-heart-fill" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M12 20.5s-7-4.6-7-10a4 4 0 0 1 7-2.5A4 4 0 0 1 19 10.5c0 5.4-7 10-7 10z"/></svg>
              </button>
              <button type="button" class="pl-add-btn" data-pladd="${{index}}" title="הוסף לפלייליסט">＋</button>
              <button type="button" class="secondary queue-play-btn" data-pick="${{index}}" aria-label="נגן שיר">
                <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="m8 5 11 7-11 7z"/></svg>
              </button>
              <button type="button" class="danger" data-delete="${{index}}">מחק</button>
            </div>
          </div>
        `;
      }}).join('');
      renderUpNext();
      updateLikeButton();
    }}

    function showAllResults() {{
      if (!items.length) {{
        quickResultsEl.innerHTML = '<div class="queue-empty">אין שירים במערכת</div>';
        setStatus('אין שירים להצגה');
        refreshAllBrowseUi();
        return;
      }}
      filteredIndices = items.map((_, i) => i);
      renderQuickResults('');
      setStatus(`מציג ${{items.length}} שירים במערכת`);
      refreshAllBrowseUi();
    }}

    function removeItemAt(index) {{
      if (!Number.isFinite(index)) return false;
      if (index < 0 || index >= items.length) return false;
      const removedName = normalizeDisplayName(items[index], index);
      items.splice(index, 1);
      saveItems();

      if (!items.length) {{
        filteredIndices = [];
        pos = 0;
        quickResultsEl.innerHTML = '<div class="queue-empty">אין שירים במערכת</div>';
        try {{ video.pause(); }} catch (e) {{}}
        video.removeAttribute('src');
        try {{ video.load(); }} catch (e) {{}}
        try {{ ytEmbed.src = 'about:blank'; }} catch (e) {{}}
        meta.textContent = 'הרשימה ריקה';
        setStatus('השיר נמחק. אין עוד שירים ברשימה');
        refreshAllBrowseUi();
        return true;
      }}

      const q = (searchEl.value || '').trim().toLowerCase();
      if (q) {{
        rebuildFilter(true, true);
      }} else {{
        filteredIndices = items.map((_, i) => i);
        pos = Math.min(pos, filteredIndices.length - 1);
        showAllResults();
        loadCurrent(false);
      }}
      setStatus(`נמחק: ${{removedName}}`);
      refreshAllBrowseUi();
      return true;
    }}

    function repairPlaylistNames() {{
      if (!items.length) {{
        setStatus('אין שירים לתקן');
        return;
      }}
      const before = JSON.stringify(items);
      items = items
        .filter(x => x && typeof x === 'object')
        .map((x, i) => ({{
          ...x,
          name: normalizeDisplayName(x, i),
        }}));
      const changed = before !== JSON.stringify(items);
      saveItems();
      filteredIndices = items.map((_, i) => i);
      pos = Math.min(pos, Math.max(0, filteredIndices.length - 1));
      renderQuickResults((searchEl.value || '').trim().toLowerCase());
      showAllResults();
      if (changed) {{
        setStatus('הרשימה נוקתה והשמות שוחזרו בהצלחה');
      }} else {{
        setStatus('לא נמצא מה לתקן - כל השמות כבר תקינים');
      }}
      refreshAllBrowseUi();
    }}

    function updateNameFromMetadata(index, title, uploader) {{
      const row = items[index];
      if (!row || typeof row !== 'object') return;
      const t = String(title || '').trim();
      const u = String(uploader || '').trim();
      if (!t && !u) return;
      const merged = t && u ? `${{t}} / ${{u}}` : (t || u);
      const current = String(row.name || '').trim();
      if (current === merged) return;
      row.name = merged;
      saveItems();
      const qq = (searchEl.value || '').trim().toLowerCase();
      renderQuickResults(qq);
      refreshAllBrowseUi();
    }}

    async function searchYouTube() {{
      const q = (ytQueryEl.value || '').trim();
      if (!q) {{
        ytResultsEl.style.display = 'none';
        ytResultsEl.innerHTML = '';
        return;
      }}
      ytResultsEl.style.display = 'block';
      ytResultsEl.innerHTML = '<div class="yt-title">מחפש ביוטיוב...</div>';
      try {{
        const r = await fetch(apiUrl(`/api/search?q=${{encodeURIComponent(q)}}`), {{ cache: 'no-store' }});
        if (!r.ok) throw new Error(`HTTP ${{r.status}}`);
        const data = await r.json();
        const results = Array.isArray(data.results) ? data.results : [];
        if (!results.length) {{
          ytResultsEl.innerHTML = '<div class="yt-title">לא נמצאו תוצאות</div>';
          return;
        }}
        ytResultsEl.innerHTML = results.map((row, i) => `
          <div class="yt-item">
            <div class="yt-title">${{row.title || ('תוצאה ' + (i + 1))}}</div>
            <button class="secondary" data-id="${{row.id}}" data-title="${{(row.title || '').replace(/"/g, '&quot;')}}">הוסף לרשימה</button>
          </div>
        `).join('');
      }} catch (e) {{
        ytResultsEl.innerHTML = '<div class="yt-title">שגיאה בחיפוש. נסה שוב.</div>';
      }}
    }}

    function addVideoToList(id, title) {{
      if (!id) return -1;
      const existing = items.findIndex((x) => x && x.id === id);
      if (existing >= 0) {{
        setStatus('הסרטון כבר קיים ברשימה');
        return existing;
      }}
      items.push({{
        name: title ? `${{title}}` : `תוספת חדשה`,
        url: `https://www.youtube.com/watch?v=${{id}}`,
        id: id,
        type: 'video'
      }});
      saveItems();
      rebuildFilter(false, false);
      refreshAllBrowseUi();
      setStatus('נוסף לרשימה בהצלחה');
      return items.length - 1;
    }}

    function ensureVideoInLibraryByResult(result) {{
      if (!result || !result.id) return -1;
      const id = String(result.id);
      const ex = items.findIndex((x) => x && String(x.id || '') === id);
      if (ex >= 0) return ex;
      return addVideoToList(id, result.title || '');
    }}

    function ensureFavoriteAt(index) {{
      const key = likeKeyForItemAt(index);
      if (!key) return false;
      const s = loadLikeSet();
      if (!s.has(key)) {{
        s.add(key);
        saveLikeSet(s);
      }}
      return true;
    }}

    function autoDjSeedQuery() {{
      const idx = currentIndex();
      const item = items[idx];
      const raw = normalizeDisplayName(item, idx) || '';
      const base = String(raw).split('/')[0].split('-')[0].trim();
      return base || 'music mix';
    }}

    async function autoDjAppendSimilar(minNeeded = 4) {{
      if (autoDjBusy) return 0;
      autoDjBusy = true;
      try {{
        const q = autoDjSeedQuery();
        const r = await fetch(apiUrl(`/api/search?q=${{encodeURIComponent(q)}}`), {{ cache: 'no-store' }});
        if (!r.ok) return 0;
        const data = await r.json();
        const results = Array.isArray(data.results) ? data.results : [];
        let added = 0;
        for (const row of results) {{
          const id = String((row && row.id) || '').trim();
          const title = String((row && row.title) || '').trim();
          if (!id) continue;
          if (items.some((x) => x && x.id === id)) continue;
          items.push({{
            name: title || 'תוספת אוטומטית',
            url: `https://www.youtube.com/watch?v=${{id}}`,
            id: id,
            type: 'auto',
          }});
          added += 1;
          if (added >= minNeeded) break;
        }}
        if (added > 0) {{
          saveItems();
          const hasSearch = !!String(searchEl.value || '').trim();
          if (!hasSearch && !favoritesOnly) {{
            filteredIndices = items.map((_, i) => i);
          }}
          renderQuickResults((searchEl.value || '').trim().toLowerCase());
          refreshAllBrowseUi();
        }}
        return added;
      }} catch (e) {{
        return 0;
      }} finally {{
        autoDjBusy = false;
      }}
    }}

    async function loadCurrent(autoPlay = true) {{
      if (!filteredIndices.length) return;
      offlinePlayingVid = null;
      const idx = currentIndex();
      const item = items[idx];
      const q = activeQuality();
      meta.textContent = `#${{idx + 1}} - ${{normalizeDisplayName(item, idx)}}`;
      refreshNpArtwork();
      renderQuickResults((searchEl.value || '').trim().toLowerCase());
      mediaShell.classList.remove('use-embed');
      try {{ video.pause(); }} catch (e) {{}}
      video.removeAttribute('src');
      try {{ video.load(); }} catch (e) {{}}
      try {{ ytEmbed.src = 'about:blank'; }} catch (e) {{}}
      setStatus('טוען סטרים ישיר...');
      const ac = new AbortController();
      const t = setTimeout(() => ac.abort(), STREAM_TIMEOUT_MS);
      try {{
        const targetUrl = encodeURIComponent(item.url || '');
        const r = await fetch(apiUrl(`/api/stream?i=${{idx}}&quality=${{encodeURIComponent(q)}}&url=${{targetUrl}}`), {{
          cache: 'no-store',
          signal: ac.signal,
        }});
        if (!r.ok) {{
          let errText = `HTTP ${{r.status}}`;
          try {{
            const j = await r.json();
            if (j && j.error) errText = String(j.error);
          }} catch (e) {{}}
          throw new Error(errText);
        }}
        const data = await r.json();
        if (data.error) {{
          throw new Error(String(data.error));
        }}
        video.src = data.stream_url;
        const realTitle = (data.title || '').trim();
        const realArtist = (data.uploader || '').trim();
        updateNameFromMetadata(idx, realTitle, realArtist);
        if (realTitle || realArtist) {{
          meta.textContent = `#${{idx + 1}} - ${{realTitle || item.name}}${{realArtist ? ' / ' + realArtist : ''}}`;
        }}
        if (quality === 'auto') {{
          setStatus(`מוכן לניגון (אוטומטי → ${{q === 'high' ? 'גבוהה' : 'רגילה'}})`);
        }} else {{
          setStatus(`מוכן לניגון (${{q === 'high' ? 'גבוהה' : 'רגילה'}})`);
        }}
        loadFailStreak = 0;
        if (autoPlay) {{
          try {{ await video.play(); }} catch (e) {{}}
        }}
        saveLastAbsoluteIndex();
        refreshNpArtwork();
        renderQuickResults((searchEl.value || '').trim().toLowerCase());
        updatePlayPauseUi();
        pushRecentFromIndex(idx);
        renderUpNext();
        updateLikeButton();
        autoDjAppendSimilar(3);
      }} catch (e) {{
        loadFailStreak += 1;
        const raw = (e && e.name === 'AbortError') ? 'Timeout: לא התקבל סטר — בדוק אינטרנט או הוסף קובץ עוגיות' : (e && e.message) ? e.message : String(e);
        const isBot = /not a bot|sign in|Sign in|confirm|בוט|HTTP 403/i.test(raw);
        const help = isBot
          ? 'YouTube חוסם גישה בלי הזדהות. לניגון רציף ואיכותי הוסיפי ‎yt_cookies.txt‎ ליד הנגן.'
          : raw;
        setStatus(help);
        if (loadFailStreak >= 3) setStatus('כמה שירים נכשלו ברצף — מדלג אוטומטית עד לשיר תקין.');
        setTimeout(() => nextTrack(), 1400);
      }} finally {{
        clearTimeout(t);
      }}
    }}

    async function nextTrack() {{
      offlinePlayingVid = null;
      if (!filteredIndices.length) return;
      if (filteredIndices.length <= 1) {{
        const added = await autoDjAppendSimilar(6);
        if (added > 0) {{
          setStatus(`Auto-DJ: נוספו ${{added}} שירים דומים מיוטיוב`);
        }}
      }}
      if (filteredIndices.length <= 1) {{
        // If user filters are too strict, relax them so skip always advances.
        if (favoritesOnly) {{
          favoritesOnly = false;
          try {{ localStorage.setItem('playerFavFilter', '0'); }} catch (e) {{}}
          syncFavoritesFilterBtn();
        }}
        if ((searchEl.value || '').trim()) {{
          searchEl.value = '';
        }}
        filteredIndices = items.map((_, i) => i);
        if (filteredIndices.length <= 1) {{
          setStatus('אין כרגע שיר הבא. נסי שוב בעוד רגע');
          return;
        }}
        setStatus('Auto-DJ: מעבר אוטומטי לשיר הבא');
      }}
      const current = currentIndex();
      playbackHistory.push(current);
      if (playbackHistory.length > 300) playbackHistory.shift();

      if (shuffleEnabled) {{
        if (!shuffleQueue.length) {{
          rebuildShuffleQueue();
        }}
        if (shuffleQueue.length) {{
          const nextIndex = shuffleQueue.shift();
          moveToIndex(nextIndex, false);
        }} else {{
          pos = (pos + 1) % filteredIndices.length;
        }}
      }} else {{
        pos = (pos + 1) % filteredIndices.length;
      }}
      loadCurrent(true);
    }}

    function prevTrack() {{
      offlinePlayingVid = null;
      if (!filteredIndices.length) return;
      if (playbackHistory.length) {{
        const prevIndex = playbackHistory.pop();
        moveToIndex(prevIndex, false);
      }} else {{
        pos = (pos - 1 + filteredIndices.length) % filteredIndices.length;
      }}
      loadCurrent(true);
    }}

    document.getElementById('next').addEventListener('click', nextTrack);
    document.getElementById('prev').addEventListener('click', prevTrack);

    async function playPauseSafe() {{
      if (!filteredIndices.length) {{
        // Recover from strict filters so Play always has something to start.
        if (favoritesOnly) {{
          favoritesOnly = false;
          try {{ localStorage.setItem('playerFavFilter', '0'); }} catch (e) {{}}
          syncFavoritesFilterBtn();
        }}
        if ((searchEl.value || '').trim()) {{
          searchEl.value = '';
        }}
        filteredIndices = items.map((_, i) => i);
        pos = 0;
        renderQuickResults('');
        if (!filteredIndices.length) {{
          setStatus('אין שירים לניגון');
          return;
        }}
      }}
      if (mediaShell.classList.contains('use-embed')) {{
        mediaShell.classList.remove('use-embed');
        try {{ ytEmbed.src = 'about:blank'; }} catch (e) {{}}
        await loadCurrent(true);
        setStatus('ניגון פעיל');
        updatePlayPauseUi();
        return;
      }}
      if (!video.src) {{
        // Audio-first path for reliable playlist playback.
        await loadCurrent(true);
        updatePlayPauseUi();
        return;
      }}
      if (video.paused) {{
        try {{
          try {{ video.muted = false; }} catch (e) {{}}
          if (!Number.isFinite(Number(volumeBar.value)) || Number(volumeBar.value) <= 0) {{
            volumeBar.value = '100';
          }}
          try {{ video.volume = Math.max(0.05, Math.min(1, Number(volumeBar.value) / 100)); }} catch (e) {{}}
          await video.play();
          setStatus('ניגון פעיל');
        }} catch (e) {{
          setStatus('לא ניתן להתחיל ניגון לשיר הזה, עוברת לשיר הבא...');
          await nextTrack();
        }}
      }} else {{
        try {{ video.pause(); }} catch (e) {{}}
        setStatus('מושהה');
      }}
      updatePlayPauseUi();
    }}

    document.getElementById('play').addEventListener('click', playPauseSafe);

    repeatBtn.addEventListener('click', () => {{
      repeatMode = repeatMode === 'off' ? 'all' : repeatMode === 'all' ? 'one' : 'off';
      localStorage.setItem(REPEAT_KEY, repeatMode);
      updateRepeatButton();
    }});

    progressBar.addEventListener('input', () => {{
      isSeekingProgress = true;
      const pct = Number(progressBar.value) / 1000;
      if (video.duration) {{
        video.currentTime = pct * video.duration;
        const g = Math.round(pct * 100);
        progressBar.style.background = `linear-gradient(90deg, var(--accent) ${{g}}%, #444 ${{g}}%)`;
        timeCurrentEl.textContent = formatTime(video.currentTime);
      }}
    }});
    progressBar.addEventListener('change', () => {{ isSeekingProgress = false; }});

    volumeBar.addEventListener('input', () => {{
      updateVolumeUi();
    }});

    video.addEventListener('play', () => updatePlayPauseUi());
    video.addEventListener('playing', () => updatePlayPauseUi());
    video.addEventListener('pause', () => updatePlayPauseUi());
    video.addEventListener('loadedmetadata', () => {{
      timeTotalEl.textContent = formatTime(video.duration);
      syncProgressFromVideo();
    }});
    video.addEventListener('timeupdate', () => syncProgressFromVideo());
    document.getElementById('downloadTrackBtn').addEventListener('click', () => {{
      if (!filteredIndices.length) return;
      const idx = currentIndex();
      const item = items[idx];
      if (!item || !item.url) {{
        setStatus('אין קישור להורדה');
        return;
      }}
      const q = activeQuality();
      const targetUrl = encodeURIComponent(item.url || '');
      const u = apiUrl(`/api/download_track?i=${{idx}}&quality=${{encodeURIComponent(q)}}&url=${{targetUrl}}`);
      setStatus('מכין הורדה...');
      try {{
        const a = document.createElement('a');
        a.href = u;
        a.rel = 'noopener';
        a.target = '_blank';
        document.body.appendChild(a);
        a.click();
        a.remove();
      }} catch (e) {{
        window.open(u, '_blank', 'noopener');
      }}
      setTimeout(() => setStatus('אם ההורדה לא התחילה — בדקי חוסם חלונות קופצים'), 1200);
    }});
    if (saveOfflineBtn) {{
      saveOfflineBtn.addEventListener('click', async () => {{
        if (!filteredIndices.length) return;
        const idx = currentIndex();
        const item = items[idx];
        if (!item || !item.url) {{
          setStatus('אין קישור לשמירה');
          return;
        }}
        const q = activeQuality();
        const targetUrl = encodeURIComponent(item.url || '');
        const u = apiUrl(`/api/offline_save?i=${{idx}}&quality=${{encodeURIComponent(q)}}&url=${{targetUrl}}`);
        saveOfflineBtn.disabled = true;
        setStatus('שומרת אצל השרת... (עשוי לקחת דקות לפי גודל השיר)');
        try {{
          const r = await fetch(u, {{ cache: 'no-store' }});
          const data = await r.json().catch(() => ({{}}));
          if (!r.ok || data.error) throw new Error(data.error || `HTTP ${{r.status}}`);
          setStatus('נשמר בתיקיית offline_library — דף "שמורים אצל השרת"');
          if (currentNavView === 'offline') renderOfflinePanel();
          renderHomeTiles();
        }} catch (e) {{
          setStatus((e && e.message) ? String(e.message) : 'שמירה אצל השרת נכשלה');
        }} finally {{
          saveOfflineBtn.disabled = false;
        }}
      }});
    }}
    if (fullscreenBtn) {{
      fullscreenBtn.addEventListener('click', async () => {{
        const fs = !!getFullscreenElement();
        if (fs) {{
          await exitAnyFullscreen();
          updateFullscreenButtonUi();
          return;
        }}
        // Prefer fullscreen on the media container; fallback to document root.
        const ok = await requestElementFullscreen(mediaShell || document.documentElement);
        if (!ok) setStatus('לא ניתן לפתוח מסך מלא במכשיר זה');
        updateFullscreenButtonUi();
      }});
      document.addEventListener('fullscreenchange', updateFullscreenButtonUi);
      document.addEventListener('webkitfullscreenchange', updateFullscreenButtonUi);
      updateFullscreenButtonUi();
    }}
    if (eqToggleBtn && eqDrawer) {{
      eqToggleBtn.addEventListener('click', () => {{
        const isOpen = eqDrawer.classList.toggle('is-open');
        eqToggleBtn.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
      }});
    }}
    qualityEl.addEventListener('change', async () => {{
      quality = ['auto', 'high', 'normal'].includes(qualityEl.value) ? qualityEl.value : 'auto';
      localStorage.setItem('playerQuality', quality);
      await loadCurrent(true);
    }});
    // Input typing should only filter UI; avoid autoplay/reload per keystroke.
    searchEl.addEventListener('input', () => {{
      rebuildFilter(false, true);
      if (globalSearchTimer) clearTimeout(globalSearchTimer);
      const q = (searchEl.value || '').trim();
      if (!q) {{
        clearGlobalSearchDropdown();
        return;
      }}
      globalSearchTimer = setTimeout(() => {{
        searchYouTubeFromTop(q);
      }}, 260);
    }});
    searchEl.addEventListener('keypress', (e) => {{
      if (e.key === 'Enter' && filteredIndices.length) {{
        e.preventDefault();
        pos = 0;
        loadCurrent(true);
        clearGlobalSearchDropdown();
      }}
    }});
    if (ytSearchPageListEl) {{
      ytSearchPageListEl.addEventListener('click', (e) => {{
        const plusBtn = e.target.closest('[data-sp-more]');
        if (plusBtn) {{
          const id = plusBtn.getAttribute('data-sp-more') || '';
          openSearchActionOverlay(id);
          return;
        }}
        const rowEl = e.target.closest('[data-sp-row]');
        if (!rowEl) return;
        const id = rowEl.getAttribute('data-sp-row') || '';
        const row = getSearchResultById(id);
        const idx = ensureVideoInLibraryByResult(row);
        if (idx >= 0) {{
          playAbsoluteIndex(idx);
          setStatus('ניגון');
        }}
      }});
      ytSearchPageListEl.addEventListener('keydown', (e) => {{
        if (e.key !== 'Enter' && e.key !== ' ') return;
        if (e.target.closest('[data-sp-more]')) return;
        const rowEl = e.target.closest('[data-sp-row]');
        if (!rowEl) return;
        e.preventDefault();
        rowEl.click();
      }});
    }}
    if (searchActClose) searchActClose.addEventListener('click', () => closeSearchActionOverlay());
    if (searchActionOverlay) {{
      searchActionOverlay.addEventListener('click', (e) => {{
        if (e.target === searchActionOverlay) closeSearchActionOverlay();
      }});
    }}
    if (searchActPlayNow) {{
      searchActPlayNow.addEventListener('click', () => {{
        const row = getSearchResultById(searchActionTargetId);
        const idx = ensureVideoInLibraryByResult(row);
        if (idx >= 0) playAbsoluteIndex(idx);
        closeSearchActionOverlay();
      }});
    }}
    if (searchActQueue) {{
      searchActQueue.addEventListener('click', () => {{
        const row = getSearchResultById(searchActionTargetId);
        const idx = ensureVideoInLibraryByResult(row);
        if (idx >= 0) setStatus('נוסף לרשימת ההשמעה');
        closeSearchActionOverlay();
      }});
    }}
    if (searchActFav) {{
      searchActFav.addEventListener('click', () => {{
        const row = getSearchResultById(searchActionTargetId);
        const idx = ensureVideoInLibraryByResult(row);
        if (idx >= 0 && ensureFavoriteAt(idx)) {{
          setStatus('נוסף למועדפים');
          refreshAllBrowseUi();
          updateLikeButton();
        }}
        closeSearchActionOverlay();
      }});
    }}
    if (searchActPlaylist) {{
      searchActPlaylist.addEventListener('click', () => {{
        const row = getSearchResultById(searchActionTargetId);
        const idx = ensureVideoInLibraryByResult(row);
        if (idx >= 0) openPlOverlay(idx);
        closeSearchActionOverlay();
      }});
    }}
    quickResultsEl.addEventListener('click', (e) => {{
      const plAdd = e.target.closest('button[data-pladd]');
      if (plAdd) {{
        e.stopPropagation();
        const ix = parseInt(plAdd.getAttribute('data-pladd') || '', 10);
        if (Number.isFinite(ix)) openPlOverlay(ix);
        return;
      }}
      const likeB = e.target.closest('button[data-like]');
      if (likeB) {{
        e.stopPropagation();
        const lix = parseInt(likeB.getAttribute('data-like') || '', 10);
        if (Number.isFinite(lix)) {{
          toggleLikeAtIndex(lix);
          if (favoritesOnly) rebuildFilter(false, false);
          else renderQuickResults((searchEl.value || '').trim().toLowerCase());
          updateLikeButton();
          refreshAllBrowseUi();
        }}
        return;
      }}
      const deleteBtn = e.target.closest('button[data-delete]');
      if (deleteBtn) {{
        const delIndex = parseInt(deleteBtn.getAttribute('data-delete') || '', 10);
        const okDelete = removeItemAt(delIndex);
        if (!okDelete) setStatus('לא ניתן למחוק את השיר');
        return;
      }}
      const source = e.target.closest('[data-pick]');
      if (!source) return;
      const pick = parseInt(source.getAttribute('data-pick') || '', 10);
      const ok = playAbsoluteIndex(pick);
      setStatus(ok ? 'שיר נבחר מהרשימה' : 'לא ניתן לטעון את השיר שנבחר');
    }});
    showAllBtn.addEventListener('click', () => showAllResults());
    repairNamesBtn.addEventListener('click', () => repairPlaylistNames());
    shuffleBtn.addEventListener('click', () => {{
      shuffleEnabled = !shuffleEnabled;
      localStorage.setItem(SHUFFLE_KEY, String(shuffleEnabled));
      shuffleQueue = [];
      playbackHistory = [];
      if (shuffleEnabled) {{
        rebuildShuffleQueue();
      }}
      updateShuffleButton();
    }});
    function setPlayerUiMode(next) {{
      uiMode = next === 'car' ? 'car' : 'normal';
      modeEl.value = uiMode;
      localStorage.setItem(MODE_KEY, uiMode);
      applyUiMode();
    }}
    modeEl.addEventListener('change', () => {{
      setPlayerUiMode(modeEl.value);
    }});
    if (exitCarModeBtn) {{
      exitCarModeBtn.addEventListener('click', () => {{
        setPlayerUiMode('normal');
      }});
    }}
    ytSearchBtn.addEventListener('click', () => searchYouTube());
    ytQueryEl.addEventListener('keypress', (e) => {{
      if (e.key === 'Enter') {{
        e.preventDefault();
        searchYouTube();
      }}
    }});
    ytResultsEl.addEventListener('click', (e) => {{
      const btn = e.target.closest('button[data-id]');
      if (!btn) return;
      const id = btn.getAttribute('data-id');
      const title = btn.getAttribute('data-title') || '';
      addVideoToList(id, title);
    }});

    if (likeBtn) {{
      likeBtn.addEventListener('click', (e) => {{
        e.stopPropagation();
        const idx = currentIndex();
        toggleLikeAtIndex(idx);
        if (favoritesOnly) rebuildFilter(false, false);
        else renderQuickResults((searchEl.value || '').trim().toLowerCase());
        updateLikeButton();
        refreshAllBrowseUi();
      }});
    }}

    if (npSummaryRow) {{
      npSummaryRow.addEventListener('click', (e) => {{
        if (e.target.closest('#likeBtn') || e.target.closest('#npQuickPlayBtn') || e.target.closest('#npPageCloseBtn')) return;
        const isCollapsed = nowPlayingCard ? nowPlayingCard.classList.contains('is-collapsed') : true;
        setNowPlayingExpanded(isCollapsed);
        if (isCollapsed) ensureSongPageClipPlayback();
      }});
      npSummaryRow.addEventListener('keydown', (e) => {{
        if (e.key !== 'Enter' && e.key !== ' ') return;
        e.preventDefault();
        const isCollapsed = nowPlayingCard ? nowPlayingCard.classList.contains('is-collapsed') : true;
        setNowPlayingExpanded(isCollapsed);
        if (isCollapsed) ensureSongPageClipPlayback();
      }});
      setNowPlayingExpanded(false);
    }}
    if (npQuickPlayBtn) {{
      npQuickPlayBtn.addEventListener('click', async (e) => {{
        e.stopPropagation();
        await playPauseSafe();
      }});
    }}
    initEqUi();
    if (npPageCloseBtn) {{
      npPageCloseBtn.addEventListener('click', (e) => {{
        e.stopPropagation();
        setNowPlayingExpanded(false);
      }});
    }}

    if (favoritesOnlyBtn) {{
      favoritesOnlyBtn.addEventListener('click', () => {{
        favoritesOnly = !favoritesOnly;
        try {{
          localStorage.setItem('playerFavFilter', favoritesOnly ? '1' : '0');
        }} catch (err) {{}}
        syncFavoritesFilterBtn();
        rebuildFilter(true, true);
      }});
    }}

    if (upNextListEl) {{
      upNextListEl.addEventListener('click', (e) => {{
        const row = e.target.closest('.up-next-row[data-pick]');
        if (!row) return;
        const pick = parseInt(row.getAttribute('data-pick') || '', 10);
        if (!Number.isFinite(pick)) return;
        if (playAbsoluteIndex(pick)) setStatus('נבחר מהתור');
      }});
    }}

    if (recentListEl) {{
      recentListEl.addEventListener('click', (e) => {{
        const row = e.target.closest('.recent-row[data-pick]');
        if (!row) return;
        const pick = parseInt(row.getAttribute('data-pick') || '', 10);
        if (Number.isFinite(pick)) playAbsoluteIndex(pick);
      }});
    }}

    document.addEventListener('keydown', (e) => {{
      const t = e.target;
      const tag = (t && t.tagName) || '';
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || (t && t.isContentEditable)) return;
      if (e.code === 'Space') {{
        e.preventDefault();
        document.getElementById('play').click();
      }} else if (e.code === 'ArrowRight') {{
        e.preventDefault();
        nextTrack();
      }} else if (e.code === 'ArrowLeft') {{
        e.preventDefault();
        prevTrack();
      }}
    }});

    video.addEventListener('ended', () => {{
      if (repeatMode === 'one') {{
        try {{ video.currentTime = 0; }} catch (e) {{}}
        loadCurrent(true);
        return;
      }}
      if (repeatMode === 'off') {{
        setStatus('ניגון הסתיים');
        updatePlayPauseUi();
        return;
      }}
      nextTrack();
    }});
    video.addEventListener('error', () => {{
      if (mediaShell.classList.contains('use-embed')) return;
      if (offlinePlayingVid) {{
        setStatus('שגיאת ניגון בקובץ המקומי');
        offlinePlayingVid = null;
        updateLikeButton();
        return;
      }}
      if (quality === 'auto' || quality === 'high') {{
        setStatus('הסטרים נפל, מנסה איכות רגילה...');
        quality = 'normal';
        qualityEl.value = 'normal';
        localStorage.setItem('playerQuality', quality);
        setTimeout(() => loadCurrent(true), 700);
        return;
      }}
      setStatus('הסטרים נפל, מנסה את השיר הבא...');
      setTimeout(() => nextTrack(), 1000);
    }});

    document.querySelectorAll('[data-nav-target]').forEach((btn) => {{
      btn.addEventListener('click', () => {{
        const v = btn.getAttribute('data-nav-target');
        if (v) setNav(v);
      }});
    }});
    if (homeTilesEl) {{
      homeTilesEl.addEventListener('click', (e) => {{
        const t = e.target.closest('[data-go]');
        if (!t) return;
        const g = t.getAttribute('data-go');
        if (g) setNav(g);
      }});
    }}
    if (offlinePanelListEl) {{
      offlinePanelListEl.addEventListener('click', async (e) => {{
        const delBtn = e.target.closest('button[data-offline-del]');
        if (delBtn) {{
          e.stopPropagation();
          const vid = delBtn.getAttribute('data-offline-del') || '';
          if (!vid || !confirm('למחוק את השיר מהאחסון של השרת?')) return;
          try {{
            const r = await fetch(apiUrl('/api/offline_delete?vid=' + encodeURIComponent(vid)), {{ cache: 'no-store' }});
            const j = await r.json().catch(() => ({{}}));
            if (!r.ok || j.error) throw new Error(j.error || r.status);
            setStatus('נמחק מהשמורים');
            renderOfflinePanel();
            renderHomeTiles();
          }} catch (err) {{
            setStatus((err && err.message) ? String(err.message) : 'מחיקה נכשלה');
          }}
          return;
        }}
        const playBtn = e.target.closest('button[data-offline-play]');
        if (playBtn) {{
          e.stopPropagation();
          const vid = playBtn.getAttribute('data-offline-play') || '';
          if (vid) await playOfflineByVid(vid);
          return;
        }}
        const row = e.target.closest('[data-offline-vid]');
        if (row) {{
          const vid = row.getAttribute('data-offline-vid') || '';
          if (vid) await playOfflineByVid(vid);
        }}
      }});
    }}
    if (playlistSidebarListEl) {{
      playlistSidebarListEl.addEventListener('click', (e) => {{
        const b = e.target.closest('[data-plsidebar]');
        if (!b) return;
        const id = b.getAttribute('data-plsidebar');
        if (id) setNav('playlistDetail', id);
      }});
    }}
    if (playlistsGridEl) {{
      playlistsGridEl.addEventListener('click', (e) => {{
        const c = e.target.closest('[data-plopen]');
        if (!c) return;
        const id = c.getAttribute('data-plopen');
        if (id) setNav('playlistDetail', id);
      }});
    }}
    if (ytRecommendedPlaylistsEl) {{
      ytRecommendedPlaylistsEl.addEventListener('click', (e) => {{
        const btn = e.target.closest('[data-yt-rec]');
        if (!btn) return;
        const url = btn.getAttribute('data-yt-rec');
        if (!url) return;
        window.open(url, '_blank', 'noopener');
      }});
    }}
    if (likedPanelListEl) {{
      likedPanelListEl.addEventListener('click', (e) => {{
        const lb = e.target.closest('button[data-like]');
        if (lb) {{
          e.stopPropagation();
          const lix = parseInt(lb.getAttribute('data-like') || '', 10);
          if (Number.isFinite(lix)) {{
            toggleLikeAtIndex(lix);
            renderLikedPanel();
            renderQuickResults((searchEl.value || '').trim().toLowerCase());
            updateLikeButton();
            refreshAllBrowseUi();
          }}
          return;
        }}
        const row = e.target.closest('[data-pick]');
        if (!row) return;
        const pick = parseInt(row.getAttribute('data-pick') || '', 10);
        if (Number.isFinite(pick)) playAbsoluteIndex(pick);
      }});
    }}
    if (playlistDetailTracks) {{
      playlistDetailTracks.addEventListener('click', (e) => {{
        const rm = e.target.closest('button[data-plremove]');
        if (rm) {{
          e.stopPropagation();
          const plId = rm.getAttribute('data-plremove');
          const sk = rm.getAttribute('data-songkey');
          if (plId && sk) removeSongFromPlaylist(plId, sk);
          return;
        }}
        const row = e.target.closest('[data-pick]');
        if (!row) return;
        const pick = parseInt(row.getAttribute('data-pick') || '', 10);
        if (Number.isFinite(pick)) playAbsoluteIndex(pick);
      }});
    }}
    if (newPlaylistBtn) {{
      newPlaylistBtn.addEventListener('click', () => {{
        const n = prompt('שם לפלייליסט חדש:');
        if (!n || !String(n).trim()) return;
        createPlaylist(String(n).trim());
        setNav('playlists');
      }});
    }}
    if (backFromPlaylist) {{
      backFromPlaylist.addEventListener('click', () => setNav('playlists'));
    }}
    if (navBackFab) {{
      navBackFab.addEventListener('click', () => {{
        if (currentNavView === 'playlistDetail') {{
          setNav('playlists');
          return;
        }}
        setNav('home');
      }});
    }}
    if (playPlaylistBtn) {{
      playPlaylistBtn.addEventListener('click', () => playActivePlaylistAll());
    }}
    if (plOverlayClose) {{
      plOverlayClose.addEventListener('click', () => closePlOverlay());
    }}
    if (plOverlayNew) {{
      plOverlayNew.addEventListener('click', () => {{
        const n = prompt('שם לפלייליסט חדש:');
        if (!n || !String(n).trim()) return;
        createPlaylist(String(n).trim());
        if (plOverlaySongIndex !== null) openPlOverlay(plOverlaySongIndex);
      }});
    }}
    if (plOverlayConfirm) {{
      plOverlayConfirm.addEventListener('click', () => {{
        if (plOverlaySongIndex === null) return;
        const plId = plOverlaySelect ? plOverlaySelect.value : '';
        const key = likeKeyForItemAt(plOverlaySongIndex);
        if (!plId || !playlists.some((p) => p.id === plId)) {{
          setStatus('בחרי פלייליסט מהרשימה');
          return;
        }}
        if (key) addKeyToPlaylist(plId, key);
        closePlOverlay();
      }});
    }}
    if (plOverlay) {{
      plOverlay.addEventListener('click', (e) => {{
        if (e.target === plOverlay) closePlOverlay();
      }});
    }}

    function hardReloadFromServer() {{
      const go = () => {{
        const u = new URL(window.location.href);
        u.searchParams.set('_reload', String(Date.now()));
        window.location.replace(u.toString());
      }};
      try {{
        if (window.caches && window.caches.keys) {{
          window.caches
            .keys()
            .then((ks) => Promise.all(ks.map((k) => window.caches.delete(k))))
            .catch(() => {{}})
            .finally(go);
        }} else {{
          go();
        }}
      }} catch (e) {{
        go();
      }}
    }}

    async function clearPwaCachesAndServiceWorker() {{
      try {{
        if ('serviceWorker' in navigator) {{
          const regs = await navigator.serviceWorker.getRegistrations();
          await Promise.all(regs.map((r) => r.unregister()));
        }}
      }} catch (e) {{}}
      try {{
        if (window.caches && window.caches.keys) {{
          const ks = await window.caches.keys();
          await Promise.all(ks.map((k) => window.caches.delete(k)));
        }}
      }} catch (e) {{}}
      const u = new URL(window.location.href);
      u.searchParams.set('_swcleared', String(Date.now()));
      window.location.replace(u.toString());
    }}

    (function setupHomeSettings() {{
      const overlay = document.getElementById('homeSettingsOverlay');
      const openBtn = document.getElementById('homeSettingsBtn');
      const closeBtn = document.getElementById('homeSettingsCloseBtn');
      const h = document.getElementById('settingsHardRefreshBtn');
      const lan = document.getElementById('settingsLanQrBtn');
      const ts = document.getElementById('settingsTailscaleBtn');
      const swBtn = document.getElementById('settingsClearCacheSwBtn');
      const trigLan = document.getElementById('lanPhoneBtn');
      const trigTs = document.getElementById('tailscaleHelpBtn');
      if (!overlay || !openBtn) return;
      function openHs() {{ overlay.classList.add('is-open'); }}
      function closeHs() {{ overlay.classList.remove('is-open'); }}
      openBtn.addEventListener('click', openHs);
      if (closeBtn) closeBtn.addEventListener('click', closeHs);
      overlay.addEventListener('click', (e) => {{ if (e.target === overlay) closeHs(); }});
      if (h) h.addEventListener('click', () => hardReloadFromServer());
      if (lan && trigLan) lan.addEventListener('click', () => trigLan.click());
      if (ts && trigTs) ts.addEventListener('click', () => trigTs.click());
      if (swBtn) swBtn.addEventListener('click', () => {{ closeHs(); clearPwaCachesAndServiceWorker(); }});
    }})();

    const stripReloadBtn = document.getElementById('stripReloadBtn');
    if (stripReloadBtn) {{
      stripReloadBtn.addEventListener('click', () => hardReloadFromServer());
    }}

    (function setupLanPhoneQr() {{
      const url = typeof UNBLOCKED_LAN_URL === 'string' ? UNBLOCKED_LAN_URL : '';
      const btn = document.getElementById('lanPhoneBtn');
      const overlay = document.getElementById('lanPhoneOverlay');
      const closeBtn = document.getElementById('lanPhoneClose');
      const qMount = document.getElementById('lanQrMount');
      const urlText = document.getElementById('lanUrlText');
      const copyBtn = document.getElementById('lanUrlCopyBtn');
      if (!url || !btn || !overlay || !qMount || !urlText) return;
      urlText.textContent = url;
      var qrInited = false;
      function loadScript(src, onload) {{
        var s = document.createElement('script');
        s.src = src;
        s.async = true;
        s.onload = function () {{ if (onload) onload(); }};
        document.head.appendChild(s);
      }}
      function renderQr() {{
        try {{
          qMount.innerHTML = '';
        }} catch (e) {{ return; }}
        try {{
          if (typeof QRCode === 'undefined') return;
          var level = 0;
          try {{
            if (QRCode.CorrectLevel && typeof QRCode.CorrectLevel.M === 'number') level = QRCode.CorrectLevel.M;
          }} catch (e) {{ level = 0; }}
          new QRCode(qMount, {{ text: url, width: 200, height: 200, colorDark: '#0a0e12', colorLight: '#ffffff', correctLevel: level }});
        }} catch (e) {{}}
        qrInited = true;
      }}
      function openLan() {{
        overlay.classList.add('is-open');
        if (qrInited) return;
        if (typeof QRCode === 'undefined') {{
          loadScript('https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js', function () {{ renderQr(); }});
        }} else {{
          renderQr();
        }}
      }}
      function closeLan() {{
        overlay.classList.remove('is-open');
      }}
      btn.addEventListener('click', openLan);
      if (closeBtn) closeBtn.addEventListener('click', closeLan);
      overlay.addEventListener('click', function (e) {{ if (e.target === overlay) closeLan(); }});
      if (copyBtn) {{
        copyBtn.addEventListener('click', function () {{
          if (!url) return;
          if (navigator.clipboard && navigator.clipboard.writeText) {{
            navigator.clipboard.writeText(url).then(function () {{
              copyBtn.textContent = 'הועתק';
              setTimeout(function () {{ copyBtn.textContent = 'העתק'; }}, 1500);
            }});
            return;
          }}
          try {{
            const ta = document.createElement('textarea');
            ta.value = url;
            document.body.appendChild(ta);
            ta.select();
            document.execCommand('copy');
            document.body.removeChild(ta);
            copyBtn.textContent = 'הועתק';
            setTimeout(function () {{ copyBtn.textContent = 'העתק'; }}, 1500);
          }} catch (e) {{}}
        }});
      }}
    }})();

    (function setupTailscaleHelp() {{
      const btn = document.getElementById('tailscaleHelpBtn');
      const overlay = document.getElementById('tailscaleOverlay');
      const closeBtn = document.getElementById('tailscaleCloseBtn');
      const copyTpl = document.getElementById('tsTemplateCopyBtn');
      const tplEl = document.getElementById('tsUrlTemplate');
      if (!btn || !overlay) return;
      function openTs() {{
        overlay.classList.add('is-open');
      }}
      function closeTs() {{
        overlay.classList.remove('is-open');
      }}
      btn.addEventListener('click', openTs);
      if (closeBtn) closeBtn.addEventListener('click', closeTs);
      overlay.addEventListener('click', function (e) {{
        if (e.target === overlay) closeTs();
      }});
      if (copyTpl && tplEl) {{
        copyTpl.addEventListener('click', function () {{
          var t = (tplEl.textContent || '').trim();
          if (!t) return;
          if (navigator.clipboard && navigator.clipboard.writeText) {{
            navigator.clipboard.writeText(t).then(function () {{
              copyTpl.textContent = 'הועתק';
              setTimeout(function () {{ copyTpl.textContent = 'העתק דוגמה'; }}, 1600);
            }});
            return;
          }}
          try {{
            const ta = document.createElement('textarea');
            ta.value = t;
            document.body.appendChild(ta);
            ta.select();
            document.execCommand('copy');
            document.body.removeChild(ta);
            copyTpl.textContent = 'הועתק';
            setTimeout(function () {{ copyTpl.textContent = 'העתק דוגמה'; }}, 1600);
          }} catch (e) {{}}
        }});
      }}
    }})();

    (function pwaInit() {{
      if ('serviceWorker' in navigator) {{
        navigator.serviceWorker.register('unblocked-sw.js', {{ scope: './' }}).catch(function () {{}});
      }}
      var PWA_DISMISS = 'pwaInstallModalDismissed';
      var installPromptEvent = null;
      var pwaModalShown = false;
      var modal = document.getElementById('pwaInstallModal');
      var modalTitle = document.getElementById('pwaModalTitle');
      var modalBody = document.getElementById('pwaModalBody');
      var btnConfirm = document.getElementById('pwaModalConfirm');
      var btnDecline = document.getElementById('pwaModalDecline');

      function isDismissed() {{
        try {{ return localStorage.getItem(PWA_DISMISS) === '1'; }} catch (e) {{ return false; }}
      }}
      function setDismissed() {{
        try {{ localStorage.setItem(PWA_DISMISS, '1'); }} catch (e) {{}}
      }}
      function mobileOk() {{
        return /Mobi|Android|iPhone|iPad|iPod/i.test(navigator.userAgent);
      }}
      function standaloneOk() {{
        return (
          (window.matchMedia && window.matchMedia('(display-mode: standalone)').matches) ||
          window.navigator.standalone === true
        );
      }}
      var isIOS =
        /iPhone|iPad|iPod/i.test(navigator.userAgent) ||
        (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);

      function closeModal() {{
        if (!modal) return;
        modal.classList.remove('is-open');
        modal.setAttribute('hidden', '');
        pwaModalShown = false;
      }}
      function openModal(native) {{
        if (!modal || !modalTitle || !modalBody || !btnConfirm || !btnDecline) return;
        if (isDismissed() || standaloneOk()) return;
        pwaModalShown = true;
        modal.removeAttribute('hidden');
        modal.classList.add('is-open');
        if (native && installPromptEvent) {{
          modalTitle.textContent = 'להתקין את הנגן על הטלפון?';
          modalBody.innerHTML =
            'הדפדפן יכול להוסיף אייקון על <strong>מסך הבית</strong> — נוח לפתיחה מהירה כשהמחשב שרץ את השרת דולק.';
          btnConfirm.textContent = 'כן, התקן';
          btnConfirm.style.display = '';
        }} else {{
          modalTitle.textContent = 'להוסיף את הנגן למסך הבית?';
          if (isIOS) {{
            modalBody.innerHTML =
              'ב־<strong>Safari</strong>: לחצי על <span dir="ltr">שיתוף</span> (↑) ואז <strong>הוסף למסך הבית</strong>.';
          }} else {{
            modalBody.innerHTML =
              'ב־Chrome/Edge: תפריט <span dir="ltr">⋮</span> → <strong>התקן אפליקציה</strong> / <strong>הוסף למסך הבית</strong>. אם אין כפתור — ייתכן שרשת HTTP (לא HTTPS) מגבילה; אפשר עדיין להוסיף קיצור דרך מהתפריט.';
          }}
          btnConfirm.textContent = 'הבנתי';
          btnConfirm.style.display = '';
        }}
        btnConfirm.onclick = function () {{
          if (native && installPromptEvent) {{
            var p = installPromptEvent;
            installPromptEvent = null;
            p.prompt();
            p.userChoice.then(function () {{ setDismissed(); closeModal(); }}).catch(function () {{ setDismissed(); closeModal(); }});
          }} else {{
            setDismissed();
            closeModal();
          }}
        }};
      }}
      btnDecline.addEventListener('click', function () {{
        setDismissed();
        closeModal();
      }});
      if (modal) {{
        modal.addEventListener('click', function (e) {{
          if (e.target && e.target.getAttribute && e.target.getAttribute('data-pwa-dismiss')) {{
            setDismissed();
            closeModal();
          }}
        }});
      }}

      window.addEventListener('beforeinstallprompt', function (e) {{
        e.preventDefault();
        installPromptEvent = e;
        if (isDismissed() || standaloneOk() || !mobileOk()) return;
        openModal(true);
      }});

      if (isIOS && mobileOk() && !standaloneOk() && !isDismissed()) {{
        setTimeout(function () {{
          if (isDismissed() || standaloneOk() || pwaModalShown) return;
          openModal(false);
        }}, 450);
      }} else if (!isIOS) {{
        setTimeout(function () {{
          if (isDismissed() || standaloneOk() || !mobileOk() || pwaModalShown) return;
          if (!installPromptEvent) openModal(false);
        }}, 1800);
      }}
    }})();

    loadItems();
    refreshPlaylistUi();
    applyUiMode();
    updateShuffleButton();
    updateRepeatButton();
    try {{ video.volume = 1; }} catch (e) {{}}
    volumeBar.value = '100';
    updateVolumeUi();
    syncFavoritesFilterBtn();
    rebuildFilter(false, false);
    renderRecentList();
    setNav('home');
  </script>
</body>
</html>"""


def resolve_stream_url(target_url, quality="high", cache_hint=""):
    if not target_url or "youtube.com" not in target_url and "youtu.be" not in target_url:
        raise ValueError("invalid youtube url")

    now = time.time()
    quality = "normal" if quality == "normal" else "high"
    cache_key = (cache_hint or target_url, quality)
    cached = STREAM_CACHE.get(cache_key)
    if cached and (now - cached["ts"] < CACHE_TTL_SECONDS):
        return cached

    target = target_url
    # IMPORTANT: browser <video> needs a single progressive stream that already
    # contains both video+audio. "bestvideo+bestaudio" returns separate tracks
    # (DASH) and may cause video with no sound.
    # Prefer browser-friendly MP4/H264/AAC first to avoid "loads but won't play"
    # behavior that can happen with some WebM/VP9 streams on specific clients.
    format_selector = (
        "best[height<=480][ext=mp4][vcodec*=avc1][acodec*=mp4a]/"
        "best[height<=480][ext=mp4][vcodec!=none][acodec!=none]/"
        "best[height<=480][vcodec!=none][acodec!=none]/"
        "best[ext=mp4][vcodec!=none][acodec!=none]/"
        "best[vcodec!=none][acodec!=none]"
        if quality == "normal"
        else
        "best[ext=mp4][vcodec*=avc1][acodec*=mp4a]/"
        "best[ext=mp4][vcodec!=none][acodec!=none]/"
        "best[vcodec!=none][acodec!=none]"
    )
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "format": format_selector,
        "extract_flat": False,
        "socket_timeout": 30,
        "retries": 2,
        "extractor_args": YOUTUBE_EXTRACTOR_ARGS,
    }
    apply_youtube_auth(ydl_opts)
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(target, download=False)
        stream_url = info.get("url")
        # Fallback: if extractor still returns split formats, try to find one
        # progressive URL that has both audio and video.
        if (not stream_url) and "formats" in info:
            for fmt in reversed(info.get("formats", [])):
                if fmt.get("vcodec") not in (None, "none") and fmt.get("acodec") not in (None, "none"):
                    candidate = fmt.get("url")
                    if candidate:
                        stream_url = candidate
                        break
        if not stream_url:
            raise RuntimeError("failed to resolve direct stream url")

    payload = {
        "stream_url": stream_url,
        "title": info.get("title") or "Track",
        "uploader": info.get("uploader") or info.get("channel") or "",
        "video_id": info.get("id") or "",
        "ts": now,
    }
    STREAM_CACHE[cache_key] = payload
    return payload


def _safe_download_filename(title: str, video_id: str) -> str:
    """ASCII-safe filename for Content-Disposition (Hebrew titles become underscores)."""
    vid = "".join(ch for ch in (video_id or "v")[:24] if ch.isalnum() or ch in "_-") or "video"
    raw = (title or "track").strip() or "track"
    safe = []
    for ch in raw[:72]:
        if ch.isascii() and (ch.isalnum() or ch in "._- "):
            safe.append(ch)
        elif ch.isspace():
            safe.append("_")
        else:
            safe.append("_")
    base = "".join(safe).strip("._ ") or "track"
    base = "_".join(part for part in base.split("_") if part) or "track"
    name = f"{base}_{vid}.mp4"
    if len(name) > 120:
        name = name[:110] + f"_{vid}.mp4"
    return name


def _offline_basename_ok(name: str) -> bool:
    if not name or len(name) > 200:
        return False
    for ch in name:
        if ch.isalnum() or ch in "._-":
            continue
        return False
    return True


def _offline_ensure_dir() -> None:
    os.makedirs(OFFLINE_DIR, exist_ok=True)


def _offline_index_path() -> str:
    return os.path.join(OFFLINE_DIR, "index.json")


def _offline_load_index() -> List[dict]:
    _offline_ensure_dir()
    p = _offline_index_path()
    if not os.path.isfile(p):
        return []
    try:
        with open(p, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, list):
            return []
        out: List[dict] = []
        for row in raw:
            if not isinstance(row, dict):
                continue
            vid = str(row.get("video_id") or "").strip()
            fn = str(row.get("file") or "").strip()
            if not vid or not _offline_basename_ok(fn):
                continue
            out.append(
                {
                    "video_id": vid,
                    "title": str(row.get("title") or "Track"),
                    "file": fn,
                }
            )
        return out
    except Exception:
        return []


def _offline_write_index(rows: List[dict]) -> None:
    _offline_ensure_dir()
    with open(_offline_index_path(), "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)


def _offline_abs_path(basename: str) -> Optional[str]:
    if not _offline_basename_ok(basename):
        return None
    return os.path.join(OFFLINE_DIR, basename)


def _offline_find_entry(video_id: str) -> Optional[dict]:
    vid = (video_id or "").strip()
    if not vid:
        return None
    for row in _offline_load_index():
        if str(row.get("video_id") or "") == vid:
            return row
    return None


def _offline_download_to_path(remote_url: str, dest_path: str) -> None:
    ua = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    )
    req = urllib.request.Request(remote_url, headers={"User-Agent": ua})
    tmp_path = dest_path + ".part"
    try:
        with urllib.request.urlopen(req, timeout=600) as remote:
            with open(tmp_path, "wb") as out:
                while True:
                    chunk = remote.read(256 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
        os.replace(tmp_path, dest_path)
    finally:
        if os.path.isfile(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def _offline_delete(video_id: str) -> bool:
    vid = (video_id or "").strip()
    if not vid:
        return False
    with _offline_lock:
        rows = _offline_load_index()
        hit: Optional[dict] = None
        for r in rows:
            if str(r.get("video_id") or "") == vid:
                hit = r
                break
        if not hit:
            return False
        path = _offline_abs_path(str(hit.get("file") or ""))
        rest = [r for r in rows if str(r.get("video_id") or "") != vid]
        _offline_write_index(rest)
    if path and os.path.isfile(path):
        try:
            os.remove(path)
        except OSError:
            pass
    return True


def _auto_reload_from_env_enabled() -> bool:
    v = (os.environ.get("UNBLOCKED_AUTO_RELOAD") or "").strip().lower()
    return v in ("1", "true", "yes", "on")


def _start_source_auto_reload_watcher(server: ThreadingHTTPServer, reload_flag: list) -> None:
    """אם UNBLOCKED_AUTO_RELOAD=1 — מפעיל מחדש את תהליך Python כש־unblocked_player.py נשמר (פיתוח)."""
    if not _auto_reload_from_env_enabled():
        return

    def watch() -> None:
        path = SCRIPT_FILE
        try:
            last = int(os.path.getmtime(path))
        except OSError:
            last = 0
        debounce_s = 0.85
        poll_s = 1.0
        while True:
            time.sleep(poll_s)
            try:
                cur = int(os.path.getmtime(path))
            except OSError:
                continue
            if cur <= last:
                continue
            time.sleep(debounce_s)
            try:
                cur2 = int(os.path.getmtime(path))
            except OSError:
                continue
            if cur2 < cur:
                last = cur2
                continue
            try:
                print(
                    "\n[auto-reload] unblocked_player.py changed - restarting server process\n",
                    flush=True,
                )
            except Exception:
                pass
            reload_flag[0] = True
            try:
                server.shutdown()
            except Exception:
                pass
            return

    threading.Thread(target=watch, name="unblocked-auto-reload", daemon=True).start()


class Handler(BaseHTTPRequestHandler):
    """Connection: close מפחית חיבורים תקועים (CLOSE_WAIT) אחרי רענונים רבים."""

    def end_headers(self):
        self.send_header("Connection", "close")
        super().end_headers()

    def _send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_aurora_static(self, path: str, *, send_body: bool) -> None:
        ext = os.path.splitext(path)[1].lower()
        mime = AURORA_MIME.get(ext, "application/octet-stream")
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except OSError:
            self.send_response(500)
            self.end_headers()
            return
        try:
            etag_body = hashlib.md5(data, usedforsecurity=False).hexdigest()
        except TypeError:
            etag_body = hashlib.md5(data).hexdigest()
        etag = '"' + etag_body + '"'
        mt = int(os.path.getmtime(path))
        lm = email.utils.formatdate(mt, usegmt=True)
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("ETag", etag)
        self.send_header("Last-Modified", lm)
        self.end_headers()
        if send_body:
            self.wfile.write(data)

    def _try_serve_aurora(self, send_body: bool) -> bool:
        parsed = urllib.parse.urlparse(self.path)
        if not parsed.path.startswith("/aurora/"):
            return False
        rel = parsed.path[len("/aurora/"):]
        path = _safe_aurora_path(rel)
        if not path:
            self.send_response(404)
            self.end_headers()
            return True
        self._send_aurora_static(path, send_body=send_body)
        return True

    def _try_serve_aurora_root_alias(self, send_body: bool) -> bool:
        parsed = urllib.parse.urlparse(self.path)
        p = parsed.path
        if len(p) < 2 or p[0] != "/":
            return False
        name = p[1:]
        if "/" in name or name not in AURORA_ROOT_STATIC_NAMES:
            return False
        path = _safe_aurora_path(name)
        if not path:
            return False
        self._send_aurora_static(path, send_body=send_body)
        return True

    def do_HEAD(self):
        if self._try_serve_aurora_root_alias(False):
            return
        if self._try_serve_aurora(False):
            return
        self.send_response(404)
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/":
            qs = urllib.parse.parse_qs(parsed.query)
            ui = (qs.get("ui", [""])[0] or "").strip().lower()
            use_aurora = aurora_template_available() and ui != "legacy"
            try:
                body = (build_html_aurora() if use_aurora else build_html()).encode("utf-8")
                ui_label = "aurora" if use_aurora else "legacy"
            except Exception as exc:
                # Fallback to legacy if aurora template fails for any reason.
                body = build_html().encode("utf-8")
                ui_label = f"legacy(aurora-error:{exc})"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            # חובה: בלי זה הדפדפן שומר HTML ישן ונראה כאילו העיצוב לא השתנה
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            self.send_header("X-Unblocked-Player", "1")
            self.send_header("X-Unblocked-Build", str(SERVER_LOADED_MTIME))
            self.send_header("X-Unblocked-UI", ui_label)
            self.end_headers()
            self.wfile.write(body)
            return

        if self._try_serve_aurora_root_alias(True):
            return

        if self._try_serve_aurora(True):
            return

        if parsed.path == "/__player_check":
            disk_mt = int(os.path.getmtime(SCRIPT_FILE))
            uhost = _url_hostname()
            lines = [
                "OK_UNBLOCKED_PLAYER_V5",
                f"unblocked_local_version={UNBLOCKED_LOCAL_SERVER_VERSION}",
                f"script={SCRIPT_FILE}",
                f"disk_mtime={disk_mt}",
                f"loaded_mtime={SERVER_LOADED_MTIME}",
                f"bind={HOST}:{PORT}",
                f"url=http://{uhost}:{PORT}/",
                "",
            ]
            if HOST == "0.0.0.0":
                lan = _guess_lan_ipv4()
                if lan:
                    lines.append(f"lan_url=http://{lan}:{PORT}/")
            lines.extend(
                [
                    "Hebrew:",
                    "אם השורה הראשונה היא OK_UNBLOCKED_PLAYER_V5 — זה השרת הנכון (unblocked_player.py).",
                    "אם בעמוד הראשי עדיין ממשק ישן (הספרייה + תור ניגון) אבל כאן OK — יש תהליך אחר על אותו פורט או דפדפן מציג מטמון. סגרי כל Python על 5600 והפעילי מחדש, או Ctrl+Shift+Delete למטמון לכתובת זו.",
                ]
            )
            body = ("\n".join(lines) + "\n").encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            self.send_header("X-Unblocked-Player", "1")
            self.send_header("X-Unblocked-Build", str(SERVER_LOADED_MTIME))
            self.end_headers()
            self.wfile.write(body)
            return

        if parsed.path == "/manifest.json":
            body = unblocked_pwa_manifest_json().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/manifest+json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)
            return

        if parsed.path == "/unblocked-sw.js":
            body = UNBLOCKED_SW_SOURCE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Service-Worker-Allowed", "/")
            self.end_headers()
            self.wfile.write(body)
            return

        if parsed.path == "/car-music-icon.png":
            icon_path = os.path.join(SCRIPT_DIR, "car-music-icon.png")
            if os.path.isfile(icon_path):
                with open(icon_path, "rb") as f:
                    data = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "public, max-age=86400")
                self.end_headers()
                self.wfile.write(data)
            else:
                self.send_response(404)
                self.end_headers()
            return

        if parsed.path == "/api/stream":
            q = urllib.parse.parse_qs(parsed.query)
            try:
                idx = int(q.get("i", ["0"])[0])
                quality = q.get("quality", ["high"])[0]
                target_url = (q.get("url", [""])[0] or "").strip()
                if not target_url:
                    # Fallback for old client behavior.
                    if idx < 0 or idx >= len(PLAYLIST):
                        raise ValueError("invalid index")
                    target_url = PLAYLIST[idx]["url"]
                payload = resolve_stream_url(target_url, quality, cache_hint=f"{idx}:{target_url}")
                self._send_json({
                    "stream_url": payload["stream_url"],
                    "index": idx,
                    "quality": quality,
                    "title": payload.get("title", ""),
                    "uploader": payload.get("uploader", ""),
                    "video_id": payload.get("video_id", ""),
                })
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=500)
            return

        if parsed.path == "/api/download_track":
            q = urllib.parse.parse_qs(parsed.query)
            try:
                idx = int(q.get("i", ["0"])[0])
                quality = q.get("quality", ["high"])[0]
                target_url = (q.get("url", [""])[0] or "").strip()
                if not target_url:
                    if idx < 0 or idx >= len(PLAYLIST):
                        raise ValueError("invalid index")
                    target_url = PLAYLIST[idx]["url"]
                payload = resolve_stream_url(target_url, quality, cache_hint=f"dl:{idx}:{target_url}")
                remote_url = payload["stream_url"]
                title = (payload.get("title") or "track").replace("\r", " ").replace("\n", " ")
                vid = (payload.get("video_id") or "").strip()
                fname = _safe_download_filename(title, vid)
                self._proxy_stream_download(remote_url, fname)
            except Exception as exc:
                try:
                    self._send_json({"error": str(exc)}, status=500)
                except Exception:
                    pass
            return

        if parsed.path == "/api/offline_list":
            try:
                self._send_json({"tracks": _offline_load_index()})
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=500)
            return

        if parsed.path == "/api/offline_save":
            q = urllib.parse.parse_qs(parsed.query)
            try:
                idx = int(q.get("i", ["0"])[0])
                quality = q.get("quality", ["high"])[0]
                target_url = (q.get("url", [""])[0] or "").strip()
                if not target_url:
                    if idx < 0 or idx >= len(PLAYLIST):
                        raise ValueError("invalid index")
                    target_url = PLAYLIST[idx]["url"]
                with _offline_lock:
                    payload = resolve_stream_url(
                        target_url, quality, cache_hint=f"offsave:{idx}:{target_url}"
                    )
                    remote_url = payload["stream_url"]
                    title = (payload.get("title") or "track").replace("\r", " ").replace("\n", " ")
                    vid = (payload.get("video_id") or "").strip()
                    if not vid:
                        raise RuntimeError("missing video id")
                    fname = _safe_download_filename(title, vid)
                    dest = os.path.join(OFFLINE_DIR, fname)
                    _offline_ensure_dir()
                    _offline_download_to_path(remote_url, dest)
                    rows = _offline_load_index()
                    rows = [r for r in rows if str(r.get("video_id") or "") != vid]
                    rows.insert(
                        0,
                        {"video_id": vid, "title": (title.strip() or "Track"), "file": fname},
                    )
                    _offline_write_index(rows)
                self._send_json(
                    {"ok": True, "video_id": vid, "title": title.strip() or "Track", "file": fname}
                )
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=500)
            return

        if parsed.path == "/api/offline_stream":
            q = urllib.parse.parse_qs(parsed.query)
            vid = (q.get("vid", [""])[0] or "").strip()
            ent = _offline_find_entry(vid)
            if not ent:
                self._send_json({"error": "לא נמצא"}, status=404)
                return
            path = _offline_abs_path(str(ent.get("file") or ""))
            if not path or not os.path.isfile(path):
                self._send_json({"error": "קובץ חסר"}, status=404)
                return
            rel = "/api/offline_file?" + urllib.parse.urlencode({"vid": vid})
            self._send_json(
                {
                    "stream_url": rel,
                    "title": ent.get("title") or "Track",
                    "video_id": vid,
                    "uploader": "",
                }
            )
            return

        if parsed.path == "/api/offline_file":
            q = urllib.parse.parse_qs(parsed.query)
            vid = (q.get("vid", [""])[0] or "").strip()
            ent = _offline_find_entry(vid)
            if not ent:
                self.send_response(404)
                self.end_headers()
                return
            path = _offline_abs_path(str(ent.get("file") or ""))
            if not path or not os.path.isfile(path):
                self.send_response(404)
                self.end_headers()
                return
            try:
                size = os.path.getsize(path)
            except OSError:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(size))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", "private, max-age=3600")
            self.end_headers()
            try:
                with open(path, "rb") as fh:
                    while True:
                        chunk = fh.read(256 * 1024)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass
            return

        if parsed.path == "/api/offline_delete":
            q = urllib.parse.parse_qs(parsed.query)
            vid = (q.get("vid", [""])[0] or "").strip()
            if _offline_delete(vid):
                self._send_json({"ok": True})
            else:
                self._send_json({"error": "לא נמצא"}, status=404)
            return

        if parsed.path == "/api/search":
            q = urllib.parse.parse_qs(parsed.query)
            query = (q.get("q", [""])[0] or "").strip()
            if not query:
                self._send_json({"results": []})
                return
            try:
                ydl_opts = {
                    "quiet": True,
                    "no_warnings": True,
                    "extract_flat": "in_playlist",
                    "skip_download": True,
                    "socket_timeout": 30,
                    "retries": 2,
                    "extractor_args": YOUTUBE_EXTRACTOR_ARGS,
                }
                apply_youtube_auth(ydl_opts)
                with YoutubeDL(ydl_opts) as ydl:
                    data = ydl.extract_info(f"ytsearch100:{query}", download=False)
                entries = data.get("entries", []) if isinstance(data, dict) else []
                results = []
                for entry in entries:
                    vid = entry.get("id")
                    if not vid:
                        continue
                    results.append({
                        "id": vid,
                        "title": entry.get("title") or f"YouTube {vid}",
                        "uploader": entry.get("uploader") or entry.get("channel") or "",
                        "views": entry.get("view_count") or 0,
                        "duration": entry.get("duration") or 0,
                        "thumb": (
                            (entry.get("thumbnails") or [{}])[-1].get("url")
                            if isinstance(entry.get("thumbnails"), list) and entry.get("thumbnails")
                            else f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg"
                        ),
                    })
                self._send_json({"results": results})
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=500)
            return

        self.send_response(404)
        self.end_headers()

    def log_message(self, fmt, *args):
        return

    def _proxy_stream_download(self, remote_url: str, filename: str) -> None:
        ua = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        req = urllib.request.Request(remote_url, headers={"User-Agent": ua})
        with urllib.request.urlopen(req, timeout=120) as remote:
            content_type = remote.headers.get("Content-Type") or "application/octet-stream"
            clen = remote.headers.get("Content-Length")
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            esc = (filename or "track.mp4").replace("\\", "_").replace('"', "'")
            self.send_header("Content-Disposition", f'attachment; filename="{esc}"')
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            self.send_header("Pragma", "no-cache")
            if clen and str(clen).isdigit():
                self.send_header("Content-Length", str(clen))
            self.end_headers()
            while True:
                chunk = remote.read(256 * 1024)
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except BrokenPipeError:
                    break


def main():
    try:
        server = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError as exc:
        print(f"\n*** לא ניתן להאזין ל־{HOST}:{PORT} — {exc}\n")
        print("סגרי כל חלון טרמינל שמריץ כבר את הנגן, או הריצי על פורט אחר:")
        print("  PowerShell:  $env:UNBLOCKED_PLAYER_PORT=5601; python unblocked_player.py")
        sys.exit(1)

    uhost = _url_hostname()
    url = f"http://{uhost}:{PORT}/?v={SERVER_LOADED_MTIME}"
    print(f"Unblocked player running: {url}")
    print(f"Script: {SCRIPT_FILE}")
    print(f"Offline library (save inside app): {OFFLINE_DIR}")
    print(f"Build (mtime at server start): {SERVER_LOADED_MTIME}")
    chk = f"http://{uhost}:{PORT}/__player_check"
    print(f"בדיקת שרת (פתחי בדפדפן — חייב להתחיל ב-OK_UNBLOCKED_PLAYER_V5): {chk}")
    _print_remote_version_hint()
    if HOST == "0.0.0.0":
        lan = _guess_lan_ipv4()
        if lan:
            print(
                f"מכשיר אחר **באותה רשת (Wi-Fi / בית)**: http://{lan}:{PORT}/  (ייתכן שצריך לאפשר בחומת האש Windows לפורט {PORT})"
            )
            print("    בדפדפן: דף הבית, ״הגדרות״, ״מובייל · סריקת QR״ — קוד לפתיחה מהטלפון באותה רשת.")
        print(
            "מחוץ ל-Wi-Fi הבית: דף הבית, ״הגדרות״, ״מרחוק · Tailscale״ (הדרכה) — או מנהרה (ngrok / Cloudflare Tunnel). לא לפתוח פורט בראוטר לכולם."
        )
    print(
        "לא להריץ 'python -m http.server' על אותו פורט כאן — זה נגן קבצים סטטי, לא השרת הזה."
    )
    print("ייצוא סטטי ל-GitHub Pages: car-player-standalone.html (מסונכן מ־build_html; לניגון מלא להריץ את השרת).")
    if _auto_reload_from_env_enabled():
        print("ריסטארט אוטומטי: UNBLOCKED_AUTO_RELOAD=1 — כל שמירה ל־unblocked_player.py תאתחל את השרת.")
    print("Press Ctrl+C to stop")

    if os.environ.get("OPEN_BROWSER") == "1":
        import webbrowser

        def _open_when_ready():
            time.sleep(0.9)
            try:
                webbrowser.open(url)
            except Exception:
                pass

        threading.Thread(target=_open_when_ready, daemon=True).start()

    reload_flag = [False]
    _start_source_auto_reload_watcher(server, reload_flag)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    if reload_flag[0]:
        argv = [sys.executable, "-u", SCRIPT_FILE] + sys.argv[1:]
        try:
            os.execv(sys.executable, argv)
        except OSError as exc:
            try:
                print(f"[auto-reload] os.execv failed ({exc}), spawning new process...", flush=True)
                subprocess.Popen(argv, cwd=SCRIPT_DIR, env=os.environ.copy())
            except OSError as exc2:
                print(f"[auto-reload] spawn failed: {exc2}", flush=True)
                sys.exit(1)
            sys.exit(0)


if __name__ == "__main__":
    main()

