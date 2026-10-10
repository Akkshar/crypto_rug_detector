from __future__ import annotations

import asyncio

from eth_utils import to_checksum_address

from config.chains import load_chain_registry
from src.ingestion.evm.client import FakeEvmClient, LogEntry, topic_address
from src.models.risk_report import Chain, RiskTier
from src.storage import SnapshotCache
from src.watch import RecordingNotifier
from src.watch.discover import PAIR_CREATED_TOPIC, POOL_CREATED_TOPIC, Firehose, NewPairScanner

REG = load_chain_registry()
ETH = REG.get("ethereum")
WETH, USDC = ETH.wrapped_native, ETH.quote_tokens["USDC"]
UNI_V2 = ETH.dexes["uniswap_v2"].factory
UNI_V3 = ETH.dexes["uniswap_v3"].factory

BENIGN = to_checksum_address("0x1111111111111111111111111111111111111111")
HONEYPOT = to_checksum_address("0x2222222222222222222222222222222222222222")
RUG = to_checksum_address("0x3333333333333333333333333333333333333333")
RANDOM = to_checksum_address("0x" + "ab" * 20)
PAIR = to_checksum_address("0x" + "cd" * 20)


def pair_created(factory: str, token0: str, token1: str, pair: str, block: int, n: int) -> LogEntry:
    data = b"\x00" * 12 + bytes.fromhex(pair[2:]) + n.to_bytes(32, "big")
    return LogEntry(
        factory, (PAIR_CREATED_TOPIC, topic_address(token0), topic_address(token1)), data, block, "0x" + f"{n:064x}"
    )


def pool_created(factory: str, token0: str, token1: str, fee: int, pool: str, block: int, n: int) -> LogEntry:
    data = (60).to_bytes(32, "big", signed=True) + b"\x00" * 12 + bytes.fromhex(pool[2:])
    topics = (POOL_CREATED_TOPIC, topic_address(token0), topic_address(token1), fee.to_bytes(32, "big"))
    return LogEntry(factory, topics, data, block, "0x" + f"{n:064x}")


def test_scanner_finds_v2_and_v3_listings_against_quote_assets():
    c = FakeEvmClient(latest_block=1_000)
    c.logs += [
        pair_created(UNI_V2, HONEYPOT, WETH, PAIR, 100, 1),  # token0 = new token
        pair_created(UNI_V2, USDC, RUG, "0x" + "ef" * 20, 101, 2),  # token1 = new token
        pair_created(UNI_V2, WETH, USDC, "0x" + "01" * 20, 102, 3),  # quote/quote → ignored
        pair_created(UNI_V2, RANDOM, BENIGN, "0x" + "02" * 20, 103, 4),  # neither is a quote → ignored
        pool_created(UNI_V3, BENIGN, WETH, 3000, "0x" + "03" * 20, 104, 5),
    ]
    pairs = NewPairScanner(c, Chain.ETHEREUM, ETH).scan(0, 1_000)
    assert [(p.token, p.quote_symbol, p.dex) for p in pairs] == [
        (HONEYPOT, "WETH", "uniswap_v2"),
        (RUG, "USDC", "uniswap_v2"),
        (BENIGN, "WETH", "uniswap_v3"),
    ]
    assert pairs[0].pair == PAIR and pairs[2].pair == to_checksum_address("0x" + "03" * 20)
    assert NewPairScanner(c, Chain.ETHEREUM, ETH).scan(105, 1_000) == []


def test_scanner_survives_factory_errors(caplog):
    class Flaky(FakeEvmClient):
        def get_logs(self, address, topics, from_block, to_block):
            if address == UNI_V2:
                raise RuntimeError("rate limited")
            return super().get_logs(address, topics, from_block, to_block)

    c = Flaky(latest_block=10)
    c.logs.append(pool_created(UNI_V3, BENIGN, WETH, 500, PAIR, 5, 1))
    with caplog.at_level("WARNING", logger="rugscan.discover"):
        pairs = NewPairScanner(c, Chain.ETHEREUM, ETH).scan(0, 10)
    assert [p.token for p in pairs] == [BENIGN] and "rate limited" in caplog.text


class FixtureProvider:
    """Serves the offline fixtures by address regardless of chain."""

    name = "fixtures"

    def __init__(self, mock):
        self.mock = mock

    async def supports(self, chain):
        return True

    async def fetch_snapshot(self, chain, address, *, block=None):
        return self.mock.resolve(address).model_copy(deep=True)


def test_firehose_scores_new_listings_and_alerts_above_min_tier(engine, provider):
    c = FakeEvmClient(latest_block=200)
    c.logs += [
        pair_created(UNI_V2, HONEYPOT, WETH, PAIR, 100, 1),
        pair_created(UNI_V2, WETH, BENIGN, "0x" + "ef" * 20, 101, 2),
        pair_created(UNI_V2, RANDOM, WETH, "0x" + "ee" * 20, 102, 3),  # no fixture → fetch error
    ]
    rec = RecordingNotifier()
    with SnapshotCache(":memory:") as cache:
        hose = Firehose(
            FixtureProvider(provider),
            engine,
            NewPairScanner(c, Chain.ETHEREUM, ETH),
            [rec],
            cache=cache,
            min_tier=RiskTier.HIGH,
        )  # type: ignore[arg-type]
        rounds = asyncio.run(hose.run(c, from_block=0))
        assert rounds == 1 and len(hose.reports) == 2
        kinds = [(e.kind, e.address) for e in rec.events]
        assert ("new_token", HONEYPOT) in kinds and ("error", RANDOM) in kinds
        assert all(not (k == "new_token" and a == BENIGN) for k, a in kinds)  # LOW tier → below min_tier, no alert
        alert = next(e for e in rec.events if e.kind == "new_token")
        assert (
            "CRITICAL" in alert.message
            and "uniswap_v2/WETH" in alert.message
            and "HONEYPOT_SELL_BLOCKED" in alert.message
        )
        assert cache.stats()["reports"] == 2 and cache.latest_report("bsc", HONEYPOT) is not None

        # a second run over the same range re-scans nothing: tokens are remembered
        asyncio.run(hose.run(c, from_block=0))
        assert len(hose.reports) == 2


def test_firehose_follow_mode_polls_until_max_rounds(engine, provider):
    c = FakeEvmClient(latest_block=50)
    hose = Firehose(
        FixtureProvider(provider), engine, NewPairScanner(c, Chain.ETHEREUM, ETH), [], max_tokens_per_round=1
    )  # type: ignore[arg-type]
    rounds = asyncio.run(hose.run(c, lookback=10, follow=True, poll_s=0, max_rounds=3))
    assert rounds == 3 and hose.reports == []


def test_firehose_caps_tokens_per_round(engine, provider, caplog):
    c = FakeEvmClient(latest_block=50)
    c.logs += [pair_created(UNI_V2, t, WETH, PAIR, 10 + i, i) for i, t in enumerate((HONEYPOT, RUG, BENIGN), start=1)]
    hose = Firehose(
        FixtureProvider(provider), engine, NewPairScanner(c, Chain.ETHEREUM, ETH), [], max_tokens_per_round=2
    )  # type: ignore[arg-type]
    with caplog.at_level("WARNING", logger="rugscan.discover"):
        reports = asyncio.run(hose.scan_round(0, 50))
    assert len(reports) == 2 and "scanning the first 2" in caplog.text
