"""EtherscanClient parsing against a stubbed ``requests``."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from eth_utils import to_checksum_address

from src.ingestion.evm import explorer as explorer_mod
from src.ingestion.evm.explorer import EtherscanClient

TOKEN = to_checksum_address("0x" + "aa" * 20)
DEPLOYER = to_checksum_address("0x" + "dd" * 20)


class StubResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


class StubRequests:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {}), timeout))
        action = params["action"]
        payload = self.routes[action]
        return StubResponse(payload(params) if callable(payload) else payload)


@pytest.fixture
def stub(monkeypatch):
    routes = {}
    s = StubRequests(routes)
    monkeypatch.setitem(__import__("sys").modules, "requests", s)
    monkeypatch.setattr(explorer_mod.time, "sleep", lambda *_: None)
    return s


def client() -> EtherscanClient:
    return EtherscanClient("https://api.etherscan.io/v2/api", chain_id=1, api_key="KEY", min_interval=0)


def test_get_source_flat_and_standard_json(stub):
    stub.routes["getsourcecode"] = {
        "status": "1",
        "message": "OK",
        "result": [
            {
                "SourceCode": "contract A { function f() external {} }",
                "ABI": json.dumps([{"type": "function", "name": "f", "inputs": [], "stateMutability": "nonpayable"}]),
                "ContractName": "A",
                "CompilerVersion": "v0.8.19",
                "Proxy": "1",
                "Implementation": "0x" + "bb" * 20,
            }
        ],
    }
    src = client().get_source(TOKEN)
    assert src.verified and src.contract_name == "A" and src.compiler_version == "v0.8.19"
    assert src.abi and src.abi[0]["name"] == "f"
    assert src.is_proxy and src.implementation == to_checksum_address("0x" + "bb" * 20)
    _url, params, timeout = stub.calls[-1]
    assert params["chainid"] == 1 and params["apikey"] == "KEY" and params["address"] == TOKEN and timeout == 20.0

    wrapped = json.dumps({"sources": {"A.sol": {"content": "contract A {}"}, "B.sol": {"content": "contract B {}"}}})
    stub.routes["getsourcecode"] = {
        "status": "1",
        "result": [{"SourceCode": "{" + wrapped + "}", "ABI": "", "Proxy": "0"}],
    }
    src = client().get_source(TOKEN)
    assert src.verified and "contract A {}" in src.source and "contract B {}" in src.source and src.abi is None


def test_get_source_unverified(stub):
    stub.routes["getsourcecode"] = {
        "status": "1",
        "result": [{"SourceCode": "", "ABI": "Contract source code not verified", "Proxy": "0"}],
    }
    src = client().get_source(TOKEN)
    assert not src.verified and src.abi is None and src.source is None and not src.is_proxy


def test_contract_creation_and_transactions(stub):
    stub.routes["getcontractcreation"] = {
        "status": "1",
        "result": [{"contractAddress": TOKEN.lower(), "contractCreator": DEPLOYER.lower(), "txHash": "0x" + "01" * 32}],
    }
    assert client().get_contract_creation(TOKEN) == (DEPLOYER, "0x" + "01" * 32)

    stub.routes["txlist"] = lambda p: {
        "status": "1",
        "result": [
            {
                "hash": "0x" + "02" * 32,
                "from": "0x" + "91" * 20,
                "to": DEPLOYER.lower(),
                "value": "1000",
                "timeStamp": "1700000000",
                "blockNumber": "10",
                "isError": "0",
            },
            {
                "hash": "0x" + "03" * 32,
                "from": DEPLOYER.lower(),
                "to": "",
                "value": "0",
                "timeStamp": "1700000100",
                "blockNumber": "11",
                "contractAddress": TOKEN.lower(),
                "isError": "0",
            },
            {
                "hash": "0x" + "04" * 32,
                "from": DEPLOYER.lower(),
                "to": "0x" + "92" * 20,
                "value": "5",
                "timeStamp": "1700000200",
                "blockNumber": "12",
                "isError": "1",
            },
        ][: int(p["offset"])],
    }
    txs = client().transactions(DEPLOYER, ascending=True, limit=3)
    assert [t.block_number for t in txs] == [10, 11, 12]
    assert (
        txs[0].to == DEPLOYER
        and txs[0].value == 1000
        and txs[0].timestamp == datetime.fromtimestamp(1_700_000_000, tz=timezone.utc)
    )
    assert txs[1].is_contract_creation and txs[1].to is None
    assert txs[2].is_error and not txs[2].is_contract_creation
    assert client().transactions(DEPLOYER, limit=1)[0].hash == "0x" + "02" * 32
    assert stub.calls[-1][1]["sort"] == "asc" and stub.calls[-1][1]["offset"] == 1


def test_empty_result_and_errors(stub):
    stub.routes["txlist"] = {"status": "0", "message": "No transactions found", "result": []}
    assert client().transactions(DEPLOYER) == []
    stub.routes["getcontractcreation"] = {"status": "0", "message": "NOTOK", "result": "Max rate limit reached"}
    assert client().get_contract_creation(TOKEN) is None
    stub.routes["getsourcecode"] = {"status": "0", "message": "NOTOK", "result": "Invalid API Key"}
    with pytest.raises(RuntimeError, match="Invalid API Key"):
        client().get_source(TOKEN)
