import { useEffect, useRef } from "react";

export function GlobeCanvas() {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const cv = canvasRef.current;
    if (!cv) return;
    const x = cv.getContext("2d");
    if (!x) return;

    const RM = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const D2 = Math.min(window.devicePixelRatio || 1, 2);
    const PI = Math.PI;

    const V3 = (la: number, lo: number): [number, number, number] => {
      la *= PI / 180;
      lo *= PI / 180;
      return [Math.cos(la) * Math.sin(lo), Math.sin(la), Math.cos(la) * Math.cos(lo)];
    };

    const dots: [[number, number, number], boolean][] = [];
    const lines: [number, number, number][][] = [];

    for (let la = -87; la <= 87; la += 3) {
      const n = Math.max(1, Math.round(120 * Math.cos((la * PI) / 180)));
      for (let k = 0; k < n; k++) {
        const p = V3(la, (k / n) * 360 - 180);
        const v =
          Math.sin(p[0] * 4.1 + 1) * Math.cos(p[1] * 3.7 - 0.5) +
          Math.sin(p[2] * 5.3 + p[0] * 2.2) * Math.sin(p[1] * 4.4 + p[2] * 1.7) +
          0.5 * Math.sin(p[0] * 9 + p[1] * 7 + p[2] * 6);
        dots.push([p, v > 0.5]);
      }
    }

    for (let la = -60; la <= 60; la += 30) {
      const l: [number, number, number][] = [];
      for (let lo = 0; lo <= 360; lo += 6) l.push(V3(la, lo));
      lines.push(l);
    }
    for (let lo = 0; lo < 360; lo += 30) {
      const l: [number, number, number][] = [];
      for (let la = -90; la <= 90; la += 6) l.push(V3(la, lo));
      lines.push(l);
    }

    const cityRaw: [number, number][] = [
      [40.7, -74],
      [51.5, -0.1],
      [1.35, 103.8],
      [35.7, 139.7],
      [-23.5, -46.6],
      [25.2, 55.3],
      [-33.9, 151.2],
      [6.5, 3.4],
      [37.8, -122.4],
      [19, 72.9],
    ];
    const city = cityRaw.map((c) => V3(c[0], c[1]));

    const arcPairs = [
      [0, 1],
      [1, 2],
      [3, 8],
      [4, 7],
      [5, 9],
      [6, 3],
      [8, 0],
      [2, 6],
    ];
    const AR = arcPairs.map((p, i) => ({
      a: city[p[0]],
      b: city[p[1]],
      o: i * 0.85,
      w: Math.acos(
        Math.max(-1, Math.min(1, city[p[0]].reduce((s, v, j) => s + v * city[p[1]][j], 0))),
      ),
    }));

    const TC = Math.cos(0.35);
    const TS = Math.sin(0.35);
    const LG = [-0.5, 0.55, 0.65];
    const LC = ["#2a3fd0", "#4d6bff", "#4FD6E0", "#c4f7ff"];
    const cz = Math.cos(0.45);
    const sz = Math.sin(0.45);

    const rp = (a: number): [number, number, number] => {
      const px = Math.cos(a) * 1.4;
      const X = px * cz;
      const y = px * sz;
      const Z = Math.sin(a) * 1.4;
      return [X, y * TC - Z * TS, y * TS + Z * TC];
    };
    const RP = Array.from({ length: 97 }, (_, i) => rp((i / 96) * PI * 2));

    let W = 0;
    let H = 0;
    let t = RM ? 1.2 : 0;
    let last = 0;
    let yaw = 0.6;
    let drag = 0;
    let lx = 0;
    let raf = 0;

    const rot = (p: [number, number, number]): [number, number, number] => {
      const c = Math.cos(yaw);
      const s = Math.sin(yaw);
      const X = p[0] * c + p[2] * s;
      const Z = -p[0] * s + p[2] * c;
      return [X, p[1] * TC - Z * TS, p[1] * TS + Z * TC];
    };

    const arc = (
      A: { a: [number, number, number]; b: [number, number, number]; w: number },
      u: number,
    ): [number, number, number] => {
      const s = Math.sin(A.w) || 1;
      const k1 = Math.sin((1 - u) * A.w) / s;
      const k2 = Math.sin(u * A.w) / s;
      const r = 1 + 0.22 * Math.sin(PI * u);
      return [
        (A.a[0] * k1 + A.b[0] * k2) * r,
        (A.a[1] * k1 + A.b[1] * k2) * r,
        (A.a[2] * k1 + A.b[2] * k2) * r,
      ];
    };

    const rs = () => {
      if (!cv) return;
      W = cv.clientWidth;
      H = cv.clientHeight;
      cv.width = W * D2;
      cv.height = H * D2;
    };
    const ro = new ResizeObserver(rs);
    ro.observe(cv);
    rs();

    const onPointerDown = (e: PointerEvent) => {
      drag = 1;
      lx = e.clientX;
      cv.setPointerCapture(e.pointerId);
    };
    const onPointerMove = (e: PointerEvent) => {
      if (drag) {
        yaw += (e.clientX - lx) * 0.01;
        lx = e.clientX;
      }
    };
    const onPointerUp = () => {
      drag = 0;
    };

    cv.addEventListener("pointerdown", onPointerDown);
    cv.addEventListener("pointermove", onPointerMove);
    cv.addEventListener("pointerup", onPointerUp);
    cv.addEventListener("pointercancel", onPointerUp);

    function draw() {
      if (!x) return;
      x.setTransform(D2, 0, 0, D2, 0, 0);
      x.clearRect(0, 0, W, H);
      const m = Math.min(W, H);
      const cx = W / 2;
      const cy = H / 2;
      const R = m * 0.34;
      const P = (q: [number, number, number]): [number, number] => [cx + q[0] * R, cy - q[1] * R];

      let g = x.createRadialGradient(cx, cy, R * 0.95, cx, cy, m * 0.5);
      g.addColorStop(0, "rgba(110,130,255,.42)");
      g.addColorStop(0.5, "rgba(91,108,255,.12)");
      g.addColorStop(1, "rgba(91,108,255,0)");
      x.fillStyle = g;
      x.fillRect(0, 0, W, H);

      const ring = (front: boolean) => {
        x.lineWidth = 1;
        x.strokeStyle = `rgba(154,176,255,${front ? 0.5 : 0.22})`;
        x.beginPath();
        let on = 0;
        for (let i = 0; i <= 96; i++) {
          const q = RP[i];
          const f = (q[2] >= 0) === front;
          if (!f) {
            on = 0;
            continue;
          }
          const p = P(q);
          if (on) x.lineTo(p[0], p[1]);
          else x.moveTo(p[0], p[1]);
          on = 1;
        }
        x.stroke();

        for (let s = 0; s < 3; s++) {
          const q = rp(t * 0.45 + s * 2.094);
          if (q[2] >= 0 !== front) continue;
          const p = P(q);
          x.fillStyle = "#fff";
          x.shadowColor = "#9ab0ff";
          x.shadowBlur = 12;
          x.beginPath();
          x.arc(p[0], p[1], 2.6, 0, 7);
          x.fill();
          x.shadowBlur = 0;
        }
      };

      ring(false);

      const b = x.createRadialGradient(cx - R * 0.35, cy - R * 0.4, 0, cx, cy, R);
      b.addColorStop(0, "rgba(70,90,235,.55)");
      b.addColorStop(1, "rgba(6,10,45,.8)");
      x.fillStyle = b;
      x.beginPath();
      x.arc(cx, cy, R, 0, 7);
      x.fill();

      g = x.createRadialGradient(cx, cy, R * 0.78, cx, cy, R);
      g.addColorStop(0, "rgba(79,214,224,0)");
      g.addColorStop(1, "rgba(122,141,255,.4)");
      x.fillStyle = g;
      x.beginPath();
      x.arc(cx, cy, R, 0, 7);
      x.fill();

      x.lineWidth = 0.6;
      x.strokeStyle = "rgba(122,141,255,.14)";
      for (const l of lines) {
        x.beginPath();
        let on = 0;
        for (const v of l) {
          const q = rot(v);
          if (q[2] < 0) {
            on = 0;
            continue;
          }
          const p = P(q);
          if (on) x.lineTo(p[0], p[1]);
          else x.moveTo(p[0], p[1]);
          on = 1;
        }
        x.stroke();
      }

      for (const [d, l] of dots) {
        const q = rot(d);
        if (q[2] < 0) continue;
        const lam = Math.max(0, q[0] * LG[0] + q[1] * LG[1] + q[2] * LG[2]);
        const s = l ? 1.9 : 1.2;
        x.globalAlpha = l ? 0.25 + 0.75 * q[2] : (0.1 + 0.3 * lam) * q[2] + 0.05;
        x.fillStyle = l ? LC[Math.min(3, Math.floor(lam * 4))] : "#3b49b8";
        x.fillRect(cx + q[0] * R - s / 2, cy - q[1] * R - s / 2, s, s);
      }
      x.globalAlpha = 1;

      x.lineWidth = 1.5;
      x.strokeStyle = "rgba(150,170,255,.55)";
      x.beginPath();
      x.arc(cx, cy, R, 0, 7);
      x.stroke();

      city.forEach((c, i) => {
        const q = rot(c);
        if (q[2] < 0.05) return;
        const p = P(q);
        const ph = (t * 0.8 + i * 0.37) % 1;
        x.strokeStyle = `rgba(79,214,224,${(1 - ph) * 0.6})`;
        x.lineWidth = 1;
        x.beginPath();
        x.arc(p[0], p[1], 2 + ph * 10, 0, 7);
        x.stroke();
        x.fillStyle = "#7ff0ff";
        x.beginPath();
        x.arc(p[0], p[1], 2.6, 0, 7);
        x.fill();
      });

      x.lineCap = "round";
      for (const A of AR) {
        const h = (((t + A.o) % 3.4) / 3.4) * 1.4;
        const h0 = Math.max(0, h - 0.4);
        const h1 = Math.min(1, h);
        if (h0 >= 1 || h1 <= h0) continue;
        let pv: [number, number, number] | null = null;
        for (let k = 0; k <= 20; k++) {
          const u = h0 + ((h1 - h0) * k) / 20;
          const q = rot(arc(A, u));
          if (pv && q[2] > 0 && pv[2] > 0) {
            const a = P(pv);
            const c = P(q);
            const f = k / 20;
            x.beginPath();
            x.moveTo(a[0], a[1]);
            x.lineTo(c[0], c[1]);
            x.lineWidth = 4;
            x.strokeStyle = `rgba(79,214,224,${0.12 * f})`;
            x.stroke();
            x.lineWidth = 1 + 1.4 * f;
            x.strokeStyle = `rgba(${k > 14 ? "210,248,255" : "79,214,224"},${0.2 + 0.8 * f})`;
            x.stroke();
          }
          pv = q;
        }
        if (pv && pv[2] > 0 && h <= 1) {
          const p = P(pv);
          x.fillStyle = "#fff";
          x.shadowColor = "#4FD6E0";
          x.shadowBlur = 14;
          x.beginPath();
          x.arc(p[0], p[1], 2.8, 0, 7);
          x.fill();
          x.shadowBlur = 0;
        }
      }
      ring(true);
    }

    const f = (n: number) => {
      raf = requestAnimationFrame(f);
      const dt = Math.min((n - last) / 1e3, 0.05) || 0;
      last = n;
      t += dt;
      if (!drag) yaw += dt * 0.22;
      draw();
    };

    if (RM) draw();
    else raf = requestAnimationFrame(f);

    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      cv.removeEventListener("pointerdown", onPointerDown);
      cv.removeEventListener("pointermove", onPointerMove);
      cv.removeEventListener("pointerup", onPointerUp);
      cv.removeEventListener("pointercancel", onPointerUp);
    };
  }, []);

  return (
    <canvas
      id="globe3"
      ref={canvasRef}
      role="img"
      aria-label="Rotating dotted globe with animated connection arcs"
    />
  );
}
