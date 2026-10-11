import { useEffect, useRef } from "react";

export interface GlobeArc {
  lat1: number;
  lon1: number;
  lat2: number;
  lon2: number;
  label1?: string;
  label2?: string;
}

export interface GlobeProps {
  size?: number;
  className?: string;
  rotationSpeed?: number;
  interactive?: boolean;
  tilt?: number; // degrees, default 23.5
  showAtmosphere?: boolean;
  showArcs?: boolean;
  arcs?: GlobeArc[];
}

// Real-world major financial & crypto hub cities
export const DEFAULT_CITIES: Array<{ name: string; lat: number; lon: number }> = [
  { name: "New York", lat: 40.71, lon: -74.01 },
  { name: "London", lat: 51.51, lon: -0.13 },
  { name: "Dubai", lat: 25.2, lon: 55.27 },
  { name: "Mumbai", lat: 19.08, lon: 72.88 },
  { name: "Singapore", lat: 1.35, lon: 103.82 },
  { name: "Tokyo", lat: 35.68, lon: 139.77 },
  { name: "Sydney", lat: -33.87, lon: 151.21 },
  { name: "São Paulo", lat: -23.55, lon: -46.63 },
  { name: "Lagos", lat: 6.52, lon: 3.38 },
];

export const DEFAULT_ARCS: GlobeArc[] = [
  { lat1: 40.71, lon1: -74.01, lat2: 51.51, lon2: -0.13, label1: "New York", label2: "London" },
  { lat1: 51.51, lon1: -0.13, lat2: 25.2, lon2: 55.27, label1: "London", label2: "Dubai" },
  { lat1: 25.2, lon1: 55.27, lat2: 19.08, lon2: 72.88, label1: "Dubai", label2: "Mumbai" },
  { lat1: 19.08, lon1: 72.88, lat2: 1.35, lon2: 103.82, label1: "Mumbai", label2: "Singapore" },
  { lat1: 1.35, lon1: 103.82, lat2: 35.68, lon2: 139.77, label1: "Singapore", label2: "Tokyo" },
  { lat1: 1.35, lon1: 103.82, lat2: -33.87, lon2: 151.21, label1: "Singapore", label2: "Sydney" },
  { lat1: 40.71, lon1: -74.01, lat2: -23.55, lon2: -46.63, label1: "New York", label2: "São Paulo" },
  { lat1: 51.51, lon1: -0.13, lat2: 6.52, lon2: 3.38, label1: "London", label2: "Lagos" },
  { lat1: -23.55, lon1: -46.63, lat2: 6.52, lon2: 3.38, label1: "São Paulo", label2: "Lagos" },
];

/**
 * High-accuracy real-world landmass test function
 * Returns true if the coordinate is on land (Africa, Americas, Europe, Asia, Australia, Antarctica)
 */
function isEarthLand(lat: number, lon: number): boolean {
  // Antarctica
  if (lat < -62) return true;

  // Australia & New Zealand
  if (lat >= -39 && lat <= -11 && lon >= 113 && lon <= 154) {
    if (lat > -19 && lon > 135 && lon < 142) return false; // Gulf of Carpentaria
    return true;
  }
  if (lat >= -47 && lat <= -34 && lon >= 166 && lon <= 178) return true; // New Zealand

  // Africa
  if (lat >= -35 && lat <= 37 && lon >= -18 && lon <= 52) {
    // Exclude Atlantic/Indian ocean borders
    if (lat < 5 && lon < 8) return false; // Gulf of Guinea
    if (lat < -10 && lon > 41) return false;
    if (lat > 32 && lon > 32 && lon < 36) return false; // Eastern Med
    if (lat > 12 && lat < 30 && lon > 32 && lon < 44) return false; // Red Sea
    return true;
  }
  // Madagascar
  if (lat >= -26 && lat <= -12 && lon >= 43 && lon <= 51) return true;

  // South America
  if (lat >= -56 && lat <= 13 && lon >= -82 && lon <= -34) {
    if (lat > 5 && lon > -50) return false;
    if (lat < -20 && lon > -53) return false; // South Atlantic
    if (lat < -40 && lon > -60) return false;
    if (lat > 0 && lon < -78) return false;
    return true;
  }

  // North America
  if (lat >= 7 && lat <= 83 && lon >= -168 && lon <= -52) {
    // Mexico & Central America
    if (lat < 30 && (lon < -116 || lon > -76)) return false;
    // Gulf of Mexico
    if (lat >= 19 && lat <= 30 && lon >= -97 && lon <= -82) return false;
    // Hudson Bay
    if (lat >= 51 && lat <= 65 && lon >= -95 && lon <= -77) return false;
    // Greenland
    if (lat >= 60 && lon >= -55 && lon <= -18) return true;
    if (lat > 50) return true;
    if (lat >= 25 && lon >= -125 && lon <= -67) return true; // USA
    if (lat >= 14 && lat < 25 && lon >= -106 && lon <= -86) return true; // Mexico
    if (lat >= 7 && lat < 14 && lon >= -86 && lon <= -77) return true; // Central America
  }

  // Europe
  if (lat >= 36 && lat <= 71 && lon >= -11 && lon <= 40) {
    // Mediterranean sea
    if (lat >= 33 && lat <= 41 && lon >= -3 && lon <= 24) {
      if (lat >= 36 && lat <= 44 && lon >= -9 && lon <= 3) return true; // Spain
      if (lat >= 37 && lat <= 46 && lon >= 8 && lon <= 19) return true; // Italy
      if (lat >= 36 && lat <= 41 && lon >= 20 && lon <= 26) return true; // Greece
      return false;
    }
    // Baltic sea
    if (lat >= 54 && lat <= 65 && lon >= 14 && lon <= 24) return false;
    // Black sea
    if (lat >= 41 && lat <= 47 && lon >= 28 && lon <= 42) return false;
    // UK & Ireland
    if (lat >= 50 && lat <= 59 && lon >= -11 && lon <= 2) return true;
    // Scandinavia
    if (lat >= 55 && lat <= 71 && lon >= 4 && lon <= 32) return true;
    // Mainland Europe
    if (lat >= 43 && lat <= 56 && lon >= -5 && lon <= 35) return true;
  }

  // Asia
  if (lat >= 1 && lat <= 77 && lon >= 34 && lon <= 180) {
    // Arabian peninsula
    if (lat >= 12 && lat <= 33 && lon >= 34 && lon <= 60) {
      if (lat > 22 && lon < 37) return false;
      return true;
    }
    // Indian subcontinent
    if (lat >= 7 && lat <= 36 && lon >= 68 && lon <= 92) {
      // Indian Ocean tapers south
      if (lat < 22 && (lon < 72 || lon > 87)) {
        if (lat >= 6 && lat <= 10 && lon >= 79 && lon <= 82) return true; // Sri Lanka
        return false;
      }
      return true;
    }
    // Southeast Asia & Indochina
    if (lat >= 1 && lat <= 24 && lon >= 95 && lon <= 110) return true;
    // Indonesia & Malaysia
    if (lat >= -11 && lat <= 7 && lon >= 95 && lon <= 141) return true;
    // Philippines
    if (lat >= 5 && lat <= 19 && lon >= 118 && lon <= 127) return true;
    // Japan
    if (lat >= 30 && lat <= 46 && lon >= 128 && lon <= 146) return true;
    // Korea
    if (lat >= 34 && lat <= 43 && lon >= 124 && lon <= 130) return true;
    // China, Central Asia, Russia / Siberia
    if (lat >= 20 && lat <= 77 && lon >= 60 && lon <= 180) {
      // Exclude Sea of Okhotsk
      if (lat >= 50 && lat <= 60 && lon >= 145 && lon <= 156) return false;
      return true;
    }
  }

  return false;
}

export function Globe({
  size = 240,
  className = "",
  rotationSpeed = 0.003,
  interactive = true,
  tilt = 23.5,
  showAtmosphere = true,
  showArcs = true,
  arcs = DEFAULT_ARCS,
}: GlobeProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const cv = canvasRef.current;
    if (!cv) return;
    const ctx = cv.getContext("2d");
    if (!ctx) return;

    let raf = 0;
    let yaw = 0.8;
    let t = 0;
    let isDragging = false;
    let lastX = 0;
    let dragVelocity = 0;

    const RM = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const D2 = Math.min(window.devicePixelRatio || 1, 2);

    // CSS theme colors
    const rootStyle = getComputedStyle(document.documentElement);
    const colPri = rootStyle.getPropertyValue("--pri").trim() || "#5b6cff";
    const colPri2 = rootStyle.getPropertyValue("--pri2").trim() || "#7a4dff";
    const colMut = rootStyle.getPropertyValue("--mut").trim() || "#8f97c9";
    const colTx = rootStyle.getPropertyValue("--tx").trim() || "#eef0ff";
    const colBg = rootStyle.getPropertyValue("--bg").trim() || "#070a1f";
    const colPanel = rootStyle.getPropertyValue("--panel").trim() || "#0d1230";

    const W = size;
    const H = size;
    cv.width = W * D2;
    cv.height = H * D2;

    const cx = W / 2;
    const cy = H / 2;
    const R = size * 0.44; // Scale to fit comfortably with atmosphere

    // Axis tilt in radians (23.5° realistic axial tilt)
    const tiltRad = (tilt * Math.PI) / 180;
    const cosTilt = Math.cos(tiltRad);
    const sinTilt = Math.sin(tiltRad);

    // Realistic sun lighting vector: coming from upper-left-front
    const Lx = -0.58;
    const Ly = 0.42;
    const Lz = 0.7;
    const Llen = Math.hypot(Lx, Ly, Lz) || 1;
    const sunL: [number, number, number] = [Lx / Llen, Ly / Llen, Lz / Llen];

    // Convert lat/lon to 3D unit sphere
    const latLonToVec = (latDeg: number, lonDeg: number): [number, number, number] => {
      const phi = (latDeg * Math.PI) / 180;
      const theta = (lonDeg * Math.PI) / 180;
      return [
        Math.cos(phi) * Math.sin(theta),
        Math.sin(phi),
        Math.cos(phi) * Math.cos(theta),
      ];
    };

    // Generate high-density realistic dot grid
    interface Dot {
      vec: [number, number, number];
      isLand: boolean;
    }
    const dots: Dot[] = [];

    // Dense latitude slices with cosine compensation
    const latStep = 2.4;
    for (let lat = -84; lat <= 84; lat += latStep) {
      const cosLat = Math.cos((lat * Math.PI) / 180);
      const count = Math.max(1, Math.round(150 * cosLat));
      const lonStep = 360 / count;
      for (let k = 0; k < count; k++) {
        const lon = -180 + k * lonStep;
        const land = isEarthLand(lat, lon);
        // Only keep land dots + sparse ocean graticule dots to ensure crisp continents
        dots.push({
          vec: latLonToVec(lat, lon),
          isLand: land,
        });
      }
    }

    // Great circle arcs with SLERP interpolation
    interface ArcTrack {
      p1: [number, number, number];
      p2: [number, number, number];
      angle: number;
      label1?: string;
      label2?: string;
      speed: number;
      offset: number;
    }

    const cityVecMap: Record<string, [number, number, number]> = {};
    DEFAULT_CITIES.forEach((c) => {
      cityVecMap[c.name] = latLonToVec(c.lat, c.lon);
    });

    const arcTracks: ArcTrack[] = arcs.map((a, i) => {
      const p1 = latLonToVec(a.lat1, a.lon1);
      const p2 = latLonToVec(a.lat2, a.lon2);
      const dotProd = Math.max(-1, Math.min(1, p1[0] * p2[0] + p1[1] * p2[1] + p1[2] * p2[2]));
      const angle = Math.acos(dotProd);
      return {
        p1,
        p2,
        angle,
        label1: a.label1,
        label2: a.label2,
        speed: 0.65 + (i % 3) * 0.15,
        offset: i * 0.32,
      };
    });

    // Rotation transform with 23.5° axial tilt
    const transformPoint = (
      v: [number, number, number],
      curYaw: number,
    ): [number, number, number] => {
      // 1. Rotate around Earth's polar axis (Y) from west to east
      const cosY = Math.cos(curYaw);
      const sinY = Math.sin(curYaw);
      const rx = v[0] * cosY + v[2] * sinY;
      const ry = v[1];
      const rz = -v[0] * sinY + v[2] * cosY;

      // 2. Apply Earth's 23.5° axial tilt (rotate around Z or X)
      // Tilts north pole toward top-right
      const px = rx * cosTilt - ry * sinTilt * 0.35;
      const py = ry * cosTilt + rx * sinTilt * 0.35;
      const pz = rz;

      return [px, py, pz];
    };

    // SLERP interpolation for arcs
    const slerp = (
      p1: [number, number, number],
      p2: [number, number, number],
      angle: number,
      u: number,
    ): [number, number, number] => {
      const sinAngle = Math.sin(angle);
      if (sinAngle < 0.001) return p1;
      const w1 = Math.sin((1 - u) * angle) / sinAngle;
      const w2 = Math.sin(u * angle) / sinAngle;
      return [
        p1[0] * w1 + p2[0] * w2,
        p1[1] * w1 + p2[1] * w2,
        p1[2] * w1 + p2[2] * w2,
      ];
    };

    // Drag interactions
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
      dragVelocity = dx * 0.012;
      yaw += dragVelocity;
      lastX = e.clientX;
    };

    const onPointerUp = (e: PointerEvent) => {
      isDragging = false;
      try {
        cv.releasePointerCapture(e.pointerId);
      } catch {
        /* ignore */
      }
    };

    cv.addEventListener("pointerdown", onPointerDown);
    cv.addEventListener("pointermove", onPointerMove);
    cv.addEventListener("pointerup", onPointerUp);
    cv.addEventListener("pointercancel", onPointerUp);

    // Animation Render Loop
    const draw = () => {
      raf = requestAnimationFrame(draw);
      t += RM ? 0.004 : 0.016;

      if (!isDragging) {
        dragVelocity *= 0.92;
        yaw += rotationSpeed + dragVelocity;
      }

      ctx.setTransform(D2, 0, 0, D2, 0, 0);
      ctx.clearRect(0, 0, W, H);

      // --- 1. Soft Outer Atmosphere Glow (Fresnel-style) ---
      if (showAtmosphere) {
        const atmoGrad = ctx.createRadialGradient(cx, cy, R * 0.88, cx, cy, R * 1.16);
        atmoGrad.addColorStop(0, "rgba(91, 108, 255, 0.28)");
        atmoGrad.addColorStop(0.4, "rgba(91, 108, 255, 0.12)");
        atmoGrad.addColorStop(0.85, "rgba(91, 108, 255, 0.03)");
        atmoGrad.addColorStop(1, "rgba(91, 108, 255, 0)");
        ctx.fillStyle = atmoGrad;
        ctx.beginPath();
        ctx.arc(cx, cy, R * 1.16, 0, Math.PI * 2);
        ctx.fill();
      }

      // --- 2. Realistic Ocean Sphere Body ---
      // Subtle gradient from --panel (#0d1230) to --bg (#070a1f) with sun specular highlight
      const oceanGrad = ctx.createRadialGradient(
        cx - R * 0.38,
        cy - R * 0.36,
        R * 0.05,
        cx,
        cy,
        R,
      );
      oceanGrad.addColorStop(0, "rgba(18, 24, 67, 0.96)"); // Lit ocean side
      oceanGrad.addColorStop(0.65, colPanel);
      oceanGrad.addColorStop(1, colBg); // Deep space terminator
      ctx.fillStyle = oceanGrad;
      ctx.beginPath();
      ctx.arc(cx, cy, R, 0, Math.PI * 2);
      ctx.fill();

      // Faint specular highlight on lit ocean side
      const specGrad = ctx.createRadialGradient(
        cx - R * 0.35,
        cy - R * 0.35,
        0,
        cx - R * 0.35,
        cy - R * 0.35,
        R * 0.55,
      );
      specGrad.addColorStop(0, "rgba(91, 108, 255, 0.18)");
      specGrad.addColorStop(0.5, "rgba(91, 108, 255, 0.05)");
      specGrad.addColorStop(1, "rgba(91, 108, 255, 0)");
      ctx.fillStyle = specGrad;
      ctx.beginPath();
      ctx.arc(cx, cy, R, 0, Math.PI * 2);
      ctx.fill();

      // Thin crisp atmospheric rim
      ctx.strokeStyle = "rgba(122, 141, 255, 0.32)";
      ctx.lineWidth = 1.2;
      ctx.beginPath();
      ctx.arc(cx, cy, R, 0, Math.PI * 2);
      ctx.stroke();

      // --- 3. Dotted Continents Rendering with Realistic Terminator Lighting ---
      for (let i = 0; i < dots.length; i++) {
        const dot = dots[i];
        if (!dot.isLand) continue; // High-density land dots produce clear continents

        const p = transformPoint(dot.vec, yaw);

        // Discard or hide dots on the back-facing hemisphere (P.z < -0.05)
        if (p[2] < -0.05) continue;

        // Depth perspective & edge compression
        const zRatio = Math.max(0, Math.min(1, p[2])); // 1 at center, 0 at limb
        const depthAlpha = 0.3 + 0.7 * zRatio;

        // Sun diffuse lighting factor: dot product with sun vector
        const diffuse = p[0] * sunL[0] + p[1] * sunL[1] + p[2] * sunL[2];
        const sunLight = Math.max(0, Math.min(1, diffuse * 0.7 + 0.35));

        // Screen projection
        const scrX = cx + p[0] * R;
        const scrY = cy - p[1] * R;

        // Radius compression near silhouette edge
        const dotR = (0.75 + 0.65 * zRatio) * (size / 240);

        // Dot color blends from muted (--mut: #8f97c9) to bright (--tx: #eef0ff)
        ctx.globalAlpha = depthAlpha * (0.2 + 0.8 * sunLight);
        ctx.fillStyle = sunLight > 0.5 ? colTx : colMut;
        ctx.beginPath();
        ctx.arc(scrX, scrY, Math.max(0.6, dotR), 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.globalAlpha = 1;

      // --- 4. Animated Great-Circle Arcs & Pulsing City Markers ---
      if (showArcs) {
        ctx.lineCap = "round";

        // Draw endpoint city beacons
        DEFAULT_CITIES.forEach((city, cIdx) => {
          const cv3 = cityVecMap[city.name];
          if (!cv3) return;
          const p = transformPoint(cv3, yaw);

          // Only visible when on front hemisphere
          if (p[2] > 0.05) {
            const px = cx + p[0] * R;
            const py = cy - p[1] * R;
            const pulse = (t * 1.6 + cIdx * 0.4) % 1;

            // City marker core
            ctx.fillStyle = colPri;
            ctx.beginPath();
            ctx.arc(px, py, 2.2, 0, Math.PI * 2);
            ctx.fill();

            // Pulsing wave ring
            ctx.strokeStyle = `rgba(122, 141, 255, ${(1 - pulse) * 0.65})`;
            ctx.lineWidth = 1;
            ctx.beginPath();
            ctx.arc(px, py, 2.5 + pulse * 7, 0, Math.PI * 2);
            ctx.stroke();
          }
        });

        // Draw traveling arcs
        arcTracks.forEach((arc) => {
          const segCount = 28;
          const headProgress = (t * arc.speed + arc.offset) % 1.4; // 0 to 1.4 loop
          const tailLen = 0.35;
          const uStart = Math.max(0, headProgress - tailLen);
          const uEnd = Math.min(1, headProgress);

          if (uStart >= 1 || uEnd <= 0) return;

          // Draw the elevated arc trajectory
          let prevPoint: [number, number, number] | null = null;

          for (let s = 0; s <= segCount; s++) {
            const u = uStart + ((uEnd - uStart) * s) / segCount;
            const sphereVec = slerp(arc.p1, arc.p2, arc.angle, u);

            // Great-circle arc elevation above Earth surface
            const elev = 1 + 0.22 * Math.sin(Math.PI * u);
            const rawPt: [number, number, number] = [
              sphereVec[0] * elev,
              sphereVec[1] * elev,
              sphereVec[2] * elev,
            ];
            const p = transformPoint(rawPt, yaw);

            if (prevPoint) {
              // Both points must be visible in front of or near limb
              const isFront = p[2] > 0 && prevPoint[2] > 0;
              if (isFront) {
                const segFrac = s / segCount;
                const alpha = Math.min(1, segFrac * 0.95);

                const x1 = cx + prevPoint[0] * R;
                const y1 = cy - prevPoint[1] * R;
                const x2 = cx + p[0] * R;
                const y2 = cy - p[1] * R;

                ctx.strokeStyle = `rgba(91, 108, 255, ${alpha * 0.7})`;
                ctx.lineWidth = 1.2 + segFrac * 0.8;
                ctx.beginPath();
                ctx.moveTo(x1, y1);
                ctx.lineTo(x2, y2);
                ctx.stroke();
              }
            }
            prevPoint = p;
          }

          // Glowing pulse head
          if (uEnd <= 1 && prevPoint && prevPoint[2] > 0) {
            const headX = cx + prevPoint[0] * R;
            const headY = cy - prevPoint[1] * R;

            ctx.fillStyle = "#ffffff";
            ctx.shadowColor = colPri2;
            ctx.shadowBlur = 10;
            ctx.beginPath();
            ctx.arc(headX, headY, 2.6, 0, Math.PI * 2);
            ctx.fill();
            ctx.shadowBlur = 0;
          }
        });
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
  }, [size, rotationSpeed, interactive, tilt, showAtmosphere, showArcs, arcs]);

  return (
    <canvas
      ref={canvasRef}
      className={`globe-canvas ${className}`}
      style={{
        width: size,
        height: size,
        display: "block",
        cursor: interactive ? "grab" : "default",
        touchAction: "none",
        background: "transparent",
      }}
      role="img"
      aria-label="3D dotted realistic Earth globe with animated arcs"
    />
  );
}

export default Globe;
