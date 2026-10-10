# crypto_rug_detector

Baseline AI-assisted detection engine for **rug pulls, liquidity-drain exit scams and honeypots** on EVM chains (Ethereum, BSC, Arbitrum, Base). It ingests contract metadata, liquidity-pool metrics, holder distribution and deployer provenance, runs a set of modular rule-based heuristics, refines the result with a lightweight scikit-learn model, and emits a composite **risk score (0–100)** and tier (`LOW` / `MEDIUM` / `HIGH` / `CRITICAL`) as a Rich terminal report or JSON.

Everything in this repo runs **fully offline** against JSON fixtures; a live Web3.py provider stub is included for later wiring.

## Quick start

```bash
uv venv .venv --python 3.12 && uv pip install --python .venv/Scripts/python.exe -e ".[dev,live]"
# or: python -m venv .venv && .venv/Scripts/pip install -e ".[dev,live]"

python main.py list-fixtures
python main.py analyze sample_honeypot
python main.py analyze 0x3333333333333333333333333333333333333333 --json -o report.json
python main.py analyze sample_benign_token --fail-on HIGH      # exit 1 if HIGH/CRITICAL (CI gate)
python main.py weights                                           # active flag weights & caps
pytest
```

Live mode (partial – see *Limitations*): copy `.env.example` to `.env`, set `RUGSCAN_<CHAIN>_RPC_URL`, then
`python main.py analyze 0x… --provider rpc --chain base`.

## Architecture

```
TokenSnapshot ──► Heuristics ──► HeuristicResult[] ──► ScoringEngine ──► RiskReport
   (ingestion)    contract_inspector     flags + hard_fail     pre-filter → caps → ML blend → tier
                  liquidity_analyzer
                  holder_metrics
                  deployer_tracer
```

| Layer | Module | Role |
|---|---|---|
| Schemas | `src/models/risk_report.py` | Pydantic v2 models for the input snapshot, flags and the final report. Unknown facts are `None`, never guessed. |
| Config | `config/settings.py`, `config/risk_weights.yaml` | Env/`.env` settings (`RUGSCAN_*`) and a validated YAML of flag points, category caps, thresholds and tiers. Every flag code a heuristic can raise **must** have a weight; the engine fails fast otherwise. |
| Ingestion | `src/ingestion/` | `BaseDataProvider` → `MockDataProvider` (fixtures) and `RPCDataProvider` (Web3.py stub). |
| Heuristics | `src/heuristics/` | Pure functions `TokenSnapshot → HeuristicResult`. Points come from YAML, not code. |
| ML | `src/models/ml_model.py` | Feature extraction, synthetic training set, `GradientBoostingClassifier` (rug probability) + `IsolationForest` (out-of-distribution guard). |
| Scoring | `src/models/scoring_engine.py` | Pre-filter → per-category capped sum → ML blend → tier. Logs `[FLAG] … +N Risk Points` for every decision. |
| CLI | `main.py` | `analyze`, `list-fixtures`, `weights`. |

## Detection heuristics

| Engine | Signal | Flag(s) |
|---|---|---|
| **contract_inspector** | Owner is an EOA / non-timelocked contract / multisig | `OWNER_ACTIVE_EOA`, `OWNER_CONTRACT_NO_TIMELOCK`, `OWNER_MULTISIG` |
| | Fee / max-tx setters with no cap or cap > 25 % | `UNBOUNDED_FEE_SETTER`, `UNBOUNDED_MAX_TX_SETTER` |
| | Mint callable by non-governance role, or hidden from the ABI | `MINT_FUNCTION_PRIVILEGED`, `HIDDEN_MINT_FUNCTION` |
| | Blacklist / pause / whitelist-only transfers | `BLACKLIST_CAPABILITY`, `PAUSE_TRADING_CAPABILITY`, `WHITELIST_TRANSFER_GATE` |
| | Upgradeable proxy with unverified logic or no upgrade delay | `PROXY_UNVERIFIED_IMPLEMENTATION`, `PROXY_UPGRADE_NO_TIMELOCK` |
| | Declared buy/sell tax ≥ 10 / 25 / 90 % | `*_TAX_ELEVATED`, `*_TAX_HIGH`, `*_TAX_EXTREME` (hard fail) |
| **liquidity_analyzer** | LP share outside UNCX / PinkLock / Team Finance / burn | `LP_MOSTLY_UNLOCKED`, `LP_PARTIALLY_UNLOCKED`, `LP_LOCK_EXPIRING` |
| | LP held by deployer/owner | `LP_HELD_BY_INSIDER` |
| | FDV ÷ liquidity ≥ 100× / 1000×, thin liquidity | `FDV_LIQUIDITY_IMBALANCE`, `FDV_LIQUIDITY_EXTREME`, `LOW_LIQUIDITY` |
| | Simulated sells: 0 % success (hard fail), < 80 %, hidden tax, abnormal gas | `HONEYPOT_SELL_BLOCKED`, `LOW_SELL_SUCCESS_RATE`, `HIDDEN_SELL_TAX`, `EXCESSIVE_SELL_GAS` |
| **holder_metrics** | Top-10 non-DEX/non-burn wallets ≥ 30 / 50 % of circulating supply; single whale ≥ 20 % | `TOP10_CONCENTRATION_*`, `SINGLE_WHALE_DOMINANT` |
| **deployer_tracer** | First funding from a mixer / no-KYC swap; prior rugs; serial launches; wallet < 7 days old | `DEPLOYER_MIXER_FUNDED`, `DEPLOYER_PRIVACY_EXCHANGE_FUNDED`, `DEPLOYER_PRIOR_RUGS`, `DEPLOYER_SERIAL_LAUNCHER`, `DEPLOYER_FRESH_WALLET` |

Renounced ownership neutralises owner-gated capabilities (they become notes rather than flags); a live 99 % tax is still flagged.

## Scoring

1. **Pre-filter** – any heuristic returning `hard_fail` (0 % sell success, measured or declared tax ≥ 90 %) forces `CRITICAL` with a score floor of 95.
2. **Weighted aggregation** – flag points are summed per category and clipped to that category's cap (`CONTRACT_PRIVILEGE` 50, `LIQUIDITY` 50, `HONEYPOT` 60, `DISTRIBUTION` 30, `DEPLOYER` 35); the capped totals are summed and clipped to 100 → `rule_score`.
3. **ML refinement** – features `top10_concentration`, `is_lp_locked`, `max_tax_rate`, `has_mint_function`, `deployer_age_days`, `tvl_usd` feed a GBM trained on a synthetic population; `final = 0.75·rule + 0.25·(100·P(rug))`. If the IsolationForest marks the input out-of-distribution or more than two features were imputed, the ML weight is halved.
4. **Tier** – `MEDIUM ≥ 25`, `HIGH ≥ 50`, `CRITICAL ≥ 75`.

All numbers above live in `config/risk_weights.yaml`; pass `--weights other.yaml` to experiment.

### Fixture results

| Fixture | Tier | Score | Path |
|---|---|---|---|
| `sample_benign_token` | LOW | ≈ 6 | locked LP, renounced, 1 % tax; one mild concentration flag |
| `sample_unlocked_rug` | CRITICAL | ≈ 99.8 | unlocked LP in deployer wallet, hidden mint, Tornado-funded serial rugger – reached by weighted score, no hard fail |
| `sample_honeypot` | CRITICAL | 100 | 99 % sell tax + 0/10 simulated sells → pre-filter hard fail |

## Extending

* **New heuristic** – subclass `BaseHeuristic`, set `name` / `category`, implement `evaluate()`, add it to `DEFAULT_HEURISTICS`, and add each new flag code to `risk_weights.yaml`. The parametrised test in `tests/test_heuristics.py` will fail on any unweighted code.
* **New provider** – subclass `BaseDataProvider` and return a `TokenSnapshot`; leave fields `None` when unknown so heuristics report `data_available=False` instead of guessing.
* **Real labels** – replace `generate_synthetic_dataset()` with a loader over labelled snapshots; `extract_features()` is shared between training and inference so they cannot drift.

## Limitations (baseline)

* The ML model is trained on **synthetic** data: it encodes priors, not learned behaviour. Treat it as a sanity check that nudges the rule score, which is why its weight is 0.25 and down-weighted on novelty.
* `RPCDataProvider` only fetches ERC-20 metadata, `owner()` and bytecode selector probes. Pool discovery, LP-locker resolution, sell simulation, holder indexing and funding-origin tracing are left as `None` and surface as `missing_data` in the report.
* Heuristic thresholds are opinionated defaults, not calibrated against a labelled corpus.
