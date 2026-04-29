// Living Color — extract dominant palette from artwork and theme the entire UI.
// Pure JS, no dependencies. ~3ms per image on modern devices.

const SAMPLE_SIZE = 64;
const cache = new Map();

function rgbToHsl(r, g, b) {
  r /= 255; g /= 255; b /= 255;
  const max = Math.max(r, g, b), min = Math.min(r, g, b);
  let h = 0, s = 0;
  const l = (max + min) / 2;
  if (max !== min) {
    const d = max - min;
    s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
    switch (max) {
      case r: h = (g - b) / d + (g < b ? 6 : 0); break;
      case g: h = (b - r) / d + 2; break;
      case b: h = (r - g) / d + 4; break;
    }
    h /= 6;
  }
  return [h * 360, s * 100, l * 100];
}

function hslToRgb(h, s, l) {
  h /= 360; s /= 100; l /= 100;
  const a = s * Math.min(l, 1 - l);
  const f = (n) => {
    const k = (n + h * 12) % 12;
    return Math.round(255 * (l - a * Math.max(-1, Math.min(k - 3, 9 - k, 1))));
  };
  return [f(0), f(8), f(4)];
}

export async function extractPalette(imageUrl) {
  if (!imageUrl) return defaultPalette();
  if (cache.has(imageUrl)) return cache.get(imageUrl);
  try {
    const palette = await new Promise((resolve, reject) => {
      const img = new Image();
      img.crossOrigin = 'anonymous';
      img.onerror = () => reject(new Error('img load failed'));
      img.onload = () => {
        const canvas = document.createElement('canvas');
        canvas.width = SAMPLE_SIZE;
        canvas.height = SAMPLE_SIZE;
        const ctx = canvas.getContext('2d', { willReadFrequently: true });
        if (!ctx) { reject(new Error('no ctx')); return; }
        ctx.drawImage(img, 0, 0, SAMPLE_SIZE, SAMPLE_SIZE);
        const { data } = ctx.getImageData(0, 0, SAMPLE_SIZE, SAMPLE_SIZE);
        resolve(quantize(data));
      };
      img.src = imageUrl;
    });
    cache.set(imageUrl, palette);
    return palette;
  } catch {
    return defaultPalette();
  }
}

function quantize(data) {
  const buckets = new Map();
  for (let i = 0; i < data.length; i += 16) {
    const r = data[i], g = data[i + 1], b = data[i + 2], a = data[i + 3];
    if (a < 200) continue;
    const [h, s, l] = rgbToHsl(r, g, b);
    if (l < 8 || l > 92) continue;
    if (s < 12) continue;
    const key = `${Math.round(h / 12) * 12}_${Math.round(s / 20) * 20}_${Math.round(l / 14) * 14}`;
    const cur = buckets.get(key) || { count: 0, h: 0, s: 0, l: 0 };
    cur.count++;
    cur.h += h; cur.s += s; cur.l += l;
    buckets.set(key, cur);
  }
  const sorted = [...buckets.values()]
    .map(b => ({ count: b.count, h: b.h / b.count, s: b.s / b.count, l: b.l / b.count }))
    .sort((a, b) => b.count - a.count);
  if (!sorted.length) return defaultPalette();
  const primary = sorted[0];
  const secondary = sorted.find(b => Math.abs(b.h - primary.h) > 30) || sorted[1] || primary;
  const accent = sorted.find(b => b.s > 50 && b.l > 50 && b.l < 80) || primary;
  return {
    primary: hslCss(primary.h, Math.min(primary.s, 70), Math.min(primary.l, 60)),
    secondary: hslCss(secondary.h, Math.min(secondary.s, 60), Math.min(secondary.l, 50)),
    accent: hslCss(accent.h, Math.min(accent.s + 10, 85), 65),
    accentRgb: hslToRgb(accent.h, Math.min(accent.s + 10, 85), 65).join(','),
    deep: hslCss(primary.h, Math.min(primary.s + 5, 60), 8),
    glow: hslCss(accent.h, 90, 70),
  };
}

function hslCss(h, s, l) {
  return `hsl(${h.toFixed(1)} ${s.toFixed(1)}% ${l.toFixed(1)}%)`;
}

function defaultPalette() {
  return {
    primary: 'hsl(195 80% 55%)',
    secondary: 'hsl(265 60% 45%)',
    accent: 'hsl(190 100% 65%)',
    accentRgb: '94,223,255',
    deep: 'hsl(220 30% 8%)',
    glow: 'hsl(190 100% 70%)',
  };
}

export function applyTheme(palette, opts = {}) {
  const root = document.documentElement;
  const transition = opts.instant ? 'none' : '1.4s cubic-bezier(.2,.7,.2,1)';
  root.style.setProperty('--theme-transition', transition);
  root.style.setProperty('--accent', palette.accent);
  root.style.setProperty('--accent-rgb', palette.accentRgb);
  root.style.setProperty('--accent-glow', palette.glow);
  root.style.setProperty('--theme-primary', palette.primary);
  root.style.setProperty('--theme-secondary', palette.secondary);
  root.style.setProperty('--theme-deep', palette.deep);
  root.style.setProperty(
    '--theme-mesh',
    `radial-gradient(at 18% 22%, ${palette.primary}55, transparent 55%),
     radial-gradient(at 82% 18%, ${palette.secondary}40, transparent 55%),
     radial-gradient(at 50% 90%, ${palette.accent}30, transparent 55%),
     radial-gradient(at 20% 80%, ${palette.deep}, transparent 70%)`,
  );
}
