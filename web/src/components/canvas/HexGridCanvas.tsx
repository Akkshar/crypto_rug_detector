import { useEffect, useRef } from "react";

interface Props {
  visible: boolean;
  targetColor?: [number, number, number];
}

export function HexGridCanvas({ visible, targetColor = [255, 74, 94] }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const targetColorRef = useRef(targetColor);
  targetColorRef.current = targetColor;

  useEffect(() => {
    const host = containerRef.current;
    const cv = canvasRef.current;
    if (!host || !cv) return;
    const x = cv.getContext("2d");
    if (!x) return;

    const RM = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const D2 = Math.min(window.devicePixelRatio || 1, 2);
    const cs = (a: number[]) => a.map(Math.round);

    const R = 17;
    const w = Math.sqrt(3) * R;
    let W = 0;
    let H = 0;
    let lx = 0;
    let ly = 0;
    let tx = 0;
    let ty = 0;
    let hv = 0;
    let t = 0;
    let GC = [91, 108, 255];
    let raf = 0;

    const rs = () => {
      W = host.clientWidth;
      H = host.clientHeight;
      cv.width = W * D2;
      cv.height = H * D2;
    };
    const ro = new ResizeObserver(rs);
    ro.observe(host);
    rs();
    lx = tx = W / 2;
    ly = ty = H / 2;

    const onPointerMove = (e: PointerEvent) => {
      const b = host.getBoundingClientRect();
      tx = e.clientX - b.left;
      ty = e.clientY - b.top;
      hv = 1;
    };
    const onPointerLeave = () => {
      hv = 0;
    };

    window.addEventListener("pointermove", onPointerMove);
    document.documentElement.addEventListener("pointerleave", onPointerLeave);

    function f() {
      raf = requestAnimationFrame(f);
      t += RM ? 0 : 0.008;
      const GT = targetColorRef.current;
      GC = GC.map((v, i) => v + (GT[i] - v) * 0.05);

      if (!hv) {
        tx = W * (0.5 + 0.4 * Math.sin(t));
        ty = H * (0.5 + 0.3 * Math.cos(t * 1.3));
      }
      lx += (tx - lx) * 0.12;
      ly += (ty - ly) * 0.12;

      if (!x) return;
      x.setTransform(D2, 0, 0, D2, 0, 0);
      x.clearRect(0, 0, W, H);
      const c = cs(GC);
      const g = x.createRadialGradient(lx, ly, 0, lx, ly, 320);
      g.addColorStop(0, `rgba(${c},.14)`);
      g.addColorStop(1, `rgba(${c},0)`);
      x.fillStyle = g;
      x.fillRect(0, 0, W, H);
      x.lineWidth = 1;

      for (let r = -1; r * R * 1.5 < H + R; r++) {
        for (let q = -1; q * w < W + w; q++) {
          const cx = q * w + (r & 1 ? w / 2 : 0);
          const cy = r * R * 1.5;
          const fl = Math.max(0, 1 - Math.hypot(cx - lx, cy - ly) / 280);
          const f2 = fl * fl;
          x.beginPath();
          for (let k = 0; k < 6; k++) {
            const a = Math.PI / 6 + (k * Math.PI) / 3;
            const px = cx + Math.cos(a) * (R - 1.5);
            const py = cy + Math.sin(a) * (R - 1.5);
            if (k) x.lineTo(px, py);
            else x.moveTo(px, py);
          }
          x.closePath();
          x.strokeStyle = `rgba(${c},${0.12 + 0.75 * f2})`;
          x.stroke();
          if (f2 > 0.02) {
            x.fillStyle = `rgba(${c},${0.14 * f2})`;
            x.fill();
          }
        }
      }
    }

    raf = requestAnimationFrame(f);

    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      window.removeEventListener("pointermove", onPointerMove);
      document.documentElement.removeEventListener("pointerleave", onPointerLeave);
    };
  }, []);

  return (
    <div
      ref={containerRef}
      className="hvbg"
      data-g="1"
      style={{ display: visible ? "" : "none" }}
    >
      <canvas ref={canvasRef} className="hv" aria-hidden="true" />
    </div>
  );
}
