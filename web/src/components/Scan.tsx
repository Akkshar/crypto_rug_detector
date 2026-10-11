import { useEffect, useRef, useState } from "react";
import { Globe } from "./Globe";
import { morph } from "./canvas/TextMorph";

interface Props {
  chain: string;
  onChainChange: (c: string) => void;
  onScan: (address: string, chain: string) => void;
  onSelectPreset: (presetKey: string) => void;
  scanning: boolean;
  error: string;
  targetColor?: [number, number, number];
}

export function Scan({
  chain,
  onChainChange,
  onScan,
  onSelectPreset,
  scanning,
  error,
}: Props) {
  const [address, setAddress] = useState("");
  const tmwRef = useRef<HTMLSpanElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    const W = ["rug pulls", "honeypots", "hidden mints", "unlocked liquidity", "sell taxes"];
    let i = 0;
    const iv = setInterval(() => {
      const e = tmwRef.current;
      if (!e) return;
      const prev = W[i % W.length];
      i++;
      const next = W[i % W.length];
      morph(e, prev, next);
    }, 2600);

    return () => {
      clearInterval(iv);
    };
  }, []);

  const handleGo = () => {
    onScan(address.trim(), chain);
  };

  return (
    <section className="card hero">
      <div className="orb" aria-hidden="true">
        <Globe size={200} interactive={true} />
      </div>
      <h1>Scan a Token</h1>
      <p>
        Detect{" "}
        <span className="tm tmw" id="tmw" ref={tmwRef}>
          rug pulls
        </span>{" "}
        and other risks with AI-powered analysis.
      </p>
      <div className="scan">
        <input
          id="addr"
          ref={inputRef}
          placeholder="Enter token contract address (0x…)"
          aria-label="Token contract address"
          autoComplete="off"
          value={address}
          onChange={(e) => setAddress(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") handleGo();
          }}
        />
        <select
          aria-label="Chain"
          value={chain}
          onChange={(e) => onChainChange(e.target.value)}
        >
          <option value="BSC">BSC</option>
          <option value="ETH">ETH</option>
          <option value="Base">Base</option>
        </select>
        <button
          className="btn"
          id="go"
          disabled={scanning}
          onClick={handleGo}
        >
          {scanning ? "Scanning…" : "Scan Token"}
        </button>
      </div>
      <div className="err" id="err" role="alert">
        {error}
      </div>
      <div className="qlinks">
        Quick Links:{" "}
        <button
          className="CRITICAL"
          data-s="honeypot"
          onClick={() => onSelectPreset("honeypot")}
        >
          Sample Honeypot
        </button>
        <button
          className="MEDIUM"
          data-s="unlocked"
          onClick={() => onSelectPreset("unlocked")}
        >
          Sample Unlocked Rug
        </button>
        <button
          className="LOW"
          data-s="benign"
          onClick={() => onSelectPreset("benign")}
        >
          Sample Benign Token
        </button>
      </div>
    </section>
  );
}
