import { useState } from "react";
import { colr, lvl, type HistoryTuple } from "../types";

interface Props {
  history: HistoryTuple[];
  threshold: number;
  onViewReport: (item: HistoryTuple) => void;
  onToggleBookmark: (item: HistoryTuple) => void;
  isBookmarked: (name: string) => boolean;
}

export function History({
  history,
  threshold,
  onViewReport,
  onToggleBookmark,
  isBookmarked,
}: Props) {
  const [query, setQuery] = useState("");
  const [sevFilter, setSevFilter] = useState("All");

  const q = query.toLowerCase();
  const filtered = history.filter(
    (h) =>
      (sevFilter === "All" || lvl(h[4]) === sevFilter) &&
      (h[1] + h[2]).toLowerCase().includes(q),
  );

  return (
    <div className="view" id="v-history">
      <section className="card">
        <h3>
          Scan History{" "}
          <small style={{ color: "var(--mut)", fontWeight: 400 }}>
            · {history.length} scans
          </small>
        </h3>
        <div className="vbar">
          <input
            id="hq"
            placeholder="Search token or symbol…"
            aria-label="Search history"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <div className="tabs" style={{ margin: 0 }}>
            {["All", "CRITICAL", "HIGH", "MEDIUM", "LOW"].map((l) => (
              <button
                key={l}
                className={sevFilter === l ? "on" : ""}
                data-hl={l}
                onClick={() => setSevFilter(l)}
              >
                {l === "All" ? "All" : l[0] + l.slice(1).toLowerCase()}
              </button>
            ))}
          </div>
        </div>
        <div className="tw">
          <table>
            <thead>
              <tr>
                <th>Token</th>
                <th>Risk</th>
                <th>Score</th>
                <th>Scanned</th>
                <th></th>
              </tr>
            </thead>
            <tbody id="hbody">
              {filtered.length > 0 ? (
                filtered.map((h, i) => {
                  const l = lvl(h[4]);
                  return (
                    <tr key={i}>
                      <td>
                        <b>{h[1]}</b>{" "}
                        <span style={{ color: "var(--mut)" }}>{h[2]}</span>
                      </td>
                      <td>
                        <span className={`badge ${l}`}>{l}</span>
                      </td>
                      <td style={{ color: colr(l) }}>
                        {h[4]}/100
                        {h[4] >= threshold && (
                          <span title="At or above your alert threshold"> ⚠</span>
                        )}
                      </td>
                      <td style={{ color: "var(--mut)" }}>{h[3]}</td>
                      <td style={{ whiteSpace: "nowrap" }}>
                        <button
                          className="ghost sm"
                          data-ho={i}
                          onClick={() => onViewReport(h)}
                        >
                          View report
                        </button>{" "}
                        <button
                          className="ghost sm"
                          data-hb={i}
                          aria-label="Toggle bookmark"
                          onClick={() => onToggleBookmark(h)}
                        >
                          {isBookmarked(h[1]) ? "★" : "☆"}
                        </button>
                      </td>
                    </tr>
                  );
                })
              ) : (
                <tr>
                  <td colSpan={5} style={{ color: "var(--mut)" }}>
                    No scans match.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
