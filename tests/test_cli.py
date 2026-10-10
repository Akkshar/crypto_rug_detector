"""CLI smoke tests via Typer's CliRunner (fully offline)."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from main import app

runner = CliRunner()


def test_analyze_json_output_is_parseable():
    result = runner.invoke(app, ["analyze", "sample_honeypot", "--json", "--quiet"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["risk_tier"] == "CRITICAL" and payload["hard_fail"] is True


def test_analyze_renders_table_and_writes_file(tmp_path: Path):
    out = tmp_path / "report.json"
    result = runner.invoke(app, ["analyze", "0x1111111111111111111111111111111111111111", "-q", "-o", str(out)])
    assert result.exit_code == 0, result.output
    assert "LOW" in result.stdout and "Category breakdown" in result.stdout
    assert json.loads(out.read_text(encoding="utf-8"))["risk_tier"] == "LOW"


def test_analyze_fail_on_threshold():
    assert runner.invoke(app, ["analyze", "sample_unlocked_rug", "-q", "--fail-on", "HIGH"]).exit_code == 1
    assert runner.invoke(app, ["analyze", "sample_benign_token", "-q", "--fail-on", "HIGH"]).exit_code == 0


def test_analyze_unknown_fixture_exits_2():
    result = runner.invoke(app, ["analyze", "does_not_exist", "-q"])
    assert result.exit_code == 2
    assert "no fixture" in result.output


def test_analyze_rpc_without_url_exits_2(monkeypatch):
    from config.settings import get_settings

    get_settings.cache_clear()
    monkeypatch.delenv("RUGSCAN_BASE_RPC_URL", raising=False)
    result = runner.invoke(app, ["analyze", "0x" + "ab" * 20, "-p", "rpc", "-c", "base", "-q"])
    assert result.exit_code == 2 and "RUGSCAN_BASE_RPC_URL" in result.output


def test_analyze_with_cache_records_history_and_diff(tmp_path: Path):
    db = tmp_path / "cache.db"
    first = runner.invoke(app, ["analyze", "sample_benign_token", "-q", "--cache", str(db)])
    assert first.exit_code == 0, first.output
    assert "vs previous scan" not in first.output

    second = runner.invoke(app, ["analyze", "sample_benign_token", "-q", "--cache", str(db), "--max-age", "999999"])
    assert second.exit_code == 0, second.output
    assert "served from cache" in second.output
    assert "vs previous scan" in second.output and "(+0.0)" in second.output

    hist = runner.invoke(
        app, ["history", "0x1111111111111111111111111111111111111111", "-c", "ethereum", "--cache", str(db)]
    )
    assert hist.exit_code == 0, hist.output
    assert "Scan history" in hist.stdout and hist.stdout.count("LOW") >= 2


def test_history_without_cache_or_data(tmp_path: Path, monkeypatch):
    from config.settings import get_settings

    get_settings.cache_clear()
    monkeypatch.delenv("RUGSCAN_CACHE_PATH", raising=False)
    assert runner.invoke(app, ["history", "0x" + "11" * 20]).exit_code == 2
    empty = runner.invoke(app, ["history", "0x" + "11" * 20, "--cache", str(tmp_path / "empty.db")])
    assert empty.exit_code == 1 and "no cached reports" in empty.output


def test_label_backtest_and_export_roundtrip(tmp_path: Path):
    db = tmp_path / "cache.db"
    for name in ("sample_benign_token", "sample_honeypot", "sample_unlocked_rug"):
        assert runner.invoke(app, ["analyze", name, "-q", "--cache", str(db)]).exit_code == 0

    assert (
        runner.invoke(app, ["label", "0x" + "11" * 20, "benign", "-c", "ethereum", "--cache", str(db)]).exit_code == 0
    )
    assert runner.invoke(app, ["label", "0x" + "22" * 20, "rug", "-c", "bsc", "--cache", str(db)]).exit_code == 0
    assert runner.invoke(app, ["label", "0x" + "33" * 20, "rug", "-c", "base", "--cache", str(db)]).exit_code == 0
    assert runner.invoke(app, ["label", "0x" + "33" * 20, "bogus", "-c", "base", "--cache", str(db)]).exit_code == 2
    assert runner.invoke(app, ["label", "0x" + "44" * 20, "rug", "--cache", str(db)]).exit_code == 1

    metrics = tmp_path / "m.json"
    bt = runner.invoke(app, ["backtest", "--cache", str(db), "-o", str(metrics)])
    assert bt.exit_code == 0, bt.output
    assert "AUC" in bt.stdout and json.loads(metrics.read_text(encoding="utf-8"))["n"] == 3

    export = tmp_path / "snaps.jsonl"
    assert runner.invoke(app, ["export-snapshots", str(export), "--cache", str(db), "--labelled-only"]).exit_code == 0
    assert len(export.read_text(encoding="utf-8").splitlines()) == 3
    assert runner.invoke(app, ["backtest", "--jsonl", str(export)]).exit_code == 0
    assert runner.invoke(app, ["backtest", "--cache", str(tmp_path / "empty.db")]).exit_code == 2


def test_watch_mock_runs_fixed_iterations(tmp_path: Path):
    result = runner.invoke(
        app, ["watch", "sample_benign_token", "--iterations", "2", "--interval", "0", "--cache", str(tmp_path / "w.db")]
    )
    assert result.exit_code == 0, result.output
    assert "[initial]" in result.output and "2 check(s) completed" in result.output
    assert runner.invoke(app, ["watch", "nope", "--iterations", "1"]).exit_code == 2


def test_import_corpus_train_and_tune_cli(tmp_path: Path):
    db = tmp_path / "corpus.db"
    csv_file = tmp_path / "corpus.csv"
    csv_file.write_text(
        "chain,address,label\n"
        "ethereum,0x1111111111111111111111111111111111111111,benign\n"
        "bsc,0x2222222222222222222222222222222222222222,rug\n"
        "base,0x3333333333333333333333333333333333333333,rug\n"
        "base,0x3333333333333333333333333333333333333333,maybe\n"
        "ethereum,0x9999999999999999999999999999999999999999,rug\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["import-corpus", str(csv_file), "--cache", str(db)])
    assert result.exit_code == 0, result.output
    assert "imported 3 snapshot(s), 2 failed" in result.stdout

    too_few = runner.invoke(app, ["train", "--cache", str(db), "--model-dir", str(tmp_path / "models")])
    assert too_few.exit_code == 2 and "at least 30" in too_few.output

    tuned = tmp_path / "tuned.yaml"
    result = runner.invoke(app, ["tune", "--cache", str(db), "-o", str(tuned), "--passes", "1"])
    assert result.exit_code == 0, result.output
    assert tuned.exists() and "proposed weights written" in result.stdout
    assert runner.invoke(app, ["backtest", "--cache", str(db), "--weights", str(tuned)]).exit_code == 0

    assert runner.invoke(app, ["import-corpus", str(csv_file)]).exit_code == 2  # no cache
    assert runner.invoke(app, ["tune", "--cache", str(tmp_path / "empty.db")]).exit_code == 2


def test_at_block_is_accepted_by_mock_provider():
    result = runner.invoke(app, ["analyze", "sample_benign_token", "--json", "-q", "--at-block", "123"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["risk_tier"] == "LOW"


def test_list_fixtures_and_weights():
    assert "sample_honeypot" in runner.invoke(app, ["list-fixtures"]).stdout
    weights = runner.invoke(app, ["weights"])
    assert weights.exit_code == 0 and "HONEYPOT_SELL_BLOCKED" in weights.stdout
