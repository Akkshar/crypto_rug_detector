export const CATEGORIES = [
  "Contract Privilege",
  "Liquidity",
  "Honeypot",
  "Distribution",
  "Deployer",
  "Trading",
] as const;

export const CAT_COLORS = ["#4b6bff", "#2fa8ff", "#ff4a5e", "#ff7a3d", "#f2f2ff", "#27d890"];
export const CAT_CAPS = [50, 50, 60, 30, 35, 30];

export type RiskLevel = "CRITICAL" | "HIGH" | "MEDIUM" | "LOW";

export interface ReportItem {
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
  fl?: number[];
  chain?: string;
  provider?: string;
  generated_at?: string;
}

export type HistoryTuple = [string, string, string, string, number]; // [type, name, "(SYM)", date, score]

export interface BookmarkItem {
  k: string;
  n: string;
  s: string;
  sc: number;
}

export interface SettingsState {
  chain: string;
  thr: number;
  fx: boolean;
  key: string;
}

export const SAMPLE_DATA: Record<string, ReportItem> = {
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
};

export const INITIAL_HIST: HistoryTuple[] = [
  ["safe", "SafeMoonElon2.0", "(SME2)", "10/11/2026, 03:52 AM", 99.8],
  ["benign", "Uniswap V2", "(UNI)", "10/10/2026, 11:24 PM", 6.1],
  ["unlocked", "TestToken", "(TTK)", "10/10/2026, 10:17 PM", 42.3],
  ["unlocked", "PepeCoin", "(PEPE)", "10/09/2026, 09:42 PM", 78.6],
  ["unlocked", "MoonVault", "(MVLT)", "10/08/2026, 06:40 PM", 71.4],
  ["honeypot", "RugKing", "(RUGK)", "10/08/2026, 05:12 PM", 96.2],
  ["benign", "StableLP", "(SLP)", "10/07/2026, 11:03 AM", 9.8],
  ["unlocked", "DogeMoon", "(DMN)", "10/07/2026, 09:31 AM", 55],
];

export const lvl = (s: number): RiskLevel =>
  s >= 90 ? "CRITICAL" : s >= 60 ? "HIGH" : s >= 30 ? "MEDIUM" : "LOW";

export const colr = (l: string): string => {
  switch (l) {
    case "CRITICAL":
      return "var(--crit)";
    case "HIGH":
      return "var(--high)";
    case "MEDIUM":
      return "var(--med)";
    case "LOW":
    default:
      return "var(--low)";
  }
};

export const LC: Record<RiskLevel, [number, number, number]> = {
  CRITICAL: [255, 74, 94],
  HIGH: [255, 122, 61],
  MEDIUM: [255, 194, 61],
  LOW: [39, 216, 144],
};

export const GL: Record<RiskLevel, number> = {
  CRITICAL: 1,
  HIGH: 0.85,
  MEDIUM: 0.65,
  LOW: 0.5,
};

export const SEC: [number, number, number] = [122, 77, 255];

export const LS = {
  g<T>(k: string, d: T): T {
    try {
      const v = localStorage.getItem(k);
      return v ? JSON.parse(v) : d;
    } catch {
      return d;
    }
  },
  s<T>(k: string, v: T): void {
    try {
      localStorage.setItem(k, JSON.stringify(v));
    } catch {
      /* ignore storage errors */
    }
  },
};

export const esc = (s: string | number): string =>
  String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c] || c));
