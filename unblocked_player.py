import json
import os
import socket
import sys
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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
    _p = int(os.environ.get("UNBLOCKED_PLAYER_PORT", "5600"))
    PORT = _p if 1 <= _p <= 65535 else 5600
except ValueError:
    PORT = 5600
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPT_FILE = os.path.abspath(__file__)
# זמן שינוי הקובץ ברגע עליית התהליך — משמש לזיהוי "שרת לא הופעל מחדש אחרי עריכה"
SERVER_LOADED_MTIME = int(os.path.getmtime(SCRIPT_FILE))

# Service Worker (PWA — "התקנה" למסך הבית). עדכן מספר אם משנים לוגיקת מטמון.
UNBLOCKED_PWA_VERSION = 2
UNBLOCKED_SW_SOURCE = """
const UNBLOCKED_PWA_VERSION = %d;
const CACHE = 'unblocked-pwa-v' + UNBLOCKED_PWA_VERSION;
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) =>
      cache.addAll(['/manifest.json', '/car-music-icon.png', '/unblocked-sw.js'])
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
  if (event.request.mode === 'navigate' || u.pathname === '/') {
    event.respondWith(
      fetch(event.request, { cache: 'no-store' })
    );
    return;
  }
  if (u.pathname.startsWith('/api/')) {
    event.respondWith(fetch(event.request, { cache: 'no-store' }));
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


def unblocked_pwa_manifest_json() -> str:
    return json.dumps(
        {
            "name": "מוזיקה Unblocked",
            "short_name": "מוזיקה",
            "description": "נגן YouTube (ספרייה, אהובים, פלייליסטים)",
            "start_url": "/",
            "scope": "/",
            "id": "/",
            "display": "standalone",
            "display_override": ["standalone", "fullscreen", "minimal-ui"],
            "background_color": "#121212",
            "theme_color": "#5edfff",
            "dir": "rtl",
            "lang": "he",
            "icons": [
                {
                    "src": "/car-music-icon.png",
                    "sizes": "192x192",
                    "type": "image/png",
                    "purpose": "any",
                },
                {
                    "src": "/car-music-icon.png",
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
    return f"""<!doctype html>
<html lang="he">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate" />
  <meta http-equiv="Pragma" content="no-cache" />
  <meta name="unblocked-player" content="1" />
  <meta name="player-build" content="{SERVER_LOADED_MTIME}" />
  <meta name="player-disk-mtime" content="{disk_mtime}" />
  <title>מוזיקה v5 · נגן YouTube</title>
  <link rel="manifest" href="/manifest.json" />
  <meta name="theme-color" content="#5edfff" />
  <meta name="color-scheme" content="dark" />
  <meta name="mobile-web-app-capable" content="yes" />
  <meta name="apple-mobile-web-app-capable" content="yes" />
  <meta name="apple-mobile-web-app-title" content="מוזיקה" />
  <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent" />
  <link rel="icon" type="image/png" href="/car-music-icon.png" />
  <link rel="apple-touch-icon" href="/car-music-icon.png" />
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

    html {{ scroll-behavior: smooth; }}

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
      padding: 20px;
      text-align: center;
      color: var(--spot-sub);
      font-size: 0.88rem;
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
      padding: 12px 18px;
      margin-bottom: 20px;
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
    .hard-refresh-btn:hover {{
      border-color: var(--accent);
      color: var(--accent);
      background: rgba(255, 255, 255, 0.06);
    }}
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
    .play-fab:hover {{ filter: brightness(1.06); }}
    .play-fab.is-paused {{ padding-left: 4px; }}

    .scrobble-row {{
      display: flex;
      align-items: center;
      gap: 12px;
      margin-bottom: 12px;
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
    }}
    .vol-ico {{ font-size: 0.9rem; opacity: 0.7; }}
    input[type="range"].range-vol {{
      flex: 1;
      height: 4px;
      -webkit-appearance: none;
      appearance: none;
      background: #444;
      border-radius: 2px;
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
      .bottom-nav-btn {{ min-height: 44px; font-size: 0.74rem; }}
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
      .bottom-nav-btn {{ min-height: 42px; font-size: 0.72rem; padding: 4px 6px; }}
      .home-tiles {{ grid-template-columns: 1fr; }}
      .home-hero {{ padding: 18px 14px; }}
      .home-hero h1 {{ font-size: 1.25rem; margin-bottom: 6px; }}
      .quick-results .queue-actions button {{ min-width: 34px; }}
    }}

    body.car-mode .spot-sidebar,
    body.car-mode .glass-top,
    body.car-mode .content-views,
    body.car-mode .yt-search,
    body.car-mode .yt-results,
    body.car-mode #openYt,
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

    .np-title-row {{
      display: flex;
      flex-direction: row-reverse;
      align-items: flex-start;
      gap: 12px;
      margin-bottom: 16px;
    }}
    .np-title-row .meta.np-title {{
      margin: 0;
      flex: 1;
      min-width: 0;
    }}
    .like-btn {{
      flex-shrink: 0;
      width: 46px;
      height: 46px;
      border-radius: 50%;
      border: 1px solid var(--spot-border);
      background: var(--spot-elevated);
      color: var(--spot-sub);
      font-size: 1.25rem;
      cursor: pointer;
      line-height: 1;
      transition: color 0.15s, border-color 0.15s, background 0.15s;
    }}
    .like-btn:hover {{ color: var(--spot-text); }}
    .like-btn.liked {{
      color: var(--accent);
      border-color: var(--accent-glow);
      background: var(--accent-soft);
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
      max-height: 160px;
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
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
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
      font-size: 1rem;
      cursor: pointer;
      line-height: 1;
      flex-shrink: 0;
    }}
    .like-inline.liked {{ color: var(--accent); }}

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
      gap: 6px;
      padding: 8px 10px calc(8px + env(safe-area-inset-bottom, 0px));
      background: linear-gradient(180deg, rgba(12, 16, 22, 0.82) 0%, rgba(9, 12, 18, 0.98) 45%);
      border-top: 1px solid var(--spot-border);
      backdrop-filter: saturate(150%) blur(14px);
      -webkit-backdrop-filter: saturate(150%) blur(14px);
    }}
    .bottom-nav-btn {{
      border: 1px solid var(--spot-border);
      border-radius: 12px;
      background: rgba(255, 255, 255, 0.04);
      color: var(--spot-sub);
      min-height: 48px;
      padding: 6px 8px;
      font-family: var(--font);
      font-size: 0.78rem;
      font-weight: 700;
      cursor: pointer;
      display: grid;
      place-items: center;
      transition: background 0.15s, border-color 0.15s, color 0.15s;
    }}
    .bottom-nav-btn:hover {{
      color: var(--spot-text);
      border-color: var(--accent-glow);
      background: rgba(255, 255, 255, 0.08);
    }}
    .bottom-nav-btn:focus-visible {{
      outline: 2px solid var(--accent);
      outline-offset: 2px;
    }}
    .bottom-nav-btn.is-active {{
      color: #fff;
      border-color: var(--accent);
      background: var(--accent-soft);
      box-shadow: inset 0 0 0 1px rgba(94, 223, 255, 0.5);
    }}
    .bottom-nav-ico {{ display: none; }}
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
    .view-panel {{ display: none; }}
    .view-panel.is-active {{ display: block; }}

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
    .view-head #search {{
      flex: 1;
      min-width: 200px;
      max-width: 420px;
      padding: 10px 14px;
      border-radius: 10px;
      border: 1px solid var(--spot-border);
      background: var(--spot-elevated);
      color: var(--spot-text);
      font-family: var(--font);
    }}

    .home-hero {{
      padding: 28px 22px;
      border-radius: 18px;
      background: linear-gradient(135deg, rgba(94, 223, 255, 0.14) 0%, rgba(24, 24, 24, 0.95) 62%);
      border: 1px solid var(--spot-border);
      margin-bottom: 20px;
    }}
    .home-hero h1 {{
      margin: 0 0 8px;
      font-size: 1.75rem;
      font-weight: 800;
    }}
    .home-hero p {{ margin: 0; color: var(--spot-sub); font-size: 0.95rem; }}
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
      transition: transform 0.12s, border-color 0.12s;
    }}
    .home-tile:hover {{ border-color: var(--accent-glow); }}
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
    }}
    .pl-card:hover {{ border-color: var(--accent); }}
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
  </style>
</head>
<body>
  <!-- ui-build: premium-cyan-v7 -->
  <div class="ui-version-strip" role="status">
    <strong>ממשק v5</strong>
    <span class="ui-version-strip-mid">דף הבית · הספרייה שלי · אהובים · פלייליסטים בסרגל · אם לא רואים — לחצי ״בדיקת שרת״</span>
    <a class="ui-version-strip-link" href="/__player_check" target="_blank" rel="noopener">בדיקת שרת</a>
    <button type="button" class="ui-version-strip-btn" id="stripReloadBtn" title="טעינה מחדש מהשרת">↻ רענון</button>
  </div>
  <div class="code-stale-banner" id="codeStaleBanner" style="display: {stale_display};" role="alert">
    עדכנת את <code>unblocked_player.py</code> בדיסק, אבל השרת Python עדיין רץ מהזיכרון הישן.
    עצורי את השרת (Ctrl+C בחלון הטרמינל) והפעילי שוב:
    <code>python unblocked_player.py</code>
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
      </nav>
      <div class="sidebar-head">קיצור לפלייליסט</div>
      <div id="playlistSidebarList" class="playlist-nav-list"></div>
    </aside>

    <main class="main-column">
      <div class="glass-top">
        <div class="glass-top-row">
          <div class="glass-top-text">
            <p class="sub-inline">נגן מוזיקה אישי נקי ונוח · ספרייה, אהובים ופלייליסטים במקום אחד.</p>
            <p class="sub-inline build-line">גרסת שרת: <strong>{SERVER_LOADED_MTIME}</strong>
              · <a href="/?v={SERVER_LOADED_MTIME}">קישור לרענון</a>
              · אימות: כותרת <code>X-Unblocked-Player</code> בכלי רשת.</p>
          </div>
          <button type="button" class="hard-refresh-btn" id="hardRefreshBtn" title="טעינה מחדש של הממשק מהשרת (כולל קבצים מעודכנים)">↻ רענון מלא</button>
        </div>
      </div>

      <div class="content-views" id="contentViews">
        <section id="viewHome" class="view-panel is-active" aria-label="דף הבית">
          <div class="home-hero">
            <h1>שלום</h1>
            <p>הספרייה, השירים שאהבת והפלייליסטים — הכול נשמר אצלך בדפדפן (מקומי).</p>
          </div>
          <div class="home-tiles" id="homeTiles"></div>
          <div class="home-recent">
            <h3>נוגנו לאחרונה</h3>
            <div id="recentList" class="recent-list"></div>
          </div>
        </section>

        <section id="viewLibrary" class="view-panel" aria-label="הספרייה שלי">
          <div class="view-head">
            <h2 class="view-title">הספרייה שלי</h2>
            <input id="search" type="search" placeholder="חיפוש לפי שם..." autocomplete="off" />
          </div>
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
        </section>

        <section id="viewPlaylistDetail" class="view-panel" aria-label="פלייליסט">
          <div class="pl-detail-actions">
            <button type="button" class="secondary" id="backFromPlaylist">← חזרה</button>
            <button type="button" class="secondary" id="playPlaylistBtn">נגן הכל</button>
          </div>
          <h2 class="view-title" id="playlistDetailTitle" style="margin-bottom:14px;">פלייליסט</h2>
          <div id="playlistDetailTracks" class="playlist-detail-list"></div>
        </section>
      </div>

      <section class="now-playing-card" id="nowPlaying" aria-label="ניגון כעת">
        <div class="np-layout">
          <div class="np-art-wrap">
            <img id="npArtwork" class="np-artwork" alt="" width="220" height="220" />
          </div>
          <div class="np-text-col">
            <div class="np-label">ניגון כעת</div>
            <div class="np-title-row">
              <button type="button" id="likeBtn" class="like-btn" title="הוסף למועדפים" aria-pressed="false">♡</button>
              <div class="meta np-title" id="meta">טוען...</div>
            </div>
            <div class="controls transport-bar">
              <button type="button" class="secondary icon-btn" id="shuffle" title="ערבוב">⇄</button>
              <button type="button" class="secondary icon-btn" id="repeatBtn" title="חזרה">↻</button>
              <button type="button" class="secondary icon-btn" id="prev" title="קודם">⏮</button>
              <button type="button" class="play-fab is-paused" id="play" title="נגן">▶</button>
              <button type="button" class="secondary icon-btn" id="next" title="הבא">⏭</button>
              <button type="button" class="secondary icon-btn" id="openYt" title="פתח ביוטיוב">↗</button>
            </div>
            <div class="scrobble-row">
              <span class="time-tag" id="timeCurrent">0:00</span>
              <input type="range" id="progressBar" class="range-progress" min="0" max="1000" value="0" step="1" aria-label="התקדמות" />
              <span class="time-tag" id="timeTotal">0:00</span>
            </div>
            <div class="vol-row">
              <span class="vol-ico" aria-hidden="true">🔊</span>
              <input type="range" id="volumeBar" class="range-vol" min="0" max="100" value="100" step="1" aria-label="עוצמה" />
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
            <video class="media-layer" id="video" controls autoplay></video>
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
        <button type="button" class="bottom-nav-btn is-active" data-nav-target="home">בית</button>
        <button type="button" class="bottom-nav-btn" data-nav-target="library">ספרייה</button>
        <button type="button" class="bottom-nav-btn" data-nav-target="liked">אהובים</button>
        <button type="button" class="bottom-nav-btn" data-nav-target="playlists">פלייליסטים</button>
      </nav>
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

  <script>
    const baseItems = {items_json};
    const video = document.getElementById('video');
    const mediaShell = document.getElementById('mediaShell');
    const ytEmbed = document.getElementById('ytEmbed');
    const meta = document.getElementById('meta');
    const statusEl = document.getElementById('status');
    const qualityEl = document.getElementById('quality');
    const modeEl = document.getElementById('mode');
    const exitCarModeBtn = document.getElementById('exitCarModeBtn');
    const searchEl = document.getElementById('search');
    const showAllBtn = document.getElementById('showAllBtn');
    const repairNamesBtn = document.getElementById('repairNamesBtn');
    const quickResultsEl = document.getElementById('quickResults');
    const shuffleBtn = document.getElementById('shuffle');
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
    const favoritesOnlyBtn = document.getElementById('favoritesOnlyBtn');
    const upNextListEl = document.getElementById('upNextList');
    const recentListEl = document.getElementById('recentList');
    const homeTilesEl = document.getElementById('homeTiles');
    const likedPanelListEl = document.getElementById('likedPanelList');
    const playlistsGridEl = document.getElementById('playlistsGrid');
    const playlistSidebarListEl = document.getElementById('playlistSidebarList');
    const viewHome = document.getElementById('viewHome');
    const viewLibrary = document.getElementById('viewLibrary');
    const viewLiked = document.getElementById('viewLiked');
    const viewPlaylists = document.getElementById('viewPlaylists');
    const viewPlaylistDetail = document.getElementById('viewPlaylistDetail');
    const newPlaylistBtn = document.getElementById('newPlaylistBtn');
    const backFromPlaylist = document.getElementById('backFromPlaylist');
    const playPlaylistBtn = document.getElementById('playPlaylistBtn');
    const playlistDetailTitle = document.getElementById('playlistDetailTitle');
    const playlistDetailTracks = document.getElementById('playlistDetailTracks');
    const plOverlay = document.getElementById('plOverlay');
    const plOverlaySelect = document.getElementById('plOverlaySelect');
    const plOverlayClose = document.getElementById('plOverlayClose');
    const plOverlayNew = document.getElementById('plOverlayNew');
    const plOverlayConfirm = document.getElementById('plOverlayConfirm');

    const LIKES_KEY = 'playerLikeKeys';
    const RECENT_KEY = 'playerRecentKeys';
    const PLAYLISTS_KEY = 'playerPlaylistsV2';

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
    let playlists = [];
    let activePlaylistDetailId = null;
    let plOverlaySongIndex = null;
    let currentNavView = 'home';
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

    function setStatus(t) {{
      statusEl.textContent = t || '';
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
        repeatBtn.textContent = '①';
        repeatBtn.title = 'חזרה: שיר אחד';
      }} else if (repeatMode === 'all') {{
        repeatBtn.textContent = '↻';
        repeatBtn.title = 'חזרה: כל הרשימה';
      }} else {{
        repeatBtn.textContent = '↻';
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
      if (embed) {{
        playBtn.textContent = '▶';
        playBtn.classList.add('is-paused');
        return;
      }}
      if (video.paused) {{
        playBtn.textContent = '▶';
        playBtn.classList.add('is-paused');
      }} else {{
        playBtn.textContent = '⏸';
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
      const idx = currentIndex();
      const key = likeKeyForItemAt(idx);
      const on = key && loadLikeSet().has(key);
      likeBtn.classList.toggle('liked', !!on);
      likeBtn.textContent = on ? '♥' : '♡';
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
      const q = (searchEl.value || '').trim().toLowerCase();
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
      }} else if (!q) {{
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
      const panels = [viewHome, viewLibrary, viewLiked, viewPlaylists, viewPlaylistDetail].filter(Boolean);
      panels.forEach((p) => p.classList.remove('is-active'));
      if (view === 'home' && viewHome) viewHome.classList.add('is-active');
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
      if (view === 'playlists') renderPlaylistsGrid();
      if (view === 'home') {{
        renderHomeTiles();
        renderRecentList();
      }}
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
        ' רשימות</div></div>';
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
        likedPanelListEl.innerHTML = '<div class="queue-empty">עדיין אין שירים שסימנת בלב. עברי ל״הספרייה שלי״ ולחצי ♥ ליד שיר.</div>';
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
              <button type="button" class="like-inline liked" data-like="${{index}}" title="הסר ממועדפים">♥</button>
              <button type="button" class="secondary" data-pick="${{index}}">▶</button>
            </div>
          </div>`;
      }}).join('');
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
              <button type="button" class="secondary" data-pick="${{index}}">▶</button>
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
      renderHomeTiles();
      renderLikedPanel();
      renderPlaylistsGrid();
      renderPlaylistSidebar();
      if (currentNavView === 'playlistDetail') renderPlaylistDetailView();
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
              <button type="button" class="like-inline ${{liked ? 'liked' : ''}}" data-like="${{index}}" title="מועדפים">♥</button>
              <button type="button" class="pl-add-btn" data-pladd="${{index}}" title="הוסף לפלייליסט">＋</button>
              <button type="button" class="secondary" data-pick="${{index}}">▶</button>
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
        const r = await fetch(`/api/search?q=${{encodeURIComponent(q)}}`, {{ cache: 'no-store' }});
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
      if (!id) return;
      if (items.some(x => x && x.type === 'video' && x.id === id)) {{
        setStatus('הסרטון כבר קיים ברשימה');
        return;
      }}
      items.push({{
        name: title ? `🎬 ${{title}}` : `🎬 תוספת חדשה`,
        url: `https://www.youtube.com/watch?v=${{id}}`,
        id: id,
        type: 'video'
      }});
      saveItems();
      rebuildFilter(true, true);
      setStatus('נוסף לרשימה בהצלחה');
    }}

    async function loadCurrent(autoPlay = true) {{
      if (!filteredIndices.length) return;
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
        const r = await fetch(`/api/stream?i=${{idx}}&quality=${{encodeURIComponent(q)}}&url=${{targetUrl}}`, {{
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
      }} catch (e) {{
        const yid = getYoutubeIdFromItem(item);
        if (yid && playEmbedFallback(yid, autoPlay)) {{
          loadFailStreak = 0;
          setStatus('מצב הטמעה: ניגון בחלון YouTube. לניגון ישיר (ללא iframe) — שמור ‎yt_cookies.txt‎ ליד הנגן.');
          saveLastAbsoluteIndex();
          refreshNpArtwork();
          renderQuickResults((searchEl.value || '').trim().toLowerCase());
          updatePlayPauseUi();
          pushRecentFromIndex(idx);
          renderUpNext();
          updateLikeButton();
          return;
        }}
        loadFailStreak += 1;
        const raw = (e && e.name === 'AbortError') ? 'Timeout: לא התקבל סטר — בדוק אינטרנט או הוסף קובץ עוגיות' : (e && e.message) ? e.message : String(e);
        const isBot = /not a bot|sign in|Sign in|confirm|בוט|HTTP 403/i.test(raw);
        const help = isBot
          ? 'YouTube חוסם גישה בלי הזדהות. אפשר ‎yt_cookies.txt‎ לניגון ישיר, או להסתמך על מצב הטמעה (אמור לעבור אוטומטית).'
          : raw;
        setStatus(help);
        if (loadFailStreak >= 3) {{
          setStatus('הנגן נעצר: אין מזהה YouTube בקישור, או כשל אחר. בדקו ‎url‎ או ‎yt_cookies.txt‎.');
          return;
        }}
        setTimeout(() => nextTrack(), 1400);
      }} finally {{
        clearTimeout(t);
      }}
    }}

    function nextTrack() {{
      if (!filteredIndices.length) return;
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
    document.getElementById('play').addEventListener('click', async () => {{
      if (mediaShell.classList.contains('use-embed')) {{
        const it = items[currentIndex()];
        const y = getYoutubeIdFromItem(it);
        if (y) playEmbedFallback(y, true);
        updatePlayPauseUi();
        return;
      }}
      if (!video.src) {{
        await loadCurrent(true);
      }} else {{
        if (video.paused) {{
          try {{ await video.play(); }} catch (e) {{}}
        }} else {{
          try {{ video.pause(); }} catch (e) {{}}
        }}
      }}
      updatePlayPauseUi();
    }});

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
      const v = Number(volumeBar.value) / 100;
      try {{ video.volume = Math.max(0, Math.min(1, v)); }} catch (e) {{}}
    }});

    video.addEventListener('play', () => updatePlayPauseUi());
    video.addEventListener('playing', () => updatePlayPauseUi());
    video.addEventListener('pause', () => updatePlayPauseUi());
    video.addEventListener('loadedmetadata', () => {{
      timeTotalEl.textContent = formatTime(video.duration);
      syncProgressFromVideo();
    }});
    video.addEventListener('timeupdate', () => syncProgressFromVideo());
    document.getElementById('openYt').addEventListener('click', () => {{
      const item = items[currentIndex()];
      if (item) {{
        window.open(item.url, '_blank', 'noopener,noreferrer');
      }}
    }});
    qualityEl.addEventListener('change', async () => {{
      quality = ['auto', 'high', 'normal'].includes(qualityEl.value) ? qualityEl.value : 'auto';
      localStorage.setItem('playerQuality', quality);
      await loadCurrent(true);
    }});
    searchEl.addEventListener('input', () => rebuildFilter(true, true));
    searchEl.addEventListener('keypress', (e) => {{
      if (e.key === 'Enter' && filteredIndices.length) {{
        e.preventDefault();
        pos = 0;
        loadCurrent(true);
      }}
    }});
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
      likeBtn.addEventListener('click', () => {{
        const idx = currentIndex();
        toggleLikeAtIndex(idx);
        if (favoritesOnly) rebuildFilter(false, false);
        else renderQuickResults((searchEl.value || '').trim().toLowerCase());
        updateLikeButton();
        refreshAllBrowseUi();
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
      const vitem = items[currentIndex()];
      const vid = getYoutubeIdFromItem(vitem);
      if (quality === 'auto' || quality === 'high') {{
        setStatus('הסטרים נפל, מנסה איכות רגילה...');
        quality = 'normal';
        qualityEl.value = 'normal';
        localStorage.setItem('playerQuality', quality);
        setTimeout(() => loadCurrent(true), 700);
        return;
      }}
      if (vid) {{
        setStatus('הסטרים נפל — עובר להטמעה YouTube...');
        playEmbedFallback(vid, true);
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

    const hardRefreshBtn = document.getElementById('hardRefreshBtn');
    if (hardRefreshBtn) {{
      hardRefreshBtn.addEventListener('click', () => hardReloadFromServer());
    }}
    const stripReloadBtn = document.getElementById('stripReloadBtn');
    if (stripReloadBtn) {{
      stripReloadBtn.addEventListener('click', () => hardReloadFromServer());
    }}

    (function pwaInit() {{
      if ('serviceWorker' in navigator) {{
        navigator.serviceWorker.register('/unblocked-sw.js', {{ scope: '/' }}).catch(function () {{}});
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

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/":
            body = build_html().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            # חובה: בלי זה הדפדפן שומר HTML ישן ונראה כאילו העיצוב לא השתנה
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            self.send_header("X-Unblocked-Player", "1")
            self.send_header("X-Unblocked-Build", str(SERVER_LOADED_MTIME))
            self.end_headers()
            self.wfile.write(body)
            return

        if parsed.path == "/__player_check":
            disk_mt = int(os.path.getmtime(SCRIPT_FILE))
            uhost = _url_hostname()
            lines = [
                "OK_UNBLOCKED_PLAYER_V5",
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
                    data = ydl.extract_info(f"ytsearch10:{query}", download=False)
                entries = data.get("entries", []) if isinstance(data, dict) else []
                results = []
                for entry in entries:
                    vid = entry.get("id")
                    if not vid:
                        continue
                    results.append({
                        "id": vid,
                        "title": entry.get("title") or f"YouTube {vid}",
                    })
                self._send_json({"results": results})
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=500)
            return

        self.send_response(404)
        self.end_headers()

    def log_message(self, fmt, *args):
        return


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
    print(f"Build (mtime at server start): {SERVER_LOADED_MTIME}")
    chk = f"http://{uhost}:{PORT}/__player_check"
    print(f"בדיקת שרת (פתחי בדפדפן — חייב להתחיל ב-OK_UNBLOCKED_PLAYER_V5): {chk}")
    if HOST == "0.0.0.0":
        lan = _guess_lan_ipv4()
        if lan:
            print(
                f"מכשיר אחר **באותה רשת (Wi-Fi / בית)**: http://{lan}:{PORT}/  (ייתכן שצריך לאפשר בחומת האש Windows לפורט {PORT})"
            )
        print(
            "גישה מרחוק באינטרנט: השתמשי במנהרה (למשל ngrok, Cloudflare Tunnel) — לא מומלץ לפתוח פורט בראוטר ביתי לכולם."
        )
    print(
        "לא להריץ 'python -m http.server' על אותו פורט כאן — זה נגן קבצים סטטי, לא השרת הזה."
    )
    print("נגן הרכב הישן (ירוק) נמצא בקובץ: car-player-standalone.html (פתיחה ידנית בדפדפן).")
    print("Press Ctrl+C to stop")

    if os.environ.get("OPEN_BROWSER") == "1":
        import threading
        import webbrowser

        def _open_when_ready():
            time.sleep(0.9)
            try:
                webbrowser.open(url)
            except Exception:
                pass

        threading.Thread(target=_open_when_ready, daemon=True).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

