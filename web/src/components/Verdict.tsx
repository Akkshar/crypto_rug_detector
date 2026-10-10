import type { RiskReport, Tier } from "../api";

const TIER_COLOR: Record<Tier, string> = {
  LOW: "var(--low)",
  MEDIUM: "var(--medium)",
  HIGH: "var(--high)",
  CRITICAL: "var(--critical)",
};

function Gauge({ score, tier }: { score: number; tier: Tier }) {
  // Half-circle arc, 0 at the left end, 100 at the right.
  const r = 54;
  const c = Math.PI * r;
  const filled = (Math.max(0, Math.min(100, score)) / 100) * c;
  return (
    <svg viewBox="0 0 140 84" className="gauge" role="img" aria-label={`risk score ${score} of 100`}>
      <path d="M 16 74 A 54 54 0 0 1 124 74" fill="none" stroke="var(--track)" strokeWidth="12" strokeLinecap="round" />
      <path
        d="M 16 74 A 54 54 0 0 1 124 74"
        fill="none"
        stroke={TIER_COLOR[tier]}
        strokeWidth="12"
        strokeLinecap="round"
        strokeDasharray={`${filled} ${c}`}
      />
      <text x="70" y="66" textAnchor="middle" className="gauge-score">
        {score.toFixed(1)}
      </text>
      <text x="70" y="80" textAnchor="middle" className="gauge-sub">
        / 100
      </text>
    </svg>
  );
}

export function Verdict({
  report,
  fromCache,
  previousScore,
}: {
  report: RiskReport;
  fromCache: boolean;
  previousScore: number | null;
}) {
  const delta = previousScore == null ? null : report.risk_score - previousScore;
  return (
    <section className="card verdict" style={{ borderColor: TIER_COLOR[report.risk_tier] }}>
      <Gauge score={report.risk_score} tier={report.risk_tier} />
      <div className="verdict-body">
        <div className="verdict-head">
          <span className="tier" style={{ background: TIER_COLOR[report.risk_tier] }}>
            {report.risk_tier}
          </span>
          <h2>
            {report.token_name ?? "Unknown token"} {report.token_symbol && <span className="muted">({report.token_symbol})</span>}
          </h2>
        </div>
        <code className="address">
          {report.address} · {report.chain}
        </code>
        <dl className="kv">
          <dt>Rule score</dt>
          <dd>{report.rule_score.toFixed(1)}</dd>
          {report.ml && (
            <>
              <dt>ML P(rug)</dt>
              <dd>
                {(report.ml.rug_probability * 100).toFixed(0)}%
                {!report.ml.reliable && <span className="chip warn small">down-weighted</span>}
                {report.ml.is_out_of_distribution && <span className="chip small">out-of-distribution</span>}
              </dd>
            </>
          )}
          <dt>Scanned</dt>
          <dd>
            {new Date(report.generated_at).toLocaleString()} via {report.provider}
            {fromCache && <span className="chip small">from cache</span>}
          </dd>
          {delta != null && (
            <>
              <dt>vs previous</dt>
              <dd className={delta > 0 ? "worse" : delta < 0 ? "better" : ""}>
                {delta > 0 ? "+" : ""}
                {delta.toFixed(1)}
              </dd>
            </>
          )}
        </dl>
        {report.hard_fail && (
          <div className="hardfail">
            <strong>Pre-filter hard fail.</strong> {report.hard_fail_reasons.join("; ")}
          </div>
        )}
      </div>
    </section>
  );
}
