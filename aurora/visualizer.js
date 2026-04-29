// Aurora Visualizer — reactive particle field + frequency rings.
// Custom Canvas2D with motion-blur compositing. Targets 60fps even on mid-range mobile.

export class Visualizer {
  constructor(canvas, getAudioContext) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.getAudioContext = getAudioContext;
    this.analyser = null;
    this.freqData = null;
    this.timeData = null;
    this.particles = [];
    this.running = false;
    this.tick = this.tick.bind(this);
    this.resize();
    window.addEventListener('resize', () => this.resize());
    this.spawnParticles(80);
  }

  resize() {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const w = this.canvas.clientWidth || this.canvas.parentElement?.clientWidth || 800;
    const h = this.canvas.clientHeight || this.canvas.parentElement?.clientHeight || 400;
    this.canvas.width = Math.round(w * dpr);
    this.canvas.height = Math.round(h * dpr);
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.w = w; this.h = h;
  }

  spawnParticles(n) {
    this.particles = [];
    for (let i = 0; i < n; i++) {
      this.particles.push({
        x: Math.random() * (this.w || 800),
        y: Math.random() * (this.h || 400),
        vx: (Math.random() - 0.5) * 0.3,
        vy: (Math.random() - 0.5) * 0.3,
        r: 1 + Math.random() * 2,
        phase: Math.random() * Math.PI * 2,
      });
    }
  }

  attach(mediaElement, opts = {}) {
    const useMediaElementSource = !!opts.useMediaElementSource;
    try {
      const ac = this.getAudioContext();
      if (!ac || !mediaElement) return;
      if (mediaElement.__auroraSource) {
        /* ensureAudioGraph() יוצר מקור בלי analyser — לא לקרוס על frequencyBinCount */
        this.analyser = mediaElement.__auroraAnalyser || null;
      } else if (useMediaElementSource) {
        const src = ac.createMediaElementSource(mediaElement);
        const analyser = ac.createAnalyser();
        analyser.fftSize = 256;
        analyser.smoothingTimeConstant = 0.82;
        src.connect(analyser);
        if (mediaElement.__auroraTail) {
          analyser.connect(mediaElement.__auroraTail);
        } else {
          analyser.connect(ac.destination);
        }
        mediaElement.__auroraSource = src;
        mediaElement.__auroraAnalyser = analyser;
        this.analyser = analyser;
      } else {
        /* זרם חוצה-מקור: MediaElementSource שקט בלי CORS — נשארים על ניגון רגיל + ויזואליזציה סינתטית */
        this.analyser = null;
      }
      if (this.analyser) {
        this.freqData = new Uint8Array(this.analyser.frequencyBinCount);
        this.timeData = new Uint8Array(this.analyser.frequencyBinCount);
      } else {
        this.freqData = null;
        this.timeData = null;
      }
    } catch (e) {
      // already-connected media element will throw — silently ignore.
    }
  }

  start() {
    if (this.running) return;
    this.running = true;
    requestAnimationFrame(this.tick);
  }

  stop() { this.running = false; }

  tick(t) {
    if (!this.running) return;
    requestAnimationFrame(this.tick);
    const ctx = this.ctx;
    const w = this.w, h = this.h;

    let bass = 0, mid = 0, treble = 0;
    if (this.analyser && this.freqData) {
      this.analyser.getByteFrequencyData(this.freqData);
      const f = this.freqData;
      const third = Math.floor(f.length / 3);
      for (let i = 0; i < third; i++) bass += f[i];
      for (let i = third; i < third * 2; i++) mid += f[i];
      for (let i = third * 2; i < f.length; i++) treble += f[i];
      bass = bass / (third * 255);
      mid = mid / (third * 255);
      treble = treble / ((f.length - third * 2) * 255);
    } else {
      const time = t / 1000;
      bass = (Math.sin(time * 1.3) + 1) * 0.18;
      mid = (Math.sin(time * 0.7) + 1) * 0.16;
      treble = (Math.sin(time * 2.1) + 1) * 0.12;
    }

    ctx.globalCompositeOperation = 'source-over';
    ctx.fillStyle = 'rgba(8,10,18,0.18)';
    ctx.fillRect(0, 0, w, h);

    const accent = getComputedStyle(document.documentElement).getPropertyValue('--accent-rgb').trim() || '94,223,255';
    const cx = w / 2, cy = h / 2;
    const baseRadius = Math.min(w, h) * 0.22;

    ctx.globalCompositeOperation = 'lighter';

    for (let ring = 0; ring < 3; ring++) {
      const energy = ring === 0 ? bass : ring === 1 ? mid : treble;
      const r = baseRadius * (1 + ring * 0.28) + energy * 80;
      ctx.beginPath();
      ctx.arc(cx, cy, r, 0, Math.PI * 2);
      ctx.strokeStyle = `rgba(${accent}, ${0.06 + energy * 0.5})`;
      ctx.lineWidth = 1 + energy * 6;
      ctx.stroke();
    }

    const segments = 96;
    if (this.freqData) {
      ctx.beginPath();
      for (let i = 0; i < segments; i++) {
        const idx = Math.floor((i / segments) * this.freqData.length * 0.7);
        const v = this.freqData[idx] / 255;
        const angle = (i / segments) * Math.PI * 2 - Math.PI / 2;
        const r = baseRadius * 1.6 + v * 90;
        const px = cx + Math.cos(angle) * r;
        const py = cy + Math.sin(angle) * r;
        if (i === 0) ctx.moveTo(px, py);
        else ctx.lineTo(px, py);
      }
      ctx.closePath();
      ctx.strokeStyle = `rgba(${accent}, 0.7)`;
      ctx.lineWidth = 1.4;
      ctx.stroke();
      ctx.fillStyle = `rgba(${accent}, 0.06)`;
      ctx.fill();
    }

    const energySum = bass + mid + treble;
    for (const p of this.particles) {
      p.phase += 0.01 + treble * 0.04;
      p.x += p.vx + Math.cos(p.phase) * (0.2 + bass * 0.7);
      p.y += p.vy + Math.sin(p.phase) * (0.2 + bass * 0.7);
      if (p.x < 0) p.x = w; else if (p.x > w) p.x = 0;
      if (p.y < 0) p.y = h; else if (p.y > h) p.y = 0;
      const r = p.r * (1 + energySum * 0.6);
      ctx.beginPath();
      ctx.arc(p.x, p.y, r, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(${accent}, ${0.18 + energySum * 0.18})`;
      ctx.fill();
    }
  }
}
