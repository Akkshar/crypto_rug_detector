import type { RiskReport } from "../api";

export function History({ reports }: { reports: RiskReport[] }) {
  const ordered = [...reports].reverse(); // API returns newest first; chart oldest → newest
  const w = 320;
  const h = 72;
  const pad = 6;
  const xs = ordered.map((_, i) => pad + (i * (w - 2 * pad)) / Math.max(1, ordered.length - 1));
  const ys = ordered.map((r) => h - pad - (r.risk_score / 100) * (h - 2 * pad));
  const points = xs.map((x, i) => `${x},${ys[i]}`).join(" ");
  return (
    <section className="card">
      <h3>Scan history ({reports.length})</h3>
      <svg viewBox={`0 0 ${w} ${h}`} className="spark" role="img" aria-label="risk score over scans">
        <line x1={pad} x2={w - pad} y1={h - pad - 0.5 * (h - 2 * pad)} y2={h - pad - 0.5 * (h - 2 * pad)} className="spark-mid" />
        <line x1={pad} x2={w - pad} y1={h - pad - 0.75 * (h - 2 * pad)} y2={h - pad - 0.75 * (h - 2 * pad)} className="spark-crit" />
        <polyline points={points} fill="none" className="spark-line" />
        {xs.map((x, i) => (
          <circle key={i} cx={x} cy={ys[i]} r={3} className={`spark-dot ${ordered[i].risk_tier.toLowerCase()}`} />
        ))}
      </svg>
      <table className="history">
        <tbody>
          {reports.slice(0, 8).map((r) => (
            <tr key={r.generated_at}>
              <td className="muted">{new Date(r.generated_at).toLocaleString()}</td>
              <td>
                <span className={`sev ${r.risk_tier.toLowerCase()}`}>{r.risk_tier}</span>
              </td>
              <td className="num">{r.risk_score.toFixed(1)}</td>
              <td className="muted">{r.flags.length} flags</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
