import type { MLAssessment } from "../api";

const FEATURE_LABELS: Record<string, string> = {
  top10_concentration: "Top-10 holder concentration",
  is_lp_locked: "LP locked",
  max_tax_rate: "Max tax rate",
  has_mint_function: "Mint capability",
  deployer_age_days: "Deployer wallet age",
  tvl_usd: "Liquidity (USD)",
};

function fmt(name: string, v: number): string {
  if (name === "tvl_usd") return `$${Math.round(v).toLocaleString()}`;
  if (name === "deployer_age_days") return `${v.toFixed(1)} d`;
  if (name === "max_tax_rate") return `${v.toFixed(1)}%`;
  if (name === "top10_concentration") return `${(v * 100).toFixed(0)}%`;
  return v ? "yes" : "no";
}

export function Drivers({ ml }: { ml: MLAssessment }) {
  const rows = Object.entries(ml.contributions).sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]));
  const max = Math.max(0.05, ...rows.map(([, v]) => Math.abs(v)));
  return (
    <section className="card">
      <h3>What moved the model</h3>
      <p className="muted small">
        Change in P(rug) if that one feature were at its training median. Red pushes toward “rug”, green away.
        {ml.imputed_features.length > 0 && <> Imputed: {ml.imputed_features.join(", ")}.</>}
      </p>
      <ul className="drivers">
        {rows.map(([name, v]) => (
          <li key={name}>
            <div className="bar-label">
              <span>
                {FEATURE_LABELS[name] ?? name} <span className="muted">{fmt(name, ml.features[name] ?? 0)}</span>
              </span>
              <span className={v > 0 ? "worse" : v < 0 ? "better" : "muted"}>
                {v > 0 ? "+" : ""}
                {(v * 100).toFixed(1)} pp
              </span>
            </div>
            <div className="driver-track">
              <div
                className={`driver-fill ${v >= 0 ? "pos" : "neg"}`}
                style={{ width: `${(Math.abs(v) / max) * 50}%`, [v >= 0 ? "left" : "right"]: "50%" } as React.CSSProperties}
              />
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
