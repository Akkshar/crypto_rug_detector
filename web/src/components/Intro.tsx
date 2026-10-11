import { useEffect, useRef, useState } from "react";
import { Globe } from "./Globe";
import { VoxelArcCanvas } from "./canvas/VoxelArcCanvas";
import { morph } from "./canvas/TextMorph";

interface Props {
  onDismiss: (scrollToSample?: boolean) => void;
}

export function Intro({ onDismiss }: Props) {
  const [closing, setClosing] = useState(false);
  const itmRef = useRef<HTMLSpanElement | null>(null);
  const enterBtnRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    const W = ["rug pull", "honeypot", "hidden mint", "unlocked liquidity", "sell tax"];
    let i = 0;
    const iv = setInterval(() => {
      const e = itmRef.current;
      if (!e) return;
      const prev = W[i % W.length];
      i++;
      const next = W[i % W.length];
      morph(e, prev, next);
    }, 2600);

    const timer = setTimeout(() => {
      enterBtnRef.current?.focus({ preventScroll: true });
    }, 2300);

    return () => {
      clearInterval(iv);
      clearTimeout(timer);
    };
  }, []);

  const handleGo = (scroll: boolean) => {
    setClosing(true);
    setTimeout(() => {
      onDismiss(scroll);
    }, 700);
  };

  return (
    <div
      id="intro"
      role="dialog"
      aria-label="Welcome to RugScan"
      className={closing ? "off" : ""}
    >
      <VoxelArcCanvas />
      <div className="in">
        <div className="itx">
          <div className="ilogo">
            <svg viewBox="0 0 40 40" fill="none">
              <path
                d="M20 3 5 9v10c0 9 6 15 15 18 9-3 15-9 15-18V9L20 3z"
                stroke="#6b7bff"
                strokeWidth="2.5"
              />
              <path d="M13 20l5 5 9-10" stroke="#9ab0ff" strokeWidth="2.5" />
            </svg>
            RugScan
          </div>
          <span className="chip">AI-Powered Rug Pull Detection</span>
          <h1>
            Spot the{" "}
            <span className="tm tmw" id="itm" ref={itmRef}>
              rug pull
            </span>
            <br />
            before it spots you.
          </h1>
          <p>
            Paste a token contract and get a risk score in seconds: honeypots, unlocked liquidity,
            hidden mints and whale concentration, explained flag by flag.
          </p>
          <div className="ibtn">
            <button
              className="btn"
              id="ienter"
              ref={enterBtnRef}
              onClick={() => handleGo(false)}
            >
              Launch Scanner →
            </button>
            <button
              className="ghost"
              id="isample"
              onClick={() => handleGo(true)}
            >
              View sample report
            </button>
          </div>
          <div className="istats">
            <div>
              <b>6</b>
              <span>risk categories</span>
            </div>
            <div>
              <b>Rules + ML</b>
              <span>hybrid scoring</span>
            </div>
            <div>
              <b>BSC · ETH · Base</b>
              <span>supported chains</span>
            </div>
          </div>
        </div>
        <div className="ifr">
          <Globe size={480} interactive={true} />
        </div>
      </div>
    </div>
  );
}
