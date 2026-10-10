import { useEffect, useState } from "react";
import { ApiError, api, type Chain, type Health, type RiskReport, type ScanResponse } from "./api";
import { Verdict } from "./components/Verdict";
import { Breakdown } from "./components/Breakdown";
import { Drivers } from "./components/Drivers";
import { Flags } from "./components/Flags";
import { History } from "./components/History";

const CHAINS: Chain[] = ["ethereum", "bsc", "arbitrum", "base"];
const EXAMPLES: { label: string; chain: Chain; address: string }[] = [
  { label: "benign fixture", chain: "ethereum", address: "0x1111111111111111111111111111111111111111" },
  { label: "honeypot fixture", chain: "bsc", address: "0x2222222222222222222222222222222222222222" },
  { label: "unlocked-LP rug fixture", chain: "base", address: "0x3333333333333333333333333333333333333333" },
];

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [chain, setChain] = useState<Chain>("ethereum");
  const [address, setAddress] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ScanResponse | null>(null);
  const [history, setHistory] = useState<RiskReport[]>([]);

  useEffect(() => {
    api.health().then(setHealth).catch(() => setHealth(null));
  }, []);

  async function runScan(c: Chain = chain, a: string = address) {
    const target = a.trim();
    if (!target) return;
    setBusy(true);
    setError(null);
    try {
      const res = await api.scan(c, target);
      setResult(res);
      if (health?.cache) {
        api.history(res.report.chain, res.report.address).then(setHistory).catch(() => setHistory([]));
      }
    } catch (e) {
      setResult(null);
      setHistory([]);
      setError(e instanceof ApiError ? `${e.status}: ${e.message}` : String(e));
    } finally {
      setBusy(false);
    }
  }

  function useExample(ex: (typeof EXAMPLES)[number]) {
    setChain(ex.chain);
    setAddress(ex.address);
    void runScan(ex.chain, ex.address);
  }

  const report = result?.report ?? null;

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="logo">◆</span>
          <div>
            <h1>rugscan</h1>
            <p className="tagline">Explainable rug-pull &amp; honeypot risk assessment for EVM tokens</p>
          </div>
        </div>
        <div className="status">
          {health ? (
            <>
              <span className="chip ok">API {health.version}</span>
              <span className="chip">provider: {health.provider}</span>
              <span className="chip">model: {health.model_trained_on ?? "none"}</span>
              <span className={`chip ${health.cache ? "ok" : "warn"}`}>{health.cache ? "cache on" : "no cache"}</span>
            </>
          ) : (
            <span className="chip warn">API unreachable</span>
          )}
        </div>
      </header>

      <section className="card scanform">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void runScan();
          }}
        >
          <select value={chain} onChange={(e) => setChain(e.target.value as Chain)} aria-label="Chain">
            {CHAINS.map((c) => (
              <option key={c} value={c} disabled={health ? !health.chains.includes(c) : false}>
                {c}
              </option>
            ))}
          </select>
          <input
            value={address}
            onChange={(e) => setAddress(e.target.value)}
            placeholder="Token contract address (0x…)"
            spellCheck={false}
            aria-label="Token address"
          />
          <button type="submit" disabled={busy || !address.trim()}>
            {busy ? "Scanning…" : "Scan"}
          </button>
        </form>
        {health?.provider === "mock" && (
          <div className="examples">
            Offline fixtures:
            {EXAMPLES.map((ex) => (
              <button key={ex.address} type="button" className="link" onClick={() => useExample(ex)}>
                {ex.label}
              </button>
            ))}
          </div>
        )}
        {error && <div className="error">{error}</div>}
      </section>

      {report && (
        <>
          <Verdict report={report} fromCache={result!.from_cache} previousScore={result!.previous_score} />
          <div className="grid">
            <Breakdown rows={report.breakdown} />
            {report.ml && <Drivers ml={report.ml} />}
          </div>
          <Flags flags={report.flags} />
          {history.length > 1 && <History reports={history} />}
          {report.missing_data.length > 0 && (
            <p className="muted">
              Not evaluated (data unavailable): {report.missing_data.join(", ")}. The score reflects only what could be
              observed.
            </p>
          )}
        </>
      )}

      {!report && !busy && (
        <section className="card empty">
          <p>
            Enter a token address to get a 0–100 risk score, a tier, and the evidence behind every point: contract
            privileges, liquidity locks, holder concentration, deployer provenance, trade flow and a simulated sell.
          </p>
        </section>
      )}

      <footer className="muted">
        A risk <em>estimate</em>, not proof of fraud. Verdicts combine rule-based checks with a model trained on
        limited data; a LOW score does not guarantee safety and a CRITICAL score is a reason to investigate, not a
        legal finding. Always verify independently before acting.
      </footer>
    </div>
  );
}
