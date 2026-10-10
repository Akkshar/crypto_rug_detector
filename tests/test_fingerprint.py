from __future__ import annotations

from datetime import datetime, timezone

from eth_utils import function_signature_to_4byte_selector, to_checksum_address

from src.heuristics.contract_inspector import ContractInspector
from src.heuristics.holder_metrics import HolderMetrics
from src.ingestion.evm.deployer import find_insider_cluster
from src.ingestion.evm.explorer import ExplorerTx, FakeExplorer
from src.ingestion.evm.fingerprint import extract_selectors, fingerprint, jaccard, strip_metadata
from src.models.risk_report import BytecodeMatch, Chain, Holder
from src.storage import SnapshotCache

A = to_checksum_address("0x" + "a1" * 20)
B = to_checksum_address("0x" + "b2" * 20)
C = to_checksum_address("0x" + "c3" * 20)
DEPLOYER = to_checksum_address("0x" + "dd" * 20)


def sel(sig: str) -> bytes:
    return function_signature_to_4byte_selector(sig)


def dispatcher(*sigs: str) -> bytes:
    """Fake runtime code: PUSH4 <selector> EQ … for each signature, plus a PUSH32 of junk that contains selector-like bytes."""
    code = b"\x60\x80\x60\x40"
    for s in sigs:
        code += b"\x63" + sel(s) + b"\x14"  # PUSH4 sel EQ
    code += b"\x7f" + sel("transfer(address,uint256)") * 8  # PUSH32 immediate must NOT be read as a selector
    return code


ERC20 = (
    "name()",
    "symbol()",
    "totalSupply()",
    "balanceOf(address)",
    "transfer(address,uint256)",
    "approve(address,uint256)",
)
RUG_EXTRAS = ("setSellFee(uint256)", "setBots(address[],bool)", "openTrading()", "_rebalanceSupply(address,uint256)")


def test_selector_extraction_skips_push_immediates():
    code = dispatcher(*ERC20)
    found = extract_selectors(code)
    assert found == frozenset("0x" + sel(s).hex() for s in ERC20)
    assert "0x" + sel("transfer(address,uint256)").hex() in found  # from the real PUSH4, once


def test_metadata_stripping_changes_nothing_but_the_trailer():
    body = dispatcher(*ERC20)
    trailer = b"\xa2\x64ipfs\x58\x22" + b"\x12" * 34 + b"\x64solc\x43\x00\x08\x13"
    code = body + trailer + len(trailer).to_bytes(2, "big")
    assert strip_metadata(code) == body
    assert strip_metadata(body) == body  # no trailer → untouched
    different_ipfs = body + trailer.replace(b"\x12" * 34, b"\x34" * 34) + len(trailer).to_bytes(2, "big")
    assert fingerprint(code).code_hash == fingerprint(different_ipfs).code_hash


def test_jaccard_similarity():
    rug = fingerprint(dispatcher(*ERC20, *RUG_EXTRAS))
    clone = fingerprint(dispatcher(*ERC20, *RUG_EXTRAS[:3], "setFee(uint256)"))
    clean = fingerprint(dispatcher(*ERC20))
    assert rug.similarity(rug) == 1.0
    assert 0.75 < rug.similarity(clone) < 1.0
    assert rug.similarity(clean) == jaccard(rug.selectors, clean.selectors) == 6 / 10
    assert jaccard(set(), set()) == 0.0


def test_cache_similarity_lookup(benign, rug):
    rug_fp = fingerprint(dispatcher(*ERC20, *RUG_EXTRAS))
    clean_fp = fingerprint(dispatcher(*ERC20))
    rug.contract.code_hash, rug.contract.selectors = rug_fp.code_hash, sorted(rug_fp.selectors)
    benign.contract.code_hash, benign.contract.selectors = clean_fp.code_hash, sorted(clean_fp.selectors)
    with SnapshotCache(":memory:") as cache:
        cache.put_snapshot(rug, "t", label="rug")
        cache.put_snapshot(benign, "t", label="benign")

        exact = cache.similar_contracts(rug_fp.code_hash, rug_fp.selectors)
        assert exact[0]["address"] == rug.address and exact[0]["identical"] and exact[0]["similarity"] == 1.0

        edited = fingerprint(dispatcher(*ERC20, *RUG_EXTRAS[:3], "setFee(uint256)"))
        near = cache.similar_contracts(edited.code_hash, edited.selectors, min_similarity=0.8)
        assert [m["label"] for m in near] == ["rug"] and not near[0]["identical"]

        assert cache.similar_contracts(None, frozenset(), min_similarity=0.5) == []


def test_contract_inspector_bytecode_flags(config, benign):
    inspector = ContractInspector(config)
    benign.contract.bytecode_matches = [
        BytecodeMatch(chain="bsc", address=B, label="rug", similarity=1.0, identical=True)
    ]
    assert "BYTECODE_IDENTICAL_TO_KNOWN_RUG" in {f.code for f in inspector.evaluate(benign).flags}

    benign.contract.bytecode_matches = [BytecodeMatch(chain="bsc", address=B, label="rug", similarity=0.93)]
    result = inspector.evaluate(benign)
    assert "BYTECODE_SIMILAR_TO_KNOWN_RUG" in {f.code for f in result.flags}

    # a benign contract that matches at least as well → shared template, not evidence
    benign.contract.bytecode_matches.append(BytecodeMatch(chain="bsc", address=C, label="benign", similarity=0.95))
    result = inspector.evaluate(benign)
    assert not {f.code for f in result.flags} & {"BYTECODE_SIMILAR_TO_KNOWN_RUG", "BYTECODE_IDENTICAL_TO_KNOWN_RUG"}
    assert any("shared template" in n for n in result.notes)

    benign.contract.bytecode_matches = [BytecodeMatch(chain="bsc", address=B, label="rug", similarity=0.6)]
    assert not {f.code for f in inspector.evaluate(benign).flags} & {"BYTECODE_SIMILAR_TO_KNOWN_RUG"}


def test_find_insider_cluster():
    ex = FakeExplorer()
    t0 = datetime(2026, 10, 1, tzinfo=timezone.utc)
    ex.txs[A] = [ExplorerTx("0x" + "01" * 32, DEPLOYER, A, 10**17, t0, 1)]  # seeded by the deployer
    ex.txs[B] = [ExplorerTx("0x" + "02" * 32, C, B, 10**17, t0, 1)]  # funded by someone else
    ex.txs[C] = [
        ExplorerTx("0x" + "03" * 32, C, DEPLOYER, 10**17, t0, 1),  # outgoing first – not a funding
        ExplorerTx("0x" + "04" * 32, DEPLOYER, C, 10**17, t0, 2),  # later deposit from deployer is not the *first*
    ]
    assert find_insider_cluster(ex, DEPLOYER, [A, B, C, DEPLOYER]) == [A]
    assert find_insider_cluster(ex, DEPLOYER, [A, B], max_lookups=1) == [A]


def test_holder_metrics_insider_cluster_flag(config, rug):
    rug.holders.top_holders = [
        Holder(address=DEPLOYER, share=0.30, label="deployer"),
        Holder(address=A, share=0.12),
        Holder(address=B, share=0.08),
        Holder(address=C, share=0.05, is_contract=True),
    ]
    rug.deployer.funded_wallets = [A, B]
    result = HolderMetrics(config).evaluate(rug)
    flag = next(f for f in result.flags if f.code == "INSIDER_CLUSTER_HOLDINGS")
    assert set(flag.evidence["wallets"]) == {A, B} and flag.evidence["cluster_share"] == 0.2

    rug.deployer.funded_wallets = [B]  # 8 % < 10 % threshold → note only
    result = HolderMetrics(config).evaluate(rug)
    assert "INSIDER_CLUSTER_HOLDINGS" not in {f.code for f in result.flags}
    assert any("deployer-funded" in n for n in result.notes)


def test_live_provider_wires_fingerprint_and_cluster():
    from tests.test_rpc_provider import HOLDER_A, TOKEN, build_chain, build_explorer, make_provider

    chain = build_chain()
    explorer = build_explorer()
    chain.code[TOKEN] = dispatcher(*ERC20, *RUG_EXTRAS)
    t0 = datetime(2026, 10, 1, tzinfo=timezone.utc)
    explorer.txs[HOLDER_A] = [ExplorerTx("0x" + "05" * 32, DEPLOYER, HOLDER_A, 10**17, t0, 3)]

    def similar(code_hash, selectors, *, min_similarity):
        return [{"chain": "bsc", "address": B, "label": "rug", "similarity": 0.97, "identical": False}]

    snap = make_provider(chain, explorer, similarity_lookup=similar).fetch_sync(Chain.ETHEREUM, TOKEN)
    assert snap.contract.code_hash and len(snap.contract.selectors) == len(ERC20) + len(RUG_EXTRAS)
    assert snap.contract.bytecode_matches[0].address == B
    assert snap.deployer.funded_wallets == [HOLDER_A]
