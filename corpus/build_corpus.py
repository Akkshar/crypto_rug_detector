"""Build the labelled evaluation corpus from public sources.

Rug labels come from the SoK rug-pull dataset (Sun et al. 2024, arXiv:2403.16082):
2,390 incidents on Ethereum/BSC, each with the reporting security firm and a URL.
Benign labels come from CoinGecko's market ranking: tokens that still have a market
and a contract on Ethereum/BSC today. Both are written with their provenance so a
write-up can state exactly where every label came from.

    python corpus/build_corpus.py            # downloads, writes corpus/*.csv
    python corpus/build_corpus.py --raw DIR  # reuse previously downloaded files

Outputs (all ``chain,address,label,block,...`` – extra columns are ignored by import-corpus):
    corpus/rugs_sok2024.csv        every SoK incident (strong rug labels)
    corpus/benign_coingecko.csv    ranked tokens with an ETH/BSC contract (strong-ish benign labels)
    corpus/eval_sample.csv         stratified, deterministic subset sized for a live --at-launch import
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOK_URL = "https://raw.githubusercontent.com/dianxiang-sun/rug_pull_dataset/main/rugpull_full_dataset_new.csv"
SOK_CITATION = "Sun et al. 2024, SoK: Rug Pull Causes, Datasets, and Detection Tools (arXiv:2403.16082)"
CG_MARKETS = "https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=market_cap_desc&per_page=250&page={}"
CG_LIST = "https://api.coingecko.com/api/v3/coins/list?include_platform=true"
CG_PAGES = 4  # ranks 1..1000

CHAIN_MAP = {"ETH": "ethereum", "BSC": "bsc"}
CG_PLATFORMS = {"ethereum": "ethereum", "binance-smart-chain": "bsc"}
# Not "launch tokens": wrapped/bridged assets, stablecoins and liquid-staking receipts are proxies or
# mint-on-demand contracts whose shape says nothing about rug risk.
BENIGN_EXCLUDE = re.compile(r"wrapped|bridged|staked|restak|liquid|usd|tether|\bdai\b|\beur|\bgbp|stable", re.I)
ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")

# eval_sample sizing: all BSC rugs, ETH rugs capped per root cause so "Sale-restrict" (83 % of the SoK set)
# does not swamp the other mechanisms, and benign tokens from below the top 100 so blue chips don't dominate.
ETH_RUG_CAP_PER_CAUSE = 30
BENIGN_MIN_RANK = 100
BENIGN_TARGET = 160


def fetch(url: str, dest: Path, *, retries: int = 3) -> bytes:
    if dest.exists():
        return dest.read_bytes()
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "rugscan-corpus-builder"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = resp.read()
            dest.write_bytes(data)
            return data
        except Exception as exc:
            if attempt == retries - 1:
                raise
            print(f"  {url}: {exc}; retrying", file=sys.stderr)
            time.sleep(5 * (attempt + 1))
    raise AssertionError("unreachable")


def stable_key(address: str) -> str:
    """Deterministic shuffle key so the sample is reproducible without a RNG seed argument."""
    return hashlib.sha256(address.lower().encode()).hexdigest()


def build_rugs(raw: Path) -> list[dict[str, str]]:
    text = fetch(SOK_URL, raw / "rugpull_full_dataset_new.csv").decode("utf-8-sig")
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for rec in csv.DictReader(text.splitlines()):
        address = (rec.get("address") or "").strip()
        chain = CHAIN_MAP.get((rec.get("Chain") or "").strip().upper())
        if not chain or not ADDRESS.match(address) or address.lower() in seen:
            continue
        seen.add(address.lower())
        rows.append(
            {
                "chain": chain,
                "address": address,
                "label": "rug",
                "block": "",
                "source": f"{SOK_CITATION}; reported by {(rec.get('Sources') or '').strip()}",
                "url": (rec.get("URL") or "").strip(),
                "rug_type": (rec.get("Type") or "").strip(),
                "root_cause": (rec.get("Root Causes") or "").strip(),
                "losses_usd": (rec.get("Losses") or "").strip().replace(",", ""),
            }
        )
    return rows


def build_benign(raw: Path, today: str) -> list[dict[str, str]]:
    platforms = {c["id"]: c.get("platforms") or {} for c in json.loads(fetch(CG_LIST, raw / "coins_list.json"))}
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for page in range(1, CG_PAGES + 1):
        coins = json.loads(fetch(CG_MARKETS.format(page), raw / f"markets_p{page}.json"))
        time.sleep(2)  # free tier: ~30 req/min
        for coin in coins:
            if BENIGN_EXCLUDE.search(f"{coin['id']} {coin['name']} {coin['symbol']}"):
                continue
            if not isinstance(coin.get("market_cap_rank"), int):
                continue
            for platform, chain in CG_PLATFORMS.items():
                address = (platforms.get(coin["id"], {}).get(platform) or "").strip()
                if not ADDRESS.match(address) or address.lower() in seen:
                    continue
                seen.add(address.lower())
                rows.append(
                    {
                        "chain": chain,
                        "address": address,
                        "label": "benign",
                        "block": "",
                        "source": f"CoinGecko market-cap rank {coin['market_cap_rank']} on {today} (still traded)",
                        "url": f"https://www.coingecko.com/en/coins/{coin['id']}",
                        "symbol": coin["symbol"],
                        "market_cap_rank": str(coin["market_cap_rank"]),
                    }
                )
                break  # one chain per coin: the native deployment, not a bridged copy
    return rows


def build_sample(rugs: list[dict[str, str]], benign: list[dict[str, str]]) -> list[dict[str, str]]:
    by_cause: dict[str, list[dict[str, str]]] = defaultdict(list)
    for r in sorted(rugs, key=lambda r: stable_key(r["address"])):
        by_cause[(r["chain"], r["root_cause"])].append(r)
    picked: list[dict[str, str]] = []
    for (chain, _cause), group in sorted(by_cause.items()):
        picked.extend(group if chain == "bsc" else group[:ETH_RUG_CAP_PER_CAUSE])
    pool = [b for b in benign if int(b["market_cap_rank"] or 0) >= BENIGN_MIN_RANK]
    pool.sort(key=lambda b: stable_key(b["address"]))
    picked.extend(pool[:BENIGN_TARGET])
    return picked


def write(path: Path, rows: list[dict[str, str]]) -> None:
    columns = ["chain", "address", "label", "block"]
    extra = sorted({k for r in rows for k in r} - set(columns))
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns + extra, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in columns + extra})
    print(f"wrote {path.name}: {len(rows)} rows")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raw", type=Path, default=HERE / ".raw", help="where downloads are cached")
    ap.add_argument("--out", type=Path, default=HERE)
    args = ap.parse_args()
    args.raw.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()

    rugs = build_rugs(args.raw)
    benign = build_benign(args.raw, today)
    sample = build_sample(rugs, benign)
    write(args.out / "rugs_sok2024.csv", rugs)
    write(args.out / "benign_coingecko.csv", benign)
    write(args.out / "eval_sample.csv", sample)
    n_rug = sum(r["label"] == "rug" for r in sample)
    print(f"eval_sample: {n_rug} rug / {len(sample) - n_rug} benign")


if __name__ == "__main__":
    main()
