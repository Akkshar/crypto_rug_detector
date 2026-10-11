import { useEffect, useRef, useState } from "react";
import type { CacheStats, RiskWeights } from "../api";
import {
  CATEGORIES,
  CAT_CAPS,
  CAT_COLORS,
  colr,
  GL,
  LC,
  lvl,
  type HistoryTuple,
  type ReportItem,
  type RiskLevel,
} from "../types";
import { AuraBlobCanvas } from "./canvas/AuraBlobCanvas";
import { Donut3DCanvas } from "./canvas/Donut3DCanvas";
import { ParallaxCarousel } from "./canvas/ParallaxCarousel";
import { Radar3DCanvas } from "./canvas/Radar3DCanvas";
import { morph } from "./canvas/TextMorph";
import { Scan } from "./Scan";

interface Props {
  report: ReportItem;
  chain: string;
  onChainChange: (c: string) => void;
  onScan: (address: string, chain: string) => void;
  onSelectPreset: (presetKey: string) => void;
  onSelectHistory: (item: HistoryTuple) => void;
  isBookmarked: boolean;
  onToggleBookmark: () => void;
  onLabelToken?: (chain: string, address: string, label: "rug" | "benign" | null) => void;
  stats?: CacheStats | null;
  weights?: RiskWeights | null;
  history: HistoryTuple[];
  scanning: boolean;
  scanError: string;
}

export function Home({
  report,
  chain,
  onChainChange,
  onScan,
  onSelectPreset,
  onSelectHistory,
  isBookmarked,
  onToggleBookmark,
  onLabelToken,
  stats,
  weights,
  history,
  scanning,
  scanError,
}: Props) {
  const [filterSev, setFilterSev] = useState<string>("All");
  const [hoveredDonutCat, setHoveredDonutCat] = useState<number>(-1);

  // Animated KPI numbers
  const [kpiScans, setKpiScans] = useState<string>("0");
  const [kpiFlag, setKpiFlag] = useState<string>("0");
  const [kpiAvg, setKpiAvg] = useState<string>("0.0");
  const [kpiSafe, setKpiSafe] = useState<string>("0");

  const prevKpiRef = useRef({ scans: 0, flag: 0, avg: 0, safe: 0 });
  const prevTokenNameRef = useRef<string>(report.n);
  const tokenNameRef = useRef<HTMLSpanElement | null>(null);

  // Morph token title on change
  useEffect(() => {
    if (tokenNameRef.current && prevTokenNameRef.current !== report.n) {
      morph(tokenNameRef.current, prevTokenNameRef.current, report.n);
      prevTokenNameRef.current = report.n;
    }
  }, [report.n]);

  // Animate KPIs when history or stats change
  useEffect(() => {
    const RM = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const n = stats?.reports !== undefined ? stats.reports : history.length;
    const fl = history.filter((h) => h[4] >= 60).length;
    const sf = history.filter((h) => h[4] < 30).length;
    const av = history.length ? history.reduce((a, h) => a + h[4], 0) / history.length : 0;

    const fromScans = prevKpiRef.current.scans;
    const fromFlag = prevKpiRef.current.flag;
    const fromAvg = prevKpiRef.current.avg;
    const fromSafe = prevKpiRef.current.safe;

    prevKpiRef.current = { scans: n, flag: fl, avg: av, safe: sf };

    if (RM) {
      setKpiScans(String(n));
      setKpiFlag(String(fl));
      setKpiAvg(av.toFixed(1));
      setKpiSafe(String(sf));
      return;
    }

    const t0 = performance.now();
    let raf = 0;
    function anim(now: number) {
      const u = Math.min(1, (now - t0) / 700);
      const e = 1 - Math.pow(1 - u, 3);
      setKpiScans(Math.round(fromScans + (n - fromScans) * e).toString());
      setKpiFlag(Math.round(fromFlag + (fl - fromFlag) * e).toString());
      setKpiAvg((fromAvg + (av - fromAvg) * e).toFixed(1));
      setKpiSafe(Math.round(fromSafe + (sf - fromSafe) * e).toString());
      if (u < 1) raf = requestAnimationFrame(anim);
    }
    raf = requestAnimationFrame(anim);
    return () => cancelAnimationFrame(raf);
  }, [history, stats]);

  const level: RiskLevel = lvl(report.sc);
  const color = colr(level);
  const targetColor = LC[level] || LC.CRITICAL;
  const glowAlpha = GL[level] || 1;
  const totPoints = report.p.reduce((a, b) => a + b, 0);

  // Category flag counts
  const flagCounts = CATEGORIES.map((_, i) =>
    report.fl && report.fl[i] !== undefined
      ? report.fl[i]
      : report.f.filter((f) => f[4] === i).length,
  );

  const categoryKeys = [
    "CONTRACT_PRIVILEGE",
    "LIQUIDITY",
    "HONEYPOT",
    "DISTRIBUTION",
    "DEPLOYER",
    "TRADING",
  ];
  const dynamicCaps = CATEGORIES.map((_, i) => {
    const k = categoryKeys[i];
    return weights?.category_caps?.[k] ?? CAT_CAPS[i];
  });

  // Findings filtering
  const tabsList = ["All", "CRITICAL", "HIGH", "MEDIUM", "LOW"];
  const filteredFlags = report.f.filter(
    (f) => filterSev === "All" || f[0] === filterSev,
  );

  return (
    <div className="view" id="v-home">
      <Scan
        chain={chain}
        onChainChange={onChainChange}
        onScan={onScan}
        onSelectPreset={onSelectPreset}
        scanning={scanning}
        error={scanError}
        targetColor={targetColor}
      />

      <div className="kpis">
        <section className="card kpi">
          <i>⌕</i>
          <b data-kpi="scans">{kpiScans}</b>
          <span>Tokens scanned</span>
        </section>
        <section className="card kpi">
          <i style={{ color: "#ff8a8a", background: "#ff4a5e22" }}>⚠</i>
          <b data-kpi="flag">{kpiFlag}</b>
          <span>High-risk flagged</span>
        </section>
        <section className="card kpi">
          <i>◔</i>
          <b data-kpi="avg">{kpiAvg}</b>
          <span>Average risk score</span>
        </section>
        <section className="card kpi">
          <i style={{ color: "#7ff0c0", background: "#27d89022" }}>✓</i>
          <b data-kpi="safe">{kpiSafe}</b>
          <span>Safe tokens</span>
        </section>
      </div>

      <section className="card pc">
        <h3>
          Sample Scans <small>· drag to explore, click to scan</small>
        </h3>
        <ParallaxCarousel onSelect={onSelectPreset} />
      </section>

      <div className="grid">
        <div className="col">
          <section className="card summary" id="sum">
            <AuraBlobCanvas targetColor={targetColor} glowAlpha={glowAlpha} />
            <div className="gauge">
              <svg width="150" height="150" viewBox="0 0 150 150">
                <circle
                  cx="75"
                  cy="75"
                  r="62"
                  fill="none"
                  stroke="#1a2158"
                  strokeWidth="12"
                />
                <circle
                  cx="75"
                  cy="75"
                  r="62"
                  fill="none"
                  stroke={color}
                  strokeWidth="12"
                  strokeLinecap="round"
                  strokeDasharray={`${3.9 * report.sc} 390`}
                  style={{ filter: `drop-shadow(0 0 6px ${color})` }}
                />
              </svg>
              <div>
                <b>{report.sc}</b>
                <small>/100</small>
                <span className={`badge ${level}`}>{level}</span>
              </div>
            </div>

            <div>
              <div className="tok">
                <h2>
                  <span className="tm" ref={tokenNameRef}>
                    {report.n}
                  </span>{" "}
                  <span>({report.s})</span>
                </h2>
                <div style={{ marginLeft: "auto", display: "flex", gap: "6px", flexWrap: "wrap", justifyContent: "flex-end" }}>
                  <button
                    className="ghost sm"
                    data-bmcur="1"
                    onClick={onToggleBookmark}
                  >
                    {isBookmarked ? "★ Saved" : "☆ Save"}
                  </button>
                  {onLabelToken && (
                    <>
                      <button
                        className="ghost sm"
                        title="Submit ground truth label 'rug' to backend cache"
                        style={{ color: "#ff8a8a", borderColor: "#ff4a5e66" }}
                        onClick={() => onLabelToken(report.chain || chain, report.a, "rug")}
                      >
                        ⚠ Rug
                      </button>
                      <button
                        className="ghost sm"
                        title="Submit ground truth label 'benign' to backend cache"
                        style={{ color: "#7ff0c0", borderColor: "#27d89066" }}
                        onClick={() => onLabelToken(report.chain || chain, report.a, "benign")}
                      >
                        ✓ Benign
                      </button>
                    </>
                  )}
                </div>
              </div>
              <div className="addr">{report.a} · {report.chain || "BSC"}</div>
              <div className="addr" style={{ marginTop: "4px" }}>
                {report.provider || "Mock Provider"} · Scanned{" "}
                {report.generated_at
                  ? new Date(report.generated_at).toLocaleString()
                  : new Date().toLocaleString()}
              </div>
              <div className={`verdict ${level}`}>{report.v}</div>
            </div>

            <div className="scores">
              <div>
                <span>Rule Score</span>
                <span>{report.rule.toFixed(1)}</span>
              </div>
              <div>
                <span>
                  ML Rug Prob.
                  <br />
                  <small style={{ color: "var(--mut)" }}>
                    {report.ml_unreliable ? "(down-weighted: unreliable)" : "(down-weighted: unreliable)"}
                  </small>
                </span>
                <span>{report.ml}</span>
              </div>
              <div className="fin">
                <span>Final Score</span>
                <span>{report.sc} /100</span>
              </div>
            </div>
          </section>

          <div className="two">
            <section className="card">
              <h3>
                Risk Breakdown{" "}
                <small style={{ color: "var(--mut)", fontWeight: 400 }}>
                  · points are capped per category
                </small>
              </h3>
              <div id="bars">
                {CATEGORIES.map((c, i) => {
                  const pts = report.p[i];
                  const cap = dynamicCaps[i];
                  const ratio = pts / cap;
                  const barBg =
                    ratio > 0.7
                      ? "var(--crit)"
                      : ratio > 0.3
                      ? "var(--med)"
                      : "var(--pri)";
                  return (
                    <div className="bar" key={i}>
                      <div>
                        {c}
                        <small>{flagCounts[i]} flags</small>
                      </div>
                      <div className="trk">
                        <i style={{ width: `${(pts / cap) * 100}%`, background: barBg }} />
                      </div>
                      <div>
                        {pts} / {cap}
                      </div>
                    </div>
                  );
                })}
              </div>
            </section>
            <section className="card">
              <h3>Category Overview</h3>
              <div id="radar">
                <Radar3DCanvas report={report} />
              </div>
            </section>
          </div>

          <section className="card">
            <div className="tabs" id="tabs">
              {tabsList.map((x) => {
                const count =
                  x === "All"
                    ? report.f.length
                    : report.f.filter((f) => f[0] === x).length;
                return (
                  <button
                    key={x}
                    className={filterSev === x ? "on" : ""}
                    data-t={x}
                    onClick={() => setFilterSev(x)}
                  >
                    {x === "All" ? "All" : x[0] + x.slice(1).toLowerCase()} ({count})
                  </button>
                );
              })}
            </div>
            <div className="tw">
              <table>
                <thead>
                  <tr>
                    <th>Severity</th>
                    <th>Code</th>
                    <th>Description</th>
                    <th>Points</th>
                    <th>Category</th>
                  </tr>
                </thead>
                <tbody id="rows">
                  {filteredFlags.length > 0 ? (
                    filteredFlags.map((f, i) => (
                      <tr key={i}>
                        <td>
                          <span className={`badge ${f[0]}`}>{f[0]}</span>
                        </td>
                        <td>{f[1]}</td>
                        <td>{f[2]}</td>
                        <td style={{ color: colr(f[0]) }}>+{f[3]}</td>
                        <td>{CATEGORIES[f[4]]}</td>
                      </tr>
                    ))
                  ) : (
                    <tr>
                      <td colSpan={5} style={{ color: "var(--mut)" }}>
                        No findings at this severity.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </section>
        </div>

        <div className="col">
          <section className="card">
            <h3>Risk Categories</h3>
            <div className="lg">
              <div className="donut d3" id="donut">
                <Donut3DCanvas
                  report={report}
                  onHoverChange={(idx) => setHoveredDonutCat(idx)}
                />
                <b>
                  {hoveredDonutCat >= 0
                    ? report.p[hoveredDonutCat]
                    : totPoints}
                </b>
                <small style={{ color: "var(--mut)" }}>
                  {hoveredDonutCat >= 0
                    ? CATEGORIES[hoveredDonutCat]
                    : "Total Raw Points"}
                </small>
              </div>
              <ul id="leg">
                {CATEGORIES.map((c, i) => (
                  <li key={i}>
                    <span>
                      <i style={{ background: CAT_COLORS[i] }} />
                      {c}
                    </span>
                    <b>{report.p[i]}</b>
                  </li>
                ))}
              </ul>
            </div>
          </section>

          <section className="card">
            <h3>Top Risk Indicators</h3>
            <div id="ind">
              {report.f.slice(0, 5).map((f, i) => (
                <div className="ind" key={i}>
                  <div>
                    <b>
                      {f[1]
                        .replace(/_/g, " ")
                        .toLowerCase()
                        .replace(/\b\w/g, (m) => m.toUpperCase())}
                    </b>
                    <small>{f[2]}</small>
                  </div>
                  <span className={`badge ${f[0]}`}>{f[0]}</span>
                  <b>+{f[3]}</b>
                </div>
              ))}
            </div>
          </section>

          <section className="card">
            <h3>Scan History</h3>
            <div id="hist">
              {history.slice(0, 5).map((h, i) => {
                const l = lvl(h[4]);
                return (
                  <button
                    key={i}
                    className="hist"
                    data-h={i}
                    onClick={() => onSelectHistory(h)}
                  >
                    <div>
                      <b>{h[1]}</b> {h[2]}
                      <small>{h[3]}</small>
                    </div>
                    <span className={`badge ${l}`}>{l}</span>
                    <b>{h[4]}/100</b>
                  </button>
                );
              })}
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}
