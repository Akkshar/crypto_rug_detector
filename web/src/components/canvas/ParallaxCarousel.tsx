import { useEffect, useRef } from "react";
import { colr, lvl, SAMPLE_DATA } from "../../types";

interface Props {
  onSelect: (k: string) => void;
}

const CARDS_DATA: [string, number, string?, string?][] = [
  ["honeypot", 99.8],
  ["unlocked", 71.4],
  ["benign", 6.1],
  ["unlocked", 78.6, "PepeCoin", "PEPE"],
  ["unlocked", 42.3, "TestToken", "TTK"],
];

export function ParallaxCarousel({ onSelect }: Props) {
  const vRef = useRef<HTMLDivElement | null>(null);
  const tRef = useRef<HTMLDivElement | null>(null);
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;

  useEffect(() => {
    const v = vRef.current;
    const t = tRef.current;
    if (!v || !t) return;

    const cards = Array.from(t.children) as HTMLElement[];
    let off = 0;
    let vel = 0;
    let dr = 0;
    let sx = 0;
    let so = 0;
    let lx = 0;
    let mv = 0;
    let raf = 0;

    const lo = () => Math.min(0, v.clientWidth - t.scrollWidth);
    const rub = (o: number) => {
      const l = lo();
      return o > 0 ? o * 0.4 : o < l ? l + (o - l) * 0.4 : o;
    };

    const onPointerDown = (e: PointerEvent) => {
      dr = 1;
      mv = 0;
      sx = lx = e.clientX;
      so = off;
      vel = 0;
      v.classList.add("dg");
    };

    const onPointerMove = (e: PointerEvent) => {
      if (!dr) return;
      const d = e.clientX - sx;
      if (Math.abs(d) > 5) mv = 1;
      vel = e.clientX - lx;
      lx = e.clientX;
      off = so + d;
    };

    const onPointerUp = () => {
      dr = 0;
      v.classList.remove("dg");
    };

    v.addEventListener("pointerdown", onPointerDown);
    window.addEventListener("pointermove", onPointerMove);
    window.addEventListener("pointerup", onPointerUp);
    window.addEventListener("pointercancel", onPointerUp);

    const onClick = (e: MouseEvent) => {
      if (mv) return;
      const target = (e.target as HTMLElement).closest<HTMLElement>(".pcc");
      if (target && target.dataset.k) {
        onSelectRef.current(target.dataset.k);
      }
    };

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Enter") {
        const target = (e.target as HTMLElement).closest<HTMLElement>(".pcc");
        if (target && target.dataset.k) {
          onSelectRef.current(target.dataset.k);
        }
      }
    };

    v.addEventListener("click", onClick);
    v.addEventListener("keydown", onKeyDown);

    function f() {
      raf = requestAnimationFrame(f);
      if (!v || !t || !v.clientWidth) return;
      if (!dr) {
        off += vel;
        vel *= 0.94;
        const l = lo();
        if (off > 0) off *= 0.85;
        else if (off < l) off = l + (off - l) * 0.85;
      }
      const r = rub(off);
      const w = v.clientWidth;
      t.style.transform = `translateX(${r}px)`;

      cards.forEach((c, i) => {
        const d = Math.max(-1.5, Math.min(1.5, (r + i * 224 + 105 - w / 2) / w));
        c.style.transform = `scale(${1 - 0.05 * Math.abs(d)})`;
        const pcg = c.querySelector<HTMLElement>(".pcg");
        if (pcg) pcg.style.transform = `translateX(${-d * 60}px)`;
        const pcb = c.querySelector<HTMLElement>(".pcb");
        if (pcb) pcb.style.transform = `translateX(${-d * 12}px)`;
      });
    }

    raf = requestAnimationFrame(f);

    return () => {
      cancelAnimationFrame(raf);
      v.removeEventListener("pointerdown", onPointerDown);
      window.removeEventListener("pointermove", onPointerMove);
      window.removeEventListener("pointerup", onPointerUp);
      window.removeEventListener("pointercancel", onPointerUp);
      v.removeEventListener("click", onClick);
      v.removeEventListener("keydown", onKeyDown);
    };
  }, []);

  return (
    <div className="pcv" id="pcv" ref={vRef}>
      <div className="pct" id="pct" ref={tRef}>
        {CARDS_DATA.map((d, idx) => {
          const s = SAMPLE_DATA[d[0]];
          const score = d[1];
          const l = lvl(score);
          const name = d[2] || s.n;
          const symbol = d[3] || s.s;
          return (
            <div
              key={idx}
              className="pcc"
              data-k={d[0]}
              tabIndex={0}
              role="button"
              aria-label={`Scan ${name}`}
            >
              <i className="pcg" style={{ color: colr(l) }}>
                {Math.round(score)}
              </i>
              <div className="pcb">
                <span className={`badge ${l}`}>{l}</span>
                <b>{name}</b>
                <small>
                  {symbol} · {s.a.slice(0, 8)}…
                </small>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
