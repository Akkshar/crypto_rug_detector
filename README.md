# crypto_rug_detector

AI-assisted detection engine for **rug pulls, liquidity-drain exit scams and honeypots** on EVM chains (Ethereum, BSC, Arbitrum, Base). It ingests contract metadata, liquidity-pool state, holder distribution and deployer provenance, runs modular rule-based heuristics, refines the result with a scikit-learn model, and emits a composite **risk score (0–100)** and tier (`LOW` / `MEDIUM` / `HIGH` / `CRITICAL`) — as a Rich terminal report, JSON, or webhook alerts.

It runs **fully offline** against JSON fixtures, or **live** against any EVM RPC (plus an optional Etherscan key for source verification and deployer tracing).

## Quick start

```bash
uv venv .venv --python 3.12 && uv sync --extra dev --extra live
# or: python -m venv .venv && .venv/Scripts/pip install -e ".[dev,live]"

python main.py list-fixtures
python main.py analyze sample_honeypot                       # offline fixture
python main.py analyze sample_unlocked_rug --json -o report.json
python main.py analyze sample_benign_token --fail-on HIGH     # exit 1 if HIGH/CRITICAL (CI gate)
pytest
```

### Live scans

```bash
cp .env.example .env            # set RUGSCAN_<CHAIN>_RPC_URL and (optionally) RUGSCAN_ETHERSCAN_API_KEY
python main.py analyze 0xTOKEN… --provider rpc --chain base --cache reports/rugscan.db
python main.py history 0xTOKEN… --chain base --cache reports/rugscan.db
python main.py watch   0xTOKEN… -p rpc -c base --interval 60 --webhook https://discord.com/api/webhooks/…
```

The live provider needs a node that supports **state overrides** on `eth_call` (geth, erigon, reth, Alchemy, Infura, QuickNode…) for the sell simulation; everything else works on any JSON-RPC endpoint.

## What it does

```
TokenSnapshot ──► Heuristics ──► HeuristicResult[] ──► ScoringEngine ──► RiskReport
  mock | rpc      contract_inspector    flags + hard_fail   pre-filter → caps → ML blend → tier
  (+ cache)       liquidity_analyzer
                  holder_metrics                             ┌─ history / diff   (SnapshotCache)
                  deployer_tracer                            ├─ watch / alerts   (TokenWatcher)
                                                             └─ backtest / lift  (labels)
```

| Layer | Module | Role |
|---|---|---|
| Schemas | `src/models/risk_report.py` | Pydantic v2 models for the input snapshot, flags and the final report. Unknown facts are `None`, never guessed. |
| Config | `config/risk_weights.yaml`, `config/chains.yaml`, `config/settings.py` | Flag points, category caps, thresholds and tiers; per-chain DEX factories, LP lockers, mixers, price sources; env/`.env` settings (`RUGSCAN_*`). Every flag a heuristic can raise **must** have a weight — the engine fails fast otherwise. |
| Ingestion | `src/ingestion/` | `MockDataProvider` (fixtures), `RPCDataProvider` (live), `CachingProvider` (SQLite-backed). |
| EVM | `src/ingestion/evm/` | `EvmClient` abstraction (web3 + programmable fake), pool discovery, LP-holder replay, Etherscan client, deployer tracing, source/ABI/bytecode capability analysis, sell simulation. |
| Heuristics | `src/heuristics/` | Pure functions `TokenSnapshot → HeuristicResult`. Points come from YAML, not code. |
| ML | `src/models/ml_model.py` | Feature extraction, `GradientBoostingClassifier` (rug probability), `IsolationForest` (out-of-distribution guard), per-feature what-if explanations, joblib persistence. |
| Scoring | `src/models/scoring_engine.py` | Pre-filter → per-category capped sum → ML blend → tier. Logs `[FLAG] … +N Risk Points` for every decision. |
| Storage | `src/storage/` | SQLite snapshot + report cache with labels, history diffs and JSONL import/export. |
| Watch | `src/watch/` | Interval re-scoring with console / webhook alerts on escalation, liquidity drops, LP leaving lockers, new critical flags. |
| Backtest | `src/backtest/` | AUC, precision/recall at tier cut-offs, best-F1 threshold, per-flag P(rug | flag) and lift over labelled snapshots. |
| CLI | `main.py` | `analyze`, `watch`, `history`, `label`, `backtest`, `export-snapshots`, `list-fixtures`, `weights`. |

## Detection heuristics

| Engine | Signal | Flag(s) |
|---|---|---|
| **contract_inspector** | Owner is an EOA / non-timelocked contract / multisig | `OWNER_ACTIVE_EOA`, `OWNER_CONTRACT_NO_TIMELOCK`, `OWNER_MULTISIG` |
| | Fee / max-tx setters with no cap or cap > 25 % (bounds parsed from verified source) | `UNBOUNDED_FEE_SETTER`, `UNBOUNDED_MAX_TX_SETTER` |
| | Supply expansion callable by a non-governance role, or hidden behind a non-"mint" name | `MINT_FUNCTION_PRIVILEGED`, `HIDDEN_MINT_FUNCTION` |
| | Blacklist / pause / whitelist-only transfer gates found in `_transfer` | `BLACKLIST_CAPABILITY`, `PAUSE_TRADING_CAPABILITY`, `WHITELIST_TRANSFER_GATE` |
| | `selfdestruct` reachable; `delegatecall` outside a proxy | `SELFDESTRUCT_CAPABILITY`, `EXTERNAL_DELEGATECALL` |
| | Upgradeable proxy with unverified logic or no upgrade delay | `PROXY_UNVERIFIED_IMPLEMENTATION`, `PROXY_UPGRADE_NO_TIMELOCK` |
| | Declared buy/sell tax ≥ 10 / 25 / 90 % | `*_TAX_ELEVATED`, `*_TAX_HIGH`, `*_TAX_EXTREME` (hard fail) |
| **liquidity_analyzer** | LP share outside UNCX / PinkLock / Team Finance / burn | `LP_MOSTLY_UNLOCKED`, `LP_PARTIALLY_UNLOCKED`, `LP_LOCK_EXPIRING` |
| | LP held by deployer/owner | `LP_HELD_BY_INSIDER` |
| | FDV ÷ liquidity ≥ 100× / 1000×, thin liquidity | `FDV_LIQUIDITY_IMBALANCE`, `FDV_LIQUIDITY_EXTREME`, `LOW_LIQUIDITY` |
| | Simulated sells: 0 % success (hard fail), < 80 %, measured tax above declared, abnormal gas | `HONEYPOT_SELL_BLOCKED`, `LOW_SELL_SUCCESS_RATE`, `HIDDEN_SELL_TAX`, `EXCESSIVE_SELL_GAS` |
| **holder_metrics** | Top-10 non-DEX/non-burn wallets ≥ 30 / 50 % of circulating supply; single whale ≥ 20 % | `TOP10_CONCENTRATION_*`, `SINGLE_WHALE_DOMINANT` |
| **deployer_tracer** | First funding from a mixer / no-KYC swap; prior rugs (from labelled cache); serial launches; wallet < 7 days old | `DEPLOYER_MIXER_FUNDED`, `DEPLOYER_PRIVACY_EXCHANGE_FUNDED`, `DEPLOYER_PRIOR_RUGS`, `DEPLOYER_SERIAL_LAUNCHER`, `DEPLOYER_FRESH_WALLET` |

Renounced ownership neutralises owner-gated capabilities (they become notes rather than flags); a live 99 % tax is still flagged.

### How the live provider gets its facts

* **Pools** – asks every DEX factory in `chains.yaml` for a pool between the token and each quote asset (wrapped native + stables). v2 pools report reserves; liquidity is priced via the configured native-price pool. v3 pools report token balances; their positions are NFTs so LP-lock status is left unknown.
* **LP lock** – the v2 pair *is* the LP token, so its `Transfer` logs are replayed into balances and each holder is classified as a known locker, burn, insider, unknown contract or wallet.
* **Sell simulation** – a throwaway address is funded via a `balance` state override; `swapExactETHForTokens…` is run with a **bisected `amountOutMin`** (the router reverts whenever real output < min, so the bisection recovers the true post-tax output). The token's `balances` and `allowance` mapping slots are located by planting sentinels through `stateDiff`, then sells of the full / half / quarter balance are bisected the same way → effective sell tax, size-gated traps, hard reverts.
* **Capabilities** – verified Solidity source is brace-parsed per function: setter bodies are scanned for `require(x <= N)` bounds, every public function body for `_mint(` / `totalSupply +=` (hidden mints), and the transfer path for blacklist/whitelist/trading-switch reads. ABI names and bytecode selector probing fill in when source is unavailable.
* **Deployer** – `getcontractcreation` → deployer; its earliest transactions give wallet age, first funding source (matched against mixer / privacy-exchange / CEX lists) and launch count. Prior-rug counts come from tokens you have labelled `rug` in the cache.

## Scoring

1. **Pre-filter** – any heuristic returning `hard_fail` (0 % sell success, measured or declared tax ≥ 90 %) forces `CRITICAL` with a score floor of 95.
2. **Weighted aggregation** – flag points are summed per category and clipped to that category's cap (`CONTRACT_PRIVILEGE` 50, `LIQUIDITY` 50, `HONEYPOT` 60, `DISTRIBUTION` 30, `DEPLOYER` 35); capped totals are summed and clipped to 100 → `rule_score`.
3. **ML refinement** – features `top10_concentration`, `is_lp_locked`, `max_tax_rate`, `has_mint_function`, `deployer_age_days`, `tvl_usd` feed a GBM; `final = 0.75·rule + 0.25·(100·P(rug))`. If the IsolationForest marks the input out-of-distribution or more than two features were imputed, the ML weight is halved. Each report carries per-feature **contributions** (`P(rug|x) − P(rug|x with feature at its training median)`), shown as "ML drivers".
4. **Tier** – `MEDIUM ≥ 25`, `HIGH ≥ 50`, `CRITICAL ≥ 75`.

All numbers above live in `config/risk_weights.yaml`; pass `--weights other.yaml` to experiment, then `backtest --weights other.yaml` to see whether it helped.

### Fixture results

| Fixture | Tier | Score | Path |
|---|---|---|---|
| `sample_benign_token` | LOW | ≈ 6 | locked LP, renounced, 1 % tax; one mild concentration flag |
| `sample_unlocked_rug` | CRITICAL | ≈ 99.8 | unlocked LP in deployer wallet, hidden mint, Tornado-funded serial rugger – reached by weighted score, no hard fail |
| `sample_honeypot` | CRITICAL | 100 | 99 % sell tax + 0/10 simulated sells → pre-filter hard fail |

## Caching, history and labels

Pass `--cache path.db` (or set `RUGSCAN_CACHE_PATH`) and every `analyze`/`watch` stores the snapshot and report. Then:

```bash
python main.py analyze 0x… -p rpc --cache c.db --max-age 3600   # reuse a snapshot < 1 h old
python main.py history 0x… -c base --cache c.db                 # score/flag diffs between scans
python main.py label   0x… rug -c base --cache c.db             # ground truth (rug | benign | clear)
python main.py backtest --cache c.db -o metrics.json            # AUC, P/R, per-flag lift
python main.py export-snapshots corpus.jsonl --cache c.db --labelled-only
python main.py backtest --jsonl corpus.jsonl --weights experiment.yaml
```

Labelled rugs also feed `DEPLOYER_PRIOR_RUGS` on live scans: deploy a second token from a wallet you've already labelled and the detector knows.

## Watch mode

```bash
python main.py watch 0x… -p rpc -c bsc --interval 30 --cache c.db --webhook $DISCORD_HOOK
```

Emits `initial`, then on each check: `tier_escalation`, `score_jump`, `new_critical_flag`, `liquidity_drop` (default ≥ 30 %), `lp_unlocked` (locked LP share falls ≥ 10 points) and `error`. Webhook payloads use the Discord/Slack `content` + `embeds` dialect.

## Development

```bash
uv sync --extra dev --extra live
ruff check . && ruff format --check . && mypy && pytest
docker build -t rugscan . && docker run --rm rugscan analyze sample_honeypot --json -q
```

CI (`.github/workflows/ci.yml`) runs lint, format, mypy, the test suite on Ubuntu + Windows × Python 3.11/3.12, an offline self-check and a Docker build. Tests include a Hypothesis property suite (random valid snapshots never crash the engine; scores stay in range; JSON round-trips) and fake-chain / fake-explorer tests for every live-provider path.

### Extending

* **New heuristic** – subclass `BaseHeuristic`, set `name` / `category`, implement `evaluate()`, add it to `DEFAULT_HEURISTICS`, add each flag code to `risk_weights.yaml`. The parametrised test fails on any unweighted code.
* **New chain / DEX / locker** – edit `config/chains.yaml`; addresses are validated and checksummed on load.
* **New provider** – subclass `BaseDataProvider` and return a `TokenSnapshot`; leave fields `None` when unknown.
* **Real labels** – `RugRiskModel(ml_config, dataset=(X, y))` trains on your own feature matrix; `extract_features()` is shared between training and inference so they cannot drift.

## Limitations

* The bundled ML model is trained on **synthetic** data: it encodes priors, not learned behaviour. Its weight is 0.25 and it is down-weighted on novelty. Use `backtest` on a labelled corpus before trusting any threshold.
* `chains.yaml` addresses are the canonical deployments as best known at authoring time — verify them against protocol docs before production use; a wrong factory address silently yields "no pool found".
* v3 LP positions are not resolved for lock status; sell simulation covers v2-style routers only.
* Holder distribution is rebuilt from `Transfer` logs with a chunk cap; very large tokens are reported as unavailable rather than partially replayed.
* Heuristic thresholds are opinionated defaults, not calibrated — that is what the backtest harness is for.
