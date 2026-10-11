# Building an evaluation corpus

Everything in `backtest`, `train` and `tune` runs on labelled snapshots in the
SQLite cache. This directory holds the CSV you feed to `import-corpus` and
notes on where labels can come from.

## CSV format

`chain,address,label[,block]` — one token per row:

| column | values |
|---|---|
| `chain` | `ethereum` · `bsc` · `arbitrum` · `base` |
| `address` | token contract address |
| `label` | `rug` (also `scam`, `honeypot`) or `benign` (also `legit`, `safe`) |
| `block` | optional – observe the token *as of* this block (archive node needed) |

```bash
python main.py import-corpus corpus/eval_sample.csv -p rpc --cache reports/corpus.db --at-launch
```

`--at-launch` pins each snapshot ~1000 blocks after contract creation when no
`block` is given. **This matters:** a known rug scanned *today* shows drained
liquidity and is trivially detectable; the question the evaluation must answer
is whether the token looked risky *before* it rugged.

## Ready-made corpus files

Built by `python corpus/build_corpus.py` (re-run to refresh; downloads are cached in `corpus/.raw/`):

| file | rows | label source |
|---|---|---|
| `rugs_sok2024.csv` | 2,390 rugs (ETH 2,290 / BSC ~90) | SoK rug-pull dataset, Sun et al. 2024 (arXiv:2403.16082, github.com/dianxiang-sun/rug_pull_dataset). Every row keeps the reporting firm, the report URL, the taxonomy type and root cause. |
| `benign_coingecko.csv` | ~540 benign | CoinGecko market-cap ranks 1–1000 with a native Ethereum/BSC contract, excluding wrapped/bridged/stable/staked assets. "Still has a market today" is the benign evidence. |
| `eval_sample.csv` | ~435 (275 rug / 160 benign) | Deterministic subset for a live `--at-launch` import: all BSC rugs, ETH rugs capped at 30 per root cause (83 % of the SoK set is one mechanism, sale restriction), benign tokens ranked ≥ 100. |

```bash
python main.py import-corpus corpus/eval_sample.csv -p rpc --cache reports/corpus.db --at-launch
python main.py backtest --cache reports/corpus.db
python main.py train --cache reports/corpus.db --compare
```

Caveats to state in any write-up: the rug set is biased towards contract-level traps (the two
largest sources are honeypot/trapdoor studies), so liquidity-pull-only rugs are under-represented;
the benign set is survivorship-selected and skews older and larger than a typical fresh launch, so
some separation comes from maturity rather than from the mechanisms the detector targets. Snapshot
both classes at launch (`--at-launch`) so that at least the observation conditions match.

## Where labels come from

1. **Confirmed rugs (strong labels).** Tokens whose liquidity was actually
   pulled or whose supply was dumped: the De.Fi REKT database, Chainabuse
   reports, the datasets referenced by Sun et al. (2024, arXiv:2403.16082),
   post-mortems. Record the source in a `source` column for your own records
   (the importer ignores extra columns).
2. **Survivors (strong benign labels).** Tokens that traded for 90+ days with
   liquidity still present and no privileged-function abuse. Snapshot them at
   launch too, so the two classes are observed under the same conditions.
3. **GoPlus weak labels.** `python main.py label-from-goplus -c bsc --addresses list.txt -o goplus.csv`
   queries the free GoPlus token-security API and labels `rug` on
   `is_honeypot` / `cannot_sell_all` / `owner_change_balance` / `selfdestruct`,
   `benign` on clean tokens (open source, LP locked, taxes ≤ 10 %, no risk
   fields). Quick, but GoPlus's verdicts are heuristics that overlap this
   project's own, so a backtest on them measures *agreement with GoPlus*.
   Report that honestly; use them to bootstrap, not to conclude.

Aim for at least 30 rows with 8+ per class before `train` will run, and a few
hundred before cross-validated numbers mean much. Class balance does not need
to be 50/50 — PR-AUC is reported precisely because rugs are the minority.

## Evaluation protocol that holds up

- Snapshot at launch (`--at-launch` or explicit `block`), never after the event.
- Keep strong and weak labels in separate CSVs and backtest them separately.
- `train --compare` reports the linear baseline next to the tree models on the
  same folds; if the baseline is within noise, the features – not the model –
  are the limit.
- `backtest` lists the largest misclassifications; read them. A "rug" the
  detector scores LOW at launch is either a label error or a genuinely new
  mechanism, and both are findings.
