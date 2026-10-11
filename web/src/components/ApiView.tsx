import { useState } from "react";
import { api } from "../api";

interface Props {
  apiKey: string;
  onCopy: (text: string) => void;
}

const ENDPOINTS = [
  ["POST", "/scan", "Score a token contract (live or fixture provider)"],
  ["GET", "/history", "List recent scan reports from cache"],
  ["GET", "/history/{chain}/{address}", "Fetch cached scan reports for a specific token"],
  ["GET", "/health", "Service status, active provider, model and chains"],
  ["GET", "/stats", "Snapshot cache and report database statistics"],
  ["GET", "/weights", "Active heuristic flag weights, category caps, and tiers"],
  ["POST", "/label", "Attach ground truth label ('rug' | 'benign')"],
];

const SNIPPET_TABS = [
  { id: "curl", label: "cURL" },
  { id: "js", label: "JavaScript" },
  { id: "py", label: "Python" },
];

const EXAMPLE_RESPONSE = JSON.stringify(
  {
    report: {
      chain: "bsc",
      address: "0x2222222222222222222222222222222222222222",
      token_name: "SafeMoonElon2.0",
      token_symbol: "SME2",
      risk_score: 99.9,
      risk_tier: "CRITICAL",
      rule_score: 100.0,
      ml: { rug_probability: 0.99, is_out_of_distribution: true },
      hard_fail: true,
      flags: [
        { code: "SELL_TAX_EXTREME", severity: "CRITICAL", points: 40.0 },
        { code: "HONEYPOT_SELL_BLOCKED", severity: "CRITICAL", points: 50.0 },
      ],
    },
    from_cache: false,
    previous_score: null,
  },
  null,
  2,
);

export function ApiView({ onCopy }: Props) {
  const [activeTab, setActiveTab] = useState<"curl" | "js" | "py">("curl");
  const [tryAddress, setTryAddress] = useState("");
  const [tryOutput, setTryOutput] = useState("// Response appears here");

  const a = "0x2222222222222222222222222222222222222222";
  const snippets = {
    curl: `curl -X POST http://127.0.0.1:8000/scan \\\n  -H "Content-Type: application/json" \\\n  -d '{"chain":"bsc","address":"${a}"}'`,
    js: `const res = await fetch("/scan", {\n  method: "POST",\n  headers: { "Content-Type": "application/json" },\n  body: JSON.stringify({ chain: "bsc", address: "${a}" }),\n});\nconsole.log(await res.json());`,
    py: `import requests\n\nres = requests.post(\n    "http://127.0.0.1:8000/scan",\n    json={"chain": "bsc", "address": "${a}"},\n)\nprint(res.json())`,
  };

  const handleRunTry = async () => {
    const v = tryAddress.trim();
    if (!/^0x[a-fA-F0-9]{40}$/.test(v)) {
      setTryOutput(
        JSON.stringify(
          {
            error: "invalid_address",
            message: "Expected 0x followed by 40 hex characters",
          },
          null,
          2,
        ),
      );
      return;
    }
    setTryOutput("Sending request to backend /scan…");
    try {
      const res = await api.scan("bsc", v);
      setTryOutput(JSON.stringify(res, null, 2));
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      setTryOutput(JSON.stringify({ error: "scan_failed", detail: msg }, null, 2));
    }
  };

  const currentSnippet = snippets[activeTab];

  return (
    <div className="view" id="v-api">
      <div className="grid">
        <div className="col">
          <section className="card">
            <h3>Endpoints</h3>
            <p style={{ color: "var(--mut)", margin: "0 0 10px" }}>
              Base URL <code>http://127.0.0.1:8000</code> · Swagger UI at{" "}
              <code>/docs</code>
            </p>
            <div className="tw">
              <table>
                <thead>
                  <tr>
                    <th>Method</th>
                    <th>Path</th>
                    <th>Description</th>
                  </tr>
                </thead>
                <tbody>
                  {ENDPOINTS.map((e, idx) => (
                    <tr key={idx}>
                      <td>
                        <span className={`badge ${e[0] === "POST" ? "MEDIUM" : "LOW"}`}>
                          {e[0]}
                        </span>
                      </td>
                      <td>
                        <code>{e[1]}</code>
                      </td>
                      <td>{e[2]}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section className="card">
            <h3>Quick start</h3>
            <div className="vbar">
              <div className="tabs" style={{ margin: 0 }}>
                {SNIPPET_TABS.map((tab) => (
                  <button
                    key={tab.id}
                    className={activeTab === tab.id ? "on" : ""}
                    data-tab={tab.id}
                    onClick={() => setActiveTab(tab.id as "curl" | "js" | "py")}
                  >
                    {tab.label}
                  </button>
                ))}
              </div>
              <button
                className="ghost sm"
                data-cp="snip"
                style={{ marginLeft: "auto" }}
                onClick={() => onCopy(currentSnippet)}
              >
                Copy
              </button>
            </div>
            <pre className="code">{currentSnippet}</pre>
          </section>

          <section className="card">
            <h3>Example response</h3>
            <pre className="code">{EXAMPLE_RESPONSE}</pre>
          </section>
        </div>

        <div className="col">
          <section className="card">
            <h3>
              Try it{" "}
              <small style={{ color: "var(--mut)", fontWeight: 400 }}>
                · mock provider
              </small>
            </h3>
            <div className="vbar">
              <input
                id="try-a"
                placeholder="0x… token address"
                aria-label="Token address"
                value={tryAddress}
                onChange={(e) => setTryAddress(e.target.value)}
              />
              <button className="btn sm2" data-run="1" onClick={handleRunTry}>
                Run
              </button>
            </div>
            <pre className="code" id="try-o">
              {tryOutput}
            </pre>
          </section>

          <section className="card">
            <h3>Rate limits</h3>
            <div className="tw">
              <table style={{ minWidth: 0 }}>
                <thead>
                  <tr>
                    <th>Plan</th>
                    <th>Requests / min</th>
                    <th>Scans / day</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td>Free</td>
                    <td>10</td>
                    <td>100</td>
                  </tr>
                  <tr>
                    <td>Pro</td>
                    <td>120</td>
                    <td>5,000</td>
                  </tr>
                  <tr>
                    <td>Enterprise</td>
                    <td>Custom</td>
                    <td>Unlimited</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}
