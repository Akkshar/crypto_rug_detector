import { useState } from "react";
import type { RiskFlag, Severity } from "../api";

const ORDER: Severity[] = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"];

export function Flags({ flags }: { flags: RiskFlag[] }) {
  const [open, setOpen] = useState<string | null>(null);
  const sorted = [...flags].sort((a, b) => b.points - a.points || ORDER.indexOf(a.severity) - ORDER.indexOf(b.severity));
  return (
    <section className="card">
      <h3>Evidence ({flags.length} flag{flags.length === 1 ? "" : "s"})</h3>
      {flags.length === 0 ? (
        <p className="muted">No risk flags raised.</p>
      ) : (
        <table className="flags">
          <thead>
            <tr>
              <th>Severity</th>
              <th>Finding</th>
              <th className="num">Points</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((f) => {
              const hasEvidence = Object.keys(f.evidence).length > 0;
              const isOpen = open === f.code;
              return (
                <tr key={f.code} className={isOpen ? "open" : ""}>
                  <td>
                    <span className={`sev ${f.severity.toLowerCase()}`}>{f.severity}</span>
                  </td>
                  <td>
                    <div className="flag-title">
                      {f.title} <code className="code">{f.code}</code>
                    </div>
                    <div className="flag-detail">{f.detail}</div>
                    {hasEvidence && (
                      <button type="button" className="link small" onClick={() => setOpen(isOpen ? null : f.code)}>
                        {isOpen ? "hide evidence" : "show evidence"}
                      </button>
                    )}
                    {isOpen && <pre className="evidence">{JSON.stringify(f.evidence, null, 2)}</pre>}
                  </td>
                  <td className="num">+{f.points}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </section>
  );
}
