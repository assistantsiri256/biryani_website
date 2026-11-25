"""Minimal BNB Smart Chain (BSC) JSON-RPC client to verify USDT (BEP-20) transfers.

No third-party packages: plain JSON-RPC over HTTPS to settings.BSC_RPC_URL.
"""
import json
import re
import urllib.error
import urllib.request
from decimal import Decimal

from django.conf import settings

TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
TXID_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")
ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
USDT_DECIMALS = 18  # USDT on BSC uses 18 decimals


class BscError(Exception):
    """The transaction is invalid for this deposit (wrong token, address, amount...)."""


class BscPending(Exception):
    """Not final yet (not found / not enough confirmations). The member can retry shortly."""


class BscUnavailable(Exception):
    """The RPC node could not be reached."""


def _rpc(method, params):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(
        settings.BSC_RPC_URL, data=body, headers={"Content-Type": "application/json", "User-Agent": "deposit-verifier/1.0"}
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        raise BscUnavailable(str(e))
    if data.get("error"):
        raise BscUnavailable(str(data["error"]))
    return data.get("result")


def to_units(usdt: Decimal) -> int:
    return int((usdt * (Decimal(10) ** USDT_DECIMALS)).to_integral_exact())


def _topic_address(topic: str) -> str:
    return "0x" + topic[-40:].lower()


def verify_usdt_transfer(txid: str, to_address: str, expected_usdt: Decimal, not_before: int) -> dict:
    """Check ``txid`` is a confirmed USDT transfer of exactly ``expected_usdt`` to ``to_address``.

    ``not_before``: unix time the payment request was created; older transfers are rejected
    so nobody can claim a payment made before their QR existed.
    Returns {"from": sender, "block": n, "timestamp": t} or raises.
    """
    if not TXID_RE.match(txid or ""):
        raise BscError("Enter a valid TxID: 0x followed by 64 characters.")
    receipt = _rpc("eth_getTransactionReceipt", [txid])
    if receipt is None:
        raise BscPending("Transaction not found yet. Wait a minute and press Verify again.")
    if receipt.get("status") != "0x1":
        raise BscError("This transaction failed on the blockchain.")

    contract = settings.USDT_BEP20_CONTRACT.lower()
    target = to_address.lower()
    expected = to_units(expected_usdt)
    sender, wrong_amount = None, None
    for log in receipt.get("logs", []):
        topics = log.get("topics", [])
        if (log.get("address", "").lower() != contract or len(topics) != 3 or topics[0].lower() != TRANSFER_TOPIC
                or _topic_address(topics[2]) != target):
            continue
        value = int(log.get("data", "0x0"), 16)
        if value == expected:
            sender = _topic_address(topics[1])
            break
        wrong_amount = value
    if sender is None:
        if wrong_amount is not None:
            got = Decimal(wrong_amount) / (Decimal(10) ** USDT_DECIMALS)
            raise BscError(f"Amount mismatch: received {got.normalize()} USDT but this deposit needs exactly {expected_usdt} USDT.")
        raise BscError("This transaction is not a USDT (BEP-20) transfer to our deposit address.")

    block = int(receipt["blockNumber"], 16)
    head = int(_rpc("eth_blockNumber", []), 16)
    confirmations = head - block + 1
    if confirmations < settings.BSC_MIN_CONFIRMATIONS:
        raise BscPending(f"Waiting for confirmations ({confirmations}/{settings.BSC_MIN_CONFIRMATIONS}). Try again in a few seconds.")
    blk = _rpc("eth_getBlockByNumber", [receipt["blockNumber"], False]) or {}
    ts = int(blk.get("timestamp", "0x0"), 16)
    if ts < not_before - 120:
        raise BscError("This transaction was made before this deposit QR was created.")
    return {"from": sender, "block": block, "timestamp": ts}
