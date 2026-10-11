import { useEffect, useRef, useState } from "react";
import {
  api,
  type CacheStats,
  type Chain,
  type Health,
  type RiskReport,
  type RiskWeights,
} from "./api";
import { ApiView } from "./components/ApiView";
import { Bookmarks } from "./components/Bookmarks";
import { HexGridCanvas } from "./components/canvas/HexGridCanvas";
import { InfinityTrailLoader } from "./components/canvas/InfinityTrailLoader";
import { initCardSpotlights, updateAllSpotlights } from "./components/canvas/Spotlight";
import { History } from "./components/History";
import { Home } from "./components/Home";
import { Intro } from "./components/Intro";
import { Settings } from "./components/Settings";
import { Sidebar } from "./components/Sidebar";
import {
  INITIAL_HIST,
  LC,
  LS,
  lvl,
  SAMPLE_DATA,
  type BookmarkItem,
  type HistoryTuple,
  type ReportItem,
  type RiskLevel,
  type SettingsState,
} from "./types";

function mapReportToUI(r: RiskReport): ReportItem {
  const catMap: Record<string, number> = {
    CONTRACT_PRIVILEGE: 0,
    LIQUIDITY: 1,
    HONEYPOT: 2,
    DISTRIBUTION: 3,
    DEPLOYER: 4,
    TRADING: 5,
  };

  const points = [0, 0, 0, 0, 0, 0];
  const flagCounts = [0, 0, 0, 0, 0, 0];
  if (r.breakdown) {
    r.breakdown.forEach((b) => {
      const idx = catMap[b.category.toUpperCase()];
      if (idx !== undefined) {
        points[idx] = Math.round(b.capped_points);
        flagCounts[idx] = b.flag_count;
      }
    });
  }

  const flags: [RiskLevel, string, string, number, number][] = (r.flags || []).map((f) => {
    const sev: RiskLevel =
      f.severity === "CRITICAL"
        ? "CRITICAL"
        : f.severity === "HIGH"
        ? "HIGH"
        : f.severity === "MEDIUM"
        ? "MEDIUM"
        : "LOW";
    const cIdx = catMap[f.category.toUpperCase()] ?? 0;
    return [sev, f.code, f.title ? `${f.title}: ${f.detail}` : f.detail, f.points, cIdx];
  });

  let verdict = "Analysis complete.";
  if (r.hard_fail && r.hard_fail_reasons && r.hard_fail_reasons.length > 0) {
    verdict = `HARD FAIL — ${r.hard_fail_reasons.join("; ")}`;
  } else if (r.risk_tier === "CRITICAL" || r.risk_tier === "HIGH") {
    verdict = `WARNING — Severe risk factors identified in smart contract and liquidity pool.`;
  } else if (r.risk_tier === "MEDIUM") {
    verdict = `CAUTION — Elevated risk signals detected. Review top holders and deployer history.`;
  } else {
    verdict = `No hard-fail rules triggered. Contract, liquidity and holders look healthy.`;
  }

  const mlProb = r.ml?.rug_probability ?? 0.05;
  const mlUnreliable = r.ml ? !r.ml.reliable || r.ml.is_out_of_distribution : false;

  return {
    n: r.token_name || "Custom Token",
    s: r.token_symbol || "TOKEN",
    a: r.address,
    sc: Number(r.risk_score.toFixed(1)),
    rule: Number(r.rule_score.toFixed(1)),
    ml: Number(mlProb.toFixed(2)),
    ml_unreliable: mlUnreliable,
    v: verdict,
    p: points,
    fl: flagCounts,
    f: flags,
    chain: r.chain ? r.chain.toUpperCase() : "BSC",
    provider: r.provider,
    generated_at: r.generated_at,
  };
}

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [introOpen, setIntroOpen] = useState(true);
  const [curView, setCurView] = useState<
    "home" | "scan" | "history" | "bookmarks" | "api" | "settings"
  >("home");
  const [curPresetKey, setCurPresetKey] = useState<string>("honeypot");
  const [curReport, setCurReport] = useState<ReportItem>(SAMPLE_DATA.honeypot);

  // Settings
  const [settings, setSettings] = useState<SettingsState>(() =>
    LS.g("rs_st", {
      chain: "BSC",
      thr: 60,
      fx: true,
      key: "rs_live_8f3a91c2d7e04b65a9f2",
    }),
  );

  // Bookmarks
  const [bookmarks, setBookmarks] = useState<BookmarkItem[]>(() =>
    LS.g("rs_bm", [{ k: "benign", n: "Uniswap V2", s: "UNI", sc: 6.1 }]),
  );

  // History
  const [history, setHistory] = useState<HistoryTuple[]>(INITIAL_HIST);

  // Scanning state
  const [scanning, setScanning] = useState(false);
  const [scanError, setScanError] = useState("");
  const [stats, setStats] = useState<CacheStats | null>(null);
  const [weights, setWeights] = useState<RiskWeights | null>(null);

  // Loader state
  const [loaderActive, setLoaderActive] = useState(true);
  const [loaderLabel, setLoaderLabel] = useState("Loading...");

  // Toast
  const [toastMsg, setToastMsg] = useState("");
  const toastTimerRef = useRef<number | null>(null);

  const showToast = (msg: string) => {
    setToastMsg(msg);
    if (toastTimerRef.current) clearTimeout(toastTimerRef.current);
    toastTimerRef.current = window.setTimeout(() => {
      setToastMsg("");
    }, 1800);
  };

  const copy = (text: string) => {
    const fb = () => {
      const a = document.createElement("textarea");
      a.value = text;
      document.body.appendChild(a);
      a.select();
      let ok = false;
      try {
        ok = document.execCommand("copy");
      } catch {
        /* ignore */
      }
      a.remove();
      showToast(ok ? "Copied" : "Copy failed");
    };
    try {
      navigator.clipboard.writeText(text).then(
        () => showToast("Copied"),
        fb,
      );
    } catch {
      fb();
    }
  };

  // Initial loader: auto-dismisses at 2200ms
  useEffect(() => {
    const timer = setTimeout(() => {
      setLoaderActive(false);
    }, 2200);
    return () => clearTimeout(timer);
  }, []);

  // Fetch backend health and load real cached scan history
  useEffect(() => {
    api
      .health()
      .then((h) => {
        setHealth(h);
      })
      .catch(() => {
        setHealth(null);
      });

    api
      .stats()
      .then((s) => {
        setStats(s);
      })
      .catch(() => {});

    api
      .weights()
      .then((w) => {
        setWeights(w);
      })
      .catch(() => {});

    api
      .globalHistory(50)
      .then((reports) => {
        if (reports && reports.length > 0) {
          const tuples: HistoryTuple[] = reports.map((r) => [
            r.risk_tier === "CRITICAL" ? "honeypot" : r.risk_tier === "HIGH" ? "unlocked" : "benign",
            r.token_name || "Token",
            `(${r.token_symbol || "TOK"})`,
            new Date(r.generated_at).toLocaleString(),
            Number(r.risk_score.toFixed(1)),
          ]);
          setHistory(tuples);
        }
      })
      .catch(() => {
        /* fallback to initial */
      });
  }, []);

  // Initialize card spotlights & handle view/report changes
  useEffect(() => {
    initCardSpotlights();
    const l = lvl(curReport.sc);
    const GT = LC[l] || LC.CRITICAL;
    const cs = (a: number[]) => a.map(Math.round);
    const mx = (a: number[], b: number[], t: number) => a.map((v, i) => v + (b[i] - v) * t);
    const c = `rgb(${cs(mx(GT, [138, 155, 255], 0.5))})`;
    updateAllSpotlights(c);
  }, [curReport, curView]);

  // Navigate view
  const handleNavigate = (v: string) => {
    const viewName = v as typeof curView;
    setCurView(viewName);
    window.scrollTo(0, 0);
    if (v === "scan") {
      setTimeout(() => {
        const addrInput = document.getElementById("addr");
        addrInput?.scrollIntoView({ block: "center" });
        addrInput?.focus({ preventScroll: true });
      }, 50);
    }
  };

  // Select sample preset
  const handleSelectPreset = (key: string) => {
    setCurPresetKey(key);
    const preset = SAMPLE_DATA[key] || SAMPLE_DATA.honeypot;
    setCurReport(preset);
    if (curView !== "home" && curView !== "scan") {
      setCurView("home");
    }
  };

  // Select history item to view
  const handleSelectHistory = (h: HistoryTuple) => {
    const key = h[0] === "safe" ? "honeypot" : h[0];
    handleSelectPreset(key);
    if (curView !== "home" && curView !== "scan") {
      setCurView("home");
    }
    const RM = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    setTimeout(() => {
      document.getElementById("sum")?.scrollIntoView({
        behavior: RM ? "auto" : "smooth",
        block: "center",
      });
    }, 50);
  };

  // Bookmarks toggle
  const isBookmarked = (name: string) => bookmarks.some((b) => b.n === name);

  const handleToggleCurrentBookmark = () => {
    const existingIdx = bookmarks.findIndex((b) => b.n === curReport.n);
    let updated: BookmarkItem[];
    if (existingIdx >= 0) {
      updated = bookmarks.filter((_, idx) => idx !== existingIdx);
    } else {
      updated = [
        ...bookmarks,
        {
          k: curPresetKey,
          n: curReport.n,
          s: curReport.s,
          sc: curReport.sc,
        },
      ];
    }
    setBookmarks(updated);
    LS.s("rs_bm", updated);
  };

  const handleToggleHistoryBookmark = (h: HistoryTuple) => {
    const existingIdx = bookmarks.findIndex((b) => b.n === h[1]);
    let updated: BookmarkItem[];
    if (existingIdx >= 0) {
      updated = bookmarks.filter((_, idx) => idx !== existingIdx);
    } else {
      const k = h[0] === "safe" ? "honeypot" : h[0];
      const s = h[2].replace(/[()]/g, "");
      updated = [
        ...bookmarks,
        {
          k,
          n: h[1],
          s,
          sc: h[4],
        },
      ];
    }
    setBookmarks(updated);
    LS.s("rs_bm", updated);
  };

  const handleOpenBookmarkReport = (b: BookmarkItem) => {
    handleSelectPreset(b.k);
    if (curView !== "home" && curView !== "scan") {
      setCurView("home");
    }
    const RM = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    setTimeout(() => {
      document.getElementById("sum")?.scrollIntoView({
        behavior: RM ? "auto" : "smooth",
        block: "center",
      });
    }, 50);
  };

  const handleRemoveBookmark = (idx: number) => {
    const updated = bookmarks.filter((_, i) => i !== idx);
    setBookmarks(updated);
    LS.s("rs_bm", updated);
  };

  // Settings update
  const handleUpdateSettings = (newSettings: SettingsState, quiet?: boolean) => {
    setSettings(newSettings);
    LS.s("rs_st", newSettings);
    if (!quiet) showToast("Settings saved");
  };

  const handleClearHistory = () => {
    setHistory([]);
    showToast("History cleared");
  };

  // Perform scan
  const handleScan = async (address: string, chainStr: string) => {
    if (!/^0x[a-fA-F0-9]{40}$/.test(address)) {
      setScanError("Enter a valid contract address: 0x followed by 40 hex characters.");
      return;
    }
    setScanError("");
    setScanning(true);
    setLoaderLabel("Scanning...");
    setLoaderActive(true);

    const chainMap: Record<string, Chain> = {
      BSC: "bsc",
      ETH: "ethereum",
      Base: "base",
    };
    const chainParam = chainMap[chainStr] || "bsc";

    try {
      const res = await api.scan(chainParam, address);
      const mapped = mapReportToUI(res.report);
      setCurReport(mapped);
      setCurPresetKey("unlocked");

      const newHistItem: HistoryTuple = [
        mapped.sc >= 90 ? "honeypot" : mapped.sc >= 60 ? "unlocked" : "benign",
        mapped.n,
        `(${mapped.s})`,
        new Date().toLocaleString(),
        mapped.sc,
      ];
      setHistory((prev) => [newHistItem, ...prev.slice(0, 49)]);
      api.stats().then(setStats).catch(() => {});
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : String(err);
      setScanError(message || "Scan failed. Please check the contract address and network connection.");
    } finally {
      setTimeout(() => {
        setScanning(false);
        setLoaderActive(false);
      }, 600);
    }
  };

  // Submit token ground truth label to backend cache
  const handleLabelToken = async (
    chainStr: string,
    addr: string,
    label: "rug" | "benign" | null,
  ) => {
    const chainMap: Record<string, Chain> = {
      BSC: "bsc",
      ETH: "ethereum",
      Base: "base",
    };
    const c = chainMap[chainStr] || "bsc";
    try {
      const res = await api.label(c, addr, label);
      showToast(
        label
          ? `Token labelled as ${label.toUpperCase()} (${res.updated} updated)`
          : "Label cleared",
      );
      api.stats().then(setStats).catch(() => {});
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      showToast(`Labelling failed: ${msg}`);
    }
  };

  // Dismiss intro
  const handleDismissIntro = (scrollToSample?: boolean) => {
    setIntroOpen(false);
    if (scrollToSample) {
      const RM = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      setTimeout(() => {
        document.querySelector(".grid")?.scrollIntoView({
          behavior: RM ? "auto" : "smooth",
        });
      }, 50);
    }
  };

  const topChainLabel =
    ({ BSC: "BSC (BNB Smart Chain)", ETH: "Ethereum", Base: "Base" })[settings.chain] ||
    settings.chain;

  const topProviderLabel = health?.provider
    ? health.provider.charAt(0).toUpperCase() + health.provider.slice(1) + " Provider"
    : "Mock Provider";

  const topModelLabel = health?.model
    ? health.model.includes("synthetic")
      ? "Synthetic Model"
      : health.model + " Model"
    : "Synthetic Model";

  return (
    <>
      <InfinityTrailLoader active={loaderActive} label={loaderLabel} />

      {introOpen && <Intro onDismiss={handleDismissIntro} />}

      {/* Decorative Fixed Background */}
      <div className="bg-decor" aria-hidden="true">
        <div className="bg-glow" />
        <HexGridCanvas visible={settings.fx} targetColor={LC[lvl(curReport.sc)]} />
      </div>

      <div id="toast" role="status" className={toastMsg ? "on" : ""}>
        {toastMsg}
      </div>

      <div className="app" inert={introOpen || undefined}>
        <Sidebar
          curView={curView}
          onNavigate={handleNavigate}
          onSelectPreset={handleSelectPreset}
          health={health}
        />

        <main>
          <div className="top">
            <span className="chip">{topChainLabel}</span>
            <span className="chip">{topProviderLabel}</span>
            <span className="chip">{topModelLabel}</span>
          </div>

          {(curView === "home" || curView === "scan") && (
            <Home
              report={curReport}
              chain={settings.chain}
              onChainChange={(newChain) => {
                handleUpdateSettings({ ...settings, chain: newChain }, true);
              }}
              onScan={handleScan}
              onSelectPreset={handleSelectPreset}
              onSelectHistory={handleSelectHistory}
              isBookmarked={isBookmarked(curReport.n)}
              onToggleBookmark={handleToggleCurrentBookmark}
              onLabelToken={handleLabelToken}
              stats={stats}
              weights={weights}
              history={history}
              scanning={scanning}
              scanError={scanError}
            />
          )}

          {curView === "history" && (
            <History
              history={history}
              threshold={settings.thr}
              onViewReport={handleSelectHistory}
              onToggleBookmark={handleToggleHistoryBookmark}
              isBookmarked={isBookmarked}
            />
          )}

          {curView === "bookmarks" && (
            <Bookmarks
              bookmarks={bookmarks}
              onOpenReport={handleOpenBookmarkReport}
              onRemoveBookmark={handleRemoveBookmark}
            />
          )}

          {curView === "api" && <ApiView apiKey={settings.key} onCopy={copy} />}

          {curView === "settings" && (
            <Settings
              settings={settings}
              onUpdateSettings={handleUpdateSettings}
              onClearHistory={handleClearHistory}
              historyCount={history.length}
              onCopy={copy}
            />
          )}
        </main>
      </div>
    </>
  );
}
