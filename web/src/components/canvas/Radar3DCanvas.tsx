import { useEffect, useRef } from "react";
import { CATEGORIES, CAT_CAPS, CAT_COLORS, type ReportItem } from "../../types";

interface Props {
  report: ReportItem;
}

export function Radar3DCanvas({ report }: Props) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const reportRef = useRef(report);
  reportRef.current = report;

  useEffect(() => {
    const cv = canvasRef.current;
    if (!cv) return;
    const x = cv.getContext("2d");
    if (!x) return;

    const RM = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const D2 = Math.min(window.devicePixelRatio || 1, 2);

    const R3 = { yaw: 0.5, drag: 0, m: null as { x: number; y: number } | null };
    let lx = 0;

    const onPointerDown = (e: PointerEvent) => {
      R3.drag = 1;
      lx = e.clientX;
      cv.setPointerCapture(e.pointerId);
      cv.style.cursor = "grabbing";
    };
    const onPointerMove = (e: PointerEvent) => {
      R3.m = { x: e.offsetX, y: e.offsetY };
      if (R3.drag) {
        R3.yaw += (e.clientX - lx) * 0.012;
        lx = e.clientX;
      }
    };
    const onPointerUp = () => {
      R3.drag = 0;
      cv.style.cursor = "grab";
    };
    const onPointerLeave = () => {
      R3.m = null;
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
    let raf = 0;

    function drawRadar() {
      if (!cv || !x) return;
      const t = reportRef.current;
      const W = cv.clientWidth;
      if (!W) return;
      const H = Math.round(W * 0.8);
      if (cv.width !== W * D2 || cv.height !== H * D2) {
        cv.width = W * D2;
        cv.height = H * D2;
        cv.style.height = H + "px";
      }

      x.setTransform(D2, 0, 0, D2, 0, 0);
      x.clearRect(0, 0, W, H);
      const P = cam(W, H, R3.yaw, 0.95, W / 300);
      const R = 100;
      const TH = 30;
      const ang = (i: number) => -Math.PI / 2 + (i * Math.PI) / 3;
      const at = (r: number, y: number, i: number) =>
        P(r * Math.cos(ang(i)), y, r * Math.sin(ang(i)));

      const path = (pts: [number, number, number][]) => {
        x.beginPath();
        pts.forEach((p, i) => (i ? x.lineTo(p[0], p[1]) : x.moveTo(p[0], p[1])));
        x.closePath();
      };

      x.lineWidth = 1;
      [1 / 3, 2 / 3, 1].forEach((f) => {
        path(CATEGORIES.map((_, i) => at(R * f, 0, i)));
        if (f === 1) {
          x.fillStyle = "rgba(91,108,255,.07)";
          x.fill();
        }
        x.strokeStyle = "#2a3380";
        x.stroke();
      });

      const c0 = P(0, 0, 0);
      CATEGORIES.forEach((_, i) => {
        const p = at(R, 0, i);
        x.beginPath();
        x.moveTo(c0[0], c0[1]);
        x.lineTo(p[0], p[1]);
        x.stroke();
      });

      const r = rd.map((v) => Math.max(0.05, v) * R);
      const Fl = CATEGORIES.map((_, i) => at(r[i], 0, i));
      const Tp = CATEGORIES.map((_, i) => at(r[i], TH, i));

      CATEGORIES.map((_, i) => {
        const j = (i + 1) % 6;
        const q = [Fl[i], Fl[j], Tp[j], Tp[i]];
        return { q, i, d: q.reduce((m, p) => m + p[2], 0) / 4 };
      })
        .sort((a, b) => b.d - a.d)
        .forEach((s) => {
          path(s.q);
          x.fillStyle = `rgba(122,77,255,${0.2 + 0.12 * (s.i % 2)})`;
          x.fill();
          x.strokeStyle = "rgba(192,122,255,.55)";
          x.stroke();
        });

      path(Tp);
      x.fillStyle = "rgba(150,110,255,.5)";
      x.fill();
      x.strokeStyle = "#c07aff";
      x.lineWidth = 2;
      x.stroke();
      x.lineWidth = 1;
      x.textAlign = "center";

      CATEGORIES.forEach((n, i) => {
        x.strokeStyle = "rgba(192,122,255,.7)";
        x.beginPath();
        x.moveTo(Fl[i][0], Fl[i][1]);
        x.lineTo(Tp[i][0], Tp[i][1]);
        x.stroke();
        x.fillStyle = CAT_COLORS[i];
        x.shadowColor = CAT_COLORS[i];
        x.shadowBlur = 8;
        x.beginPath();
        x.arc(Tp[i][0], Tp[i][1], 3.5, 0, 7);
        x.fill();
        x.shadowBlur = 0;

        x.fillStyle = "#fff";
        x.font = "bold 11px 'Segoe UI',sans-serif";
        x.fillText(String(t.p[i]), Tp[i][0], Tp[i][1] - 9);
        const L = at(R * 1.2, 0, i);
        x.fillStyle = "#cfd3ff";
        x.font = "10px 'Segoe UI',sans-serif";
        x.fillText(n.split(" ")[0], L[0], L[1] + 3);
      });
    }

    function loop() {
      raf = requestAnimationFrame(loop);
      const t = reportRef.current;
      rd = rd.map((v, i) => v + (t.p[i] / CAT_CAPS[i] - v) * 0.08);
      if (!RM && !R3.drag && !R3.m) {
        R3.yaw += 0.004;
      }
      drawRadar();
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
      id="r3"
      ref={canvasRef}
      role="img"
      aria-label="3D radar of category scores"
      style={{ width: "100%", display: "block" }}
    />
  );
}
