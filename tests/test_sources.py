from __future__ import annotations

from pathlib import Path

from eth_utils import to_checksum_address

from src.backtest.sources import GOPLUS_URL, classify, fetch_goplus, weak_labels
from src.models.risk_report import Chain

A = to_checksum_address("0x" + "a1" * 20)
B = to_checksum_address("0x" + "b2" * 20)
C = to_checksum_address("0x" + "c3" * 20)
D = to_checksum_address("0x" + "d4" * 20)

HONEYPOT = {"is_honeypot": "1", "sell_tax": "0.99", "is_open_source": "1"}
CLEAN = {
    "is_honeypot": "0",
    "cannot_sell_all": "0",
    "is_open_source": "1",
    "buy_tax": "0.01",
    "sell_tax": "0.01",
    "lp_holders": [{"address": "0x663a…", "is_locked": "1", "percent": "0.95"}],
}
UNLOCKED = {**CLEAN, "lp_holders": [{"address": "0xdeployer", "is_locked": "0"}]}
MINTABLE = {**CLEAN, "is_mintable": "1"}


def fake_get(responses: dict[str, dict]):
    calls = []

    def get(url: str, params: dict):
        calls.append((url, params))
        wanted = [to_checksum_address(a) for a in params["contract_addresses"].split(",")]
        return {"code": 1, "result": {a.lower(): responses[a] for a in wanted if a in responses}}

    get.calls = calls  # type: ignore[attr-defined]
    return get


def test_classify_rules():
    assert classify(HONEYPOT, benign_if_clean=True) == ("rug", ("is_honeypot", "sell_tax=99%"))
    assert classify({"owner_change_balance": "1"}, benign_if_clean=True)[0] == "rug"
    assert classify(CLEAN, benign_if_clean=True)[0] == "benign"
    assert classify(CLEAN, benign_if_clean=False)[0] is None
    assert classify(UNLOCKED, benign_if_clean=True) == (None, ("lp not locked",))
    assert classify(MINTABLE, benign_if_clean=True) == (None, ("is_mintable",))
    assert classify({**CLEAN, "sell_tax": "0.25"}, benign_if_clean=True)[0] is None
    assert classify({**CLEAN, "is_open_source": "0"}, benign_if_clean=True) == (None, ("not open source",))


def test_fetch_batches_and_tolerates_failures(caplog):
    get = fake_get({A: HONEYPOT, B: CLEAN})
    records = fetch_goplus(Chain.BSC, [A, B, C], get=get, batch=2)
    assert set(records) == {A, B} and len(get.calls) == 2
    assert get.calls[0][0] == GOPLUS_URL.format(chain_id=56)

    def flaky(url, params):
        raise ConnectionError("boom")

    with caplog.at_level("WARNING", logger="rugscan.sources"):
        assert fetch_goplus(Chain.BASE, [A], get=flaky) == {}
    assert "GoPlus batch" in caplog.text


def test_weak_labels_end_to_end():
    labels = {
        w.address: w
        for w in weak_labels(Chain.ETHEREUM, [A, B, C, D], get=fake_get({A: HONEYPOT, B: CLEAN, D: UNLOCKED}))
    }
    assert labels[A].label == "rug" and "is_honeypot" in labels[A].reasons
    assert labels[B].label == "benign"
    assert labels[C].label is None and labels[C].reasons == ("not found at GoPlus",)
    assert labels[D].label is None and labels[D].raw == UNLOCKED


def test_cli_label_from_goplus(tmp_path: Path, monkeypatch):
    from typer.testing import CliRunner

    import src.backtest.sources as sources
    from main import app

    monkeypatch.setattr(sources, "_default_get", fake_get({A: HONEYPOT, B: CLEAN}))
    addresses = tmp_path / "addrs.txt"
    addresses.write_text(f"{A}\n{B},ignored\n{C}\n# comment\n", encoding="utf-8")
    out_csv = tmp_path / "labels.csv"
    result = CliRunner().invoke(
        app, ["label-from-goplus", "-c", "bsc", "--addresses", str(addresses), "-o", str(out_csv)]
    )
    assert result.exit_code == 0, result.output
    assert "1 rug, 1 benign, 1 undecided" in result.stdout
    rows = out_csv.read_text(encoding="utf-8").splitlines()
    assert rows[0] == "chain,address,label" and len(rows) == 3
    assert CliRunner().invoke(app, ["label-from-goplus", "-c", "bsc"]).exit_code == 2


def test_seed_corpus_template_imports(tmp_path: Path):
    from typer.testing import CliRunner

    from main import app

    db = tmp_path / "c.db"
    result = CliRunner().invoke(app, ["import-corpus", "corpus/seed_corpus.csv", "--cache", str(db)])
    assert result.exit_code == 0, result.output
    assert "imported 3 snapshot(s), 0 failed" in result.stdout
