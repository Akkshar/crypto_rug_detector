// Types mirror src/models/risk_report.py (RiskReport) and src/api/app.py.

export type Chain = "ethereum" | "bsc" | "arbitrum" | "base";
export type Tier = "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
export type Severity = "INFO" | "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";

export interface RiskFlag {
  code: string;
  category: string;
  severity: Severity;
  title: string;
  detail: string;
  points: number;
  evidence: Record<string, unknown>;
}

export interface CategoryBreakdown {
  category: string;
  raw_points: number;
  capped_points: number;
  cap: number;
  flag_count: number;
}

export interface MLAssessment {
  model: string;
  rug_probability: number;
  anomaly_score: number;
  is_out_of_distribution: boolean;
  imputed_features: string[];
  features: Record<string, number>;
  contributions: Record<string, number>;
  reliable: boolean;
}

export interface RiskReport {
  chain: Chain;
  address: string;
  token_name: string | null;
  token_symbol: string | null;
  generated_at: string;
  provider: string;
  risk_score: number;
  risk_tier: Tier;
  rule_score: number;
  ml: MLAssessment | null;
  hard_fail: boolean;
  hard_fail_reasons: string[];
  breakdown: CategoryBreakdown[];
  flags: RiskFlag[];
  warnings: string[];
  missing_data: string[];
}

export interface ScanResponse {
  report: RiskReport;
  from_cache: boolean;
  previous_score: number | null;
}

export interface Health {
  status: string;
  version: string;
  provider: string;
  chains: Chain[];
  model: string | null;
  model_trained_on: string | null;
  cache: boolean;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, { headers: { "content-type": "application/json" }, ...init });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body);
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

export const api = {
  health: () => request<Health>("/health"),
  scan: (chain: Chain, address: string, maxAgeS?: number) =>
    request<ScanResponse>("/scan", {
      method: "POST",
      body: JSON.stringify({ chain, address, max_age_s: maxAgeS ?? null }),
    }),
  history: (chain: Chain, address: string, limit = 30) =>
    request<RiskReport[]>(`/history/${chain}/${address}?limit=${limit}`),
  label: (chain: Chain, address: string, label: "rug" | "benign" | null) =>
    request<{ updated: number }>("/label", { method: "POST", body: JSON.stringify({ chain, address, label }) }),
};
