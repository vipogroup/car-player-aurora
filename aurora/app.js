// Aurora — main app entry point.
// Wires the UI to the existing /api/* endpoints (stream, search, offline_*).
// Pure ES modules, zero dependencies.

import { buildIconSprite, icon } from './icons.js';
import { extractPalette, applyTheme } from './color.js';
import { Visualizer } from './visualizer.js';

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const APP = window.__AURORA__ || { playlist: [], lanUrl: '', build: '', version: '' };

// ============================================================
// State
// ============================================================
const KEYS = {
  library: 'aurora.library',
  favorites: 'aurora.favorites',
  recents: 'aurora.recents',
  playlists: 'aurora.playlists',
  shuffle: 'aurora.shuffle',
  repeat: 'aurora.repeat',
  quality: 'aurora.quality',
  volume: 'aurora.volume',
  eq: 'aurora.eq',
  lastIndex: 'aurora.lastIndex',
};

const state = {
  library: loadJSON(KEYS.library, null) || migrateOrSeed(),
  favorites: loadJSON(KEYS.favorites, []),
  recents: loadJSON(KEYS.recents, []),
  playlists: loadJSON(KEYS.playlists, []),
  offline: [],
  searchResults: [],

  currentTrack: null,
  currentSource: 'library',
  currentIndex: -1,
  queue: [],

  isPlaying: false,
  shuffle: loadJSON(KEYS.shuffle, false),
  repeat: loadJSON(KEYS.repeat, 'off'),
  quality: loadJSON(KEYS.quality, 'high'),
  volume: loadJSON(KEYS.volume, 0.85),

  view: 'home',
  paletteOpen: false,
  playerOpen: false,
  driveMode: false,
  queueOpen: false,
  navOpen: false,

  paletteFocus: 0,
  paletteItems: [],

  eq: loadJSON(KEYS.eq, defaultEq()),
};

function defaultEq() {
  return {
    enabled: false,
    comp: false,
    clip: false,
    preamp: 0,
    bands: [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
  };
}

function loadJSON(key, fallback) {
  try {
    const v = localStorage.getItem(key);
    if (v == null) return fallback;
    return JSON.parse(v);
  } catch { return fallback; }
}

function saveJSON(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch {}
}

function migrateOrSeed() {
  const legacy = loadJSON('unblockedPlayerItems', null);
  const items = legacy || (APP.playlist || []);
  return items.map((it, i) => ({
    id: makeId(it.url, i),
    name: it.name || `שיר ${i + 1}`,
    url: it.url,
    videoId: extractVideoId(it.url),
    addedAt: Date.now() - (items.length - i) * 1000,
  }));
}

function makeId(url, salt = 0) {
  const v = extractVideoId(url) || `t${salt}`;
  return `t_${v}`;
}

function extractVideoId(url) {
  if (!url) return '';
  try {
    const u = new URL(url);
    if (u.hostname.includes('youtu.be')) return u.pathname.slice(1);
    return u.searchParams.get('v') || '';
  } catch { return ''; }
}

function thumbFor(track) {
  const v = track?.videoId || extractVideoId(track?.url || '');
  if (!v) return '';
  return `https://i.ytimg.com/vi/${v}/mqdefault.jpg`;
}

function persist() {
  saveJSON(KEYS.library, state.library);
  saveJSON(KEYS.favorites, state.favorites);
  saveJSON(KEYS.recents, state.recents);
  saveJSON(KEYS.playlists, state.playlists);
  saveJSON(KEYS.shuffle, state.shuffle);
  saveJSON(KEYS.repeat, state.repeat);
  saveJSON(KEYS.quality, state.quality);
  saveJSON(KEYS.volume, state.volume);
  saveJSON(KEYS.eq, state.eq);
}

// ============================================================
// Audio engine
// ============================================================
/* let — אחרי מעבר מ-Web-Audio לניגון ישיר מחליפים את ה-<video> (פעם אחת לכל מקור) */
let audio = $('#audio');
let audioContext = null;
let preampNode = null;
let eqNodes = [];
let compNode = null;
let limiterNode = null;
let visualizer = null;

const EQ_BANDS = [60, 170, 310, 600, 1000, 3000, 6000, 12000, 14000, 16000];

function getAudioContext() {
  if (!audioContext) {
    try {
      const Ctor = window.AudioContext || window.webkitAudioContext;
      audioContext = new Ctor();
    } catch { audioContext = null; }
  }
  return audioContext;
}

/** זרמים ממקור אחר (YouTube/googlevideo וכו') — בלי CORS מתאים, MediaElementAudioSourceNode שקט לחלוטין */
function streamNeedsNativePlayback(streamUrl) {
  if (!streamUrl || typeof streamUrl !== 'string') return true;
  try {
    const u = new URL(streamUrl, window.location.href);
    return u.origin !== window.location.origin;
  } catch {
    return true;
  }
}

/** מסיר Web-Audio מהאלמנט (פעם אחת לכל <video>) — חייבים אלמנט חדש כדי לשמוע שוב זרמים חיצוניים */
function resetAudioForNativePlayback() {
  if (!audio.__auroraSource) return;
  try {
    const ac = audioContext;
    if (ac && ac.state !== 'closed') ac.close();
  } catch (_) {}
  audioContext = null;
  preampNode = null;
  eqNodes = [];
  compNode = null;
  limiterNode = null;

  const old = audio;
  const parent = old.parentNode;
  if (!parent) return;
  const nu = document.createElement('video');
  nu.id = 'audio';
  nu.setAttribute('preload', 'auto');
  nu.playsInline = true;
  nu.setAttribute('webkit-playsinline', '');
  nu.style.display = 'none';
  nu.volume = state.volume;
  parent.replaceChild(nu, old);
  audio = nu;
  bindAudio();
}

function ensureAudioGraph(forUrl) {
  const ac = getAudioContext();
  if (!ac) return;
  if (audio.__auroraSource) return;
  const urlToCheck = forUrl || audio.currentSrc || audio.src;
  if (!urlToCheck || streamNeedsNativePlayback(urlToCheck)) return;
  try {
    const src = ac.createMediaElementSource(audio);
    preampNode = ac.createGain();
    preampNode.gain.value = Math.pow(10, state.eq.preamp / 20);
    eqNodes = EQ_BANDS.map((freq, i) => {
      const filter = ac.createBiquadFilter();
      filter.type = (i === 0) ? 'lowshelf' : (i === EQ_BANDS.length - 1) ? 'highshelf' : 'peaking';
      filter.frequency.value = freq;
      filter.Q.value = 1.0;
      filter.gain.value = state.eq.enabled ? state.eq.bands[i] : 0;
      return filter;
    });
    compNode = ac.createDynamicsCompressor();
    compNode.threshold.value = -20;
    compNode.ratio.value = 4;
    compNode.attack.value = 0.005;
    compNode.release.value = 0.25;
    if (!state.eq.comp) compNode.threshold.value = 0;

    limiterNode = ac.createDynamicsCompressor();
    limiterNode.threshold.value = -1;
    limiterNode.knee.value = 0;
    limiterNode.ratio.value = 20;
    limiterNode.attack.value = 0.001;
    limiterNode.release.value = 0.05;

    let last = src.connect(preampNode);
    for (const f of eqNodes) last = last.connect(f);
    last = last.connect(compNode);
    if (state.eq.clip) last = last.connect(limiterNode);
    last.connect(ac.destination);

    audio.__auroraSource = src;
    audio.__auroraTail = ac.destination;
  } catch (e) {
    // already connected, ignore.
  }
}

function applyEqLive() {
  if (!eqNodes.length) return;
  for (let i = 0; i < eqNodes.length; i++) {
    eqNodes[i].gain.value = state.eq.enabled ? state.eq.bands[i] : 0;
  }
  if (preampNode) preampNode.gain.value = Math.pow(10, state.eq.preamp / 20);
  if (compNode) compNode.threshold.value = state.eq.comp ? -20 : 0;
}

// ============================================================
// API
// ============================================================
async function apiSearch(q) {
  const r = await fetch(`/api/search?q=${encodeURIComponent(q)}`, { cache: 'no-store' });
  if (!r.ok) throw new Error('search failed');
  const data = await r.json();
  return data.results || [];
}

async function apiResolveStream(track, quality) {
  const url = `/api/stream?i=0&quality=${encodeURIComponent(quality)}&url=${encodeURIComponent(track.url)}`;
  const r = await fetch(url, { cache: 'no-store' });
  if (!r.ok) throw new Error('stream failed');
  const data = await r.json();
  if (data.error) throw new Error(data.error);
  return data;
}

async function apiOfflineList() {
  const r = await fetch('/api/offline_list', { cache: 'no-store' });
  if (!r.ok) return [];
  const data = await r.json();
  return data.tracks || [];
}

async function apiOfflineSave(track, quality) {
  const url = `/api/offline_save?i=0&quality=${encodeURIComponent(quality)}&url=${encodeURIComponent(track.url)}`;
  const r = await fetch(url, { cache: 'no-store' });
  const data = await r.json();
  if (data.error) throw new Error(data.error);
  return data;
}

async function apiOfflineStream(videoId) {
  const r = await fetch(`/api/offline_stream?vid=${encodeURIComponent(videoId)}`, { cache: 'no-store' });
  if (!r.ok) throw new Error('not found');
  return r.json();
}

async function apiOfflineDelete(videoId) {
  const r = await fetch(`/api/offline_delete?vid=${encodeURIComponent(videoId)}`, { cache: 'no-store' });
  return r.ok;
}

// ============================================================
// Routing / views
// ============================================================
function setView(view) {
  state.view = view;
  $$('.ar-view').forEach(el => el.classList.toggle('is-active', el.dataset.view === view));
  $$('.ar-nav-item').forEach(el => el.classList.toggle('is-active', el.dataset.view === view));
  if (view === 'library') renderLibrary();
  if (view === 'favorites') renderFavorites();
  if (view === 'playlists') renderAllPlaylists();
  if (view === 'offline') refreshOffline();
  document.body.classList.remove('is-nav-open');
  state.navOpen = false;
  $('.ar-app').classList.remove('is-nav-open');
}

// ============================================================
// Renders
// ============================================================
function renderHome() {
  const greeting = greetByTime();
  $('#heroGreeting').textContent = greeting.eyebrow;

  const homeSource = state.recents.length ? state.recents : state.library;
  const fromRecents = state.recents.length > 0;
  const chipSub = fromRecents ? 'המשך מאיפה שהפסקת' : 'מהספרייה שלך';

  const quickHost = $('#heroQuick');
  const quickItems = [...homeSource.slice(0, 4)];
  quickHost.innerHTML = quickItems.map((t) => `
    <button class="ar-hero-chip" data-play-id="${t.id}">
      <img class="ar-hero-chip-thumb" src="${thumbFor(t)}" alt="" loading="lazy" onerror="this.style.visibility='hidden'"/>
      <div>
        <div style="font-weight:700;font-size:0.88rem;line-height:1.2">${escapeHtml(t.name)}</div>
        <div style="font-size:0.74rem;color:var(--text-3)">${chipSub}</div>
      </div>
    </button>
  `).join('') || '';
  if (!quickItems.length) {
    quickHost.innerHTML = `<div style="color:var(--text-3);font-size:0.92rem">בחרי שיר מהספרייה כדי להתחיל</div>`;
  }

  const recentsHost = $('#recentsRow');
  const recents = homeSource.slice(0, 12);
  const emptyRecentsMsg = fromRecents
    ? 'אין שירים אחרונים — נגן משהו'
    : 'אין עדיין היסטוריית ניגון — להלן שירים מהספרייה';
  recentsHost.innerHTML = recents.map((t) => `
    <article class="ar-recent-card" data-play-id="${t.id}">
      <img class="ar-recent-art" src="${thumbFor(t)}" alt="" loading="lazy" onerror="this.style.visibility='hidden'"/>
      <div class="ar-recent-title">${escapeHtml(t.name)}</div>
      <div class="ar-recent-sub">${t.uploader || 'YouTube'}</div>
      <button class="ar-recent-play" data-play-id="${t.id}" aria-label="נגן">${icon('play')}</button>
    </article>
  `).join('') || `<div style="color:var(--text-3);padding:18px">${emptyRecentsMsg}</div>`;

  $('#bentoFavCount').textContent = `${state.favorites.length} שירים`;
  $('#bentoLibCount').textContent = `${state.library.length} שירים`;
  $('#bentoOfflineCount').textContent = `${state.offline.length} שירים`;

  if (state.currentTrack) {
    $('#bentoNowTitle').textContent = state.currentTrack.name;
    $('#bentoNowSub').textContent = state.currentTrack.uploader || 'YouTube';
    const bg = $('#bentoNowBg');
    const t = thumbFor(state.currentTrack);
    bg.style.backgroundImage = t ? `url('${t}')` : 'none';
  }

  renderPlaylistsGrid($('#playlistsGrid'));
}

function greetByTime() {
  const h = new Date().getHours();
  if (h < 5) return { eyebrow: 'לילה טוב' };
  if (h < 12) return { eyebrow: 'בוקר טוב' };
  if (h < 17) return { eyebrow: 'צהריים נעימים' };
  if (h < 21) return { eyebrow: 'ערב טוב' };
  return { eyebrow: 'לילה טוב' };
}

function renderLibrary() {
  const filter = state.libraryFilter || 'all';
  $$('[data-filter]').forEach(b => b.classList.toggle('is-active', b.dataset.filter === filter));
  let items = state.library.slice();
  if (filter === 'favorites') items = items.filter(t => state.favorites.includes(t.id));
  if (filter === 'recent') {
    const ids = state.recents.map(r => r.id);
    items = items.filter(t => ids.includes(t.id)).sort((a, b) => ids.indexOf(a.id) - ids.indexOf(b.id));
  }
  renderTrackList($('#libraryList'), items, 'library');
}

function renderFavorites() {
  const items = state.library.filter(t => state.favorites.includes(t.id));
  renderTrackList($('#favoritesList'), items, 'favorites');
}

function renderAllPlaylists() {
  renderPlaylistsGrid($('#allPlaylistsGrid'));
}

function renderPlaylistsGrid(host) {
  if (!host) return;
  if (!state.playlists.length) {
    host.innerHTML = `<div style="grid-column:1/-1;color:var(--text-3);padding:18px">אין פלייליסטים. צרי חדש בכפתור למעלה.</div>`;
    return;
  }
  host.innerHTML = state.playlists.map((p) => {
    const cover = p.tracks.slice(0, 4).map(id => {
      const tr = state.library.find(x => x.id === id);
      return tr ? `<img src="${thumbFor(tr)}" alt="" loading="lazy"/>` : '<div></div>';
    }).join('') || `<div class="ar-playlist-cover-empty">${icon('music')}</div>`;
    return `
      <article class="ar-playlist-card" data-playlist-id="${p.id}">
        <div class="ar-playlist-cover">${cover}</div>
        <div class="ar-playlist-name">${escapeHtml(p.name)}</div>
        <div class="ar-playlist-count">${p.tracks.length} שירים</div>
      </article>
    `;
  }).join('');
}

function renderTrackList(host, items, source) {
  if (!host) return;
  if (!items.length) {
    host.innerHTML = `<div style="color:var(--text-3);padding:18px">רשימה ריקה</div>`;
    return;
  }
  host.innerHTML = items.map((t, i) => {
    const isActive = state.currentTrack && state.currentTrack.id === t.id;
    const isFav = state.favorites.includes(t.id);
    return `
      <div class="ar-track-row ${isActive ? 'is-active' : ''}" data-play-id="${t.id}" data-source="${source}">
        <div class="ar-track-num"><span>${i + 1}</span></div>
        <div class="ar-track-num-play">${icon('play')}</div>
        <img class="ar-track-art" src="${thumbFor(t)}" alt="" loading="lazy" onerror="this.style.visibility='hidden'"/>
        <div class="ar-track-info">
          <div class="ar-track-title">${escapeHtml(t.name)}</div>
          <div class="ar-track-sub">${t.uploader || 'YouTube'}</div>
        </div>
        <div class="ar-track-actions">
          <button class="ar-iconbtn-mini ${isFav ? 'is-on' : ''}" data-action="like" data-id="${t.id}" aria-label="אהוב">
            ${icon(isFav ? 'heartFill' : 'heart')}
          </button>
          <button class="ar-iconbtn-mini" data-action="add-to-playlist-id" data-id="${t.id}" aria-label="לפלייליסט">${icon('plus')}</button>
          <button class="ar-iconbtn-mini" data-action="save-offline-id" data-id="${t.id}" aria-label="שמור">${icon('download')}</button>
          <button class="ar-iconbtn-mini" data-action="remove-from-library" data-id="${t.id}" aria-label="הסר">${icon('trash')}</button>
        </div>
      </div>
    `;
  }).join('');
}

async function refreshOffline() {
  try {
    state.offline = await apiOfflineList();
  } catch { state.offline = []; }
  $('#bentoOfflineCount').textContent = `${state.offline.length} שירים`;
  const host = $('#offlineList');
  if (!state.offline.length) {
    host.innerHTML = `<div style="color:var(--text-3);padding:18px">אין שירים שמורים אופליין. שמרי שיר מהנגן.</div>`;
    return;
  }
  host.innerHTML = state.offline.map((t, i) => `
    <div class="ar-track-row" data-offline-id="${t.video_id}">
      <div class="ar-track-num"><span>${i + 1}</span></div>
      <div class="ar-track-num-play">${icon('play')}</div>
      <img class="ar-track-art" src="https://i.ytimg.com/vi/${t.video_id}/mqdefault.jpg" alt="" loading="lazy" onerror="this.style.visibility='hidden'"/>
      <div class="ar-track-info">
        <div class="ar-track-title">${escapeHtml(t.title || 'Track')}</div>
        <div class="ar-track-sub">שמור במחשב · ${t.file || ''}</div>
      </div>
      <div class="ar-track-actions">
        <button class="ar-iconbtn-mini" data-action="delete-offline" data-vid="${t.video_id}" aria-label="מחק">${icon('trash')}</button>
      </div>
    </div>
  `).join('');
}

// ============================================================
// Search view
// ============================================================
let searchAbort = null;
let searchTimer = null;

function bindSearchView() {
  const input = $('#searchInput');
  const meta = $('#searchMeta');
  const host = $('#searchResults');
  const clearBtn = $('#searchClear');

  const run = async (q) => {
    if (searchAbort) searchAbort.abort();
    searchAbort = new AbortController();
    if (!q || q.length < 2) {
      meta.textContent = 'התחילי להקליד כדי לחפש';
      host.innerHTML = '';
      return;
    }
    meta.textContent = `מחפש "${q}"…`;
    host.innerHTML = Array.from({ length: 10 }).map(() => `
      <div class="ar-result-card"><div class="ar-result-thumb ar-skeleton"></div><div class="ar-skeleton" style="height:14px;margin-bottom:6px"></div><div class="ar-skeleton" style="height:10px;width:60%"></div></div>
    `).join('');
    try {
      const results = await apiSearch(q);
      state.searchResults = results;
      meta.textContent = `${results.length} תוצאות עבור "${q}"`;
      host.innerHTML = results.map((r) => `
        <article class="ar-result-card" data-search-id="${r.id}">
          <img class="ar-result-thumb" src="${r.thumb || `https://i.ytimg.com/vi/${r.id}/mqdefault.jpg`}" alt="" loading="lazy"/>
          <div class="ar-result-title">${escapeHtml(r.title)}</div>
          <div class="ar-result-sub">${escapeHtml(r.uploader || 'YouTube')}</div>
        </article>
      `).join('');
    } catch (e) {
      meta.textContent = `שגיאה בחיפוש: ${e.message}`;
      host.innerHTML = '';
    }
  };

  input.addEventListener('input', (e) => {
    clearTimeout(searchTimer);
    const q = e.target.value.trim();
    searchTimer = setTimeout(() => run(q), 380);
  });
  clearBtn.addEventListener('click', () => { input.value = ''; run(''); input.focus(); });
}

// ============================================================
// Playback
// ============================================================
async function playTrack(track, opts = {}) {
  if (!track) return;
  if (!track.offline && !track.url) {
    toast('לשיר אין קישור — לא ניתן לנגן', 'error');
    return;
  }

  state.currentTrack = track;
  updateNowPlayingUI();
  toast(`טוען: ${track.name}`);

  let streamUrl = '';
  try {
    if (track.offline) {
      const data = await apiOfflineStream(track.videoId);
      streamUrl = data.stream_url || '';
    } else {
      const data = await apiResolveStream(track, state.quality);
      streamUrl = data.stream_url || '';
      if (data.title && (!track.name || track.name.startsWith('שיר ') || track.name.startsWith('קישור '))) {
        track.name = data.title;
      }
      if (data.uploader) track.uploader = data.uploader;
      if (data.video_id) track.videoId = data.video_id;
    }
  } catch (e) {
    toast(`לא הצלחתי לטעון את השיר: ${e.message}`, 'error');
    return;
  }

  if (!streamUrl) {
    toast('אין קישור זרימה לשיר', 'error');
    return;
  }

  const useWebAudio = !streamNeedsNativePlayback(streamUrl);
  if (!useWebAudio) {
    if (audio.__auroraSource) resetAudioForNativePlayback();
  } else {
    ensureAudioGraph(streamUrl);
  }

  const ac = getAudioContext();
  if (ac && ac.state === 'suspended') {
    try {
      await ac.resume();
    } catch (_) {}
  }

  if (visualizer) {
    visualizer.attach(audio, { useMediaElementSource: useWebAudio });
  }

  try {
    audio.volume = state.volume;
    audio.src = streamUrl;
    await audio.play();
    state.isPlaying = true;
    document.body.classList.add('is-playing');
    document.querySelector('.ar-app').classList.add('is-playing');
    pushRecent(track);
    updateNowPlayingUI();
    themeFromTrack(track);
    updateMediaSession(track);
    if (visualizer) visualizer.start();
  } catch (e) {
    toast(`לא הצלחתי להריץ: ${e.message}`, 'error');
    state.isPlaying = false;
    document.body.classList.remove('is-playing');
  }
}

function pushRecent(track) {
  const filtered = state.recents.filter(r => r.id !== track.id);
  filtered.unshift({ id: track.id, name: track.name, url: track.url, videoId: track.videoId, uploader: track.uploader });
  state.recents = filtered.slice(0, 30);
  if (!state.library.find(t => t.id === track.id)) {
    state.library.unshift(track);
  } else {
    state.library = state.library.map(t => t.id === track.id ? { ...t, ...track } : t);
  }
  persist();
  renderHome();
}

function togglePlay() {
  if (!state.currentTrack) {
    if (state.library.length) playTrack(state.library[0]);
    return;
  }
  if (audio.paused) {
    const ac = getAudioContext();
    if (ac && ac.state === 'suspended') {
      ac.resume().catch(() => {});
    }
    audio.play().then(() => { state.isPlaying = true; updatePlayIcons(); }).catch(() => {});
  } else {
    audio.pause();
    state.isPlaying = false;
    updatePlayIcons();
  }
}

function nextTrack() {
  const list = currentPlayQueue();
  if (!list.length) return;
  let idx = list.findIndex(t => t.id === state.currentTrack?.id);
  if (state.shuffle) {
    let next;
    do { next = Math.floor(Math.random() * list.length); } while (list.length > 1 && next === idx);
    idx = next;
  } else {
    idx = (idx + 1) % list.length;
  }
  playTrack(list[idx]);
}

function prevTrack() {
  const list = currentPlayQueue();
  if (!list.length) return;
  if (audio.currentTime > 4) { audio.currentTime = 0; return; }
  let idx = list.findIndex(t => t.id === state.currentTrack?.id);
  idx = (idx - 1 + list.length) % list.length;
  playTrack(list[idx]);
}

function currentPlayQueue() {
  if (state.queue.length) return state.queue;
  return state.library;
}

function updatePlayIcons() {
  const playing = state.isPlaying && !audio.paused;
  const useId = playing ? 'i-pause' : 'i-play';
  ['#dockPlayIcon', '#playerPlayIcon', '#driveIcon'].forEach((sel) => {
    const el = $(sel);
    if (el) el.innerHTML = `<use href="#${useId}"/>`;
  });
}

function updateNowPlayingUI() {
  const t = state.currentTrack;
  const dockArt = $('#dockArt');
  const dockTitle = $('#dockTitle');
  const dockSub = $('#dockSub');
  const playerArt = $('#playerArt');
  const playerTitle = $('#playerTitle');
  const playerSub = $('#playerSub');
  const driveArt = $('#driveArt');
  const driveTitle = $('#driveTitle');
  const driveSub = $('#driveSub');

  if (!t) return;
  const thumb = thumbFor(t);
  if (thumb) {
    dockArt.src = thumb; playerArt.src = thumb;
    if (driveArt) driveArt.style.backgroundImage = `url('${thumb}')`;
  }
  dockTitle.textContent = t.name;
  dockSub.textContent = t.uploader || 'YouTube';
  playerTitle.textContent = t.name;
  playerSub.textContent = t.uploader || 'YouTube';
  driveTitle.textContent = t.name;
  driveSub.textContent = t.uploader || 'YouTube';

  const isFav = state.favorites.includes(t.id);
  ['#dockLike', '#playerLike'].forEach((sel) => {
    const el = $(sel);
    if (!el) return;
    el.classList.toggle('is-on', isFav);
    el.querySelector('.ic').innerHTML = `<use href="#i-${isFav ? 'heartFill' : 'heart'}"/>`;
  });
  $('#qualityBtn').textContent = state.quality === 'high' ? 'HD' : 'SD';

  // mark active rows
  $$('.ar-track-row').forEach(r => r.classList.toggle('is-active', r.dataset.playId === t.id));
}

async function themeFromTrack(track) {
  try {
    const palette = await extractPalette(thumbFor(track));
    applyTheme(palette);
  } catch {}
}

function updateMediaSession(track) {
  if (!('mediaSession' in navigator)) return;
  navigator.mediaSession.metadata = new MediaMetadata({
    title: track.name || 'Track',
    artist: track.uploader || 'YouTube',
    artwork: [{ src: thumbFor(track) || '', sizes: '320x180', type: 'image/jpeg' }],
  });
  navigator.mediaSession.setActionHandler('play', () => togglePlay());
  navigator.mediaSession.setActionHandler('pause', () => togglePlay());
  navigator.mediaSession.setActionHandler('previoustrack', () => prevTrack());
  navigator.mediaSession.setActionHandler('nexttrack', () => nextTrack());
}

// ============================================================
// Audio events
// ============================================================
function bindAudio() {
  if (audio.__auroraAudioBound) return;
  audio.__auroraAudioBound = true;
  audio.addEventListener('play', () => { state.isPlaying = true; updatePlayIcons(); });
  audio.addEventListener('pause', () => { state.isPlaying = false; updatePlayIcons(); });
  audio.addEventListener('ended', () => {
    if (state.repeat === 'one') { audio.currentTime = 0; audio.play(); }
    else nextTrack();
  });
  audio.addEventListener('timeupdate', () => {
    const pct = audio.duration ? (audio.currentTime / audio.duration) * 100 : 0;
    $('#progressFill').style.width = `${pct}%`;
    $('#progressThumb').style.insetInlineEnd = `calc(${100 - pct}% - 7px)`;
    $('#dockProgress span').style.width = `${pct}%`;
    $('#timeCurrent').textContent = formatTime(audio.currentTime);
    $('#timeTotal').textContent = formatTime(audio.duration);
  });
  audio.addEventListener('volumechange', () => {
    const v = audio.volume;
    $('#volumeFill').style.width = `${v * 100}%`;
  });
  audio.addEventListener('error', () => {
    toast('שגיאת ניגון — מנסה את הבא', 'error');
    setTimeout(nextTrack, 800);
  });
}

function formatTime(s) {
  if (!isFinite(s) || s < 0) return '0:00';
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return `${m}:${sec < 10 ? '0' : ''}${sec}`;
}

// ============================================================
// Progress / volume seek
// ============================================================
function bindSeek() {
  const track = $('#progressTrack');
  function seek(e) {
    const rect = track.getBoundingClientRect();
    const x = (e.touches ? e.touches[0].clientX : e.clientX) - rect.left;
    const pct = Math.min(1, Math.max(0, document.dir === 'rtl' ? 1 - x / rect.width : x / rect.width));
    if (audio.duration) audio.currentTime = pct * audio.duration;
  }
  track.addEventListener('click', seek);

  const vol = $('#volumeTrack');
  function vseek(e) {
    const rect = vol.getBoundingClientRect();
    const x = (e.touches ? e.touches[0].clientX : e.clientX) - rect.left;
    const pct = Math.min(1, Math.max(0, document.dir === 'rtl' ? 1 - x / rect.width : x / rect.width));
    audio.volume = pct;
    state.volume = pct;
    persist();
  }
  vol.addEventListener('click', vseek);
}

// ============================================================
// Command palette
// ============================================================
function openPalette() {
  state.paletteOpen = true;
  $('.ar-app').classList.add('is-palette-open');
  setTimeout(() => $('#paletteInput').focus(), 80);
  refreshPalette('');
}
function closePalette() {
  state.paletteOpen = false;
  $('.ar-app').classList.remove('is-palette-open');
}

function refreshPalette(query) {
  const q = (query || '').toLowerCase().trim();
  const items = [];

  // Actions
  const actions = [
    { kind: 'action', icon: 'play', title: 'נגן/השהה', sub: 'Space', run: togglePlay },
    { kind: 'action', icon: 'next', title: 'השיר הבא', sub: '→', run: nextTrack },
    { kind: 'action', icon: 'prev', title: 'השיר הקודם', sub: '←', run: prevTrack },
    { kind: 'action', icon: 'shuffle', title: 'ערבוב on/off', sub: '', run: () => { state.shuffle = !state.shuffle; persist(); toast(`ערבוב ${state.shuffle ? 'דלוק' : 'כבוי'}`); } },
    { kind: 'action', icon: 'repeat', title: 'מצב חזרה', sub: '', run: cycleRepeat },
    { kind: 'action', icon: 'car', title: 'הפעל מצב רכב', sub: '', run: enterDrive },
    { kind: 'action', icon: 'qr', title: 'הצג QR למובייל', sub: '', run: openQR },
    { kind: 'action', icon: 'globe', title: 'מדריך Tailscale (גישה מרחוק)', sub: '', run: () => openModal('tsModal') },
    { kind: 'action', icon: 'waves', title: 'פתח EQ', sub: '', run: () => openModal('eqModal') },
    { kind: 'action', icon: 'plus', title: 'פלייליסט חדש', sub: '', run: createPlaylist },
    { kind: 'action', icon: 'download', title: 'שמור את המתנגן עכשיו אופליין', sub: '', run: saveCurrentOffline },
    { kind: 'action', icon: 'sparkles', title: 'החלף איכות (HD/SD)', sub: '', run: toggleQuality },
  ];
  for (const a of actions) {
    if (!q || a.title.toLowerCase().includes(q)) items.push(a);
  }

  // Library tracks
  const tracks = state.library.filter(t => !q || (t.name || '').toLowerCase().includes(q));
  for (const t of tracks.slice(0, 8)) {
    items.push({
      kind: 'track', icon: 'music', title: t.name, sub: t.uploader || 'בספרייה',
      thumb: thumbFor(t),
      run: () => { playTrack(t); closePalette(); },
    });
  }

  // Playlists
  for (const p of state.playlists) {
    if (!q || p.name.toLowerCase().includes(q)) {
      items.push({
        kind: 'playlist', icon: 'music', title: p.name, sub: `${p.tracks.length} שירים`,
        run: () => { openPlaylistDetail(p.id); closePalette(); },
      });
    }
  }

  state.paletteItems = items;
  state.paletteFocus = 0;
  renderPalette();

  // Trigger background search if 2+ chars
  if (q && q.length >= 2) {
    backgroundPaletteSearch(q);
  }
}

let paletteSearchAbort;
async function backgroundPaletteSearch(q) {
  try {
    if (paletteSearchAbort) paletteSearchAbort.abort();
    paletteSearchAbort = new AbortController();
    const r = await apiSearch(q);
    if (state.paletteItems.length === 0 || (r.length && state.paletteOpen)) {
      const yt = r.slice(0, 6).map(item => ({
        kind: 'yt', icon: 'search', title: item.title, sub: `YouTube · ${item.uploader || ''}`,
        thumb: item.thumb,
        run: () => {
          const tr = { id: makeId(`https://www.youtube.com/watch?v=${item.id}`), name: item.title, url: `https://www.youtube.com/watch?v=${item.id}`, videoId: item.id, uploader: item.uploader };
          playTrack(tr);
          closePalette();
        },
      }));
      state.paletteItems.push({ kind: 'header', title: 'תוצאות YouTube' }, ...yt);
      renderPalette();
    }
  } catch {}
}

function renderPalette() {
  const host = $('#paletteResults');
  if (!state.paletteItems.length) {
    host.innerHTML = `<div style="padding:24px;color:var(--text-3);text-align:center">אין תוצאות</div>`;
    return;
  }
  let html = '';
  let inSection = '';
  state.paletteItems.forEach((item, i) => {
    if (item.kind === 'header') {
      inSection = item.title;
      html += `<div class="ar-palette-section-head">${escapeHtml(item.title)}</div>`;
      return;
    }
    if (i === 0 || (state.paletteItems[i - 1].kind && state.paletteItems[i - 1].kind !== item.kind && item.kind !== 'header')) {
      const labels = { action: 'פעולות', track: 'בספרייה', playlist: 'פלייליסטים', yt: 'תוצאות YouTube' };
      if (labels[item.kind] && labels[item.kind] !== inSection) {
        html += `<div class="ar-palette-section-head">${labels[item.kind]}</div>`;
        inSection = labels[item.kind];
      }
    }
    const focused = i === state.paletteFocus ? 'is-focused' : '';
    const thumb = item.thumb ? `<img class="ar-palette-item-thumb" src="${item.thumb}" alt="" loading="lazy"/>` : `<svg class="ic"><use href="#i-${item.icon || 'sparkles'}"/></svg>`;
    html += `
      <div class="ar-palette-item ${focused}" data-palette-idx="${i}">
        ${thumb}
        <div class="ar-palette-item-body">
          <div class="ar-palette-item-title">${escapeHtml(item.title)}</div>
          <div class="ar-palette-item-sub">${escapeHtml(item.sub || '')}</div>
        </div>
      </div>
    `;
  });
  host.innerHTML = html;
}

// ============================================================
// Playlists
// ============================================================
function createPlaylist() {
  const name = prompt('שם הפלייליסט:');
  if (!name || !name.trim()) return;
  state.playlists.unshift({ id: 'pl_' + Date.now(), name: name.trim(), tracks: [] });
  persist();
  renderPlaylistsGrid($('#playlistsGrid'));
  renderAllPlaylists();
  toast(`פלייליסט "${name.trim()}" נוצר`, 'success');
}

function openPlaylistDetail(id) {
  const p = state.playlists.find(x => x.id === id);
  if (!p) return;
  state._currentPlaylistId = id;
  $('#playlistDetailTitle').textContent = p.name;
  const tracks = p.tracks.map(tid => state.library.find(t => t.id === tid)).filter(Boolean);
  renderTrackList($('#playlistTracks'), tracks, 'playlist');
  setView('playlist-detail');
}

function addToPlaylist(trackId) {
  if (!state.playlists.length) {
    if (confirm('אין פלייליסטים. ליצור חדש?')) createPlaylist();
    return;
  }
  const names = state.playlists.map((p, i) => `${i + 1}. ${p.name}`).join('\n');
  const choice = prompt(`לאיזה פלייליסט?\n${names}\nכתבי מספר (1-${state.playlists.length})`);
  const idx = parseInt(choice, 10) - 1;
  if (idx < 0 || idx >= state.playlists.length) return;
  const pl = state.playlists[idx];
  if (!pl.tracks.includes(trackId)) {
    pl.tracks.push(trackId);
    persist();
    toast(`נוסף ל-"${pl.name}"`, 'success');
  } else {
    toast('השיר כבר בפלייליסט');
  }
}

// ============================================================
// EQ
// ============================================================
const EQ_PRESETS = {
  flat: [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
  bass: [6, 5, 3, 1, 0, 0, 0, 0, 0, 0],
  vocal: [-2, -1, 0, 2, 4, 4, 3, 1, 0, 0],
  car: [4, 3, 1, 0, -1, 1, 2, 3, 4, 5],
};

function buildEqUI() {
  const grid = $('#eqGrid');
  grid.innerHTML = EQ_BANDS.map((freq, i) => {
    const v = state.eq.bands[i];
    const label = freq >= 1000 ? `${freq / 1000}k` : `${freq}`;
    return `
      <div class="ar-eq-band">
        <div class="ar-eq-band-value" data-eq-value="${i}">${v.toFixed(1)}</div>
        <input type="range" class="ar-eq-slider" min="-12" max="12" step="0.5" value="${v}" data-eq-band="${i}" orient="vertical"/>
        <div class="ar-eq-band-label">${label}</div>
      </div>
    `;
  }).join('');
  grid.addEventListener('input', (e) => {
    const idx = parseInt(e.target.dataset.eqBand, 10);
    if (Number.isInteger(idx)) {
      state.eq.bands[idx] = parseFloat(e.target.value);
      $(`[data-eq-value="${idx}"]`).textContent = state.eq.bands[idx].toFixed(1);
      applyEqLive();
      persist();
    }
  });
  $('#eqPreamp').value = state.eq.preamp;
  $('#eqPreamp').addEventListener('input', (e) => {
    state.eq.preamp = parseFloat(e.target.value);
    applyEqLive(); persist();
  });
  refreshEqToolbar();
}
function refreshEqToolbar() {
  $('#eqEnableBtn').classList.toggle('is-active', state.eq.enabled);
  $('#eqCompBtn').classList.toggle('is-active', state.eq.comp);
  $('#eqClipBtn').classList.toggle('is-active', state.eq.clip);
}

// ============================================================
// QR
// ============================================================
function openQR() {
  const url = APP.lanUrl || `${window.location.protocol}//${window.location.host}/`;
  $('#qrUrl').textContent = url;
  drawQR($('#qrCode'), url);
  openModal('qrModal');
}
function drawQR(host, text) {
  // Use Google Charts API (no JS dependency); fallback to text if offline.
  const url = `https://api.qrserver.com/v1/create-qr-code/?size=300x300&data=${encodeURIComponent(text)}`;
  host.innerHTML = `<img src="${url}" alt="QR" onerror="this.outerHTML='<div style=\\'color:#000;font-size:0.85rem;text-align:center\\'>${text}</div>'"/>`;
}

// ============================================================
// Drive Mode
// ============================================================
function enterDrive() {
  state.driveMode = true;
  $('.ar-app').classList.add('is-drive');
  if (screen?.orientation?.lock) screen.orientation.lock('landscape').catch(() => {});
  if (document.documentElement.requestFullscreen) document.documentElement.requestFullscreen().catch(() => {});
}
function exitDrive() {
  state.driveMode = false;
  $('.ar-app').classList.remove('is-drive');
  if (document.exitFullscreen) document.exitFullscreen().catch(() => {});
}

// ============================================================
// Modals & helpers
// ============================================================
function openModal(id) { $('#' + id).classList.add('is-open'); }
function closeModal(id) { $('#' + id).classList.remove('is-open'); }

function openPlayer() { state.playerOpen = true; $('.ar-app').classList.add('is-player-open'); }
function closePlayer() { state.playerOpen = false; $('.ar-app').classList.remove('is-player-open'); }
function openQueue() {
  state.queueOpen = true;
  $('.ar-app').classList.add('is-queue-open');
  const pane = $('#queuePane');
  if (pane) pane.setAttribute('aria-hidden', 'false');
  renderQueue();
}
function closeQueue() {
  state.queueOpen = false;
  $('.ar-app').classList.remove('is-queue-open');
  const pane = $('#queuePane');
  if (pane) pane.setAttribute('aria-hidden', 'true');
}
function renderQueue() {
  const host = $('#queueBody');
  const list = (state.queue.length ? state.queue : state.library).slice(0, 50);
  host.innerHTML = list.length ? list.map(t => `
    <div class="ar-track-row" data-play-id="${t.id}">
      <div class="ar-track-num"><span></span></div>
      <div class="ar-track-num-play">${icon('play')}</div>
      <img class="ar-track-art" src="${thumbFor(t)}" alt="" loading="lazy"/>
      <div class="ar-track-info">
        <div class="ar-track-title">${escapeHtml(t.name)}</div>
        <div class="ar-track-sub">${t.uploader || 'YouTube'}</div>
      </div>
    </div>
  `).join('') : `<div style="color:var(--text-3);padding:18px">אין שירים בתור — הוסיפי שירים לספרייה</div>`;
}

function cycleRepeat() {
  const order = ['off', 'all', 'one'];
  state.repeat = order[(order.indexOf(state.repeat) + 1) % order.length];
  persist();
  $('#repeatBtn').classList.toggle('is-on', state.repeat !== 'off');
  $('#repeatBtn').querySelector('.ic').innerHTML = `<use href="#i-${state.repeat === 'one' ? 'repeatOne' : 'repeat'}"/>`;
  toast(`חזרה: ${state.repeat === 'off' ? 'כבוי' : state.repeat === 'all' ? 'הכל' : 'שיר נוכחי'}`);
}

function toggleQuality() {
  state.quality = state.quality === 'high' ? 'normal' : 'high';
  $('#qualityBtn').textContent = state.quality === 'high' ? 'HD' : 'SD';
  persist();
  toast(`איכות: ${state.quality === 'high' ? 'HD' : 'SD'} (יחול בשיר הבא)`);
}

async function saveCurrentOffline() {
  if (!state.currentTrack) { toast('אין שיר מתנגן', 'error'); return; }
  toast('שומר אופליין… זה יכול לקחת רגע');
  try {
    await apiOfflineSave(state.currentTrack, state.quality);
    toast('נשמר אופליין', 'success');
    refreshOffline();
  } catch (e) {
    toast(`שגיאה: ${e.message}`, 'error');
  }
}

function toast(msg, kind = '') {
  const host = $('#toasts');
  const el = document.createElement('div');
  el.className = `ar-toast ${kind === 'error' ? 'is-error' : kind === 'success' ? 'is-success' : ''}`;
  el.textContent = msg;
  host.appendChild(el);
  setTimeout(() => { el.style.opacity = '0'; el.style.transform = 'translateY(-10px)'; el.style.transition = 'all 0.3s'; }, 2400);
  setTimeout(() => el.remove(), 2800);
}

function escapeHtml(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

// ============================================================
// Global event delegation
// ============================================================
function bindEvents() {
  document.addEventListener('click', (e) => {
    /* חשוב: לא להשתמש ב-[data-view] כללי — גם <section class="ar-view"> נושא data-view
       ואז כל לחיצה בבית קוראת setView ו-return לפני playId (שירים לא מנגנים). */
    const navItem = e.target.closest('.ar-nav-item[data-view]');
    const bentoNav = e.target.closest('.ar-bento-tile[data-view]');
    const action = e.target.closest('[data-action]')?.dataset.action;
    const playId = e.target.closest('[data-play-id]')?.dataset.playId;
    const playlistId = e.target.closest('[data-playlist-id]')?.dataset.playlistId;
    const offlineId = e.target.closest('[data-offline-id]')?.dataset.offlineId;
    const searchId = e.target.closest('[data-search-id]')?.dataset.searchId;
    const paletteIdx = e.target.closest('[data-palette-idx]')?.dataset.paletteIdx;
    const eqToggle = e.target.closest('[data-eq-toggle]')?.dataset.eqToggle;
    const eqPreset = e.target.closest('[data-eq-preset]')?.dataset.eqPreset;
    const filter = e.target.closest('[data-filter]')?.dataset.filter;
    const inlineId = e.target.closest('[data-id]')?.dataset.id;
    const inlineVid = e.target.closest('[data-vid]')?.dataset.vid;

    // Track actions (delete row buttons) take priority
    if (action === 'like' && inlineId) { toggleFavorite(inlineId); return; }
    if (action === 'remove-from-library' && inlineId) { removeFromLibrary(inlineId); return; }
    if (action === 'add-to-playlist-id' && inlineId) { addToPlaylist(inlineId); return; }
    if (action === 'save-offline-id' && inlineId) { saveOfflineById(inlineId); return; }
    if (action === 'delete-offline' && inlineVid) { deleteOffline(inlineVid); return; }

    if (eqToggle) {
      if (eqToggle === 'enable') state.eq.enabled = !state.eq.enabled;
      if (eqToggle === 'comp') state.eq.comp = !state.eq.comp;
      if (eqToggle === 'clip') state.eq.clip = !state.eq.clip;
      ensureAudioGraph(audio.currentSrc || audio.src);
      applyEqLive();
      refreshEqToolbar();
      persist();
      return;
    }
    if (eqPreset) {
      state.eq.bands = (EQ_PRESETS[eqPreset] || EQ_PRESETS.flat).slice();
      buildEqUI();
      applyEqLive();
      persist();
      return;
    }

    if (paletteIdx != null) {
      const item = state.paletteItems[parseInt(paletteIdx, 10)];
      if (item && item.run) item.run();
      return;
    }

    if (action) {
      switch (action) {
        case 'toggle-nav': document.querySelector('.ar-app').classList.toggle('is-nav-open'); return;
        case 'open-palette': openPalette(); return;
        case 'close-palette': closePalette(); return;
        case 'open-player': openPlayer(); return;
        case 'close-player': closePlayer(); return;
        case 'open-queue': openQueue(); return;
        case 'close-queue': closeQueue(); return;
        case 'open-eq': openModal('eqModal'); return;
        case 'close-eq': closeModal('eqModal'); return;
        case 'qr-mobile': openQR(); return;
        case 'close-qr': closeModal('qrModal'); return;
        case 'open-tailscale': openModal('tsModal'); return;
        case 'close-ts': closeModal('tsModal'); return;
        case 'drive-mode': enterDrive(); return;
        case 'exit-drive': exitDrive(); return;
        case 'toggle-play': togglePlay(); return;
        case 'next': nextTrack(); return;
        case 'prev': prevTrack(); return;
        case 'shuffle': state.shuffle = !state.shuffle; $('#shuffleBtn').classList.toggle('is-on', state.shuffle); persist(); toast(`ערבוב ${state.shuffle ? 'דלוק' : 'כבוי'}`); return;
        case 'repeat': cycleRepeat(); return;
        case 'like-current': if (state.currentTrack) toggleFavorite(state.currentTrack.id); return;
        case 'add-to-playlist': if (state.currentTrack) addToPlaylist(state.currentTrack.id); return;
        case 'save-current-offline': saveCurrentOffline(); return;
        case 'toggle-quality': toggleQuality(); return;
        case 'new-playlist': createPlaylist(); return;
        case 'show-recents': setView('library'); state.libraryFilter = 'recent'; renderLibrary(); return;
        case 'back-playlists': setView('playlists'); return;
        case 'play-playlist': playPlaylist(state._currentPlaylistId); return;
        case 'rename-playlist': renamePlaylist(state._currentPlaylistId); return;
        case 'delete-playlist': deletePlaylist(state._currentPlaylistId); return;
        case 'copy-lan': navigator.clipboard?.writeText(APP.lanUrl || window.location.href).then(() => toast('הועתק', 'success')); return;
      }
    }

    if (navItem) {
      setView(navItem.dataset.view);
      document.body.classList.remove('is-nav-open');
      document.querySelector('.ar-app')?.classList.remove('is-nav-open');
      return;
    }
    if (bentoNav && !e.target.closest('button')) {
      setView(bentoNav.dataset.view);
      return;
    }
    if (filter) { state.libraryFilter = filter; renderLibrary(); return; }
    if (playlistId) { openPlaylistDetail(playlistId); return; }
    if (offlineId) { playOfflineById(offlineId); return; }
    if (searchId) { playSearchResult(searchId); return; }
    if (playId) {
      const t =
        state.library.find((x) => x.id === playId) ||
        state.recents.find((x) => x.id === playId) ||
        state.queue.find((x) => x.id === playId);
      if (t) {
        playTrack(t);
        if (state.queueOpen) closeQueue();
      } else {
        toast('השיר לא נמצא ברשימה — נסי לרענן את הדף', 'error');
      }
      return;
    }

    // Click outside palette closes it
    if (state.paletteOpen && !e.target.closest('.ar-palette-card')) closePalette();
    // Click outside modals closes them
    ['eqModal', 'qrModal', 'tsModal'].forEach((id) => {
      const m = $('#' + id);
      if (m.classList.contains('is-open') && e.target === m) m.classList.remove('is-open');
    });
    if (state.queueOpen && !e.target.closest('.ar-queue')) {
      closeQueue();
    }
  });

  // Keyboard shortcuts
  document.addEventListener('keydown', (e) => {
    const inField = ['INPUT', 'TEXTAREA'].includes(document.activeElement?.tagName);
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); state.paletteOpen ? closePalette() : openPalette(); return; }
    if (e.key === 'Escape') {
      if (state.paletteOpen) closePalette();
      else if (state.driveMode) exitDrive();
      else if (state.playerOpen) closePlayer();
      else if (state.queueOpen) closeQueue();
      else ['eqModal', 'qrModal', 'tsModal'].forEach((id) => closeModal(id));
      return;
    }
    if (state.paletteOpen) {
      if (e.key === 'ArrowDown') { e.preventDefault(); state.paletteFocus = Math.min(state.paletteItems.length - 1, state.paletteFocus + 1); renderPalette(); }
      if (e.key === 'ArrowUp') { e.preventDefault(); state.paletteFocus = Math.max(0, state.paletteFocus - 1); renderPalette(); }
      if (e.key === 'Enter') { e.preventDefault(); const it = state.paletteItems[state.paletteFocus]; if (it && it.run) it.run(); }
      return;
    }
    if (inField) return;
    if (e.code === 'Space') { e.preventDefault(); togglePlay(); }
    if (e.key === 'ArrowRight') { document.dir === 'rtl' ? nextTrack() : prevTrack(); }
    if (e.key === 'ArrowLeft') { document.dir === 'rtl' ? prevTrack() : nextTrack(); }
  });

  // Palette input
  $('#paletteInput').addEventListener('input', (e) => refreshPalette(e.target.value));
}

function toggleFavorite(id) {
  const i = state.favorites.indexOf(id);
  if (i >= 0) state.favorites.splice(i, 1);
  else state.favorites.unshift(id);
  persist();
  updateNowPlayingUI();
  if (state.view === 'favorites') renderFavorites();
  if (state.view === 'library') renderLibrary();
  $('#bentoFavCount').textContent = `${state.favorites.length} שירים`;
}

function removeFromLibrary(id) {
  if (!confirm('להסיר מהספרייה?')) return;
  state.library = state.library.filter(t => t.id !== id);
  state.favorites = state.favorites.filter(x => x !== id);
  state.recents = state.recents.filter(r => r.id !== id);
  for (const p of state.playlists) p.tracks = p.tracks.filter(x => x !== id);
  persist();
  renderLibrary(); renderHome();
}

function saveOfflineById(id) {
  const t = state.library.find(x => x.id === id);
  if (t) { state.currentTrack = t; saveCurrentOffline(); }
}

async function deleteOffline(vid) {
  if (!confirm('למחוק מהספרייה האופליין?')) return;
  await apiOfflineDelete(vid);
  refreshOffline();
}

async function playOfflineById(vid) {
  const ent = state.offline.find(t => t.video_id === vid);
  if (!ent) return;
  const track = { id: 'off_' + vid, name: ent.title, url: '', videoId: vid, uploader: 'אופליין', offline: true };
  playTrack(track);
}

function playSearchResult(id) {
  const r = state.searchResults.find(x => x.id === id);
  if (!r) return;
  const tr = { id: makeId(`https://www.youtube.com/watch?v=${id}`), name: r.title, url: `https://www.youtube.com/watch?v=${id}`, videoId: id, uploader: r.uploader };
  playTrack(tr);
}

function playPlaylist(plId) {
  const p = state.playlists.find(x => x.id === plId);
  if (!p || !p.tracks.length) return;
  state.queue = p.tracks.map(tid => state.library.find(t => t.id === tid)).filter(Boolean);
  if (state.queue.length) playTrack(state.queue[0]);
}

function renamePlaylist(plId) {
  const p = state.playlists.find(x => x.id === plId);
  if (!p) return;
  const name = prompt('שם חדש:', p.name);
  if (name && name.trim()) { p.name = name.trim(); persist(); openPlaylistDetail(plId); }
}
function deletePlaylist(plId) {
  if (!confirm('למחוק פלייליסט?')) return;
  state.playlists = state.playlists.filter(x => x.id !== plId);
  persist();
  setView('playlists');
}

// ============================================================
// Bootstrap
// ============================================================
function init() {
  buildIconSprite();
  extractPalette('').then((p) => applyTheme(p, { instant: true })).catch(() => {});

  bindAudio();
  bindSeek();
  bindEvents();
  bindSearchView();
  buildEqUI();

  audio.volume = state.volume;

  // Visualizer
  visualizer = new Visualizer($('#vizCanvas'), getAudioContext);
  visualizer.start();

  // Tailscale example URL
  const lanIp = (APP.lanUrl || '').match(/\/\/([\d.]+)/);
  if (lanIp) $('#tsExample').textContent = `http://<Tailscale-IP>:${(APP.lanUrl || '').split(':').pop().replace('/', '') || '5600'}`;

  // First render
  renderHome();
  refreshOffline();
  setView('home');

  const vf = $('#volumeFill');
  if (vf) vf.style.width = `${state.volume * 100}%`;
  const sb = $('#shuffleBtn');
  if (sb) sb.classList.toggle('is-on', state.shuffle);
  const rb = $('#repeatBtn');
  if (rb) {
    rb.classList.toggle('is-on', state.repeat !== 'off');
    const ic = rb.querySelector('.ic');
    if (ic) ic.innerHTML = `<use href="#i-${state.repeat === 'one' ? 'repeatOne' : 'repeat'}"/>`;
  }

  // Resume audio context on first interaction (browser policy)
  const resume = () => {
    const ac = getAudioContext();
    if (ac && ac.state === 'suspended') ac.resume();
    document.removeEventListener('click', resume);
    document.removeEventListener('touchstart', resume);
  };
  document.addEventListener('click', resume);
  document.addEventListener('touchstart', resume);
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
