import { useEffect, useRef } from "react";
import { CATEGORIES, CAT_CAPS, CAT_COLORS, type ReportItem } from "../../types";

interface Props {
  report: ReportItem;
  onHoverChange?: (index: number) => void;
}

export function Donut3DCanvas({ report, onHoverChange }: Props) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const reportRef = useRef(report);
  reportRef.current = report;
  const onHoverRef = useRef(onHoverChange);
  onHoverRef.current = onHoverChange;

  useEffect(() => {
    const cv = canvasRef.current;
    if (!cv) return;
    const x = cv.getContext("2d");
    if (!x) return;

    const RM = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const D2 = Math.min(window.devicePixelRatio || 1, 2);

    const D3 = { yaw: 0.3, drag: 0, m: null as { x: number; y: number } | null };
    let lx = 0;

    const rgbOf = (h: string) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));

    const onPointerDown = (e: PointerEvent) => {
      D3.drag = 1;
      lx = e.clientX;
      cv.setPointerCapture(e.pointerId);
      cv.style.cursor = "grabbing";
    };
    const onPointerMove = (e: PointerEvent) => {
      D3.m = { x: e.offsetX, y: e.offsetY };
      if (D3.drag) {
        D3.yaw += (e.clientX - lx) * 0.012;
        lx = e.clientX;
      }
    };
    const onPointerUp = () => {
      D3.drag = 0;
      cv.style.cursor = "grab";
    };
    const onPointerLeave = () => {
      D3.m = null;
    };

    cv.style.cursor = "grab";
    cv.style.touchAction = "pan-y";
    cv.addEventListener("pointerdown", onPointerDown);
    cv.addEventListener("pointermove", onPointerMove);
    cv.addEventListener("pointerup", onPointerUp);
    cv.addEventListener("pointercancel", onPointerUp);
    cv.addEventListener("pointerleave", onPointerLeave);

    function cam(W: number, H: number, yaw: number, pit: number, sc: number, F = 520) {
      const c = Math.cos(yaw);
      const s = Math.sin(yaw);
      const cp = Math.cos(pit);
      const sp = Math.sin(pit);
      return (px: number, py: number, pz: number): [number, number, number] => {
        const X = px * c + pz * s;
        const Z = -px * s + pz * c;
        const Y = py * cp + Z * sp;
        const D = Z * cp - py * sp;
        const k = (F / (F + D)) * sc;
        return [W / 2 + X * k, H / 2 - Y * k, D];
      };
    }

    let rd = CATEGORIES.map(() => 0);
    let dd = CATEGORIES.map(() => 0);
    let hovState = -1;
    let raf = 0;

    function drawDonut() {
      if (!cv || !x) return;
      const W = cv.clientWidth;
      const H = cv.clientHeight;
      if (!W || !H) return;
      if (cv.width !== W * D2 || cv.height !== H * D2) {
        cv.width = W * D2;
        cv.height = H * D2;
      }

      x.setTransform(D2, 0, 0, D2, 0, 0);
      x.clearRect(0, 0, W, H);
      const tot = dd.reduce((a, b) => a + b, 0);
      if (tot < 0.5) return;

      const sc = Math.min(W / 190, H / 170);
      const P = cam(W, H, D3.yaw, 1, sc);
      const R1 = 66;
      const R0 = 38;
      const hh = (i: number) => 6 + 28 * rd[i];
      interface Slice {
        i: number;
        a0: number;
        a1: number;
        n: number;
      }
      const S: Slice[] = [];
      let a = -Math.PI / 2;

      dd.forEach((v, i) => {
        const da = (v / tot) * Math.PI * 2;
        const g = Math.min(0.03, da * 0.3) / 2;
        if (da > 0.02) {
          S.push({ i, a0: a + g, a1: a + da - g, n: Math.max(2, Math.ceil((da / 6.283) * 72)) });
        }
        a += da;
      });

      let hov = -1;
      if (D3.m) {
        for (const s of S) {
          const p2 = new Path2D();
          const h = hh(s.i);
          for (let j = 0; j < s.n; j++) {
            const ta = s.a0 + ((s.a1 - s.a0) * j) / s.n;
            const tb = s.a0 + ((s.a1 - s.a0) * (j + 1)) / s.n;
            [
              [R1, ta],
              [R1, tb],
              [R0, tb],
              [R0, ta],
            ].forEach(([r, ang], k) => {
              const p = P(r * Math.cos(ang), h, r * Math.sin(ang));
              if (k) p2.lineTo(p[0], p[1]);
              else p2.moveTo(p[0], p[1]);
            });
            p2.closePath();
          }
          if (x.isPointInPath(p2, D3.m.x * D2, D3.m.y * D2)) {
            hov = s.i;
            break;
          }
        }
      }

      if (hov !== hovState) {
        hovState = hov;
        onHoverRef.current?.(hov);
      }

      const c0 = P(0, 0, 0);
      x.fillStyle = "rgba(0,0,0,.28)";
      x.beginPath();
      x.ellipse(c0[0], c0[1] + 4, R1 * sc * 1.05, R1 * sc * Math.sin(1) * 1.0, 0, 0, 7);
      x.fill();

      interface Face {
        pts: [number, number, number][];
        c: string;
        d: number;
      }
      const F: Face[] = [];
      const ys = D3.yaw;
      const sy = Math.sin(ys);
      const cy = Math.cos(ys);

      for (const s of S) {
        const i = s.i;
        const hv = i === hov;
        const h = hh(i) + (hv ? 5 : 0);
        const am = (s.a0 + s.a1) / 2;
        const ex = hv ? 9 : 0;
        const ox = Math.cos(am) * ex;
        const oz = Math.sin(am) * ex;
        const br = hv ? 1.2 : 1;
        const rgb = rgbOf(CAT_COLORS[i]);
        const pt = (r: number, ang: number, y: number) =>
          P(r * Math.cos(ang) + ox, y, r * Math.sin(ang) + oz);

        const add = (pts: [number, number, number][], sh: number) =>
          F.push({
            pts,
            c: `rgb(${rgb
              .map((v) => Math.min(255, Math.round(v * Math.max(0.3, Math.min(1, sh)) * br)))
              .join()})`,
            d: pts.reduce((m, p) => m + p[2], 0) / pts.length,
          });

        for (let j = 0; j < s.n; j++) {
          const ta = s.a0 + ((s.a1 - s.a0) * j) / s.n;
          const tb = s.a0 + ((s.a1 - s.a0) * (j + 1)) / s.n;
          const zn = Math.sin((ta + tb) / 2 - ys);
          add([pt(R1, ta, h), pt(R1, tb, h), pt(R0, tb, h), pt(R0, ta, h)], 1);
          if (zn < 0.15) {
            add([pt(R1, ta, 0), pt(R1, tb, 0), pt(R1, tb, h), pt(R1, ta, h)], 0.55 - 0.3 * zn);
          }
          if (zn > -0.15) {
            add([pt(R0, ta, 0), pt(R0, tb, 0), pt(R0, tb, h), pt(R0, ta, h)], 0.55 + 0.3 * zn);
          }
        }

        [
          [s.a0, 1],
          [s.a1, -1],
        ].forEach(([tAng, sg]) => {
          const nx = sg * Math.sin(tAng);
          const nz = -sg * Math.cos(tAng);
          const zn = -nx * sy + nz * cy;
          if (zn < 0.15) {
            add([pt(R0, tAng, 0), pt(R1, tAng, 0), pt(R1, tAng, h), pt(R0, tAng, h)], 0.55 - 0.3 * zn);
          }
        });
      }

      F.sort((p, q) => q.d - p.d).forEach((f) => {
        x.beginPath();
        f.pts.forEach((p, k) => (k ? x.lineTo(p[0], p[1]) : x.moveTo(p[0], p[1])));
        x.closePath();
        x.fillStyle = f.c;
        x.fill();
        x.strokeStyle = f.c;
        x.lineWidth = 0.6;
        x.stroke();
      });
    }

    function loop() {
      raf = requestAnimationFrame(loop);
      const t = reportRef.current;
      rd = rd.map((v, i) => v + (t.p[i] / CAT_CAPS[i] - v) * 0.08);
      dd = dd.map((v, i) => v + (t.p[i] - v) * 0.08);
      if (!RM && !D3.drag && !D3.m) {
        D3.yaw += 0.004;
      }
      drawDonut();
    }

    raf = requestAnimationFrame(loop);

    return () => {
      cancelAnimationFrame(raf);
      cv.removeEventListener("pointerdown", onPointerDown);
      cv.removeEventListener("pointermove", onPointerMove);
      cv.removeEventListener("pointerup", onPointerUp);
      cv.removeEventListener("pointercancel", onPointerUp);
      cv.removeEventListener("pointerleave", onPointerLeave);
    };
  }, []);

  return (
    <canvas
      id="d3c"
      ref={canvasRef}
      style={{
        position: "absolute",
        inset: 0,
        width: "100%",
        height: "100%",
      }}
    />
  );
}
