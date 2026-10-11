import { useEffect, useRef } from "react";
import { SEC } from "../../types";

interface Props {
  targetColor?: [number, number, number];
  glowAlpha?: number;
}

export function AuraBlobCanvas({
  targetColor = [255, 74, 94],
  glowAlpha = 1,
}: Props) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const targetColorRef = useRef(targetColor);
  const glowAlphaRef = useRef(glowAlpha);
  targetColorRef.current = targetColor;
  glowAlphaRef.current = glowAlpha;

  useEffect(() => {
    const cv = canvasRef.current;
    if (!cv) return;
    const x = cv.getContext("2d");
    if (!x) return;

    const RM = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const D2 = Math.min(window.devicePixelRatio || 1, 2);
    const cs = (a: number[]) => a.map(Math.round);
    const mx = (a: number[], b: number[], t: number) => a.map((v, i) => v + (b[i] - v) * t);

    const S = 270;
    cv.width = cv.height = S * D2;
    let t = 0;
    let GC = [91, 108, 255];
    let raf = 0;

    function f() {
      raf = requestAnimationFrame(f);
      t += RM ? 0 : 0.016;
      const GT = targetColorRef.current;
      const GA = glowAlphaRef.current;
      GC = GC.map((v, i) => v + (GT[i] - v) * 0.05);

      if (!x) return;
      x.setTransform(D2, 0, 0, D2, 0, 0);
      x.clearRect(0, 0, S, S);
      x.globalCompositeOperation = "lighter";

      [44, 60, 76, 94].forEach((R0, i) => {
        const c = cs(mx(GC, SEC, i / 4));
        const R = R0 * (0.85 + 0.25 * GA);
        const cx = S / 2 + Math.sin(t * 0.7 + i * 1.7) * 5 * i;
        const cy = S / 2 + Math.cos(t * 0.6 + i * 2.1) * 5 * i;
        const a = [0.85, 0.5, 0.3, 0.2][i] * GA;

        x.beginPath();
        for (let k = 0; k <= 72; k++) {
          const th = (k / 72) * Math.PI * 2;
          const r =
            R *
            (1 +
              0.07 * Math.sin(3 * th + t * (1.1 + 0.3 * i) + i) +
              0.05 * Math.sin(5 * th - t * 0.9 + i * 2));
          if (k) x.lineTo(cx + Math.cos(th) * r, cy + Math.sin(th) * r);
          else x.moveTo(cx + Math.cos(th) * r, cy + Math.sin(th) * r);
        }
        const g = x.createRadialGradient(cx, cy, 0, cx, cy, R * 1.12);
        g.addColorStop(0, `rgba(${c},${a})`);
        g.addColorStop(0.55, `rgba(${c},${a * 0.45})`);
        g.addColorStop(1, `rgba(${c},0)`);
        x.fillStyle = g;
        x.fill();
      });
    }

    raf = requestAnimationFrame(f);

    return () => {
      cancelAnimationFrame(raf);
    };
  }, []);

  return <canvas className="aura" ref={canvasRef} aria-hidden="true" />;
}
