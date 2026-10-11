interface SpotOptions {
  color?: string;
  intensity?: number;
  size?: number;
  softness?: number;
  shape?: "circle" | "beam";
  edge?: number;
  prox?: number;
  smooth?: number;
  flare?: number;
  ambient?: boolean;
}

interface SpotInstance {
  root: HTMLElement;
  o: Required<SpotOptions>;
  attach: () => void;
  move: (e: PointerEvent | MouseEvent) => void;
  out: (e: MouseEvent) => void;
  up: (e: PointerEvent | MouseEvent) => void;
  scr: () => void;
  destroy: () => void;
}

const SP = new Set<SpotInstance>();
const cl = (v: number, a: number, b: number) => Math.min(b, Math.max(a, v));

function falloff(color: string, softness: number) {
  const soft = cl(softness, 0, 1);
  const core = Math.pow(1 - soft, 1.5) * 0.85;
  const stop = (a: number, at: number) =>
    `color-mix(in srgb, ${color} ${(a * 100).toFixed(2)}%, transparent) ${(at * 100).toFixed(2)}%`;
  const list = [stop(1, 0)];
  for (let i = 0; i <= 14; i++) {
    const t = core + ((1 - core) * i) / 14;
    const g = (t - core) / (1 - core);
    const sm = 1 - g * g * (3 - 2 * g);
    const gl = Math.exp(-4.6 * g * g) * (1 - Math.pow(g, 6));
    list.push(stop(sm + (gl - sm) * soft, t));
  }
  return list.join(", ");
}

export function createSpot(root: HTMLElement, userOptions?: SpotOptions): SpotInstance | null {
  if (root.classList.contains("spl")) return null;
  const RM = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const o: Required<SpotOptions> = {
    color: "#8a9bff",
    intensity: 0.22,
    size: 260,
    softness: 0.7,
    shape: "circle",
    edge: 0.8,
    prox: 90,
    smooth: 0.3,
    flare: 1,
    ambient: window.matchMedia("(hover:none)").matches,
    ...userOptions,
  };

  const mk = (c: string) => {
    const s = document.createElement("span");
    s.className = c;
    s.setAttribute("aria-hidden", "true");
    return s;
  };

  const L = mk("spl-l");
  const B = mk("spl-b");
  const E = mk("spl-e");

  const attach = () => {
    if (!L.isConnected) root.prepend(L);
    if (!B.isConnected) root.append(B, E);
  };
  root.classList.add("spl");
  attach();

  const st = {
    x: 0,
    y: 0,
    pr: 0,
    fl: 0,
    tm: Math.random() * 60,
    pressed: 0,
    vis: true,
  };
  const pt = { x: 0, y: 0, on: 0 };
  const dr = { l: "", e: "" };
  let raf = 0;
  let last = 0;
  let ck = "";
  let fill = "";
  let rim = "";

  const grad = (sz: number, x: number, y: number, stops: string) =>
    o.shape === "beam"
      ? `radial-gradient(${(sz * 0.55).toFixed(1)}px ${Math.max(sz * 0.6, y + sz * 0.55).toFixed(1)}px at ${x.toFixed(1)}px 0px, ${stops})`
      : `radial-gradient(circle ${sz.toFixed(1)}px at ${x.toFixed(1)}px ${y.toFixed(1)}px, ${stops})`;

  function paint() {
    const k = o.color + "|" + o.softness;
    if (k !== ck) {
      ck = k;
      fill = falloff(o.color, o.softness);
      rim = falloff(o.color, 1);
    }
    const sz = o.size * (1 + 0.2 * st.fl);
    const bo = 1 + 0.7 * st.fl;
    const f = grad(sz, st.x, st.y, fill);
    const r = grad(sz * 0.6, st.x, o.shape === "beam" ? 0 : st.y, rim);
    if (f !== dr.l) {
      dr.l = f;
      L.style.background = f;
    }
    if (r !== dr.e) {
      dr.e = r;
      E.style.background = r;
    }
    L.style.opacity = String(Math.min(1, st.pr * o.intensity * bo));
    E.style.opacity = String(Math.min(1, st.pr * o.edge * bo));
  }

  function tick(now: number) {
    raf = 0;
    if (!root.isConnected || !root.offsetWidth) return;
    const dt = Math.min(0.05, Math.max(0.001, (now - last) / 1e3));
    last = now;
    const rc = root.getBoundingClientRect();
    st.vis = rc.bottom > 0 && rc.top < window.innerHeight;
    let tx = st.x;
    let ty = st.y;
    let tg = 0;

    if (pt.on) {
      tx = pt.x - rc.left;
      ty = pt.y - rc.top;
      const gap = Math.hypot(
        Math.max(0, -tx, tx - rc.width),
        Math.max(0, -ty, ty - rc.height),
      );
      if (gap === 0) tg = 1;
      else if (o.prox > 0) tg = Math.pow(Math.max(0, 1 - gap / o.prox), 2);
    }

    if (tg < 0.6 && o.ambient && st.vis) {
      if (!RM) st.tm += dt;
      const d = 0.6 - tg;
      tx += (rc.width * (0.5 + 0.34 * Math.sin(st.tm * 0.43)) - tx) * (d / 0.6);
      ty += (rc.height * (0.5 + 0.3 * Math.sin(st.tm * 0.61 + 1.3)) - ty) * (d / 0.6);
      tg = 0.6;
    }

    if (st.pr < 0.02 && tg > 0 && !o.ambient) {
      st.x = tx;
      st.y = ty;
    }

    const fo = RM || o.smooth === 0 ? 1 : 1 - Math.exp(-dt / (0.015 + o.smooth * 0.22));
    st.x += (tx - st.x) * fo;
    st.y += (ty - st.y) * fo;
    const fa = RM ? 1 : 1 - Math.exp(-dt / (tg > st.pr ? 0.1 : 0.32));
    st.pr += (tg - st.pr) * fa;
    if (Math.abs(tg - st.pr) < 0.002) st.pr = tg;

    const pr = st.pressed && o.flare && !RM ? 1 : 0;
    st.fl += (pr - st.fl) * (1 - Math.exp(-dt / (pr ? 0.05 : 0.28)));
    if (Math.abs(pr - st.fl) < 0.002) st.fl = pr;
    paint();

    const mv = Math.abs(tx - st.x) > 0.1 || Math.abs(ty - st.y) > 0.1;
    if (mv || st.pr !== tg || st.fl !== pr || (o.ambient && st.vis && !RM)) {
      raf = requestAnimationFrame(tick);
    }
  }

  const wake = () => {
    if (raf) return;
    last = performance.now();
    raf = requestAnimationFrame(tick);
  };

  const onPointerDown = (e: PointerEvent) => {
    pt.x = e.clientX;
    pt.y = e.clientY;
    pt.on = 1;
    st.pressed = 1;
    wake();
  };

  root.addEventListener("pointerdown", onPointerDown);

  const instance: SpotInstance = {
    root,
    o,
    attach,
    move(e: PointerEvent | MouseEvent) {
      pt.x = e.clientX;
      pt.y = e.clientY;
      pt.on = 1;
      if (root.offsetWidth) wake();
    },
    out(e: MouseEvent) {
      if (e.relatedTarget) return;
      pt.on = 0;
      wake();
    },
    up(e: PointerEvent | MouseEvent) {
      if ("pointerType" in e && e.pointerType !== "mouse") pt.on = 0;
      if (!st.pressed && "pointerType" in e && e.pointerType === "mouse") return;
      st.pressed = 0;
      wake();
    },
    scr() {
      if (pt.on || st.pr > 0) wake();
    },
    destroy() {
      if (raf) cancelAnimationFrame(raf);
      root.removeEventListener("pointerdown", onPointerDown);
      root.classList.remove("spl");
      L.remove();
      B.remove();
      E.remove();
      SP.delete(instance);
    },
  };

  SP.add(instance);
  wake();
  return instance;
}

let listenersAttached = false;
function ensureGlobalListeners() {
  if (listenersAttached || typeof window === "undefined") return;
  listenersAttached = true;

  const each = (f: (i: SpotInstance) => void) => {
    SP.forEach((i) => {
      if (i.root.isConnected) f(i);
      else {
        i.destroy();
      }
    });
  };

  window.addEventListener("pointermove", (e) => each((i) => i.move(e)));
  window.addEventListener("pointerup", (e) => each((i) => i.up(e)));
  window.addEventListener("pointercancel", (e) => each((i) => i.up(e)));
  window.addEventListener("scroll", () => each((i) => i.scr()), {
    capture: true,
    passive: true,
  });
  document.addEventListener("pointerout", (e) => each((i) => i.out(e)));

  document.addEventListener("pointermove", (e) => {
    const c = (e.target as HTMLElement)?.closest?.<HTMLElement>(".card,aside");
    if (!c) return;
    const b = c.getBoundingClientRect();
    c.style.setProperty("--mx", e.clientX - b.left + "px");
    c.style.setProperty("--my", e.clientY - b.top + "px");
  });
}

export function updateAllSpotlights(color: string) {
  SP.forEach((i) => {
    i.o.color = color;
  });
}

export function initCardSpotlights() {
  ensureGlobalListeners();
  const SEL = ".card, .side-card, .pcc, .bmc";
  document.querySelectorAll<HTMLElement>(SEL).forEach((el) => {
    if (el.classList.contains("hero")) {
      createSpot(el, { shape: "beam", size: 420, intensity: 0.3 });
    } else {
      createSpot(el);
    }
  });
}
