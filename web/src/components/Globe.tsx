import { useEffect, useRef } from "react";

export interface GlobeArc {
  lat1: number;
  lon1: number;
  lat2: number;
  lon2: number;
  color?: string;
  speed?: number;
}

export interface GlobeProps {
  size?: number;
  className?: string;
  rotationSpeed?: number;
  interactive?: boolean;
  dotColor?: string;
  dotSize?: number;
  atmosphereColor?: string;
  arcColor?: string;
  showOrbitRing?: boolean;
  showAtmosphere?: boolean;
  arcs?: GlobeArc[];
}

export default function Globe({
  size = 460,
  className = "",
  rotationSpeed = 0.006,
  interactive = true,
  dotColor = "#7a8fff",
  dotSize = 1.6,
  atmosphereColor = "rgba(91, 108, 255, 0.4)",
  arcColor = "rgba(79, 214, 224, 0.9)",
  showOrbitRing = true,
  showAtmosphere = true,
  arcs,
}: GlobeProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const cv = canvasRef.current;
    if (!cv) return;
    const ctx = cv.getContext("2d");
    if (!ctx) return;

    let raf = 0;
    let yaw = 0.55;
    let t = 0;
    let isDragging = false;
    let lastX = 0;
    let dragVelocity = 0;

    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const W = size;
    const H = size;
    cv.width = W * dpr;
    cv.height = H * dpr;
    ctx.scale(dpr, dpr);

    const cx = W / 2;
    const cy = H / 2;
    const R = size * 0.35; // Radius of globe

    // Precalculate dots with spherical harmonics to simulate continents
    const V3 = (la: number, lo: number): [number, number, number] => {
      const laR = (la * Math.PI) / 180;
      const loR = (lo * Math.PI) / 180;
      return [
        Math.cos(laR) * Math.sin(loR),
        Math.sin(laR),
        Math.cos(laR) * Math.cos(loR),
      ];
    };

    const dots: Array<{ p: [number, number, number]; isLand: boolean }> = [];
    for (let la = -84; la <= 84; la += 3.5) {
      const n = Math.max(1, Math.round(110 * Math.cos((la * Math.PI) / 180)));
      for (let k = 0; k < n; k++) {
        const lo = (k / n) * 360 - 180;
        const p = V3(la, lo);
        // Harmonic noise approximating Earth continents
        const v =
          Math.sin(p[0] * 4.1 + 1) * Math.cos(p[1] * 3.7 - 0.5) +
          Math.sin(p[2] * 5.3 + p[0] * 2.2) * Math.sin(p[1] * 4.4 + p[2] * 1.7) +
          0.5 * Math.sin(p[0] * 9 + p[1] * 7 + p[2] * 6);
        dots.push({ p, isLand: v > 0.45 });
      }
    }

    // Default arcs between key crypto nodes (New York, London, Singapore, Tokyo, Dubai, São Paulo)
    const defaultCities = [
      { name: "New York", lat: 40.71, lon: -74.0 },
      { name: "London", lat: 51.5, lon: -0.12 },
      { name: "Singapore", lat: 1.35, lon: 103.82 },
      { name: "Tokyo", lat: 35.68, lon: 139.76 },
      { name: "Dubai", lat: 25.2, lon: 55.27 },
      { name: "Frankfurt", lat: 50.11, lon: 8.68 },
    ];

    const activeArcs =
      arcs && arcs.length > 0
        ? arcs
        : [
            { lat1: defaultCities[0].lat, lon1: defaultCities[0].lon, lat2: defaultCities[1].lat, lon2: defaultCities[1].lon, speed: 0.9 },
            { lat1: defaultCities[1].lat, lon1: defaultCities[1].lon, lat2: defaultCities[2].lat, lon2: defaultCities[2].lon, speed: 0.8 },
            { lat1: defaultCities[2].lat, lon1: defaultCities[2].lon, lat2: defaultCities[3].lat, lon2: defaultCities[3].lon, speed: 1.1 },
            { lat1: defaultCities[1].lat, lon1: defaultCities[1].lon, lat2: defaultCities[4].lat, lon2: defaultCities[4].lon, speed: 1.0 },
            { lat1: defaultCities[0].lat, lon1: defaultCities[0].lon, lat2: defaultCities[3].lat, lon2: defaultCities[3].lon, speed: 0.75 },
          ];

    // Orbit ring precomputation
    const tilt = 0.38;
    const cosTilt = Math.cos(tilt);
    const sinTilt = Math.sin(tilt);

    const onPointerDown = (e: PointerEvent) => {
      if (!interactive) return;
      isDragging = true;
      lastX = e.clientX;
      dragVelocity = 0;
      cv.setPointerCapture(e.pointerId);
    };

    const onPointerMove = (e: PointerEvent) => {
      if (!isDragging) return;
      const dx = e.clientX - lastX;
      dragVelocity = dx * 0.01;
      yaw += dragVelocity;
      lastX = e.clientX;
    };

    const onPointerUp = (e: PointerEvent) => {
      isDragging = false;
      try {
        cv.releasePointerCapture(e.pointerId);
      } catch {
        // pointer not captured
      }
    };

    cv.addEventListener("pointerdown", onPointerDown);
    cv.addEventListener("pointermove", onPointerMove);
    cv.addEventListener("pointerup", onPointerUp);
    cv.addEventListener("pointercancel", onPointerUp);

    const draw = () => {
      raf = requestAnimationFrame(draw);
      t += 0.016;

      if (!isDragging) {
        dragVelocity *= 0.92;
        yaw += rotationSpeed + dragVelocity;
      }

      ctx.clearRect(0, 0, W, H);

      // 1. Atmosphere Halo
      if (showAtmosphere) {
        const glow = ctx.createRadialGradient(cx, cy, R * 0.85, cx, cy, R * 1.35);
        glow.addColorStop(0, atmosphereColor);
        glow.addColorStop(0.5, "rgba(91, 108, 255, 0.12)");
        glow.addColorStop(1, "rgba(0, 0, 0, 0)");
        ctx.fillStyle = glow;
        ctx.fillRect(0, 0, W, H);
      }

      // Back of Orbit Ring
      if (showOrbitRing) {
        ctx.lineWidth = 1;
        ctx.strokeStyle = "rgba(154, 176, 255, 0.2)";
        ctx.beginPath();
        for (let a = 0; a <= Math.PI * 2; a += 0.08) {
          const rx = Math.cos(a) * R * 1.45;
          const rz = Math.sin(a) * R * 1.45;
          const ry = rz * sinTilt;
          const rzProj = rz * cosTilt;
          if (rzProj < 0) {
            ctx.lineTo(cx + rx, cy + ry);
          } else {
            ctx.moveTo(cx + rx, cy + ry);
          }
        }
        ctx.stroke();
      }

      // 2. Base Sphere Lit Body
      const sphereGrad = ctx.createRadialGradient(cx - R * 0.35, cy - R * 0.35, 0, cx, cy, R);
      sphereGrad.addColorStop(0, "rgba(25, 36, 110, 0.88)");
      sphereGrad.addColorStop(0.7, "rgba(12, 17, 58, 0.95)");
      sphereGrad.addColorStop(1, "rgba(5, 7, 28, 0.98)");
      ctx.fillStyle = sphereGrad;
      ctx.beginPath();
      ctx.arc(cx, cy, R, 0, Math.PI * 2);
      ctx.fill();

      // Inner Atmosphere edge
      ctx.strokeStyle = "rgba(122, 141, 255, 0.4)";
      ctx.lineWidth = 1.4;
      ctx.stroke();

      // 3. Render Globe Dots
      const cosY = Math.cos(yaw);
      const sinY = Math.sin(yaw);

      for (let i = 0; i < dots.length; i++) {
        const { p, isLand } = dots[i];
        // Rotate around Y axis
        const rx = p[0] * cosY + p[2] * sinY;
        const rz = -p[0] * sinY + p[2] * cosY;
        const ry = p[1];

        // Apply tilt
        const px = rx;
        const py = ry * cosTilt - rz * sinTilt;
        const pz = ry * sinTilt + rz * cosTilt;

        if (pz > 0) {
          const depth = pz;
          const alpha = isLand ? 0.35 + 0.65 * depth : 0.08 + 0.2 * depth;
          const dotRadius = isLand ? dotSize : dotSize * 0.65;

          ctx.fillStyle = isLand ? dotColor : "rgba(100, 120, 220, 0.4)";
          ctx.globalAlpha = alpha;
          ctx.beginPath();
          ctx.arc(cx + px * R, cy - py * R, dotRadius, 0, Math.PI * 2);
          ctx.fill();
        }
      }
      ctx.globalAlpha = 1;

      // 4. City Beacons & Animated Connection Arcs
      const to3D = (lat: number, lon: number): [number, number, number] => {
        const v = V3(lat, lon);
        const rx = v[0] * cosY + v[2] * sinY;
        const rz = -v[0] * sinY + v[2] * cosY;
        const ry = v[1];
        return [rx, ry * cosTilt - rz * sinTilt, ry * sinTilt + rz * cosTilt];
      };

      // Draw active arcs
      activeArcs.forEach((arc, idx) => {
        const p1 = to3D(arc.lat1, arc.lon1);
        const p2 = to3D(arc.lat2, arc.lon2);

        // Only draw arc if at least one point is somewhat facing the camera
        if (p1[2] > -0.2 || p2[2] > -0.2) {
          const x1 = cx + p1[0] * R;
          const y1 = cy - p1[1] * R;
          const x2 = cx + p2[0] * R;
          const y2 = cy - p2[1] * R;

          const midX = (x1 + x2) / 2;
          const midY = (y1 + y2) / 2 - R * 0.32; // arch outward

          // Arc trail line
          ctx.beginPath();
          ctx.moveTo(x1, y1);
          ctx.quadraticCurveTo(midX, midY, x2, y2);
          ctx.strokeStyle = "rgba(79, 214, 224, 0.28)";
          ctx.lineWidth = 1.4;
          ctx.stroke();

          // Traveling glowing pulse
          const spd = arc.speed || 0.8;
          const progress = ((t * spd + idx * 0.35) % 1);
          const u = progress;
          const pulseX = (1 - u) * (1 - u) * x1 + 2 * (1 - u) * u * midX + u * u * x2;
          const pulseY = (1 - u) * (1 - u) * y1 + 2 * (1 - u) * u * midY + u * u * y2;

          ctx.fillStyle = "#ffffff";
          ctx.shadowColor = arcColor;
          ctx.shadowBlur = 12;
          ctx.beginPath();
          ctx.arc(pulseX, pulseY, 3.2, 0, Math.PI * 2);
          ctx.fill();
          ctx.shadowBlur = 0;

          // Pulsing city nodes
          if (p1[2] > 0) {
            ctx.fillStyle = "#4FD6E0";
            ctx.beginPath();
            ctx.arc(x1, y1, 2.8, 0, Math.PI * 2);
            ctx.fill();

            const wave = (t * 1.5 + idx) % 1;
            ctx.strokeStyle = `rgba(79, 214, 224, ${1 - wave})`;
            ctx.lineWidth = 1;
            ctx.beginPath();
            ctx.arc(x1, y1, 3 + wave * 9, 0, Math.PI * 2);
            ctx.stroke();
          }

          if (p2[2] > 0) {
            ctx.fillStyle = "#4FD6E0";
            ctx.beginPath();
            ctx.arc(x2, y2, 2.8, 0, Math.PI * 2);
            ctx.fill();
          }
        }
      });

      // Front of Orbit Ring & orbiting satellites
      if (showOrbitRing) {
        ctx.lineWidth = 1.2;
        ctx.strokeStyle = "rgba(154, 176, 255, 0.45)";
        ctx.beginPath();
        for (let a = 0; a <= Math.PI * 2; a += 0.08) {
          const rx = Math.cos(a) * R * 1.45;
          const rz = Math.sin(a) * R * 1.45;
          const ry = rz * sinTilt;
          const rzProj = rz * cosTilt;
          if (rzProj >= 0) {
            ctx.lineTo(cx + rx, cy + ry);
          } else {
            ctx.moveTo(cx + rx, cy + ry);
          }
        }
        ctx.stroke();

        // 2 Orbiting Satellite Dots
        for (let s = 0; s < 2; s++) {
          const sAngle = t * 0.45 + s * Math.PI;
          const sx = Math.cos(sAngle) * R * 1.45;
          const sz = Math.sin(sAngle) * R * 1.45;
          const sy = sz * sinTilt;
          const szProj = sz * cosTilt;

          if (szProj >= 0) {
            ctx.fillStyle = "#ffffff";
            ctx.shadowColor = "#9ab0ff";
            ctx.shadowBlur = 10;
            ctx.beginPath();
            ctx.arc(cx + sx, cy + sy, 3, 0, Math.PI * 2);
            ctx.fill();
            ctx.shadowBlur = 0;
          }
        }
      }
    };

    draw();

    return () => {
      cancelAnimationFrame(raf);
      cv.removeEventListener("pointerdown", onPointerDown);
      cv.removeEventListener("pointermove", onPointerMove);
      cv.removeEventListener("pointerup", onPointerUp);
      cv.removeEventListener("pointercancel", onPointerUp);
    };
  }, [
    size,
    rotationSpeed,
    interactive,
    dotColor,
    dotSize,
    atmosphereColor,
    arcColor,
    showOrbitRing,
    showAtmosphere,
    arcs,
  ]);

  return (
    <canvas
      ref={canvasRef}
      className={`block select-none ${className}`}
      style={{
        width: size,
        height: size,
        cursor: interactive ? "grab" : "default",
        touchAction: "none",
      }}
      role="img"
      aria-label="3D dotted globe with animated connection arcs"
    />
  );
}
