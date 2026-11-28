"""Automatic verification of Binance Pay and USDT (TRC20) transaction IDs.

Flow (top-up, then purchases):
  1. The customer pays with Binance Pay (to our Pay ID) or with USDT on
     TRON TRC20 (to our wallet address) and pastes the transaction ID
     (TxID) in the bot instead of sending a receipt photo.
  2. Binance Pay is verified with the signed endpoint
     GET /sapi/v1/pay/transactions (a read-only API key: Enable Reading
     only, never trading or withdrawals).
  3. USDT TRC20 is verified on the public TRON network with TronGrid
     (no key): the transaction receipt must be SUCCESS and contain a
     USDT Transfer event to our wallet for at least the expected amount.
  4. Every TxID is recorded in VerifiedTransaction, so the same TxID can
     never credit the balance twice.

Yemeni wallets (Kuraimi / Jeeb / Mobile Money) have no API, so they keep
the receipt-photo flow. This module never touches them.
"""

import hashlib
import hmac
import os
import re
import time
import urllib.parse
from decimal import Decimal

import aiohttp

BINANCE_BASE_URL = "https://api.binance.com"
BINANCE_PAY_TX_URL = BINANCE_BASE_URL + "/sapi/v1/pay/transactions"
TRONGRID_TX_INFO_URL = "https://api.trongrid.io/walletsolidity/gettransactioninfobyid"

USDT_TRC20_CONTRACT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
# keccak256("Transfer(address,address,uint256)")
_TRANSFER_TOPIC = "ddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
_USDT_DECIMALS = 6

_BINANCE_TXID_RE = re.compile(r"^M_P_\d+$")
_TRON_TXID_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_TOLERANCE = Decimal("0.01")

_B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


class TxVerifyError(Exception):
    """The verification API itself failed (network, auth, bad response)."""


def classify_txid_method(payment_method_name: str) -> str | None:
    """Which automatic verifier (if any) applies to a PaymentMethod row.

    Returns "binance_pay", "tron_usdt" or None (photo receipt stays).
    Matching is by the row's own name, which the owner controls.
    """
    name = (payment_method_name or "").lower()
    if "binance" in name or "بايننس" in name:
        return "binance_pay"
    if "usdt" in name or "trc20" in name or "tron" in name or "كريبتو" in name or "crypto" in name:
        return "tron_usdt"
    return None


def validate_txid_format(kind: str, txid: str) -> bool:
    """Cheap format check before any API call, so typos get a clear answer."""
    txid = (txid or "").strip()
    if kind == "binance_pay":
        return bool(_BINANCE_TXID_RE.match(txid))
    if kind == "tron_usdt":
        return bool(_TRON_TXID_RE.match(txid))
    return False


def base58_to_hex(address: str) -> str:
    """TRON base58check address -> hex (41 + 20 bytes), checksum verified."""
    raw = _base58check_decode(address)
    return raw.hex()


def _base58check_decode(address: str) -> bytes:
    n = 0
    for ch in (address or "").strip():
        if ch not in _B58_ALPHABET:
            raise ValueError("invalid base58 character")
        n = n * 58 + _B58_ALPHABET.index(ch)
    pad = 0
    for ch in (address or "").strip():
        if ch == "1":
            pad += 1
        else:
            break
    body = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    raw = b"\x00" * pad + body
    if len(raw) < 5:
        raise ValueError("address too short")
    payload, checksum = raw[:-4], raw[-4:]
    digest = hashlib.sha256(hashlib.sha256(payload).digest()).digest()
    if digest[:4] != checksum:
        raise ValueError("bad address checksum")
    return payload


async def _default_get(url: str, headers: dict, params: dict) -> dict:
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, headers=headers, params=params) as resp:
            data = await resp.json(content_type=None)
            if resp.status != 200:
                raise TxVerifyError(f"HTTP {resp.status}: {data!r}"[:300])
            return data


async def _default_post(url: str, payload: dict) -> dict:
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, json=payload) as resp:
            data = await resp.json(content_type=None)
            if resp.status != 200:
                raise TxVerifyError(f"HTTP {resp.status}: {data!r}"[:300])
            return data


class BinancePayVerifier:
    """Verify a Binance Pay (C2C) transfer by its transaction ID.

    Uses the account's own read-only API key. The key can only read the
    Pay history; it cannot trade or withdraw anything.
    """

    def __init__(self, api_key: str, api_secret: str, pay_id: str, http_get=None):
        self.api_key = api_key
        self.api_secret = api_secret
        self.pay_id = str(pay_id)
        self.http_get = http_get or _default_get

    def _signed_params(self, params: dict) -> dict:
        query = urllib.parse.urlencode(params)
        signature = hmac.new(
            self.api_secret.encode(), query.encode(), hashlib.sha256
        ).hexdigest()
        signed = dict(params)
        signed["signature"] = signature
        return signed

    async def find_transaction(self, txid: str, days: int = 7, limit: int = 100) -> dict | None:
        """Return the Pay history item with this transactionId, or None."""
        now_ms = int(time.time() * 1000)
        params = {
            "startTime": now_ms - days * 24 * 60 * 60 * 1000,
            "endTime": now_ms,
            "limit": limit,
            "recvWindow": 5000,
            "timestamp": now_ms,
        }
        signed = self._signed_params(params)
        headers = {"X-MBX-APIKEY": self.api_key}
        data = await self.http_get(BINANCE_PAY_TX_URL, headers, signed)
        if isinstance(data, dict) and data.get("code") not in (None, "000000"):
            raise TxVerifyError(f"Binance error {data.get('code')}: {data.get('message')}")
        items = data.get("data", []) if isinstance(data, dict) else []
        txid = (txid or "").strip()
        for item in items:
            if str(item.get("transactionId", "")).strip() == txid:
                return item
        return None

    def _usdt_amount(self, item: dict) -> Decimal | None:
        """USDT actually received in this Pay transaction, if any."""
        total = Decimal("0")
        found = False
        for entry in item.get("fundsDetail") or []:
            if str(entry.get("currency", "")).upper() == "USDT":
                try:
                    total += Decimal(str(entry.get("amount")))
                    found = True
                except Exception:
                    continue
        if found:
            return total
        if str(item.get("currency", "")).upper() == "USDT":
            try:
                return Decimal(str(item.get("amount")))
            except Exception:
                return None
        return None

    async def verify(self, txid: str, expected_usd: Decimal):
        """Return (ok, received_usd_or_None, reason).

        Reasons: ok | not_found | wrong_type | wrong_receiver | amount_low.
        Raises TxVerifyError when Binance itself cannot be reached.
        """
        item = await self.find_transaction(txid)
        if item is None:
            return False, None, "not_found"
        if str(item.get("orderType", "")) != "C2C":
            return False, None, "wrong_type"
        receiver = (item.get("receiverInfo") or {}).get("accountId")
        if str(receiver) != self.pay_id:
            return False, None, "wrong_receiver"
        received = self._usdt_amount(item)
        if received is None:
            return False, None, "wrong_receiver"
        if received < Decimal(str(expected_usd)) - _TOLERANCE:
            return False, received, "amount_low"
        return True, received, "ok"


class TronUsdtVerifier:
    """Verify a USDT TRC20 transfer by its transaction hash on TRON."""

    def __init__(self, wallet_address: str, http_post=None):
        self.wallet_address = wallet_address
        self.http_post = http_post or _default_post

    async def verify(self, txid: str, expected_usd: Decimal):
        """Return (ok, received_usd_or_None, reason).

        Reasons: ok | not_found | tx_failed | wrong_receiver | amount_low.
        Raises TxVerifyError when TronGrid cannot be reached.
        """
        txid = (txid or "").strip()
        data = await self.http_post(TRONGRID_TX_INFO_URL, {"value": txid})
        if not isinstance(data, dict) or not data:
            return False, None, "not_found"
        receipt = data.get("receipt") or {}
        if receipt.get("result") != "SUCCESS":
            # A failed on-chain transaction exists but paid nobody.
            if data.get("id") or data.get("blockNumber"):
                return False, None, "tx_failed"
            return False, None, "not_found"
        try:
            wallet_hex = base58_to_hex(self.wallet_address)
            usdt_hex = base58_to_hex(USDT_TRC20_CONTRACT)
        except ValueError:
            raise TxVerifyError("bad wallet/contract address")
        received = Decimal("0")
        found = False
        for log in data.get("log") or []:
            if str(log.get("address", "")).lower() != usdt_hex.lower():
                continue
            topics = log.get("topics") or []
            if len(topics) < 3:
                continue
            if str(topics[0]).lower() != _TRANSFER_TOPIC:
                continue
            to_hex = str(topics[2])[-40:].lower()
            if to_hex != wallet_hex[2:].lower():
                continue
            try:
                value = int(str(log.get("data", "0x0")), 16)
            except ValueError:
                continue
            received += Decimal(value) / Decimal(10 ** _USDT_DECIMALS)
            found = True
        if not found:
            return False, None, "wrong_receiver"
        if received < Decimal(str(expected_usd)) - _TOLERANCE:
            return False, received, "amount_low"
        return True, received, "ok"


async def verify_binance_pay_txid(txid: str, expected_usd: Decimal):
    """Verify a Binance Pay TxID using Render env vars.

    Needs BINANCE_API_KEY, BINANCE_API_SECRET and BINANCE_PAY_ID.
    Returns (ok, received_or_None, reason); reason "not_configured"
    when the credentials are missing.
    """
    api_key = os.environ.get("BINANCE_API_KEY") or ""
    api_secret = os.environ.get("BINANCE_API_SECRET") or ""
    pay_id = os.environ.get("BINANCE_PAY_ID") or ""
    if not api_key or not api_secret or not pay_id:
        return False, None, "not_configured"
    verifier = BinancePayVerifier(api_key, api_secret, pay_id)
    return await verifier.verify(txid, expected_usd)


async def verify_tron_usdt_txid(txid: str, expected_usd: Decimal):
    """Verify a USDT TRC20 TxID using the USDT_TRC20_ADDRESS env var.

    TronGrid is public, no key needed. Returns (ok, received_or_None,
    reason); reason "not_configured" when the wallet address is missing.
    """
    wallet = os.environ.get("USDT_TRC20_ADDRESS") or ""
    if not wallet:
        return False, None, "not_configured"
    verifier = TronUsdtVerifier(wallet)
    return await verifier.verify(txid, expected_usd)


def copyable_account_value(method_name: str, account_info: str) -> str | None:
    """The single value the user must copy (Binance Pay ID / TRC20 address).

    Binance: the digits on the "Pay ID:" line. TRC20: the 34-char address
    line starting with "T". Anything else returns None, and the caller keeps
    the whole account_info in one code block.
    """
    kind = classify_txid_method(method_name or "")
    if kind == "binance_pay":
        lines = (account_info or "").splitlines()
        for i, line in enumerate(lines):
            s = line.strip()
            if s.lower().startswith("pay id"):
                value = s.split(":", 1)[-1].strip()
                if value:
                    return value
                # The number sits on the next non-empty line ("Pay ID:" alone).
                for nxt in lines[i + 1:]:
                    ns = nxt.strip()
                    if ns:
                        return ns
                return None
        return None
    if kind == "tron_usdt":
        for line in (account_info or "").splitlines():
            s = line.strip()
            if len(s) == 34 and s.startswith("T"):
                return s
        return None
    return None


def account_info_rest_lines(account_info: str, value: str | None) -> list[str]:
    """account_info lines excluding the copyable value, cleaned for display.

    Drops the line holding the value (and a bare label line above it, such
    as "Pay ID:" when the number was on the next line), drops blank lines,
    and rejoins a "label:" line with the line after it ("الاسم:" +
    "SubWorldStore" -> "الاسم: SubWorldStore").
    """
    if not value:
        return (account_info or "").splitlines()
    lines = (account_info or "").splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        s = lines[i].strip()
        if not s:
            i += 1
            continue
        if value in s:
            if out and out[-1].strip().endswith(":"):
                out.pop()
            i += 1
            continue
        if s.endswith(":") and i + 1 < len(lines) and lines[i + 1].strip() and value not in lines[i + 1]:
            out.append(s + " " + lines[i + 1].strip())
            i += 2
            continue
        out.append(lines[i])
        i += 1
    return out


def payment_method_order_key(method) -> tuple:
    """Sort key for the payment-method list: Binance first, crypto second,
    everything else by id (so the Yemeni wallets keep their order)."""
    kind = classify_txid_method(getattr(method, "name", "") or "")
    if kind == "binance_pay":
        return (0, getattr(method, "id", 0))
    if kind == "tron_usdt":
        return (1, getattr(method, "id", 0))
    return (2, getattr(method, "id", 0))


def account_info_display_lines(method_name: str, account_info: str) -> list[str]:
    """Lines for the account display: each copyable value alone in <code>.

    Binance/Crypto: the single Pay ID / address in one code span, the rest
    plain lines. Wallets: every long number (account number, 9+ digits)
    in its own code span, labels, rates and names plain. Falls back to
    the whole block in one code span when nothing copyable is found.
    """
    value = copyable_account_value(method_name, account_info)
    if value:
        lines = [f"<code>{value}</code>"]
        lines.extend(account_info_rest_lines(account_info, value))
        return lines
    rendered = re.sub(r"\d{9,}", lambda m: f"<code>{m.group(0)}</code>", account_info or "")
    if rendered != (account_info or ""):
        return rendered.split("\n")
    return [f"<code>{account_info}</code>"]
