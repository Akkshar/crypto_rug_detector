import { useEffect, useRef } from "react";

interface Props {
  targetColor?: [number, number, number];
}

export function AstralShellCanvas({ targetColor = [255, 74, 94] }: Props) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const targetColorRef = useRef(targetColor);
  targetColorRef.current = targetColor;

  useEffect(() => {
    const cv = canvasRef.current;
    if (!cv) return;
    const x = cv.getContext("2d");
    if (!x) return;

    const RM = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const D2 = Math.min(window.devicePixelRatio || 1, 2);
    const cs = (a: number[]) => a.map(Math.round);

    const S = 240;
    const N = 64;
    const P: [number, number, number][] = [];
    const E: [number, number, number][] = [];

    cv.width = cv.height = S * D2;

    for (let i = 0; i < N; i++) {
      const y = 1 - (2 * (i + 0.5)) / N;
      const r = Math.sqrt(1 - y * y);
      const a = i * 2.39996;
      P.push([Math.cos(a) * r, y, Math.sin(a) * r]);
    }

    for (let i = 0; i < N; i++) {
      for (let j = i + 1; j < N; j++) {
        const d = Math.hypot(P[i][0] - P[j][0], P[i][1] - P[j][1], P[i][2] - P[j][2]);
        if (d < 0.7) E.push([i, j, d]);
      }
    }

    let t = 0;
    let GC = [91, 108, 255];
    const ct = Math.cos(0.35);
    const st = Math.sin(0.35);
    let raf = 0;

    function f() {
      raf = requestAnimationFrame(f);
      t += RM ? 0.0005 : 0.004;
      const GT = targetColorRef.current;
      GC = GC.map((v, i) => v + (GT[i] - v) * 0.05);

      if (!x) return;
      x.setTransform(D2, 0, 0, D2, 0, 0);
      x.clearRect(0, 0, S, S);
      const cy = Math.cos(t);
      const sy = Math.sin(t);
      const c = cs(GC);
      const g = x.createRadialGradient(S / 2, S / 2, 0, S / 2, S / 2, 105);
      g.addColorStop(0, `rgba(${c},.2)`);
      g.addColorStop(1, `rgba(${c},0)`);
      x.fillStyle = g;
      x.fillRect(0, 0, S, S);

      const Q = P.map((p) => {
        const X = p[0] * cy + p[2] * sy;
        let Z = -p[0] * sy + p[2] * cy;
        const Y = p[1] * ct - Z * st;
        Z = p[1] * st + Z * ct;
        return [S / 2 + X * 92, S / 2 + Y * 92, Z];
      });

      x.lineWidth = 0.8;
      E.forEach(([i, j, d]) => {
        const a = Q[i];
        const b = Q[j];
        x.strokeStyle = `rgba(${c},${
          (0.08 + (0.45 * ((a[2] + b[2]) / 2 + 1)) / 2) * (1.1 - d / 0.7)
        })`;
        x.beginPath();
        x.moveTo(a[0], a[1]);
        x.lineTo(b[0], b[1]);
        x.stroke();
      });

      Q.forEach((q) => {
        const k = (q[2] + 1) / 2;
        x.fillStyle = `rgba(235,240,255,${0.25 + 0.75 * k})`;
        x.beginPath();
        x.arc(q[0], q[1], 0.8 + 1.6 * k, 0, 7);
        x.fill();
      });
    }

    raf = requestAnimationFrame(f);

    return () => {
      cancelAnimationFrame(raf);
    };
  }, []);

  return <canvas id="shell" ref={canvasRef} />;
}
