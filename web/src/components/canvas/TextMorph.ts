export function morph(el: HTMLElement, a: string, b: string) {
  const RM = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  el.textContent = "";
  const mk = (s: string) =>
    [...s].map((ch) => {
      const e = document.createElement("span");
      e.textContent = ch;
      return e;
    });
  const A = mk(a);
  const B = mk(b);
  const lay = (L: HTMLElement[]) => {
    L.forEach((s) => el.appendChild(s));
    const p = L.map((s) => s.offsetLeft);
    L.forEach((s) => s.remove());
    return p;
  };
  const ax = lay(A);
  const bx = lay(B);

  el.setAttribute("aria-label", b);
  B.forEach((s) => el.appendChild(s));
  if (RM) return;

  const used = new Set<number>();
  const o: KeyframeAnimationOptions = {
    duration: 750,
    easing: "cubic-bezier(.7,0,.2,1)",
    fill: "backwards",
  };

  B.forEach((s, j) => {
    const i = A.findIndex(
      (q, n) =>
        !used.has(n) && q.textContent?.toLowerCase() === s.textContent?.toLowerCase(),
    );
    if (i >= 0) {
      used.add(i);
      s.animate([{ transform: `translateX(${ax[i] - bx[j]}px)` }, { transform: "none" }], o);
    } else {
      s.animate([{ opacity: 0, transform: "translateY(.5em)" }, { opacity: 1, transform: "none" }], o);
    }
  });

  A.forEach((s, i) => {
    if (used.has(i)) return;
    s.style.cssText = `position:absolute;left:${ax[i]}px;top:0`;
    el.appendChild(s);
    const anim = s.animate(
      [{ opacity: 1, transform: "none" }, { opacity: 0, transform: "translateY(-.5em)" }],
      { duration: 500, easing: "ease-in", fill: "forwards" },
    );
    anim.onfinish = () => s.remove();
  });
}
