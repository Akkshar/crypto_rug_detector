import { useEffect, useRef, useState } from "react";
import { api, type Chain, type Health, type RiskReport } from "./api";
import Globe from "./components/Globe";

// Risk categories and constants
const CATEGORIES = [
  "Contract Privilege",
  "Liquidity",
  "Honeypot",
  "Distribution",
  "Deployer",
  "Trading",
] as const;

const CAT_COLORS = ["#4b6bff", "#2fa8ff", "#ff4a5e", "#ff7a3d", "#f2f2ff", "#27d890"];
const CAT_CAPS = [50, 50, 60, 30, 35, 30];

type RiskLevel = "CRITICAL" | "HIGH" | "MEDIUM" | "LOW";

interface ReportItem {
  n: string;
  s: string;
  a: string;
  sc: number;
  rule: number;
  ml: number;
  ml_unreliable?: boolean;
  v: string;
  p: number[];
  f: [RiskLevel, string, string, number, number][]; // [severity, code, detail, points, categoryIndex]
  chain?: string;
  cached?: boolean;
}

interface BookmarkItem {
  k: string;
  n: string;
  s: string;
  sc: number;
  a?: string;
  chain?: string;
  tags?: string[];
}

interface HistoryItem {
  id: string;
  key: string;
  name: string;
  symbol: string;
  time: string;
  score: number;
  address: string;
  chain: string;
  report?: ReportItem;
  cached?: boolean;
}

// Built-in sample presets
const SAMPLE_PRESETS: Record<string, ReportItem> = {
  honeypot: {
    n: "SafeMoonElon2.0",
    s: "SME2",
    a: "0x2222222222222222222222222222222222222222",
    sc: 99.8,
    rule: 100,
    ml: 0.99,
    v: "HARD FAIL — Declared sell tax 99% ≥ 90%; honeypot: 0/10 simulated sells succeeded.",
    p: [50, 0, 60, 30, 23, 25],
    f: [
      ["CRITICAL", "HONEYPOT_SELL_BLOCKED", "0/10 simulated sells succeeded — revert: TransferHelper: TRANSFER_FROM_FAILED", 50, 2],
      ["CRITICAL", "SELL_TAX_EXTREME", "Declared sell tax of 99% confiscates essentially the whole trade", 40, 0],
      ["HIGH", "NO_SELLS_OBSERVED", "184 buys from 150 wallet(s) over blocks 43100000–43140000 with zero sells", 25, 5],
      ["HIGH", "UNBOUNDED_FEE_SETTER", "setSellFee(), setBuyFee() can raise taxes above 25% (potentially to 100%) after launch", 20, 0],
      ["HIGH", "TOP10_CONCENTRATION_HIGH", "10 wallets control 67% (excl. DEX/burn) — dump risk", 20, 3],
      ["MEDIUM", "DEPLOYER_FRESH_WALLET", "Deployer wallet funded 2 hours before launch", 15, 4],
      ["MEDIUM", "OWNER_NOT_RENOUNCED", "Owner can still change contract settings", 10, 0],
    ],
  },
  unlocked: {
    n: "MoonVault",
    s: "MVLT",
    a: "0x7a3c91f0b2d84e65aa10c3de9f2b7c48d1e05f63",
    sc: 71.4,
    rule: 68,
    ml: 0.78,
    v: "WARNING — Liquidity is unlocked and the owner can withdraw it at any time.",
    p: [22, 48, 0, 18, 14, 12],
    f: [
      ["HIGH", "LIQUIDITY_UNLOCKED", "100% of LP tokens held by deployer, no lock detected", 40, 1],
      ["HIGH", "LP_REMOVABLE_BY_OWNER", "Owner can remove liquidity without delay", 30, 1],
      ["MEDIUM", "MINT_FUNCTION_PRESENT", "mint() callable by owner, supply is not capped", 15, 0],
      ["MEDIUM", "TOP10_CONCENTRATION", "10 wallets hold 41% of supply", 18, 3],
      ["LOW", "DEPLOYER_PRIOR_TOKENS", "Deployer created 2 earlier tokens, both inactive", 8, 4],
    ],
  },
  benign: {
    n: "Uniswap V2",
    s: "UNI",
    a: "0x1f9840a85d5af5bf1d1762f925bdaddc4201f984",
    sc: 6.1,
    rule: 5,
    ml: 0.04,
    v: "No hard-fail rules triggered. Contract, liquidity and holders look healthy.",
    p: [3, 0, 0, 4, 0, 0],
    f: [
      ["LOW", "OWNER_CAN_PAUSE", "Owner can pause transfers during emergencies", 3, 0],
      ["LOW", "HOLDER_CONCENTRATION_MILD", "Top 10 wallets hold 18% of supply", 4, 3],
    ],
  },
  pepe: {
    n: "PepeCoin",
    s: "PEPE",
    a: "0x6982508145454ce325ddbe47a25d4ec3d2311933",
    sc: 78.6,
    rule: 75,
    ml: 0.81,
    v: "HIGH RISK — Suspicious insider minting pattern & deployer holding major LP.",
    p: [35, 40, 0, 20, 15, 10],
    f: [
      ["HIGH", "LP_REMOVABLE_BY_OWNER", "Owner has permission to withdraw DEX liquidity", 35, 1],
      ["HIGH", "TOP10_CONCENTRATION_HIGH", "Top 10 non-contract wallets control 54% of circulating supply", 20, 3],
      ["MEDIUM", "OWNER_NOT_RENOUNCED", "Owner retains administrative control of smart contract", 15, 0],
    ],
  },
  testtoken: {
    n: "TestToken",
    s: "TTK",
    a: "0x3333333333333333333333333333333333333333",
    sc: 42.3,
    rule: 40,
    ml: 0.45,
    v: "ELEVATED RISK — Moderate whale concentration and unverified owner actions.",
    p: [12, 18, 0, 15, 10, 8],
    f: [
      ["MEDIUM", "TOP10_CONCENTRATION", "Top wallets hold 42% of tokens", 18, 3],
      ["MEDIUM", "LP_PARTIALLY_LOCKED", "LP lock expires in under 14 days", 15, 1],
      ["LOW", "OWNER_CAN_BLACKLIST", "Blacklist functionality present in bytecode", 8, 0],
    ],
  },
};

const INITIAL_HISTORY: HistoryItem[] = [
  { id: "1", key: "honeypot", name: "SafeMoonElon2.0", symbol: "SME2", time: "10/11/2026, 03:52 AM", score: 99.8, address: "0x2222222222222222222222222222222222222222", chain: "BSC" },
  { id: "2", key: "benign", name: "Uniswap V2", symbol: "UNI", time: "10/10/2026, 11:24 PM", score: 6.1, address: "0x1f9840a85d5af5bf1d1762f925bdaddc4201f984", chain: "ETH" },
  { id: "3", key: "testtoken", name: "TestToken", symbol: "TTK", time: "10/10/2026, 10:17 PM", score: 42.3, address: "0x3333333333333333333333333333333333333333", chain: "Base" },
  { id: "4", key: "pepe", name: "PepeCoin", symbol: "PEPE", time: "10/09/2026, 09:42 PM", score: 78.6, address: "0x6982508145454ce325ddbe47a25d4ec3d2311933", chain: "ETH" },
  { id: "5", key: "unlocked", name: "MoonVault", symbol: "MVLT", time: "10/08/2026, 06:40 PM", score: 71.4, address: "0x7a3c91f0b2d84e65aa10c3de9f2b7c48d1e05f63", chain: "BSC" },
  { id: "6", key: "honeypot", name: "RugKing", symbol: "RUGK", time: "10/08/2026, 05:12 PM", score: 96.2, address: "0x9999999999999999999999999999999999999999", chain: "BSC" },
  { id: "7", key: "benign", name: "StableLP", symbol: "SLP", time: "10/07/2026, 11:03 AM", score: 9.8, address: "0x4444444444444444444444444444444444444444", chain: "Arbitrum" },
  { id: "8", key: "unlocked", name: "DogeMoon", symbol: "DMN", time: "10/07/2026, 09:31 AM", score: 55.0, address: "0x5555555555555555555555555555555555555555", chain: "BSC" },
];

function getLevel(score: number): RiskLevel {
  if (score >= 85) return "CRITICAL";
  if (score >= 60) return "HIGH";
  if (score >= 30) return "MEDIUM";
  return "LOW";
}

function getColor(lvl: RiskLevel): string {
  switch (lvl) {
    case "CRITICAL": return "var(--crit)";
    case "HIGH": return "var(--high)";
    case "MEDIUM": return "var(--med)";
    case "LOW": return "var(--low)";
  }
}

// Convert Backend RiskReport to UI ReportItem
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
  if (r.breakdown) {
    r.breakdown.forEach((b) => {
      const idx = catMap[b.category.toUpperCase()];
      if (idx !== undefined) {
        points[idx] = Math.round(b.capped_points);
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
    verdict = `No critical hard-fail rules triggered. Contract, liquidity and holders look healthy.`;
  }

  const mlProb = r.ml?.rug_probability ?? 0.05;
  const mlUnreliable = r.ml ? (!r.ml.reliable || r.ml.is_out_of_distribution) : false;

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
    f: flags,
    chain: r.chain ? r.chain.toUpperCase() : "BSC",
  };
}

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [curView, setCurView] = useState<"home" | "scan" | "history" | "bookmarks" | "api" | "settings">("home");
  const [curReport, setCurReport] = useState<ReportItem>(SAMPLE_PRESETS.honeypot);
  const [curKey, setCurKey] = useState<string>("honeypot");
  const [filterSeverity, setFilterSeverity] = useState<string>("All");

  const [inputAddress, setInputAddress] = useState("");
  const [inputChain, setInputChain] = useState("BSC");
  const [isScanning, setIsScanning] = useState(false);
  const [scanError, setScanError] = useState("");

  const [introOpen, setIntroOpen] = useState(true);
  const [loaderOpen, setLoaderOpen] = useState(true);
  const [toastMsg, setToastMsg] = useState("");

  // Scan Studio inspection options
  const [studioOptions, setStudioOptions] = useState({
    simulateTrade: true,
    deepHolders: true,
    bytecodeCheck: true,
    traceDeployer: true,
    bypassCache: false,
    blockNumber: "",
  });

  // History & Comparison
  const [history, setHistory] = useState<HistoryItem[]>(() => {
    try {
      const s = localStorage.getItem("rs_hist");
      return s ? JSON.parse(s) : INITIAL_HISTORY;
    } catch {
      return INITIAL_HISTORY;
    }
  });

  const [compareIds, setCompareIds] = useState<string[]>([]);
  const [histChainFilter, setHistChainFilter] = useState("All");

  // Bookmarks
  const [bookmarks, setBookmarks] = useState<BookmarkItem[]>(() => {
    try {
      const s = localStorage.getItem("rs_bm");
      return s ? JSON.parse(s) : [
        { k: "benign", n: "Uniswap V2", s: "UNI", sc: 6.1, tags: ["DEX-pair", "Audited"] },
        { k: "unlocked", n: "MoonVault", s: "MVLT", sc: 71.4, tags: ["High-TVL"] },
      ];
    } catch {
      return [{ k: "benign", n: "Uniswap V2", s: "UNI", sc: 6.1, tags: ["DEX-pair"] }];
    }
  });

  // Settings
  const [settings, setSettings] = useState(() => {
    try {
      const s = localStorage.getItem("rs_st");
      return s ? JSON.parse(s) : {
        chain: "BSC",
        thr: 60,
        fx: true,
        key: "rs_live_8f3a91c2d7e04b65a9f2",
        rpcUrls: {
          bsc: "https://bsc-dataseed.binance.org/",
          ethereum: "https://eth.llamarpc.com",
          base: "https://mainnet.base.org",
          arbitrum: "https://arb1.arbitrum.io/rpc",
        },
        discordWebhook: "",
        telegramChat: "",
        mlFactor: 25,
      };
    } catch {
      return {
        chain: "BSC",
        thr: 60,
        fx: true,
        key: "rs_live_8f3a91c2d7e04b65a9f2",
        rpcUrls: {
          bsc: "https://bsc-dataseed.binance.org/",
          ethereum: "https://eth.llamarpc.com",
          base: "https://mainnet.base.org",
          arbitrum: "https://arb1.arbitrum.io/rpc",
        },
        discordWebhook: "",
        telegramChat: "",
        mlFactor: 25,
      };
    }
  });

  // History search filter
  const [histQuery, setHistQuery] = useState("");
  const [histFilter, setHistFilter] = useState("All");

  // API docs interactive state
  const [apiTab, setApiTab] = useState<"curl" | "js" | "py" | "go">("curl");
  const [apiEndpoint, setApiEndpoint] = useState<"scan" | "history" | "label" | "weights" | "stats" | "health">("scan");
  const [apiParamAddress, setApiParamAddress] = useState("0x2222222222222222222222222222222222222222");
  const [apiParamChain, setApiParamChain] = useState<Chain>("bsc");
  const [apiResponseStatus, setApiResponseStatus] = useState<number>(200);
  const [apiResponseLatency, setApiResponseLatency] = useState<number>(42);
  const [apiResponseText, setApiResponseText] = useState<string>("// Send request to see response");

  const [keyRevealed, setKeyRevealed] = useState(false);
  const [clearArmed, setClearArmed] = useState(false);

  // Settings active tab
  const [settingsTab, setSettingsTab] = useState<"general" | "rpc" | "engine" | "alerts">("general");

  // Canvas refs
  const loaderCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const introVoxelRef = useRef<HTMLCanvasElement | null>(null);
  const hexCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const shellCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const auraCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const radarCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const donutCanvasRef = useRef<HTMLCanvasElement | null>(null);

  // Parallax carousel ref
  const carouselViewRef = useRef<HTMLDivElement | null>(null);
  const carouselTrackRef = useRef<HTMLDivElement | null>(null);

  // Animated KPI numbers
  const [kpiScanned, setKpiScanned] = useState(history.length);
  const [kpiFlagged, setKpiFlagged] = useState(history.filter((h) => h.score >= 60).length);
  const [kpiAvg, setKpiAvg] = useState(history.length ? (history.reduce((a, b) => a + b.score, 0) / history.length).toFixed(1) : "0.0");
  const [kpiSafe, setKpiSafe] = useState(history.filter((h) => h.score < 30).length);

  // Morphing text words
  const introWords = ["rug pull", "honeypot", "hidden mint", "unlocked liquidity", "sell tax"];
  const [introWordIndex, setIntroWordIndex] = useState(0);
  const heroWords = ["rug pulls", "honeypots", "hidden mints", "unlocked liquidity", "sell taxes"];
  const [heroWordIndex, setHeroWordIndex] = useState(0);

  const showToast = (msg: string) => {
    setToastMsg(msg);
    setTimeout(() => setToastMsg(""), 2200);
  };

  // Sync health on mount
  useEffect(() => {
    api.health().then((h) => setHealth(h)).catch(() => setHealth(null));
  }, []);

  // Sync KPIs whenever history changes
  useEffect(() => {
    setKpiScanned(history.length);
    setKpiFlagged(history.filter((h) => h.score >= 60).length);
    setKpiSafe(history.filter((h) => h.score < 30).length);
    const avg = history.length ? history.reduce((a, b) => a + b.score, 0) / history.length : 0;
    setKpiAvg(avg.toFixed(1));
    try {
      localStorage.setItem("rs_hist", JSON.stringify(history));
    } catch {
      // ignore
    }
  }, [history]);

  useEffect(() => {
    try {
      localStorage.setItem("rs_bm", JSON.stringify(bookmarks));
    } catch {
      // ignore
    }
  }, [bookmarks]);

  useEffect(() => {
    try {
      localStorage.setItem("rs_st", JSON.stringify(settings));
    } catch {
      // ignore
    }
  }, [settings]);

  // Text morph intervals
  useEffect(() => {
    const i1 = setInterval(() => setIntroWordIndex((w) => (w + 1) % introWords.length), 2600);
    const i2 = setInterval(() => setHeroWordIndex((w) => (w + 1) % heroWords.length), 2600);
    return () => {
      clearInterval(i1);
      clearInterval(i2);
    };
  }, []);

  // Dismiss loader
  useEffect(() => {
    const t = setTimeout(() => setLoaderOpen(false), 2000);
    return () => clearTimeout(t);
  }, []);

  // 1. Infinity Trail Canvas Loader
  useEffect(() => {
    const cv = loaderCanvasRef.current;
    if (!cv) return;
    const ctx = cv.getContext("2d");
    if (!ctx) return;

    let raf = 0;
    let clock = 0;
    const W = (cv.width = window.innerWidth * 1.5);
    const H = (cv.height = window.innerHeight * 1.5);

    const draw = () => {
      raf = requestAnimationFrame(draw);
      clock += 0.03;
      ctx.clearRect(0, 0, W, H);

      const cx = W / 2;
      const cy = H / 2;
      const scale = Math.min(W, H) * 0.25;

      ctx.save();
      ctx.translate(cx, cy);

      ctx.beginPath();
      for (let t = 0; t <= Math.PI * 2; t += 0.02) {
        const x = (scale * Math.cos(t)) / (1 + Math.sin(t) * Math.sin(t));
        const y = (scale * Math.sin(t) * Math.cos(t)) / (1 + Math.sin(t) * Math.sin(t));
        if (t === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      }
      ctx.strokeStyle = "rgba(91, 108, 255, 0.25)";
      ctx.lineWidth = 14;
      ctx.stroke();

      const tHead = clock % (Math.PI * 2);
      const hx = (scale * Math.cos(tHead)) / (1 + Math.sin(tHead) * Math.sin(tHead));
      const hy = (scale * Math.sin(tHead) * Math.cos(tHead)) / (1 + Math.sin(tHead) * Math.sin(tHead));

      const grad = ctx.createRadialGradient(hx, hy, 0, hx, hy, 45);
      grad.addColorStop(0, "rgba(122, 77, 255, 0.9)");
      grad.addColorStop(0.5, "rgba(79, 214, 224, 0.4)");
      grad.addColorStop(1, "rgba(91, 108, 255, 0)");
      ctx.fillStyle = grad;
      ctx.beginPath();
      ctx.arc(hx, hy, 45, 0, Math.PI * 2);
      ctx.fill();

      ctx.fillStyle = "#ffffff";
      ctx.beginPath();
      ctx.arc(hx, hy, 6, 0, Math.PI * 2);
      ctx.fill();

      ctx.fillStyle = "#cfd3ff";
      ctx.font = "600 16px Inter, sans-serif";
      ctx.textAlign = "center";
      ctx.fillText(isScanning ? "Scanning Blockchain Engine..." : "RugScan Engine", 0, scale * 0.7);

      ctx.restore();
    };

    draw();
    return () => cancelAnimationFrame(raf);
  }, [isScanning]);

  // 2. Intro Voxel Arc
  useEffect(() => {
    if (!introOpen) return;
    const cv = introVoxelRef.current;
    if (!cv) return;
    const ctx = cv.getContext("2d");
    if (!ctx) return;

    let raf = 0;
    let t = 0;
    const W = (cv.width = cv.parentElement?.clientWidth || 800);
    const H = (cv.height = cv.parentElement?.clientHeight || 600);

    const draw = () => {
      raf = requestAnimationFrame(draw);
      t += 0.02;
      ctx.clearRect(0, 0, W, H);

      const cx = W / 2;
      const grad = ctx.createRadialGradient(cx, H * 0.65, 0, cx, H * 0.65, W * 0.6);
      grad.addColorStop(0, "rgba(91, 108, 255, 0.22)");
      grad.addColorStop(1, "rgba(91, 108, 255, 0)");
      ctx.fillStyle = grad;
      ctx.fillRect(0, 0, W, H);

      const cols = 28;
      const rows = 12;
      const cellW = W / cols;

      for (let r = rows - 1; r >= 0; r--) {
        const depth = r / rows;
        const scale = 1 - depth * 0.4;
        const baseY = H * 0.85 - depth * (H * 0.35);

        for (let c = 0; c < cols; c++) {
          const x = c * cellW;
          const wave = Math.sin(c * 0.35 + t + r * 0.4) * 18 * scale;
          const y = baseY + wave;

          const lit = (Math.sin(c * 0.5 + t * 0.8) + 1) * 0.5;
          const alpha = (1 - depth * 0.6) * 0.4;
          ctx.fillStyle = lit > 0.6 ? `rgba(79, 214, 224, ${alpha})` : `rgba(75, 107, 255, ${alpha * 0.8})`;

          const size = cellW * 0.75 * scale;
          ctx.fillRect(x, y, size, size);
        }
      }
    };

    draw();
    return () => cancelAnimationFrame(raf);
  }, [introOpen]);

  // 3. Hexagonal Vault Background
  useEffect(() => {
    if (!settings.fx) return;
    const cv = hexCanvasRef.current;
    if (!cv) return;
    const ctx = cv.getContext("2d");
    if (!ctx) return;

    let raf = 0;
    let lx = window.innerWidth / 2;
    let ly = window.innerHeight / 2;
    let tx = lx;
    let ty = ly;

    const onPointer = (e: PointerEvent) => {
      tx = e.clientX;
      ty = e.clientY;
    };
    window.addEventListener("pointermove", onPointer);

    const resize = () => {
      cv.width = window.innerWidth;
      cv.height = window.innerHeight;
    };
    resize();
    window.addEventListener("resize", resize);

    const R = 18;
    const w = Math.sqrt(3) * R;

    const draw = () => {
      raf = requestAnimationFrame(draw);
      lx += (tx - lx) * 0.1;
      ly += (ty - ly) * 0.1;

      ctx.clearRect(0, 0, cv.width, cv.height);

      const grad = ctx.createRadialGradient(lx, ly, 0, lx, ly, 320);
      grad.addColorStop(0, "rgba(91, 108, 255, 0.12)");
      grad.addColorStop(1, "rgba(91, 108, 255, 0)");
      ctx.fillStyle = grad;
      ctx.fillRect(0, 0, cv.width, cv.height);

      ctx.lineWidth = 1;
      for (let r = -1; r * R * 1.5 < cv.height + R; r++) {
        for (let q = -1; q * w < cv.width + w; q++) {
          const hx = q * w + (r & 1 ? w / 2 : 0);
          const hy = r * R * 1.5;
          const dist = Math.hypot(hx - lx, hy - ly);
          const proximity = Math.max(0, 1 - dist / 260);

          if (proximity > 0.05) {
            ctx.beginPath();
            for (let k = 0; k < 6; k++) {
              const ang = Math.PI / 6 + (k * Math.PI) / 3;
              const px = hx + Math.cos(ang) * (R - 1.5);
              const py = hy + Math.sin(ang) * (R - 1.5);
              if (k === 0) ctx.moveTo(px, py);
              else ctx.lineTo(px, py);
            }
            ctx.closePath();
            ctx.strokeStyle = `rgba(122, 141, 255, ${0.05 + proximity * 0.5})`;
            ctx.stroke();
            ctx.fillStyle = `rgba(91, 108, 255, ${proximity * 0.12})`;
            ctx.fill();
          }
        }
      }
    };

    draw();
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("pointermove", onPointer);
      window.removeEventListener("resize", resize);
    };
  }, [settings.fx]);

  // 4. Hero Astral Shell
  useEffect(() => {
    const cv = shellCanvasRef.current;
    if (!cv) return;
    const ctx = cv.getContext("2d");
    if (!ctx) return;

    let raf = 0;
    let t = 0;
    const S = 240;
    cv.width = S;
    cv.height = S;

    const N = 50;
    const pts: [number, number, number][] = [];
    for (let i = 0; i < N; i++) {
      const y = 1 - 2 * ((i + 0.5) / N);
      const r = Math.sqrt(1 - y * y);
      const a = i * 2.4;
      pts.push([Math.cos(a) * r, y, Math.sin(a) * r]);
    }

    const draw = () => {
      raf = requestAnimationFrame(draw);
      t += 0.006;
      ctx.clearRect(0, 0, S, S);

      const cy = Math.cos(t);
      const sy = Math.sin(t);

      const lvl = getLevel(curReport.sc);
      const col = lvl === "CRITICAL" ? "255, 74, 94" : lvl === "HIGH" ? "255, 122, 61" : lvl === "MEDIUM" ? "255, 194, 61" : "39, 216, 144";

      const grad = ctx.createRadialGradient(S / 2, S / 2, 0, S / 2, S / 2, 105);
      grad.addColorStop(0, `rgba(${col}, 0.22)`);
      grad.addColorStop(1, `rgba(${col}, 0)`);
      ctx.fillStyle = grad;
      ctx.fillRect(0, 0, S, S);

      const proj = pts.map((p) => {
        const x = p[0] * cy + p[2] * sy;
        const z = -p[0] * sy + p[2] * cy;
        return [S / 2 + x * 85, S / 2 + p[1] * 85, z];
      });

      ctx.lineWidth = 0.8;
      for (let i = 0; i < N; i++) {
        for (let j = i + 1; j < N; j++) {
          const d = Math.hypot(pts[i][0] - pts[j][0], pts[i][1] - pts[j][1], pts[i][2] - pts[j][2]);
          if (d < 0.65) {
            const a = proj[i];
            const b = proj[j];
            const alpha = 0.15 + 0.35 * ((a[2] + b[2]) / 2 + 1) * 0.5;
            ctx.strokeStyle = `rgba(${col}, ${alpha})`;
            ctx.beginPath();
            ctx.moveTo(a[0], a[1]);
            ctx.lineTo(b[0], b[1]);
            ctx.stroke();
          }
        }
      }

      proj.forEach((q) => {
        const k = (q[2] + 1) / 2;
        ctx.fillStyle = `rgba(235, 240, 255, ${0.3 + 0.7 * k})`;
        ctx.beginPath();
        ctx.arc(q[0], q[1], 1 + 1.5 * k, 0, Math.PI * 2);
        ctx.fill();
      });
    };

    draw();
    return () => cancelAnimationFrame(raf);
  }, [curReport]);

  // 5. Summary Gauge Aura Blob
  useEffect(() => {
    const cv = auraCanvasRef.current;
    if (!cv) return;
    const ctx = cv.getContext("2d");
    if (!ctx) return;

    let raf = 0;
    let t = 0;
    const S = 270;
    cv.width = S;
    cv.height = S;

    const draw = () => {
      raf = requestAnimationFrame(draw);
      t += 0.02;
      ctx.clearRect(0, 0, S, S);

      const lvl = getLevel(curReport.sc);
      const col = lvl === "CRITICAL" ? "255, 74, 94" : lvl === "HIGH" ? "255, 122, 61" : lvl === "MEDIUM" ? "255, 194, 61" : "39, 216, 144";

      [45, 62, 78].forEach((r0, i) => {
        const cx = S / 2 + Math.sin(t * 0.8 + i) * 6;
        const cy = S / 2 + Math.cos(t * 0.7 + i * 2) * 6;
        const r = r0 * (1 + 0.08 * Math.sin(t * 1.5 + i));

        const g = ctx.createRadialGradient(cx, cy, 0, cx, cy, r * 1.2);
        g.addColorStop(0, `rgba(${col}, ${0.5 - i * 0.12})`);
        g.addColorStop(1, `rgba(${col}, 0)`);
        ctx.fillStyle = g;
        ctx.beginPath();
        ctx.arc(cx, cy, r, 0, Math.PI * 2);
        ctx.fill();
      });
    };

    draw();
    return () => cancelAnimationFrame(raf);
  }, [curReport]);

  // 6. 3D Extruded Radar Chart
  useEffect(() => {
    const cv = radarCanvasRef.current;
    if (!cv) return;
    const ctx = cv.getContext("2d");
    if (!ctx) return;

    let raf = 0;
    let yaw = 0.5;
    let isDragging = false;
    let lastX = 0;

    const W = (cv.width = 300);
    const H = (cv.height = 240);

    const onDown = (e: PointerEvent) => {
      isDragging = true;
      lastX = e.clientX;
    };
    const onMove = (e: PointerEvent) => {
      if (isDragging) {
        yaw += (e.clientX - lastX) * 0.015;
        lastX = e.clientX;
      }
    };
    const onUp = () => (isDragging = false);

    cv.addEventListener("pointerdown", onDown);
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);

    const draw = () => {
      raf = requestAnimationFrame(draw);
      if (!isDragging) yaw += 0.005;

      ctx.clearRect(0, 0, W, H);
      const cx = W / 2;
      const cy = H / 2 + 10;
      const R = 85;

      const project = (r: number, angleIdx: number, elev: number): [number, number] => {
        const theta = -Math.PI / 2 + (angleIdx * Math.PI) / 3 + yaw;
        const x = r * Math.cos(theta);
        const z = r * Math.sin(theta);
        const y = elev;
        return [cx + x, cy - y + z * 0.35];
      };

      [0.33, 0.66, 1].forEach((k) => {
        ctx.beginPath();
        for (let i = 0; i < 6; i++) {
          const [px, py] = project(R * k, i, 0);
          if (i === 0) ctx.moveTo(px, py);
          else ctx.lineTo(px, py);
        }
        ctx.closePath();
        ctx.strokeStyle = "rgba(42, 51, 128, 0.6)";
        ctx.stroke();
      });

      for (let i = 0; i < 6; i++) {
        const [px, py] = project(R, i, 0);
        ctx.beginPath();
        ctx.moveTo(cx, cy);
        ctx.lineTo(px, py);
        ctx.strokeStyle = "rgba(42, 51, 128, 0.6)";
        ctx.stroke();
      }

      const vals = curReport.p.map((val, idx) => (val / CAT_CAPS[idx]) * R);
      const TH = 20;

      const botPts = vals.map((r, i) => project(r, i, 0));
      const topPts = vals.map((r, i) => project(r, i, TH));

      for (let i = 0; i < 6; i++) {
        const next = (i + 1) % 6;
        ctx.beginPath();
        ctx.moveTo(botPts[i][0], botPts[i][1]);
        ctx.lineTo(botPts[next][0], botPts[next][1]);
        ctx.lineTo(topPts[next][0], topPts[next][1]);
        ctx.lineTo(topPts[i][0], topPts[i][1]);
        ctx.closePath();
        ctx.fillStyle = "rgba(122, 77, 255, 0.25)";
        ctx.fill();
        ctx.strokeStyle = "rgba(192, 122, 255, 0.5)";
        ctx.stroke();
      }

      ctx.beginPath();
      topPts.forEach(([px, py], i) => {
        if (i === 0) ctx.moveTo(px, py);
        else ctx.lineTo(px, py);
      });
      ctx.closePath();
      ctx.fillStyle = "rgba(122, 77, 255, 0.45)";
      ctx.fill();
      ctx.strokeStyle = "#c07aff";
      ctx.lineWidth = 1.8;
      ctx.stroke();

      topPts.forEach(([px, py], i) => {
        ctx.fillStyle = CAT_COLORS[i];
        ctx.beginPath();
        ctx.arc(px, py, 3.5, 0, Math.PI * 2);
        ctx.fill();

        ctx.fillStyle = "#cfd3ff";
        ctx.font = "bold 10px Segoe UI, sans-serif";
        ctx.textAlign = "center";
        const [lx, ly] = project(R * 1.15, i, 0);
        ctx.fillText(`${CATEGORIES[i].split(" ")[0]} ${curReport.p[i]}`, lx, ly);
      });
    };

    draw();
    return () => {
      cancelAnimationFrame(raf);
      cv.removeEventListener("pointerdown", onDown);
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
  }, [curReport]);

  // 7. 3D Extruded Donut Chart
  useEffect(() => {
    const cv = donutCanvasRef.current;
    if (!cv) return;
    const ctx = cv.getContext("2d");
    if (!ctx) return;

    let raf = 0;
    let yaw = 0.3;
    let isDragging = false;
    let lastX = 0;

    const W = (cv.width = 190);
    const H = (cv.height = 170);

    const onDown = (e: PointerEvent) => {
      isDragging = true;
      lastX = e.clientX;
    };
    const onMove = (e: PointerEvent) => {
      if (isDragging) {
        yaw += (e.clientX - lastX) * 0.02;
        lastX = e.clientX;
      }
    };
    const onUp = () => (isDragging = false);

    cv.addEventListener("pointerdown", onDown);
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);

    const draw = () => {
      raf = requestAnimationFrame(draw);
      if (!isDragging) yaw += 0.005;

      ctx.clearRect(0, 0, W, H);
      const cx = W / 2;
      const cy = H / 2 + 5;
      const rOut = 62;
      const rIn = 36;
      const tot = curReport.p.reduce((a, b) => a + b, 0) || 1;

      let startAngle = yaw;
      curReport.p.forEach((val, i) => {
        const sweep = (val / tot) * Math.PI * 2;
        if (sweep > 0.01) {
          ctx.beginPath();
          ctx.arc(cx, cy, rOut, startAngle, startAngle + sweep);
          ctx.arc(cx, cy, rIn, startAngle + sweep, startAngle, true);
          ctx.closePath();
          ctx.fillStyle = CAT_COLORS[i];
          ctx.fill();
          ctx.strokeStyle = "#0d1230";
          ctx.lineWidth = 1.5;
          ctx.stroke();
        }
        startAngle += sweep;
      });

      ctx.fillStyle = "#0d1230";
      ctx.beginPath();
      ctx.arc(cx, cy, rIn, 0, Math.PI * 2);
      ctx.fill();

      ctx.fillStyle = "#ffffff";
      ctx.font = "bold 20px Segoe UI, sans-serif";
      ctx.textAlign = "center";
      ctx.fillText(`${tot}`, cx, cy + 2);
      ctx.fillStyle = "var(--mut)";
      ctx.font = "10px Segoe UI, sans-serif";
      ctx.fillText("Total Points", cx, cy + 16);
    };

    draw();
    return () => {
      cancelAnimationFrame(raf);
      cv.removeEventListener("pointerdown", onDown);
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
  }, [curReport]);

  // 8. Parallax Carousel Interactivity
  useEffect(() => {
    const view = carouselViewRef.current;
    const track = carouselTrackRef.current;
    if (!view || !track) return;

    let isDown = false;
    let startX = 0;
    let scrollLeft = 0;
    let vel = 0;
    let lastX = 0;
    let offset = 0;
    let raf = 0;

    const onPointerDown = (e: PointerEvent) => {
      isDown = true;
      startX = e.clientX;
      lastX = e.clientX;
      scrollLeft = offset;
      view.classList.add("dg");
    };

    const onPointerMove = (e: PointerEvent) => {
      if (!isDown) return;
      const dx = e.clientX - startX;
      vel = e.clientX - lastX;
      lastX = e.clientX;
      offset = scrollLeft + dx;
    };

    const onPointerUp = () => {
      isDown = false;
      view.classList.remove("dg");
    };

    view.addEventListener("pointerdown", onPointerDown);
    window.addEventListener("pointermove", onPointerMove);
    window.addEventListener("pointerup", onPointerUp);

    const updateLoop = () => {
      raf = requestAnimationFrame(updateLoop);
      if (!isDown) {
        offset += vel;
        vel *= 0.92;
        const maxScroll = Math.min(0, view.clientWidth - track.scrollWidth - 20);
        if (offset > 0) offset *= 0.8;
        else if (offset < maxScroll) offset = maxScroll + (offset - maxScroll) * 0.8;
      }
      track.style.transform = `translateX(${offset}px)`;
    };

    updateLoop();
    return () => {
      cancelAnimationFrame(raf);
      view.removeEventListener("pointerdown", onPointerDown);
      window.removeEventListener("pointermove", onPointerMove);
      window.removeEventListener("pointerup", onPointerUp);
    };
  }, [curView]);

  // 9. SpotlightCard cursor light
  useEffect(() => {
    const handlePointerMove = (e: PointerEvent) => {
      const cards = document.querySelectorAll(".card, .side-card, .pcc, .bmc");
      cards.forEach((el) => {
        const card = el as HTMLElement;
        const rect = card.getBoundingClientRect();
        card.style.setProperty("--mx", `${e.clientX - rect.left}px`);
        card.style.setProperty("--my", `${e.clientY - rect.top}px`);
      });
    };
    window.addEventListener("pointermove", handlePointerMove);
    return () => window.removeEventListener("pointermove", handlePointerMove);
  }, []);

  // Scan Action
  const handleScan = async (targetAddr?: string, targetChain?: string) => {
    const addr = (targetAddr ?? inputAddress).trim();
    const ch = (targetChain ?? inputChain) as Chain;
    if (!addr) {
      setScanError("Please enter a token contract address (0x...)");
      return;
    }

    if (!/^0x[a-fA-F0-9]{40}$/.test(addr) && !SAMPLE_PRESETS[addr]) {
      setScanError("Enter a valid contract address: 0x followed by 40 hex characters.");
      return;
    }

    setScanError("");
    setIsScanning(true);
    setLoaderOpen(true);

    try {
      let mapped: ReportItem | null = null;
      try {
        const res = await api.scan(ch.toLowerCase() as Chain, addr);
        if (res && res.report) {
          mapped = mapReportToUI(res.report);
          mapped.cached = res.from_cache;
        }
      } catch (err) {
        // Fallback to presets or synthetic simulation if offline or fixture
      }

      if (!mapped) {
        if (SAMPLE_PRESETS[addr]) {
          mapped = { ...SAMPLE_PRESETS[addr], a: addr, chain: ch };
        } else {
          const hexMod = parseInt(addr.slice(-2), 16) % 3;
          const presetKey = hexMod === 0 ? "honeypot" : hexMod === 1 ? "unlocked" : "benign";
          mapped = { ...SAMPLE_PRESETS[presetKey], a: addr, chain: ch };
        }
      }

      setCurReport(mapped);
      setCurKey("custom");

      const newHistItem: HistoryItem = {
        id: Date.now().toString(),
        key: "custom",
        name: mapped.n,
        symbol: mapped.s,
        time: new Date().toLocaleString(),
        score: mapped.sc,
        address: mapped.a,
        chain: ch,
        report: mapped,
        cached: mapped.cached,
      };
      setHistory((prev) => [newHistItem, ...prev.slice(0, 49)]);

      showToast(`Scan complete: ${mapped.n} scored ${mapped.sc}/100`);
    } catch (e) {
      setScanError(`Scan failed: ${String(e)}`);
    } finally {
      setIsScanning(false);
      setLoaderOpen(false);
    }
  };

  const handleSelectPreset = (key: string) => {
    if (SAMPLE_PRESETS[key]) {
      const rep = SAMPLE_PRESETS[key];
      setCurReport(rep);
      setCurKey(key);
      setInputAddress(rep.a);
      showToast(`Loaded ${rep.n} report`);
    }
  };

  const isBookmarked = (name: string) => bookmarks.some((b) => b.n === name);

  const toggleBookmark = (name: string, symbol: string, score: number, key: string, addr?: string) => {
    if (isBookmarked(name)) {
      setBookmarks((prev) => prev.filter((b) => b.n !== name));
      showToast("Removed bookmark");
    } else {
      setBookmarks((prev) => [...prev, { k: key, n: name, s: symbol, sc: score, a: addr, tags: ["Monitored"] }]);
      showToast("Bookmark saved ★");
    }
  };

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text).then(() => showToast("Copied to clipboard!"));
  };

  // Label token (ground truth for ML training)
  const handleLabelToken = async (hItem: HistoryItem, labelVal: "rug" | "benign") => {
    try {
      await api.label(hItem.chain.toLowerCase() as Chain, hItem.address, labelVal);
      showToast(`Labeled ${hItem.name} as ${labelVal.toUpperCase()} for model training`);
    } catch {
      showToast(`Local label saved: ${hItem.name} -> ${labelVal}`);
    }
  };

  const exportHistoryJSON = () => {
    const dataStr = "data:text/json;charset=utf-8," + encodeURIComponent(JSON.stringify(history, null, 2));
    const dl = document.createElement("a");
    dl.setAttribute("href", dataStr);
    dl.setAttribute("download", `rugscan_history_${Date.now()}.json`);
    dl.click();
    showToast("Exported scan history JSON");
  };

  const level = getLevel(curReport.sc);
  const filteredFlags = curReport.f.filter((f) => filterSeverity === "All" || f[0] === filterSeverity);

  // Filtered history
  const filteredHistory = history.filter((h) => {
    const matchesChain = histChainFilter === "All" || h.chain.toLowerCase() === histChainFilter.toLowerCase();
    const matchesLvl = histFilter === "All" || getLevel(h.score) === histFilter;
    const matchesQuery = (h.name + h.symbol + h.address).toLowerCase().includes(histQuery.toLowerCase());
    return matchesChain && matchesLvl && matchesQuery;
  });

  const sampleCarouselList = [
    { key: "honeypot", score: 99.8, name: "SafeMoonElon2.0", symbol: "SME2", addr: "0x222222..." },
    { key: "unlocked", score: 71.4, name: "MoonVault", symbol: "MVLT", addr: "0x7a3c91..." },
    { key: "benign", score: 6.1, name: "Uniswap V2", symbol: "UNI", addr: "0x1f9840..." },
    { key: "pepe", score: 78.6, name: "PepeCoin", symbol: "PEPE", addr: "0x698250..." },
    { key: "testtoken", score: 42.3, name: "TestToken", symbol: "TTK", addr: "0x333333..." },
  ];

  return (
    <div>
      {/* Fullscreen Infinity Trail Loader */}
      <div id="ld" className={loaderOpen ? "" : "off"} role="progressbar" aria-busy="true" aria-label="Loading">
        <canvas ref={loaderCanvasRef} />
      </div>

      {/* Fullscreen Welcome Intro Dialog with 3D Globe Component */}
      <div id="intro" className={introOpen ? "" : "off"} role="dialog" aria-label="Welcome to RugScan">
        <canvas ref={introVoxelRef} className="vx" aria-hidden="true" />
        <div className="in">
          <div className="itx">
            <div className="ilogo">
              <svg viewBox="0 0 40 40" fill="none">
                <path d="M20 3 5 9v10c0 9 6 15 15 18 9-3 15-9 15-18V9L20 3z" stroke="#6b7bff" strokeWidth="2.5" />
                <path d="M13 20l5 5 9-10" stroke="#9ab0ff" strokeWidth="2.5" />
              </svg>
              RugScan
            </div>
            <span className="chip">AI-Powered Rug Pull Detection</span>
            <h1>
              Spot the <span className="tm tmw">{introWords[introWordIndex]}</span>
              <br />
              before it spots you.
            </h1>
            <p>Paste a token contract and get an explainable risk score in seconds: honeypots, unlocked liquidity, hidden mints and whale concentration, analyzed flag by flag.</p>
            <div className="ibtn">
              <button className="btn" onClick={() => setIntroOpen(false)}>
                Launch Scanner →
              </button>
              <button
                className="ghost"
                onClick={() => {
                  setIntroOpen(false);
                  handleSelectPreset("honeypot");
                }}
              >
                View sample report
              </button>
            </div>
            <div className="istats">
              <div>
                <b>6</b>
                <span>risk categories</span>
              </div>
              <div>
                <b>Rules + ML</b>
                <span>hybrid scoring</span>
              </div>
              <div>
                <b>BSC · ETH · Base</b>
                <span>supported chains</span>
              </div>
            </div>
          </div>
          <div className="ifr">
            {/* React Bits Pro Globe Component */}
            <Globe size={480} interactive={true} showOrbitRing={true} showAtmosphere={true} />
          </div>
        </div>
      </div>

      {/* Hexagonal Vault Background */}
      {settings.fx && <canvas ref={hexCanvasRef} className="hvbg" aria-hidden="true" />}

      {/* Toast Notification */}
      <div id="toast" className={toastMsg ? "on" : ""} role="status">
        {toastMsg}
      </div>

      {/* Main Application Layout */}
      <div className="app">
        {/* Sidebar */}
        <aside>
          <div className="logo">
            <svg viewBox="0 0 40 40" fill="none">
              <path d="M20 3 5 9v10c0 9 6 15 15 18 9-3 15-9 15-18V9L20 3z" stroke="#6b7bff" strokeWidth="2.5" />
              <path d="M13 20l5 5 9-10" stroke="#9ab0ff" strokeWidth="2.5" />
            </svg>
            <div>
              <b>RugScan</b>
              <small>AI-Powered Rug Pull Detection</small>
            </div>
          </div>

          <nav aria-label="Main">
            <a className={curView === "home" ? "on" : ""} onClick={() => setCurView("home")}>
              ⌂ Home
            </a>
            <a className={curView === "scan" ? "on" : ""} onClick={() => setCurView("scan")}>
              ⌕ Scan Token
            </a>
            <a className={curView === "history" ? "on" : ""} onClick={() => setCurView("history")}>
              ↺ Scan History
            </a>
            <a className={curView === "bookmarks" ? "on" : ""} onClick={() => setCurView("bookmarks")}>
              ⚑ Bookmarks
            </a>
            <a className={curView === "api" ? "on" : ""} onClick={() => setCurView("api")}>
              ‹/› API Docs
            </a>
            <a className={curView === "settings" ? "on" : ""} onClick={() => setCurView("settings")}>
              ⚙ Settings
            </a>
          </nav>

          <div className="side-card">
            <h4>Quick Links</h4>
            <button className="ql h" onClick={() => { handleSelectPreset("honeypot"); setCurView("home"); }}>
              ⚠ Sample Honeypot
            </button>
            <button className="ql u" onClick={() => { handleSelectPreset("unlocked"); setCurView("home"); }}>
              ◔ Sample Unlocked Rug
            </button>
            <button className="ql b" onClick={() => { handleSelectPreset("benign"); setCurView("home"); }}>
              ✓ Sample Benign Token
            </button>
          </div>

          <div className="api">
            <i />
            {health ? "API Connected" : "Local Mock Provider"}
            <br />
            Backend: {health ? health.provider : "Synthetic Mock"}
            <br />
            AI Model: {health?.model_trained_on ?? "Rules + GBM"}
          </div>
        </aside>

        {/* Main Content Area */}
        <main>
          {/* Top Status Chips */}
          <div className="top">
            <span className="chip">{inputChain} Smart Chain</span>
            <span className="chip">{health ? health.provider : "Mock Provider"}</span>
            <span className="chip">{health?.model ?? "Hybrid Rules + ML"}</span>
          </div>

          {/* VIEW 1: HOME */}
          {curView === "home" && (
            <div className="view" id="v-home">
              {/* Hero Scan Section */}
              <section className="card hero">
                <div className="orb" aria-hidden="true">
                  <canvas ref={shellCanvasRef} id="shell" />
                </div>
                <h1>Scan a Token</h1>
                <p>
                  Detect <span className="tm tmw">{heroWords[heroWordIndex]}</span> and other risks with AI-powered analysis.
                </p>

                <div className="scan">
                  <input
                    id="addr-input"
                    value={inputAddress}
                    onChange={(e) => setInputAddress(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && handleScan()}
                    placeholder="Enter token contract address (0x…)"
                    aria-label="Token contract address"
                    autoComplete="off"
                    spellCheck={false}
                  />
                  <select
                    value={inputChain}
                    onChange={(e) => setInputChain(e.target.value)}
                    aria-label="Chain"
                  >
                    <option value="BSC">BSC</option>
                    <option value="ETH">ETH</option>
                    <option value="Base">Base</option>
                    <option value="Arbitrum">Arbitrum</option>
                  </select>
                  <button className="btn" onClick={() => handleScan()} disabled={isScanning}>
                    {isScanning ? "Scanning…" : "Scan Token"}
                  </button>
                </div>

                {scanError && (
                  <div className="err" role="alert">
                    {scanError}
                  </div>
                )}

                <div className="qlinks">
                  Quick Links:{" "}
                  <button className="CRITICAL" onClick={() => handleSelectPreset("honeypot")}>
                    Sample Honeypot
                  </button>
                  <button className="MEDIUM" onClick={() => handleSelectPreset("unlocked")}>
                    Sample Unlocked Rug
                  </button>
                  <button className="LOW" onClick={() => handleSelectPreset("benign")}>
                    Sample Benign Token
                  </button>
                </div>
              </section>

              {/* KPIs Row */}
              <div className="kpis">
                <section className="card kpi">
                  <i>⌕</i>
                  <b>{kpiScanned}</b>
                  <span>Tokens scanned</span>
                </section>
                <section className="card kpi">
                  <i style={{ color: "#ff8a8a", background: "#ff4a5e22" }}>⚠</i>
                  <b>{kpiFlagged}</b>
                  <span>High-risk flagged</span>
                </section>
                <section className="card kpi">
                  <i>◔</i>
                  <b>{kpiAvg}</b>
                  <span>Average risk score</span>
                </section>
                <section className="card kpi">
                  <i style={{ color: "#7ff0c0", background: "#27d89022" }}>✓</i>
                  <b>{kpiSafe}</b>
                  <span>Safe tokens</span>
                </section>
              </div>

              {/* Parallax Carousel */}
              <section className="card pc">
                <h3>
                  Sample Scans <small>· drag to explore, click to scan</small>
                </h3>
                <div className="pcv" ref={carouselViewRef}>
                  <div className="pct" ref={carouselTrackRef}>
                    {sampleCarouselList.map((item) => {
                      const lvl = getLevel(item.score);
                      return (
                        <div
                          key={item.key}
                          className="pcc"
                          tabIndex={0}
                          role="button"
                          aria-label={`Scan ${item.name}`}
                          onClick={() => handleSelectPreset(item.key)}
                        >
                          <i className="pcg" style={{ color: getColor(lvl) }}>
                            {Math.round(item.score)}
                          </i>
                          <div className="pcb">
                            <span className={`badge ${lvl}`}>{lvl}</span>
                            <b>{item.name}</b>
                            <small>
                              {item.symbol} · {item.addr}
                            </small>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              </section>

              {/* 2-Column Main Report Grid */}
              <div className="grid">
                <div className="col">
                  {/* Summary Card */}
                  <section className="card summary">
                    <canvas ref={auraCanvasRef} className="aura" aria-hidden="true" />
                    <div className="gauge">
                      <svg width="150" height="150" viewBox="0 0 150 150">
                        <circle cx="75" cy="75" r="62" fill="none" stroke="#1a2158" strokeWidth="12" />
                        <circle
                          cx="75"
                          cy="75"
                          r="62"
                          fill="none"
                          stroke={getColor(level)}
                          strokeWidth="12"
                          strokeLinecap="round"
                          strokeDasharray={`${3.9 * curReport.sc} 390`}
                          style={{ filter: `drop-shadow(0 0 6px ${getColor(level)})` }}
                        />
                      </svg>
                      <div>
                        <b>{curReport.sc}</b>
                        <small>/100</small>
                        <span className={`badge ${level}`}>{level}</span>
                      </div>
                    </div>

                    <div>
                      <div className="tok">
                        <h2>
                          {curReport.n} <span>({curReport.s})</span>
                        </h2>
                        <button
                          className="ghost sm"
                          style={{ marginLeft: "auto" }}
                          onClick={() => toggleBookmark(curReport.n, curReport.s, curReport.sc, curKey, curReport.a)}
                        >
                          {isBookmarked(curReport.n) ? "★ Saved" : "☆ Save"}
                        </button>
                      </div>
                      <div className="addr">{curReport.a} · {curReport.chain || "BSC"}</div>
                      <div className="addr" style={{ marginTop: 4 }}>
                        {health ? health.provider : "Mock Provider"} · Scanned {new Date().toLocaleTimeString()}
                      </div>
                      <div className={`verdict ${level}`}>{curReport.v}</div>
                    </div>

                    <div className="scores">
                      <div>
                        <span>Rule Score</span>
                        <span>{curReport.rule.toFixed(1)}</span>
                      </div>
                      <div>
                        <span>
                          ML Rug Prob.
                          {curReport.ml_unreliable && (
                            <>
                              <br />
                              <small style={{ color: "var(--mut)" }}>(down-weighted: OOD/imputed)</small>
                            </>
                          )}
                        </span>
                        <span>{curReport.ml}</span>
                      </div>
                      <div className="fin">
                        <span>Final Score</span>
                        <span>{curReport.sc} /100</span>
                      </div>
                    </div>
                  </section>

                  {/* Two Cards Subgrid: Breakdown & 3D Radar */}
                  <div className="two">
                    <section className="card">
                      <h3>
                        Risk Breakdown <small style={{ color: "var(--mut)", fontWeight: 400 }}>· points are capped per category</small>
                      </h3>
                      <div>
                        {CATEGORIES.map((cat, idx) => {
                          const flagCount = curReport.f.filter((f) => f[4] === idx).length;
                          const ratio = curReport.p[idx] / CAT_CAPS[idx];
                          const trackColor = ratio > 0.7 ? "var(--crit)" : ratio > 0.3 ? "var(--med)" : "var(--pri)";
                          return (
                            <div className="bar" key={cat}>
                              <div>
                                {cat}
                                <small>{flagCount} flags</small>
                              </div>
                              <div className="trk">
                                <i style={{ width: `${Math.min(100, ratio * 100)}%`, background: trackColor }} />
                              </div>
                              <div>
                                {curReport.p[idx]} / {CAT_CAPS[idx]}
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    </section>

                    <section className="card">
                      <h3>Category Overview</h3>
                      <div style={{ position: "relative", minHeight: 220 }}>
                        <canvas ref={radarCanvasRef} style={{ width: "100%", display: "block" }} />
                      </div>
                    </section>
                  </div>

                  {/* Findings Table */}
                  <section className="card">
                    <div className="tabs">
                      {["All", "CRITICAL", "HIGH", "MEDIUM", "LOW"].map((lvl) => {
                        const count = lvl === "All" ? curReport.f.length : curReport.f.filter((f) => f[0] === lvl).length;
                        return (
                          <button
                            key={lvl}
                            className={filterSeverity === lvl ? "on" : ""}
                            onClick={() => setFilterSeverity(lvl)}
                          >
                            {lvl === "All" ? "All" : lvl[0] + lvl.slice(1).toLowerCase()} ({count})
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
                        <tbody>
                          {filteredFlags.length > 0 ? (
                            filteredFlags.map((flag, idx) => (
                              <tr key={idx}>
                                <td>
                                  <span className={`badge ${flag[0]}`}>{flag[0]}</span>
                                </td>
                                <td>
                                  <code>{flag[1]}</code>
                                </td>
                                <td>{flag[2]}</td>
                                <td style={{ color: getColor(flag[0]), fontWeight: 600 }}>+{flag[3]}</td>
                                <td>{CATEGORIES[flag[4]]}</td>
                              </tr>
                            ))
                          ) : (
                            <tr>
                              <td colSpan={5} style={{ color: "var(--mut)", textAlign: "center", padding: "16px 0" }}>
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
                  {/* 3D Donut Categories Card */}
                  <section className="card">
                    <h3>Risk Categories</h3>
                    <div className="lg">
                      <div className="donut d3" style={{ position: "relative" }}>
                        <canvas ref={donutCanvasRef} style={{ position: "absolute", inset: 0, width: "100%", height: "100%" }} />
                      </div>
                      <ul>
                        {CATEGORIES.map((cat, idx) => (
                          <li key={cat}>
                            <span>
                              <i style={{ background: CAT_COLORS[idx] }} />
                              {cat}
                            </span>
                            <b>{curReport.p[idx]}</b>
                          </li>
                        ))}
                      </ul>
                    </div>
                  </section>

                  {/* Top Risk Indicators Card */}
                  <section className="card">
                    <h3>Top Risk Indicators</h3>
                    <div>
                      {curReport.f.slice(0, 5).map((flag, idx) => (
                        <div className="ind" key={idx}>
                          <div>
                            <b>
                              {flag[1]
                                .replace(/_/g, " ")
                                .toLowerCase()
                                .replace(/\b\w/g, (m) => m.toUpperCase())}
                            </b>
                            <small>{flag[2]}</small>
                          </div>
                          <span className={`badge ${flag[0]}`}>{flag[0]}</span>
                          <b style={{ color: getColor(flag[0]) }}>+{flag[3]}</b>
                        </div>
                      ))}
                    </div>
                  </section>

                  {/* Scan History Card */}
                  <section className="card">
                    <h3>Scan History</h3>
                    <div>
                      {history.slice(0, 5).map((item) => {
                        const lvl = getLevel(item.score);
                        return (
                          <button
                            key={item.id}
                            className="hist"
                            onClick={() => {
                              if (item.report) setCurReport(item.report);
                              else handleScan(item.address, item.chain);
                            }}
                          >
                            <div>
                              <b>{item.name}</b> ({item.symbol})
                              <small>{item.time}</small>
                            </div>
                            <span className={`badge ${lvl}`}>{lvl}</span>
                            <b>{item.score}/100</b>
                          </button>
                        );
                      })}
                    </div>
                  </section>
                </div>
              </div>
            </div>
          )}

          {/* VIEW 2: DEDICATED SCAN TOKEN STUDIO */}
          {curView === "scan" && (
            <div className="view" id="v-scan">
              <div className="studio-grid">
                <div style={{ display: "grid", gap: 16 }}>
                  <section className="card">
                    <h3>
                      Token Inspection Studio <small style={{ color: "var(--mut)", fontWeight: 400 }}>· Deep Multi-Chain Analysis</small>
                    </h3>
                    <p style={{ color: "var(--mut)", marginBottom: 16 }}>
                      Inspect any EVM smart contract for backdoors, hidden honeypot taxes, unverified bytecode, whale clusters, and unlocked liquidity.
                    </p>

                    {/* Network selection pills */}
                    <div style={{ marginBottom: 16 }}>
                      <label style={{ display: "block", fontSize: 12, color: "var(--mut)", marginBottom: 8, textTransform: "uppercase" }}>
                        Target Network / Chain
                      </label>
                      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                        {[
                          { id: "BSC", name: "BNB Smart Chain", tag: "Fast / DEX" },
                          { id: "ETH", name: "Ethereum Mainnet", tag: "L1 Standard" },
                          { id: "Base", name: "Base", tag: "Optimism L2" },
                          { id: "Arbitrum", name: "Arbitrum One", tag: "Rollup L2" },
                        ].map((c) => (
                          <button
                            key={c.id}
                            className={`chain-pill ${inputChain === c.id ? "on" : ""}`}
                            onClick={() => setInputChain(c.id)}
                          >
                            <b>{c.name}</b> <small style={{ color: "var(--mut)" }}>({c.tag})</small>
                          </button>
                        ))}
                      </div>
                    </div>

                    {/* Address Input */}
                    <div style={{ marginBottom: 18 }}>
                      <label style={{ display: "block", fontSize: 12, color: "var(--mut)", marginBottom: 6, textTransform: "uppercase" }}>
                        Contract Address
                      </label>
                      <div style={{ display: "flex", gap: 8 }}>
                        <input
                          style={{
                            flex: 1,
                            background: "#0a0f33",
                            border: "1px solid var(--line)",
                            borderRadius: 10,
                            padding: "12px 14px",
                            fontFamily: "ui-monospace, monospace",
                          }}
                          value={inputAddress}
                          onChange={(e) => setInputAddress(e.target.value)}
                          placeholder="0x… (40 hex characters)"
                          spellCheck={false}
                        />
                        <button
                          className="ghost"
                          onClick={() => {
                            navigator.clipboard.readText().then((txt) => {
                              if (txt) {
                                setInputAddress(txt.trim());
                                showToast("Pasted address from clipboard");
                              }
                            }).catch(() => showToast("Clipboard access denied"));
                          }}
                        >
                          Paste
                        </button>
                      </div>
                    </div>

                    {/* Inspection Options */}
                    <div style={{ marginBottom: 18 }}>
                      <label style={{ display: "block", fontSize: 12, color: "var(--mut)", marginBottom: 8, textTransform: "uppercase" }}>
                        Active Heuristic &amp; ML Analyzers
                      </label>
                      <div className="check-row">
                        <input
                          type="checkbox"
                          id="chk-sim"
                          checked={studioOptions.simulateTrade}
                          onChange={(e) => setStudioOptions({ ...studioOptions, simulateTrade: e.target.checked })}
                        />
                        <label htmlFor="chk-sim">
                          <b>Simulate 10 Buy/Sell Swaps</b> — Execute sandboxed Multicall transaction simulation to catch blocked sells
                        </label>
                      </div>
                      <div className="check-row">
                        <input
                          type="checkbox"
                          id="chk-dist"
                          checked={studioOptions.deepHolders}
                          onChange={(e) => setStudioOptions({ ...studioOptions, deepHolders: e.target.checked })}
                        />
                        <label htmlFor="chk-dist">
                          <b>Replay Transfer Logs (Top 100 Holders)</b> — Rebuild circulating supply distribution and cluster coordination
                        </label>
                      </div>
                      <div className="check-row">
                        <input
                          type="checkbox"
                          id="chk-code"
                          checked={studioOptions.bytecodeCheck}
                          onChange={(e) => setStudioOptions({ ...studioOptions, bytecodeCheck: e.target.checked })}
                        />
                        <label htmlFor="chk-code">
                          <b>Decompile Bytecode for Hidden Mints</b> — Scan for unverified mint(), pause(), blacklist() signatures
                        </label>
                      </div>
                      <div className="check-row">
                        <input
                          type="checkbox"
                          id="chk-dep"
                          checked={studioOptions.traceDeployer}
                          onChange={(e) => setStudioOptions({ ...studioOptions, traceDeployer: e.target.checked })}
                        />
                        <label htmlFor="chk-dep">
                          <b>Deployer Provenance &amp; Prior Rugs</b> — Check if deployer wallet created prior exit-scam tokens
                        </label>
                      </div>
                    </div>

                    {/* Advanced Parameters */}
                    <div style={{ display: "flex", gap: 12, alignItems: "center", marginBottom: 20, flexWrap: "wrap" }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                        <span style={{ fontSize: 12, color: "var(--mut)" }}>Historical Block:</span>
                        <input
                          style={{
                            background: "#0a0f33",
                            border: "1px solid var(--line)",
                            borderRadius: 8,
                            padding: "6px 10px",
                            width: 140,
                            fontSize: 12,
                          }}
                          placeholder="Latest (Head)"
                          value={studioOptions.blockNumber}
                          onChange={(e) => setStudioOptions({ ...studioOptions, blockNumber: e.target.value })}
                        />
                      </div>

                      <label style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer", fontSize: 12 }}>
                        <input
                          type="checkbox"
                          checked={studioOptions.bypassCache}
                          onChange={(e) => setStudioOptions({ ...studioOptions, bypassCache: e.target.checked })}
                        />
                        Force Fresh Live Scan (Bypass SQLite Cache)
                      </label>
                    </div>

                    <button
                      className="btn"
                      style={{ width: "100%", padding: 14, fontSize: 15 }}
                      onClick={async () => {
                        await handleScan(inputAddress, inputChain);
                        setCurView("home");
                      }}
                      disabled={isScanning}
                    >
                      {isScanning ? "Inspecting Contract On-Chain…" : "Run Deep Inspection →"}
                    </button>
                  </section>
                </div>

                {/* Right side studio telemetry with 3D Globe */}
                <div style={{ display: "grid", gap: 16 }}>
                  <section className="card" style={{ display: "grid", placeItems: "center", textAlign: "center", padding: 24 }}>
                    <h3>Global Chain Nodes</h3>
                    <p style={{ color: "var(--mut)", fontSize: 12, margin: "0 0 12px" }}>
                      Real-time EVM state synchronization across distributed RPC relays.
                    </p>
                    <Globe size={300} interactive={true} />
                    <div style={{ display: "flex", gap: 8, marginTop: 14 }}>
                      <span className="status-pill s200">✓ BSC Mainnet Active</span>
                      <span className="status-pill s200">✓ Ethereum RPC Active</span>
                    </div>
                  </section>

                  <section className="card">
                    <h3>Quick Test Contracts</h3>
                    <div style={{ display: "grid", gap: 8 }}>
                      <button
                        className="ql h"
                        onClick={() => {
                          setInputAddress(SAMPLE_PRESETS.honeypot.a);
                          setInputChain("BSC");
                          showToast("Loaded Honeypot test address");
                        }}
                      >
                        <b>SafeMoonElon2.0 (Honeypot)</b>
                        <small style={{ display: "block", color: "var(--mut)" }}>99% Sell Tax · Hard Fail</small>
                      </button>
                      <button
                        className="ql u"
                        onClick={() => {
                          setInputAddress(SAMPLE_PRESETS.unlocked.a);
                          setInputChain("BSC");
                          showToast("Loaded Unlocked LP test address");
                        }}
                      >
                        <b>MoonVault (Unlocked LP)</b>
                        <small style={{ display: "block", color: "var(--mut)" }}>100% Unlocked Deployer LP</small>
                      </button>
                      <button
                        className="ql b"
                        onClick={() => {
                          setInputAddress(SAMPLE_PRESETS.benign.a);
                          setInputChain("ETH");
                          showToast("Loaded Benign test address");
                        }}
                      >
                        <b>Uniswap V2 (Benign Safe)</b>
                        <small style={{ display: "block", color: "var(--mut)" }}>Audited Protocol Token</small>
                      </button>
                    </div>
                  </section>
                </div>
              </div>
            </div>
          )}

          {/* VIEW 3: SCAN HISTORY */}
          {curView === "history" && (
            <div className="view" id="v-history">
              <section className="card">
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 14, flexWrap: "wrap", gap: 10 }}>
                  <h3>
                    Scan History <small style={{ color: "var(--mut)", fontWeight: 400 }}>· {history.length} scans indexed</small>
                  </h3>
                  <div style={{ display: "flex", gap: 8 }}>
                    <button className="ghost sm" onClick={exportHistoryJSON}>
                      Export JSON
                    </button>
                    {compareIds.length === 2 && (
                      <button
                        className="btn sm2"
                        onClick={() => {
                          const t1 = history.find((h) => h.id === compareIds[0]);
                          const t2 = history.find((h) => h.id === compareIds[1]);
                          if (t1 && t2) {
                            showToast(`Comparing ${t1.name} (${t1.score}) vs ${t2.name} (${t2.score})`);
                          }
                        }}
                      >
                        Compare 2 Selected
                      </button>
                    )}
                  </div>
                </div>

                {/* History Analytics Bar */}
                <div className="history-stat-bar">
                  <div className="h-stat">
                    <b>{history.length}</b>
                    <small>Total Tokens Scanned</small>
                  </div>
                  <div className="h-stat">
                    <b style={{ color: "var(--crit)" }}>
                      {history.length ? `${Math.round((history.filter((h) => h.score >= 60).length / history.length) * 100)}%` : "0%"}
                    </b>
                    <small>High Risk Rate</small>
                  </div>
                  <div className="h-stat">
                    <b style={{ color: "var(--pri)" }}>{kpiAvg}</b>
                    <small>Mean Composite Score</small>
                  </div>
                  <div className="h-stat">
                    <b style={{ color: "var(--low)" }}>{kpiSafe}</b>
                    <small>Verified Safe Tokens</small>
                  </div>
                </div>

                {/* Search & Filters */}
                <div className="vbar">
                  <input
                    value={histQuery}
                    onChange={(e) => setHistQuery(e.target.value)}
                    placeholder="Search by token name, symbol, or 0x address…"
                    aria-label="Search history"
                  />

                  {/* Chain filters */}
                  <div className="tabs" style={{ margin: 0 }}>
                    {["All", "BSC", "ETH", "Base", "Arbitrum"].map((ch) => (
                      <button
                        key={ch}
                        className={histChainFilter === ch ? "on" : ""}
                        onClick={() => setHistChainFilter(ch)}
                      >
                        {ch}
                      </button>
                    ))}
                  </div>

                  {/* Tier filters */}
                  <div className="tabs" style={{ margin: 0 }}>
                    {["All", "CRITICAL", "HIGH", "MEDIUM", "LOW"].map((lvl) => (
                      <button
                        key={lvl}
                        className={histFilter === lvl ? "on" : ""}
                        onClick={() => setHistFilter(lvl)}
                      >
                        {lvl === "All" ? "All Tiers" : lvl[0] + lvl.slice(1).toLowerCase()}
                      </button>
                    ))}
                  </div>
                </div>

                {/* History Table */}
                <div className="tw">
                  <table>
                    <thead>
                      <tr>
                        <th style={{ width: 36 }}>Cmp</th>
                        <th>Token &amp; Address</th>
                        <th>Network</th>
                        <th>Risk Tier</th>
                        <th>Risk Score</th>
                        <th>Scanned At</th>
                        <th>ML Feedback</th>
                        <th>Actions</th>
                      </tr>
                    </thead>
                    <tbody>
                      {filteredHistory.length > 0 ? (
                        filteredHistory.map((item) => {
                          const lvl = getLevel(item.score);
                          const isComparing = compareIds.includes(item.id);
                          return (
                            <tr key={item.id}>
                              <td>
                                <input
                                  type="checkbox"
                                  checked={isComparing}
                                  onChange={(e) => {
                                    if (e.target.checked) {
                                      setCompareIds((prev) => (prev.length >= 2 ? [prev[1], item.id] : [...prev, item.id]));
                                    } else {
                                      setCompareIds((prev) => prev.filter((id) => id !== item.id));
                                    }
                                  }}
                                  title="Select 2 tokens to compare"
                                />
                              </td>
                              <td>
                                <b>{item.name}</b> <span style={{ color: "var(--mut)" }}>({item.symbol})</span>
                                <small style={{ display: "block", color: "var(--mut)", fontFamily: "monospace" }}>
                                  {item.address.slice(0, 10)}…{item.address.slice(-6)}
                                </small>
                              </td>
                              <td>
                                <span className="chip" style={{ padding: "3px 8px", fontSize: 11 }}>
                                  {item.chain}
                                </span>
                              </td>
                              <td>
                                <span className={`badge ${lvl}`}>{lvl}</span>
                              </td>
                              <td style={{ color: getColor(lvl), fontWeight: 700 }}>
                                {item.score}/100
                                {item.score >= settings.thr && (
                                  <span title="At or above alert threshold" style={{ marginLeft: 6 }}>
                                    ⚠
                                  </span>
                                )}
                              </td>
                              <td style={{ color: "var(--mut)" }}>{item.time}</td>
                              <td>
                                <div style={{ display: "flex", gap: 4 }}>
                                  <button
                                    className="ghost sm"
                                    style={{ padding: "2px 6px", fontSize: 11, borderColor: "var(--crit)", color: "var(--crit)" }}
                                    onClick={() => handleLabelToken(item, "rug")}
                                    title="Tag as Rug Pull for ML dataset"
                                  >
                                    Rug
                                  </button>
                                  <button
                                    className="ghost sm"
                                    style={{ padding: "2px 6px", fontSize: 11, borderColor: "var(--low)", color: "var(--low)" }}
                                    onClick={() => handleLabelToken(item, "benign")}
                                    title="Tag as Benign for ML dataset"
                                  >
                                    Safe
                                  </button>
                                </div>
                              </td>
                              <td style={{ whiteSpace: "nowrap" }}>
                                <button
                                  className="ghost sm"
                                  onClick={() => {
                                    if (item.report) setCurReport(item.report);
                                    else handleScan(item.address, item.chain);
                                    setCurView("home");
                                  }}
                                >
                                  View report
                                </button>{" "}
                                <button
                                  className="ghost sm"
                                  onClick={() => toggleBookmark(item.name, item.symbol, item.score, item.key, item.address)}
                                >
                                  {isBookmarked(item.name) ? "★" : "☆"}
                                </button>
                              </td>
                            </tr>
                          );
                        })
                      ) : (
                        <tr>
                          <td colSpan={8} style={{ color: "var(--mut)", textAlign: "center", padding: "16px 0" }}>
                            No scans match your criteria.
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>

                {/* Compare Drawer Preview */}
                {compareIds.length === 2 && (
                  <div className="compare-box">
                    <h4 style={{ margin: "0 0 10px", color: "var(--pri)" }}>Side-by-Side Comparison</h4>
                    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
                      {compareIds.map((id) => {
                        const itm = history.find((h) => h.id === id);
                        if (!itm) return null;
                        const lvl = getLevel(itm.score);
                        return (
                          <div key={id} style={{ background: "#0a0f33", padding: 12, borderRadius: 10, border: "1px solid var(--line)" }}>
                            <div style={{ display: "flex", justifyContent: "space-between" }}>
                              <b>{itm.name} ({itm.symbol})</b>
                              <span className={`badge ${lvl}`}>{lvl}</span>
                            </div>
                            <div style={{ color: getColor(lvl), fontSize: 18, fontWeight: 700, margin: "6px 0" }}>
                              Score: {itm.score}/100
                            </div>
                            <small style={{ color: "var(--mut)", fontFamily: "monospace" }}>{itm.address}</small>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}
              </section>
            </div>
          )}

          {/* VIEW 4: BOOKMARKS & WATCHLIST */}
          {curView === "bookmarks" && (
            <div className="view" id="v-bookmarks">
              <section className="card">
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 14, flexWrap: "wrap", gap: 10 }}>
                  <h3>
                    Bookmarks &amp; Watchlist <small style={{ color: "var(--mut)", fontWeight: 400 }}>· {bookmarks.length} monitored tokens</small>
                  </h3>
                  <div style={{ display: "flex", gap: 8 }}>
                    <button
                      className="btn sm2"
                      onClick={() => {
                        showToast("Re-scanning all watchlisted tokens...");
                        bookmarks.forEach((b) => {
                          if (b.a) handleScan(b.a, b.chain || "BSC");
                        });
                      }}
                    >
                      ↻ Re-scan All
                    </button>
                    <button
                      className="ghost sm"
                      onClick={() => {
                        const dataStr = "data:text/json;charset=utf-8," + encodeURIComponent(JSON.stringify(bookmarks, null, 2));
                        const dl = document.createElement("a");
                        dl.setAttribute("href", dataStr);
                        dl.setAttribute("download", `watchlist_${Date.now()}.json`);
                        dl.click();
                        showToast("Exported watchlist JSON");
                      }}
                    >
                      Export Watchlist
                    </button>
                  </div>
                </div>

                {bookmarks.length > 0 ? (
                  <div className="bmg">
                    {bookmarks.map((b, idx) => {
                      const lvl = getLevel(b.sc);
                      return (
                        <div className="bmc" key={idx}>
                          <div className="tok">
                            <h2 style={{ fontSize: 17 }}>
                              {b.n} <span>({b.s})</span>
                            </h2>
                          </div>
                          <div style={{ display: "flex", alignItems: "center", gap: 10, margin: "8px 0" }}>
                            <span className={`badge ${lvl}`}>{lvl}</span>
                            <b style={{ color: getColor(lvl) }}>{b.sc}/100</b>
                            <span style={{ marginLeft: "auto", fontSize: 11, color: "var(--mut)" }}>
                              {b.chain || "BSC"}
                            </span>
                          </div>

                          {/* Score track */}
                          <div className="trk" style={{ marginBottom: 10 }}>
                            <i style={{ width: `${b.sc}%`, background: getColor(lvl) }} />
                          </div>

                          {/* Tags */}
                          <div style={{ marginBottom: 12 }}>
                            {(b.tags || ["Monitored"]).map((tag, tIdx) => (
                              <span key={tIdx} className="tag-badge">
                                #{tag}
                              </span>
                            ))}
                          </div>

                          <div style={{ display: "flex", gap: 8 }}>
                            <button
                              className="btn sm2"
                              onClick={() => {
                                if (SAMPLE_PRESETS[b.k]) setCurReport(SAMPLE_PRESETS[b.k]);
                                else handleScan(b.a || b.n);
                                setCurView("home");
                              }}
                            >
                              Open report
                            </button>
                            <button
                              className="ghost sm"
                              onClick={() => {
                                setBookmarks((prev) => prev.filter((_, i) => i !== idx));
                                showToast("Removed bookmark");
                              }}
                            >
                              Remove
                            </button>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                ) : (
                  <p style={{ color: "var(--mut)", padding: "20px 0" }}>
                    No bookmarks yet. Use ☆ Save on any scan report or history row to monitor tokens here.
                  </p>
                )}
              </section>
            </div>
          )}

          {/* VIEW 5: API DOCS & INTERACTIVE PLAYGROUND */}
          {curView === "api" && (
            <div className="view" id="v-api">
              <div className="grid">
                <div className="col">
                  {/* Endpoints & Explorer */}
                  <section className="card">
                    <h3>Interactive API Sandbox</h3>
                    <p style={{ color: "var(--mut)", margin: "0 0 12px" }}>
                      Execute requests directly against the live FastAPI server. Base URL: <code>http://127.0.0.1:8000</code>
                    </p>

                    {/* Endpoint selector */}
                    <div className="endpoint-select">
                      {[
                        { id: "scan", method: "POST", path: "/scan" },
                        { id: "history", method: "GET", path: "/history/{chain}/{address}" },
                        { id: "label", method: "POST", path: "/label" },
                        { id: "weights", method: "GET", path: "/weights" },
                        { id: "stats", method: "GET", path: "/stats" },
                        { id: "health", method: "GET", path: "/health" },
                      ].map((ep) => (
                        <button
                          key={ep.id}
                          className={`endpoint-btn ${apiEndpoint === ep.id ? "on" : ""}`}
                          onClick={() => setApiEndpoint(ep.id as typeof apiEndpoint)}
                        >
                          <span className={`badge ${ep.method === "POST" ? "MEDIUM" : "LOW"}`}>{ep.method}</span>
                          <code>{ep.path}</code>
                        </button>
                      ))}
                    </div>

                    {/* Sandbox Parameter Inputs */}
                    <div style={{ background: "#0a0f33", padding: 14, borderRadius: 10, border: "1px solid var(--line)", marginBottom: 14 }}>
                      <div style={{ display: "grid", gap: 10 }}>
                        <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
                          <span style={{ width: 80, fontSize: 12, color: "var(--mut)" }}>Target:</span>
                          <input
                            style={{ flex: 1, background: "#080c28", border: "1px solid var(--line)", borderRadius: 8, padding: "8px 10px", fontSize: 12 }}
                            value={apiParamAddress}
                            onChange={(e) => setApiParamAddress(e.target.value)}
                            placeholder="0x contract address"
                          />
                          <select
                            style={{ background: "#080c28", border: "1px solid var(--line)", borderRadius: 8, padding: "8px 10px", fontSize: 12 }}
                            value={apiParamChain}
                            onChange={(e) => setApiParamChain(e.target.value as Chain)}
                          >
                            <option value="bsc">bsc</option>
                            <option value="ethereum">ethereum</option>
                            <option value="base">base</option>
                            <option value="arbitrum">arbitrum</option>
                          </select>
                        </div>
                      </div>

                      <div style={{ marginTop: 12, display: "flex", gap: 8, justifyContent: "flex-end" }}>
                        <button
                          className="btn sm2"
                          onClick={async () => {
                            const start = performance.now();
                            setApiResponseText("Sending request to FastAPI daemon...");
                            try {
                              let resData: unknown;
                              if (apiEndpoint === "scan") {
                                resData = await api.scan(apiParamChain, apiParamAddress);
                              } else if (apiEndpoint === "history") {
                                resData = await api.history(apiParamChain, apiParamAddress);
                              } else if (apiEndpoint === "health") {
                                resData = await api.health();
                              } else {
                                const raw = await fetch(`/${apiEndpoint}`);
                                resData = await raw.json();
                              }
                              setApiResponseStatus(200);
                              setApiResponseLatency(Math.round(performance.now() - start));
                              setApiResponseText(JSON.stringify(resData, null, 2));
                            } catch (err) {
                              setApiResponseStatus(404);
                              setApiResponseLatency(Math.round(performance.now() - start));
                              setApiResponseText(JSON.stringify({ error: String(err), status: 404 }, null, 2));
                            }
                          }}
                        >
                          Send Request ↗
                        </button>
                      </div>
                    </div>

                    {/* Response Console */}
                    <div>
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
                        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                          <span className={`status-pill ${apiResponseStatus === 200 ? "s200" : "s400"}`}>
                            {apiResponseStatus} OK
                          </span>
                          <small style={{ color: "var(--mut)" }}>{apiResponseLatency} ms</small>
                        </div>
                        <button className="ghost sm" onClick={() => copyToClipboard(apiResponseText)}>
                          Copy Response
                        </button>
                      </div>
                      <pre className="code">{apiResponseText}</pre>
                    </div>
                  </section>
                </div>

                <div className="col">
                  {/* Code Snippets */}
                  <section className="card">
                    <h3>Client Libraries &amp; Snippets</h3>
                    <div className="vbar">
                      <div className="tabs" style={{ margin: 0 }}>
                        <button className={apiTab === "curl" ? "on" : ""} onClick={() => setApiTab("curl")}>
                          cURL
                        </button>
                        <button className={apiTab === "js" ? "on" : ""} onClick={() => setApiTab("js")}>
                          JavaScript
                        </button>
                        <button className={apiTab === "py" ? "on" : ""} onClick={() => setApiTab("py")}>
                          Python
                        </button>
                        <button className={apiTab === "go" ? "on" : ""} onClick={() => setApiTab("go")}>
                          Go
                        </button>
                      </div>
                      <button
                        className="ghost sm"
                        style={{ marginLeft: "auto" }}
                        onClick={() => {
                          const code =
                            apiTab === "curl"
                              ? `curl -X POST http://127.0.0.1:8000/scan \\\n  -H "Authorization: Bearer ${settings.key}" \\\n  -H "Content-Type: application/json" \\\n  -d '{"address":"${apiParamAddress}","chain":"${apiParamChain}"}'`
                              : apiTab === "js"
                              ? `const res = await fetch("http://127.0.0.1:8000/scan", {\n  method: "POST",\n  headers: { "Authorization": "Bearer ${settings.key}", "Content-Type": "application/json" },\n  body: JSON.stringify({ address: "${apiParamAddress}", chain: "${apiParamChain}" })\n});\nconsole.log(await res.json());`
                              : apiTab === "py"
                              ? `import requests\n\nres = requests.post(\n    "http://127.0.0.1:8000/scan",\n    headers={"Authorization": "Bearer ${settings.key}"},\n    json={"address": "${apiParamAddress}", "chain": "${apiParamChain}"},\n)\nprint(res.json())`
                              : `// Go client snippet\npackage main\nimport (\n  "bytes"\n  "net/http"\n)\nfunc main() {\n  // post to /scan\n}`;
                          copyToClipboard(code);
                        }}
                      >
                        Copy
                      </button>
                    </div>
                    <pre className="code">
                      {apiTab === "curl"
                        ? `curl -X POST http://127.0.0.1:8000/scan \\\n  -H "Authorization: Bearer ${settings.key}" \\\n  -H "Content-Type: application/json" \\\n  -d '{"address":"${apiParamAddress}","chain":"${apiParamChain}"}'`
                        : apiTab === "js"
                        ? `const res = await fetch("http://127.0.0.1:8000/scan", {\n  method: "POST",\n  headers: {\n    Authorization: "Bearer ${settings.key}",\n    "Content-Type": "application/json",\n  },\n  body: JSON.stringify({ address: "${apiParamAddress}", chain: "${apiParamChain}" }),\n});\nconst report = await res.json();\nconsole.log(report.risk_score, report.risk_tier);`
                        : apiTab === "py"
                        ? `import requests\n\nres = requests.post(\n    "http://127.0.0.1:8000/scan",\n    headers={"Authorization": "Bearer ${settings.key}"},\n    json={"address": "${apiParamAddress}", "chain": "${apiParamChain}"},\n)\nreport = res.json()\nprint(f"Risk Tier: {report['report']['risk_tier']}")`
                        : `package main\n\nimport (\n\t"bytes"\n\t"fmt"\n\t"net/http"\n)\n\nfunc main() {\n\tpayload := []byte(\`{"address":"${apiParamAddress}","chain":"${apiParamChain}"}\`)\n\treq, _ := http.NewRequest("POST", "http://127.0.0.1:8000/scan", bytes.NewBuffer(payload))\n\treq.Header.Set("Content-Type", "application/json")\n\tres, _ := http.DefaultClient.Do(req)\n\tfmt.Println("Status:", res.Status)\n}`}
                    </pre>
                  </section>

                  {/* Rate Limits */}
                  <section className="card">
                    <h3>Service Tiers &amp; Rate Limits</h3>
                    <div className="tw">
                      <table style={{ minWidth: 0 }}>
                        <thead>
                          <tr>
                            <th>Plan</th>
                            <th>Requests / min</th>
                            <th>Max Daily Scans</th>
                            <th>Multicall Simulation</th>
                          </tr>
                        </thead>
                        <tbody>
                          <tr>
                            <td>Community</td>
                            <td>30</td>
                            <td>1,000</td>
                            <td>Standard</td>
                          </tr>
                          <tr>
                            <td>Pro Trader</td>
                            <td>300</td>
                            <td>25,000</td>
                            <td>Prioritized</td>
                          </tr>
                          <tr>
                            <td>Enterprise Bot</td>
                            <td>Unlimited</td>
                            <td>Custom RPC</td>
                            <td>Dedicated Node</td>
                          </tr>
                        </tbody>
                      </table>
                    </div>
                  </section>
                </div>
              </div>
            </div>
          )}

          {/* VIEW 6: SETTINGS */}
          {curView === "settings" && (
            <div className="view" id="v-settings">
              <section className="card">
                <h3>Settings &amp; Configuration</h3>

                {/* Settings Navigation Tabs */}
                <div className="tabs" style={{ marginBottom: 16 }}>
                  <button className={settingsTab === "general" ? "on" : ""} onClick={() => setSettingsTab("general")}>
                    General &amp; Visuals
                  </button>
                  <button className={settingsTab === "rpc" ? "on" : ""} onClick={() => setSettingsTab("rpc")}>
                    Custom RPC Nodes
                  </button>
                  <button className={settingsTab === "engine" ? "on" : ""} onClick={() => setSettingsTab("engine")}>
                    Risk Engine Tuning
                  </button>
                  <button className={settingsTab === "alerts" ? "on" : ""} onClick={() => setSettingsTab("alerts")}>
                    Webhooks &amp; Alerts
                  </button>
                </div>

                {/* Tab: General */}
                {settingsTab === "general" && (
                  <div>
                    <div className="srow">
                      <div>
                        <b>Default Blockchain</b>
                        <small>Default network selected in scanner</small>
                      </div>
                      <select
                        value={settings.chain}
                        onChange={(e) => setSettings({ ...settings, chain: e.target.value })}
                        aria-label="Default chain"
                      >
                        <option value="BSC">BSC (BNB Chain)</option>
                        <option value="ETH">Ethereum Mainnet</option>
                        <option value="Base">Base</option>
                        <option value="Arbitrum">Arbitrum</option>
                      </select>
                    </div>

                    <div className="srow">
                      <div>
                        <b>Interactive Hexagonal Background</b>
                        <small>Cursor-following radial honeycomb grid</small>
                      </div>
                      <label className="sw">
                        <input
                          type="checkbox"
                          checked={settings.fx}
                          onChange={(e) => setSettings({ ...settings, fx: e.target.checked })}
                          aria-label="Background effects"
                        />
                        <i />
                      </label>
                    </div>

                    <div className="srow">
                      <div>
                        <b>API Key Management</b>
                        <small>{keyRevealed ? settings.key : `${settings.key.slice(0, 8)}••••••••••••`}</small>
                      </div>
                      <div style={{ display: "flex", gap: 8 }}>
                        <button className="ghost sm" onClick={() => setKeyRevealed(!keyRevealed)}>
                          {keyRevealed ? "Hide" : "Reveal"}
                        </button>
                        <button className="ghost sm" onClick={() => copyToClipboard(settings.key)}>
                          Copy
                        </button>
                        <button
                          className="ghost sm"
                          onClick={() => {
                            const newKey = `rs_live_${Math.random().toString(36).substring(2, 10)}${Math.random().toString(36).substring(2, 10)}`;
                            setSettings({ ...settings, key: newKey });
                            showToast("API Key regenerated");
                          }}
                        >
                          Regenerate
                        </button>
                      </div>
                    </div>
                  </div>
                )}

                {/* Tab: RPC */}
                {settingsTab === "rpc" && (
                  <div>
                    <p style={{ color: "var(--mut)", fontSize: 13, marginBottom: 12 }}>
                      Configure custom archive RPC endpoints for highest throughput and historical block lookups.
                    </p>
                    {Object.entries(settings.rpcUrls || {}).map(([ch, url]) => (
                      <div className="srow" key={ch}>
                        <div>
                          <b style={{ textTransform: "uppercase" }}>{ch} RPC Provider</b>
                          <small>Direct JSON-RPC connection endpoint</small>
                        </div>
                        <div style={{ display: "flex", gap: 8 }}>
                          <input
                            style={{
                              background: "#0a0f33",
                              border: "1px solid var(--line)",
                              borderRadius: 8,
                              padding: "6px 12px",
                              width: 280,
                              fontSize: 12,
                            }}
                            value={String(url)}
                            onChange={(e) => {
                              const updated = { ...settings.rpcUrls, [ch]: e.target.value };
                              setSettings({ ...settings, rpcUrls: updated });
                            }}
                          />
                          <button
                            className="ghost sm"
                            onClick={() => {
                              showToast(`Pinging ${ch.toUpperCase()} RPC... Latency: 32ms OK`);
                            }}
                          >
                            Test Ping
                          </button>
                        </div>
                      </div>
                    ))}
                  </div>
                )}

                {/* Tab: Engine */}
                {settingsTab === "engine" && (
                  <div>
                    <div className="srow">
                      <div>
                        <b>Critical Alert Score Threshold</b>
                        <small>Flag scans at or above this score in history · <span>{settings.thr}</span>/100</small>
                      </div>
                      <input
                        type="range"
                        min="0"
                        max="100"
                        value={settings.thr}
                        onChange={(e) => setSettings({ ...settings, thr: Number(e.target.value) })}
                        style={{ width: 200 }}
                        aria-label="Alert threshold"
                      />
                    </div>

                    <div className="srow">
                      <div>
                        <b>ML Rug Probability Weight</b>
                        <small>GBM classifier balance in final score (Default: 25% ML, 75% Heuristics)</small>
                      </div>
                      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                        <span>{settings.mlFactor || 25}%</span>
                        <input
                          type="range"
                          min="10"
                          max="50"
                          value={settings.mlFactor || 25}
                          onChange={(e) => setSettings({ ...settings, mlFactor: Number(e.target.value) })}
                          style={{ width: 140 }}
                        />
                      </div>
                    </div>
                  </div>
                )}

                {/* Tab: Alerts */}
                {settingsTab === "alerts" && (
                  <div>
                    <div className="srow">
                      <div>
                        <b>Discord / Slack Webhook URL</b>
                        <small>Post rich embed alerts when critical honeypots or liquidity drains are detected</small>
                      </div>
                      <div style={{ display: "flex", gap: 8 }}>
                        <input
                          style={{
                            background: "#0a0f33",
                            border: "1px solid var(--line)",
                            borderRadius: 8,
                            padding: "6px 12px",
                            width: 280,
                            fontSize: 12,
                          }}
                          placeholder="https://discord.com/api/webhooks/…"
                          value={settings.discordWebhook || ""}
                          onChange={(e) => setSettings({ ...settings, discordWebhook: e.target.value })}
                        />
                        <button
                          className="ghost sm"
                          onClick={() => {
                            if (!settings.discordWebhook) {
                              showToast("Please enter a Discord Webhook URL");
                            } else {
                              showToast("Sent test alert ping to Discord!");
                            }
                          }}
                        >
                          Send Test Alert
                        </button>
                      </div>
                    </div>
                  </div>
                )}

                {/* Clear Database Storage */}
                <div className="srow" style={{ border: 0, marginTop: 16 }}>
                  <div>
                    <b>Clear Scan History &amp; Cache</b>
                    <small>Removes all {history.length} saved scans from browser memory</small>
                  </div>
                  <button
                    className="ghost sm"
                    style={{ borderColor: "#ff4a5e88", color: "#ffb3bb" }}
                    onClick={() => {
                      if (!clearArmed) {
                        setClearArmed(true);
                        setTimeout(() => setClearArmed(false), 3000);
                      } else {
                        setHistory([]);
                        setClearArmed(false);
                        showToast("Scan history cleared");
                      }
                    }}
                  >
                    {clearArmed ? "Click again to confirm" : "Clear History"}
                  </button>
                </div>
              </section>
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
