from __future__ import annotations

from eth_utils import function_signature_to_4byte_selector

from src.ingestion.evm.capabilities import (
    _has_opcode,
    analyse_abi,
    analyse_source,
    extract_functions,
    merge,
    probe_bytecode,
)

HONEYPOT_SOURCE = """
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

contract SafeMoonElon is Context, IERC20, Ownable {
    mapping(address => uint256) private _balances;
    mapping(address => bool) public isBlacklisted;
    mapping(address => bool) private _whitelisted;
    uint256 public sellFee = 99;
    uint256 public buyFee = 2;
    bool public tradingEnabled;

    /* fee controls */
    function setSellFee(uint256 fee) external onlyOwner {
        sellFee = fee; // no cap!
    }

    function setBuyFee(uint256 fee) external onlyOwner {
        require(fee <= 25, "too high");
        buyFee = fee;
    }

    function setMaxTxAmount(uint256 amount) external onlyOwner { maxTx = amount; }

    // looks like housekeeping, actually prints tokens
    function _rebalanceSupply(address to, uint256 amount) external onlyOwner {
        _totalSupply += amount;
        _balances[to] += amount;
    }

    function burn(uint256 amount) external { _balances[msg.sender] -= amount; }

    function enableTrading() external onlyOwner { tradingEnabled = true; }

    function _transfer(address from, address to, uint256 amount) internal {
        require(!isBlacklisted[from] && !isBlacklisted[to], "bot");
        require(tradingEnabled || _whitelisted[from], "not open");
        if (to == pair) { amount = amount * (100 - sellFee) / 100; }
        _balances[from] -= amount;
        _balances[to] += amount;
    }

    function rescue() external onlyOwner { selfdestruct(payable(owner())); }
}
"""

CLEAN_SOURCE = """
contract Clean is ERC20 {
    constructor() ERC20("Clean", "CLN") { _mint(msg.sender, 1e24); }
    function _transfer(address from, address to, uint256 amount) internal override {
        super._transfer(from, to, amount);
    }
    function burn(uint256 amount) external { _burn(msg.sender, amount); }
}
"""


def test_extract_functions_brace_matching():
    fns = {f.name: f for f in extract_functions(HONEYPOT_SOURCE)}
    assert "setSellFee" in fns and "_transfer" in fns and "rescue" in fns
    assert fns["_transfer"].visibility == "internal"
    assert fns["setSellFee"].restricted_to == "onlyOwner"
    assert "amount * (100 - sellFee)" in fns["_transfer"].body


def test_analyse_source_honeypot():
    caps = analyse_source(HONEYPOT_SOURCE)
    assert caps.source_analysed
    setters = {f.name: f for f in caps.fee_setters}
    assert setters["setSellFee"].upper_bound_pct is None
    assert setters["setBuyFee"].upper_bound_pct == 25.0
    assert setters["setSellFee"].restricted_to == "onlyOwner"
    assert [f.name for f in caps.max_tx_setters] == ["setMaxTxAmount"]

    mints = {f.name: f for f in caps.mint_functions}
    assert "_rebalanceSupply" in mints and mints["_rebalanceSupply"].hidden is True
    assert "burn" not in mints

    assert caps.has_blacklist and caps.has_whitelist_only_transfers and caps.can_pause_trading
    assert caps.has_selfdestruct and not caps.has_delegatecall


def test_analyse_source_clean_token():
    caps = analyse_source(CLEAN_SOURCE)
    assert caps.fee_setters == [] and caps.max_tx_setters == [] and caps.mint_functions == []
    assert not (caps.has_blacklist or caps.has_whitelist_only_transfers or caps.can_pause_trading)
    assert not caps.has_selfdestruct


def test_bound_detection_basis_points():
    src = (
        "contract X { function setFees(uint256 b, uint256 s) external { require(b + s <= 2500); buy = b; sell = s; } }"
    )
    assert analyse_source(src).fee_setters[0].upper_bound_pct == 25.0
    src = "contract X { function setTax(uint256 t) external { if (t > 30) revert TooHigh(); tax = t; } }"
    assert analyse_source(src).fee_setters[0].upper_bound_pct == 30.0


def test_analyse_abi():
    abi = [
        {"type": "function", "name": "setTaxFee", "inputs": [{"type": "uint256"}], "stateMutability": "nonpayable"},
        {
            "type": "function",
            "name": "mint",
            "inputs": [{"type": "address"}, {"type": "uint256"}],
            "stateMutability": "nonpayable",
        },
        {"type": "function", "name": "burnMinted", "inputs": [{"type": "uint256"}], "stateMutability": "nonpayable"},
        {
            "type": "function",
            "name": "setBots",
            "inputs": [{"type": "address[]"}, {"type": "bool"}],
            "stateMutability": "nonpayable",
        },
        {"type": "function", "name": "openTrading", "inputs": [], "stateMutability": "nonpayable"},
        {"type": "function", "name": "balanceOf", "inputs": [{"type": "address"}], "stateMutability": "view"},
    ]
    caps = analyse_abi(abi)
    assert [f.name for f in caps.fee_setters] == ["setTaxFee"]
    assert [f.name for f in caps.mint_functions] == ["mint"]
    assert caps.has_blacklist and caps.can_pause_trading
    assert caps.fee_setters[0].selector == "0x" + function_signature_to_4byte_selector("setTaxFee(uint256)").hex()


def test_probe_bytecode_and_opcode_walk():
    code = (
        b"\x60\x80" + function_signature_to_4byte_selector("setSellFee(uint256)") + b"\x61\xff\xf4"
    )  # PUSH2 0xfff4 = data, not opcodes
    caps = probe_bytecode(code)
    assert [f.name for f in caps.fee_setters] == ["setSellFee"]
    assert not caps.has_selfdestruct and not caps.has_delegatecall  # 0xff / 0xf4 were PUSH immediates
    assert _has_opcode(b"\x60\x01\xf4", 0xF4) and not _has_opcode(b"\x61\xf4\xf4", 0xF4)
    assert probe_bytecode(b"\x00\xff").has_selfdestruct


def test_merge_prefers_source():
    src = analyse_source(HONEYPOT_SOURCE)
    probed = probe_bytecode(function_signature_to_4byte_selector("mint(address,uint256)"))
    merged = merge(src, probed)
    assert [f.name for f in merged.mint_functions] == ["_rebalanceSupply"]  # source wins
    assert merged.has_blacklist and merged.source_analysed
    empty_first = merge(probe_bytecode(b""), probed)
    assert [f.name for f in empty_first.mint_functions] == ["mint"]
