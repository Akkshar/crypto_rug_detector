"""Runtime-bytecode fingerprints for "have we seen this contract before?".

Serial ruggers redeploy the same template with a new name, so two cheap
signals carry a lot of weight:

* **normalised code hash** – keccak of the runtime bytecode with the Solidity
  CBOR metadata trailer stripped (it embeds an IPFS hash that changes with
  every compile even when the code does not).
* **selector set** – the 4-byte function selectors found as ``PUSH4``
  immediates in the dispatcher. Jaccard similarity between selector sets is
  a robust "same template, lightly edited" detector that survives renamed
  variables, changed constants and different compiler versions.
"""

from __future__ import annotations

from dataclasses import dataclass

from eth_utils import keccak

# Opcode ranges
PUSH1, PUSH4, PUSH32 = 0x60, 0x63, 0x7F


@dataclass(frozen=True)
class Fingerprint:
    code_hash: str  # keccak(normalised runtime code), 0x-prefixed
    selectors: frozenset[str]  # "0xabcdef12"
    size: int

    def similarity(self, other: Fingerprint | frozenset[str]) -> float:
        other_sel = other.selectors if isinstance(other, Fingerprint) else other
        return jaccard(self.selectors, other_sel)


def strip_metadata(code: bytes) -> bytes:
    """Drop the CBOR metadata trailer (length encoded in the final two bytes)."""
    if len(code) < 2:
        return code
    length = int.from_bytes(code[-2:], "big")
    if 0 < length < len(code) - 2 and length <= 0x200:
        trailer = code[-(length + 2) : -2]
        # Solidity metadata is a CBOR map starting 0xa1..0xa4 containing "ipfs"/"bzzr"/"solc" keys.
        if trailer[:1] in {b"\xa1", b"\xa2", b"\xa3", b"\xa4"} and any(
            k in trailer for k in (b"ipfs", b"bzzr", b"solc")
        ):
            return code[: -(length + 2)]
    return code


def extract_selectors(code: bytes) -> frozenset[str]:
    """Collect PUSH4 immediates while walking opcodes so bytes inside other PUSH data are not misread."""
    found: set[str] = set()
    i = 0
    n = len(code)
    while i < n:
        op = code[i]
        if PUSH1 <= op <= PUSH32:
            width = op - PUSH1 + 1
            imm = code[i + 1 : i + 1 + width]
            if op == PUSH4 and len(imm) == 4 and imm != b"\xff\xff\xff\xff":
                found.add("0x" + imm.hex())
            i += 1 + width
        else:
            i += 1
    return frozenset(found)


def fingerprint(code: bytes) -> Fingerprint:
    normalised = strip_metadata(code)
    return Fingerprint(
        code_hash="0x" + keccak(normalised).hex(),
        selectors=extract_selectors(normalised),
        size=len(normalised),
    )


def jaccard(a: frozenset[str] | set[str], b: frozenset[str] | set[str]) -> float:
    if not a and not b:
        return 0.0
    return len(set(a) & set(b)) / len(set(a) | set(b))
