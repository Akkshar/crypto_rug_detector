import { useEffect, useRef } from "react";

interface Props {
  active: boolean;
  label?: string;
}

export function InfinityTrailLoader({ active, label = "Loading..." }: Props) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const cv = canvasRef.current;
    if (!cv) return;
    const ctx = cv.getContext("2d");
    if (!ctx) return;

    const RM = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const hx = (h: string) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16) / 255);
    const mx = (a: number[], b: number[], t: number) => a.map((v, i) => v + (b[i] - v) * t);
    const rg = (c: number[], a: number) =>
      `rgba(${c.map((v) => Math.round(v * 255))},${Math.max(0, Math.min(1, a)).toFixed(4)})`;
    const sst = (a: number, b: number, x: number) => {
      const t = Math.max(0, Math.min(1, (x - a) / Math.max(1e-4, b - a)));
      return t * t * (3 - 2 * t);
    };

    const M = 600;
    const K = 140;
    const SP = 128;
    const A = {
      periodMs: 2610,
      startPct: 19,
      offsetY: 3,
      lobeRatio: 44,
      headWidth: 57,
      tailWidth: 5,
      taperPow: 98,
      fadePow: 128,
      coreTint: 55,
      coreWidth: 55,
      innerGlowR: 182,
      innerGlow: 90,
      haloRadius: 200,
      haloAlpha: 25,
      haloFollow: 40,
      lobeWash: 17,
      hueSoft: 73,
      trackOpacity: 10,
      sparkCount: 6,
      sparkMs: 150,
      sparkLifeMs: 1050,
      sparkDrift: 5,
      sparkSize: 4,
      labelOffset: 40,
      labelSize: 6,
      labelWeight: 600,
      labelOpacity: 60,
    };

    const pal = ["#6A5CFF", "#4FD6E0", "#FF0000", "#000DFF"].map(hx);
    const WH = [0.976, 0.992, 0.992];
    const TR = hx("#5F7E9A");
    const size = 300;
    const half = 150;

    const N = 1440;
    const k0 = A.lobeRatio / 100 / 0.35355339;
    const rx: number[] = [];
    const ry: number[] = [];
    const cum: number[] = [0];

    for (let i = 0; i <= N; i++) {
      const t = Math.PI / 2 - (2 * Math.PI * i) / N;
      const s = Math.sin(t);
      const c = Math.cos(t);
      const d = 1 + s * s;
      rx[i] = c / d;
      ry[i] = (-k0 * s * c) / d;
      if (i) cum[i] = cum[i - 1] + Math.hypot(rx[i] - rx[i - 1], ry[i] - ry[i - 1]);
    }

    const px: number[] = [];
    const py: number[] = [];
    const nx: number[] = [];
    const ny: number[] = [];

    {
      let i = 0;
      for (let j = 0; j < M; j++) {
        const tg = (cum[N] * j) / M;
        while (i < N - 1 && cum[i + 1] < tg) i++;
        const f = (tg - cum[i]) / Math.max(1e-9, cum[i + 1] - cum[i]);
        px[j] = rx[i] + (rx[i + 1] - rx[i]) * f;
        py[j] = ry[i] + (ry[i + 1] - ry[i]) * f;
      }
      for (let j = 0; j < M; j++) {
        const a = (j - 1 + M) % M;
        const b = (j + 1) % M;
        const dx = px[b] - px[a];
        const dy = py[b] - py[a];
        const l = Math.hypot(dx, dy) || 1;
        nx[j] = -dy / l;
        ny[j] = dx / l;
      }
    }

    const plen = cum[N];
    const PT = { x: 0, y: 0, nx: 0, ny: 0 };
    function smp(s: number) {
      const f = (s - Math.floor(s)) * M;
      let j = Math.floor(f);
      if (j >= M) j = 0;
      const fr = f - j;
      const b = (j + 1) % M;
      PT.x = px[j] + (px[b] - px[j]) * fr;
      PT.y = py[j] + (py[b] - py[j]) * fr;
      PT.nx = nx[j];
      PT.ny = ny[j];
    }

    const sprite = document.createElement("canvas");
    sprite.width = sprite.height = SP;
    {
      const s = sprite.getContext("2d");
      if (s) {
        const r = SP / 2;
        const g = s.createRadialGradient(r, r, 0, r, r, r);
        [
          [0, 1],
          [0.2, 0.75],
          [0.45, 0.3],
          [0.7, 0.08],
          [1, 0],
        ].forEach(([o, a]) => g.addColorStop(o, `rgba(255,255,255,${a})`));
        s.fillStyle = g;
        s.fillRect(0, 0, SP, SP);
      }
    }

    const tint = document.createElement("canvas");
    tint.width = tint.height = SP;
    const tc = tint.getContext("2d");
    const xs = new Float32Array(K + 1);
    const ys = new Float32Array(K + 1);

    interface Spark {
      x: number;
      y: number;
      vx: number;
      vy: number;
      age: number;
      life: number;
    }
    let sparks: Spark[] = [];
    let sT = 0;
    let clock = 0;
    let last = -1;
    let raf = 0;
    let W = 0;
    let H = 0;
    let dpr = 1;

    function resize() {
      if (!cv) return;
      W = cv.clientWidth;
      H = cv.clientHeight;
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      cv.width = Math.max(1, Math.round(W * dpr));
      cv.height = Math.max(1, Math.round(H * dpr));
    }

    function draw(dt: number) {
      if (!ctx || !tc) return;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, W, H);
      const cx = W / 2;
      const oy = H / 2 + (H * A.offsetY) / 100;
      const len = 0.55;
      const glow = 1;
      const head = clock / (A.periodMs / 1e3) + A.startPct / 100;

      let sx = 0;
      let sw = 0;
      let rw = 0;
      for (let k = 0; k <= K; k++) {
        const u = k / K;
        smp(head - u * len);
        xs[k] = cx + PT.x * half;
        ys[k] = oy + PT.y * half;
        const w = 1 - 0.5 * u;
        sx += PT.x * w;
        sw += w;
        if (PT.x > 0) rw += w;
      }

      const soft = A.hueSoft / 100;
      const col = (() => {
        const tt = sst(-soft, soft, sx / Math.max(1e-4, sw)) * (pal.length - 1);
        const i = Math.min(pal.length - 2, Math.floor(tt));
        return mx(pal[i], pal[i + 1], tt - i);
      })();
      const rF = rw / Math.max(1e-4, sw);
      const hx0 = xs[0];
      const hy0 = ys[0];
      const headW = (size * A.headWidth) / 1e3;
      const tailW = Math.max(0.3, (size * A.tailWidth) / 1e3);

      ctx.globalAlpha = 1;
      ctx.globalCompositeOperation = "source-over";
      {
        const f = A.haloFollow / 100;
        const hcx = cx + (hx0 - cx) * f;
        const hcy = oy + (hy0 - oy) * f;
        const hr = (size * A.haloRadius) / 100;
        const ha = (A.haloAlpha / 100) * glow;
        const g = ctx.createRadialGradient(hcx, hcy, 0, hcx, hcy, hr);
        g.addColorStop(0, rg(col, ha));
        g.addColorStop(0.35, rg(col, ha * 0.5));
        g.addColorStop(0.7, rg(col, ha * 0.15));
        g.addColorStop(1, rg(col, 0));
        ctx.fillStyle = g;
        ctx.fillRect(0, 0, W, H);
      }

      const wash = (A.lobeWash / 100) * Math.min(1.5, glow);
      for (let lo = 0; lo < 2; lo++) {
        const a = wash * (lo ? 1 - rF : rF);
        if (a < 0.003) continue;
        const fr = lo ? M / 2 : 0;
        const to = lo ? M : M / 2;
        ctx.beginPath();
        for (let j = fr; j <= to; j++) {
          const jj = j % M;
          const x = cx + px[jj] * half;
          const y = oy + py[jj] * half;
          if (j === fr) ctx.moveTo(x, y);
          else ctx.lineTo(x, y);
        }
        ctx.closePath();
        const lx = cx + (lo ? -0.58 : 0.58) * half;
        const g = ctx.createRadialGradient(lx, oy, 0, lx, oy, half * 0.6);
        g.addColorStop(0, rg(col, a));
        g.addColorStop(1, rg(col, a * 0.35));
        ctx.fillStyle = g;
        ctx.fill();
      }

      ctx.beginPath();
      for (let j = 0; j < M; j++) {
        const x = cx + px[j] * half;
        const y = oy + py[j] * half;
        if (j) ctx.lineTo(x, y);
        else ctx.moveTo(x, y);
      }
      ctx.closePath();
      ctx.lineJoin = ctx.lineCap = "round";
      const ta = A.trackOpacity / 100;
      ctx.lineWidth = headW * 2.2;
      ctx.strokeStyle = rg(TR, ta * 0.3);
      ctx.stroke();
      ctx.lineWidth = headW;
      ctx.strokeStyle = rg(TR, ta);
      ctx.stroke();

      const spacing = (((plen * half * len) / K) * 1);
      tc.globalCompositeOperation = "copy";
      tc.drawImage(sprite, 0, 0);
      tc.globalCompositeOperation = "source-in";
      tc.fillStyle = rg(col, 1);
      tc.fillRect(0, 0, SP, SP);
      tc.globalCompositeOperation = "source-over";
      ctx.globalCompositeOperation = "lighter";

      for (let k = K; k >= 0; k--) {
        const fall = 1 - k / K;
        const r = Math.max(1, ((headW * A.innerGlowR) / 100) * (0.4 + 0.6 * fall));
        const a = Math.min(
          1,
          (A.innerGlow / 100) * glow * Math.pow(fall, 1.5) * (spacing / r) * 2.2,
        );
        if (a < 0.002) continue;
        ctx.globalAlpha = a;
        ctx.drawImage(tint, xs[k] - r, ys[k] - r, r * 2, r * 2);
      }

      ctx.globalAlpha = 1;
      ctx.globalCompositeOperation = "source-over";
      const tp = A.taperPow / 100;
      const fd = A.fadePow / 100;
      const edge = mx(col, WH, A.coreTint / 100);
      const cw = A.coreWidth / 100;
      ctx.lineCap = "butt";

      for (let pass = 0; pass < 2; pass++) {
        const c = pass ? WH : edge;
        const wm = pass ? cw : 1;
        for (let k = K - 1; k >= 0; k--) {
          const um = (k + 0.5) / K;
          const a = Math.pow(1 - um, fd);
          if (a < 0.003) continue;
          ctx.lineWidth = (tailW + (headW - tailW) * Math.pow(1 - um, tp)) * wm;
          ctx.strokeStyle = rg(c, a);
          ctx.beginPath();
          ctx.moveTo(xs[k + 1], ys[k + 1]);
          ctx.lineTo(xs[k], ys[k]);
          ctx.stroke();
        }
      }

      ctx.fillStyle = rg(edge, 1);
      ctx.beginPath();
      ctx.arc(hx0, hy0, headW / 2, 0, 7);
      ctx.fill();
      ctx.fillStyle = rg(WH, 1);
      ctx.beginPath();
      ctx.arc(hx0, hy0, (headW / 2) * cw, 0, 7);
      ctx.fill();

      const every = A.sparkMs / 1e3;
      const life = A.sparkLifeMs / 1e3;
      const dU = (A.sparkDrift / 100) * 2;
      sT += dt;
      let g8 = 0;
      while (sT >= every && g8++ < 8) {
        sT -= every;
        if (sparks.length >= A.sparkCount) continue;
        smp(head - (0.45 + 0.5 * Math.random()) * len);
        const sd = Math.random() < 0.5 ? -1 : 1;
        const l = life * (0.7 + 0.6 * Math.random());
        const v = (dU / l) * (0.6 + 0.8 * Math.random());
        sparks.push({ x: PT.x, y: PT.y, vx: PT.nx * sd * v, vy: PT.ny * sd * v, age: 0, life: l });
      }
      if (sT > every) sT = 0;

      sparks = sparks.filter(
        (s) => ((s.age += dt), (s.x += s.vx * dt), (s.y += s.vy * dt), s.age < s.life),
      );
      ctx.globalCompositeOperation = "lighter";
      const sr = Math.max(0.6, (size * A.sparkSize) / 1e3);
      const scl = mx(col, WH, 0.7);
      for (const s of sparks) {
        ctx.fillStyle = rg(scl, (1 - s.age / s.life) * 0.85);
        ctx.beginPath();
        ctx.arc(cx + s.x * half, oy + s.y * half, sr, 0, 7);
        ctx.fill();
      }
      ctx.globalCompositeOperation = "source-over";

      ctx.font = `${A.labelWeight} ${Math.max(6, (size * A.labelSize) / 100)}px Inter,system-ui,sans-serif`;
      ctx.textAlign = "center";
      ctx.fillStyle = rg(TR, A.labelOpacity / 100);
      ctx.fillText(label, cx, oy + (size * A.labelOffset) / 100);
    }

    function frame(n: number) {
      raf = requestAnimationFrame(frame);
      const dt = last < 0 ? 0 : Math.min((n - last) / 1e3, 0.05);
      last = n;
      const d = dt * (RM ? 0.25 : 1);
      clock += d;
      draw(d);
    }

    resize();
    last = -1;
    raf = requestAnimationFrame(frame);
    window.addEventListener("resize", resize);

    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", resize);
    };
  }, [label]);

  return (
    <div
      id="ld"
      role="progressbar"
      aria-busy="true"
      aria-label="Loading"
      className={active ? "" : "off"}
    >
      <canvas ref={canvasRef} />
    </div>
  );
}
