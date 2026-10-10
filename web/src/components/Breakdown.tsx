import type { CategoryBreakdown } from "../api";

const LABELS: Record<string, string> = {
  CONTRACT_PRIVILEGE: "Contract privileges",
  LIQUIDITY: "Liquidity",
  HONEYPOT: "Honeypot / sell path",
  DISTRIBUTION: "Holder distribution",
  DEPLOYER: "Deployer provenance",
  TRADING: "Trade flow",
};

export function Breakdown({ rows }: { rows: CategoryBreakdown[] }) {
  return (
    <section className="card">
      <h3>Where the points come from</h3>
      <p className="muted small">
        Each category is capped so one noisy engine cannot dominate; the capped totals sum to the rule score (clipped
        to 100).
      </p>
      <ul className="bars">
        {rows.map((r) => {
          const pct = r.cap > 0 ? Math.min(100, (r.capped_points / r.cap) * 100) : 0;
          const overflow = r.raw_points > r.cap;
          return (
            <li key={r.category}>
              <div className="bar-label">
                <span>{LABELS[r.category] ?? r.category}</span>
                <span className="muted">
                  {r.capped_points.toFixed(0)} / {r.cap.toFixed(0)}
                  {overflow && <span title={`raw ${r.raw_points.toFixed(0)} before cap`}> ▲</span>}
                  {r.flag_count > 0 && ` · ${r.flag_count} flag${r.flag_count > 1 ? "s" : ""}`}
                </span>
              </div>
              <div className="bar-track">
                <div className="bar-fill" style={{ width: `${pct}%` }} data-level={pct >= 80 ? "high" : pct >= 40 ? "mid" : "low"} />
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
