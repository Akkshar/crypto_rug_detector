import { useEffect, useRef } from "react";

export function VoxelArcCanvas() {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const cv = canvasRef.current;
    if (!cv) return;
    const x = cv.getContext("2d");
    if (!x) return;

    const RM = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const D2 = Math.min(window.devicePixelRatio || 1, 2);
    const cs = (a: number[]) => a.map(Math.round);
    const mx = (a: number[], b: number[], t: number) => a.map((v, i) => v + (b[i] - v) * t);

    const NZ = 24;
    const VS = 34;
    const BG = [5, 7, 26];
    const B1 = [70, 90, 255];
    const B2 = [79, 214, 224];

    let W = 0;
    let H = 0;
    let t = 0;
    let last = 0;
    let raf = 0;

    const rs = () => {
      const parent = cv.parentElement;
      if (!parent) return;
      W = parent.clientWidth;
      H = parent.clientHeight;
      cv.width = W * D2;
      cv.height = H * D2;
    };
    const parent = cv.parentElement;
    const ro = new ResizeObserver(rs);
    if (parent) ro.observe(parent);
    rs();

    function draw() {
      if (!x) return;
      x.setTransform(D2, 0, 0, D2, 0, 0);
      x.clearRect(0, 0, W, H);
      const cx = W / 2;
      const g = x.createRadialGradient(cx, H * 0.62, 0, cx, H * 0.62, W * 0.6);
      g.addColorStop(0, "rgba(91,108,255,.22)");
      g.addColorStop(1, "rgba(91,108,255,0)");
      x.fillStyle = g;
      x.fillRect(0, 0, W, H);
      x.globalAlpha = 0.8;

      for (let z = NZ - 1; z >= 0; z--) {
        const d = z / (NZ - 1);
        const sc = 1 / (1 + 2.2 * d);
        const tt = (1 - sc) / 0.6875;
        const w = VS * sc;
        const y0 = H * 0.97 - tt * H * 0.42;
        const n = Math.ceil(W / 2 / w) + 2;
        const fog = Math.min(0.85, d * 0.9);

        for (let i = -n; i <= n; i++) {
          const sx = cx + i * w;
          const u = (sx - cx) / W;
          const s =
            (Math.sin(i * 0.32 + t * 0.8 + z * 0.35) +
              Math.sin(z * 0.55 - t * 0.6 + i * 0.12) +
              2) /
            4;
          const lv = Math.floor(s * 4.999);
          const h = lv * VS * 0.3 * sc;
          const y = y0 + u * u * H * 0.5 * tt - h;
          const tc = mx(mx(B1, B2, lv / 4), BG, fog);

          x.fillStyle = `rgb(${cs(mx(tc, BG, 0.45))})`;
          x.fillRect(sx - w / 2 + 0.5, y + w, w - 1, h + w * 0.5);
          x.fillStyle = `rgb(${cs(tc)})`;
          x.fillRect(sx - w / 2 + 0.5, y, w - 1, w);
        }
      }
      x.globalAlpha = 1;
    }

    const f = (n: number) => {
      raf = requestAnimationFrame(f);
      t += Math.min((n - last) / 1e3, 0.05) || 0;
      last = n;
      draw();
    };

    if (RM) draw();
    else raf = requestAnimationFrame(f);

    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
    };
  }, []);

  return <canvas className="vx" ref={canvasRef} aria-hidden="true" />;
}
