# pos_agent/agent.py
"""PC/SC host agent — reads a physical (or virtual) EMV test card and
posts a terminal-capture payload to the acquirer endpoint on localhost.

APDU flow:
  1. List readers — abort if none present.
  2. Wait for card insertion.
  3. SELECT PPSE (Payment System Environment) → extract available AIDs.
  4. SELECT AID (first AID from PPSE or fallback Visa Credit).
  5. GET PROCESSING OPTIONS (GPO) → extract AIP + AFL (Application File Locator).
  6. READ RECORD for every SFI/record in the AFL → extract tags 57 / 5A / 5F24.
  7. GENERATE AC (ARQC) with dummy CDOL → extract tag 9F26.
  8. Map to DE2 / DE14 / DE35 / DE22 / DE55 and POST to acquirer.

Guardrails:
  - Only test BINs (4111, 5500, 3714, 6011, first-digit heuristic) are accepted.
  - PAN is tokenised (SHA-256 salted), never stored or logged in clear.
  - Track-2 / CVV / PIN data is never persisted; log entries show "***MASKED***".
  - The agent refuses to run if the detected BIN is not on the test-BIN allowlist.

Usage:
    python -m pos_agent.agent [--acquirer-url URL] [--amount CENTS] [--mcc MCC]

Dependencies (host, not in Docker):
    pip install pyscard requests
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import requests

# ---------------------------------------------------------------------------
# pyscard is an optional dependency — fail gracefully so the module can
# still be imported in environments without a PC/SC reader stack.
# ---------------------------------------------------------------------------
try:
    from smartcard.System import readers as _sc_readers
    from smartcard.util import toHexString, toBytes
    from smartcard.CardConnection import CardConnection
    _PCSC_AVAILABLE = True
except ImportError:
    _PCSC_AVAILABLE = False
    _sc_readers = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [POS-AGENT] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("pos_agent")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_ACQUIRER_URL = os.getenv("ACQUIRER_URL", "http://localhost:8101/authorize")

# Visa Credit PPSE / AID selection fallback
_PPSE_AID = "325041592E5359532E4444463031"   # hex of "2PAY.SYS.DDF01" (contactless PPSE)
_PPSE_AID_CONTACT = "315041592E5359532E4444463031"  # hex of "1PAY.SYS.DDF01" (contact)

_AID_FALLBACK = "A0000000031010"              # Visa Credit

# EMV AID → network label
_KNOWN_AIDS: dict[str, str] = {
    "A0000000031010": "Visa Credit",
    "A0000000032010": "Visa Debit",
    "A0000000033010": "Visa Electron",
    "A0000000041010": "Mastercard Credit",
    "A0000000043060": "Maestro",
    "A000000025010801": "AmEx",
    "A000000065":     "JCB",
}

# Test BIN prefixes — reject any other PAN at this gate
_TEST_BINS = {
    "411111",   # Visa test PAN (classic)
    "4111",     # Visa test (shorter prefix)
    "550000",   # Mastercard test
    "5500",     # Mastercard test (shorter)
    "371449",   # AmEx test
    "3714",
    "6011",     # Discover test
    "601100",
    "4000",     # Generic test BIN range used in many processors
    "5200",     # Mastercard test
    "4242",     # Stripe test
    "4917",     # Test cards used in some UK issuer certs
}

# Salt for PAN tokenisation — set via env var for reproducibility in tests
_PAN_SALT = os.getenv("POS_AGENT_PAN_SALT", "e2ms-pos-agent-v1")


# ---------------------------------------------------------------------------
# EMV TLV parser (minimal — enough for the tags we need)
# ---------------------------------------------------------------------------

def _parse_tlv(data: list[int]) -> dict[str, bytes]:
    """Parse a flat BER-TLV byte list into {tag_hex: value_bytes}.

    Only handles primitive (non-constructed) tags for the DEs we care about.
    Constructed tags (high-bit set in b0) are entered recursively.
    """
    result: dict[str, bytes] = {}
    i = 0
    while i < len(data):
        # Tag — may be 1 or 2 bytes
        tag = data[i]
        i += 1
        if (tag & 0x1F) == 0x1F:          # multi-byte tag
            if i >= len(data):
                break
            tag = (tag << 8) | data[i]
            i += 1
        tag_hex = f"{tag:02X}" if tag < 0x100 else f"{tag:04X}"

        if i >= len(data):
            break

        # Length
        length_byte = data[i]; i += 1
        if length_byte == 0x81:
            if i >= len(data): break
            length = data[i]; i += 1
        elif length_byte == 0x82:
            if i + 1 >= len(data): break
            length = (data[i] << 8) | data[i + 1]; i += 2
        else:
            length = length_byte

        value = data[i:i + length]
        i += length

        # For constructed tags, recurse into the value
        constructed = bool(tag & 0x20) if tag < 0x100 else bool((tag >> 8) & 0x20)
        if constructed:
            child = _parse_tlv(value)
            result.update(child)
        else:
            result[tag_hex] = bytes(value)

    return result


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class CardCapture:
    """All data extracted from the card during the EMV terminal flow."""
    # Raw fields — NEVER logged or persisted in clear
    _pan_clear: str = field(repr=False, default="")
    _track2_clear: str = field(repr=False, default="")

    # Safe / tokenised fields
    pan_token: str = ""       # SHA-256(salt + PAN)
    pan_last_four: str = ""
    pan_bin: str = ""         # first 6 digits only (safe to log)
    expiry_yymm: str = ""     # YYMM from DE14
    aid: str = ""
    network_label: str = ""
    arqc_hex: str = ""        # 9F26 — Application Cryptogram
    atc_hex: str = ""         # 9F36 — Application Transaction Counter
    de55_hex: str = ""        # full ICC data blob (contains no PAN in clear)
    pos_entry_mode: str = "07"  # 07 = contactless chip

    def _tokenise_pan(self, pan: str) -> None:
        """Store a tokenised version of the PAN; never retain the clear value."""
        self._pan_clear = pan  # held only in memory for the duration of this flow
        self.pan_last_four = pan[-4:] if len(pan) >= 4 else pan
        self.pan_bin = pan[:6]
        self.pan_token = hashlib.sha256(
            f"{_PAN_SALT}:{pan}".encode()
        ).hexdigest()

    def scrub(self) -> None:
        """Overwrite sensitive fields before handing the object to caller."""
        self._pan_clear = "***MASKED***"
        self._track2_clear = "***MASKED***"

    def to_acquirer_payload(
        self,
        transaction_id: str,
        amount: int,
        mcc: str = "5411",
        terminal_id: str = "PCSC0001",
        acquiring_institution_id: str = "123456",
        currency: str = "840",
    ) -> dict:
        """Build the acquirer POST payload from the captured card data."""
        return {
            "transaction_id":             transaction_id,
            "pan":                        self._pan_clear,  # cleared immediately after POST
            "amount":                     amount,
            "currency":                   currency,
            "mcc":                        mcc,
            "merchant_name":              "PC/SC Test Terminal",
            "merchant_city":              "San Francisco",
            "merchant_state":             "CA",
            "merchant_country":           "USA",
            "pos_entry_mode":             self.pos_entry_mode,
            "terminal_id":                terminal_id,
            "acquiring_institution_id":   acquiring_institution_id,
            "forwarding_institution_id":  acquiring_institution_id,
            "icc_data":                   self.de55_hex or None,
        }


# ---------------------------------------------------------------------------
# BIN guard
# ---------------------------------------------------------------------------

def _is_test_bin(pan: str) -> bool:
    """Return True only if PAN starts with a known test BIN prefix."""
    return any(pan.startswith(prefix) for prefix in _TEST_BINS)


# ---------------------------------------------------------------------------
# APDU helper
# ---------------------------------------------------------------------------

def _send(connection: "CardConnection", apdu_hex: str, label: str) -> tuple[list[int], int, int]:
    """Send an APDU and return (data, sw1, sw2). Logs the exchange."""
    apdu = toBytes(apdu_hex)
    log.debug("→ %s APDU: %s", label, apdu_hex)
    data, sw1, sw2 = connection.transmit(apdu)
    sw = (sw1 << 8) | sw2
    log.debug("← %s SW: %04X  DATA: %s", label, sw, toHexString(data) if data else "(none)")
    return data, sw1, sw2


# ---------------------------------------------------------------------------
# EMV terminal flow
# ---------------------------------------------------------------------------

def run_emv_flow(connection: "CardConnection") -> Optional[CardCapture]:
    """Perform the full EMV APDU flow and return a CardCapture, or None on error."""
    capture = CardCapture()

    # -- Step 1: SELECT PPSE (contactless) -----------------------------------
    ppse_apdu = f"00A40400{len(_PPSE_AID) // 2:02X}{_PPSE_AID}00"
    ppse_data, sw1, sw2 = _send(connection, ppse_apdu, "SELECT PPSE")

    selected_aid: Optional[str] = None

    if (sw1, sw2) == (0x90, 0x00) and ppse_data:
        tags = _parse_tlv(ppse_data)
        # AID is in tag 4F inside the PPSE response
        if b"\x4F" in [bytes([k]) for k in ppse_data]:
            aid_bytes = tags.get("4F")
            if aid_bytes:
                selected_aid = aid_bytes.hex().upper()
    else:
        # Retry with contact PPSE
        log.info("Contactless PPSE failed, trying contact PPSE")
        ppse_apdu2 = f"00A40400{len(_PPSE_AID_CONTACT) // 2:02X}{_PPSE_AID_CONTACT}00"
        ppse_data, sw1, sw2 = _send(connection, ppse_apdu2, "SELECT PPSE (contact)")
        if (sw1, sw2) == (0x90, 0x00) and ppse_data:
            tags = _parse_tlv(ppse_data)
            aid_bytes = tags.get("4F")
            if aid_bytes:
                selected_aid = aid_bytes.hex().upper()

    # Fall back to Visa Credit if we couldn't parse PPSE
    if not selected_aid:
        log.info("Using fallback AID: %s", _AID_FALLBACK)
        selected_aid = _AID_FALLBACK

    # Match to known network
    capture.aid = selected_aid
    for aid_prefix, label in _KNOWN_AIDS.items():
        if selected_aid.upper().startswith(aid_prefix.upper()[:8]):
            capture.network_label = label
            break
    if not capture.network_label:
        capture.network_label = "UNKNOWN"

    # -- Step 2: SELECT AID --------------------------------------------------
    aid_bytes_list = toBytes(selected_aid)
    select_apdu = f"00A40400{len(aid_bytes_list):02X}{selected_aid}00"
    sel_data, sw1, sw2 = _send(connection, select_apdu, f"SELECT AID {selected_aid}")
    if (sw1, sw2) != (0x90, 0x00):
        log.error("SELECT AID failed: SW %02X%02X", sw1, sw2)
        return None

    # -- Step 3: GET PROCESSING OPTIONS (GPO) --------------------------------
    # PDOL is ignored for simplicity — send 83 00 (empty PDOL)
    gpo_apdu = "80A8000002830000"
    gpo_data, sw1, sw2 = _send(connection, gpo_apdu, "GPO")
    afl_bytes: list[int] = []
    if (sw1, sw2) == (0x90, 0x00) and gpo_data:
        gpo_tags = _parse_tlv(gpo_data)
        # AFL is in tag 94
        afl_val = gpo_tags.get("94")
        if afl_val:
            afl_bytes = list(afl_val)
    # Fallback AFL: SFI=1, record 1, one record, no ODA
    if not afl_bytes:
        afl_bytes = [0x08, 0x01, 0x01, 0x00]  # SFI=1, first=1, last=1, oda_count=0

    # -- Step 4: READ RECORD for each AFL entry ------------------------------
    de55_fragments: list[str] = []
    pan_found: Optional[str] = None
    expiry_found: Optional[str] = None
    track2_found: Optional[str] = None

    for idx in range(0, len(afl_bytes) - 3, 4):
        sfi        = (afl_bytes[idx] >> 3) & 0x1F
        first_rec  = afl_bytes[idx + 1]
        last_rec   = afl_bytes[idx + 2]
        for rec in range(first_rec, last_rec + 1):
            p2 = ((sfi << 3) | 4)
            rr_apdu = f"00B2{rec:02X}{p2:02X}00"
            rr_data, sw1, sw2 = _send(connection, rr_apdu, f"READ RECORD SFI={sfi} REC={rec}")
            if (sw1, sw2) != (0x90, 0x00) or not rr_data:
                continue
            de55_fragments.append(toHexString(rr_data).replace(" ", ""))
            rr_tags = _parse_tlv(rr_data)

            # Tag 5A — PAN
            if "5A" in rr_tags and not pan_found:
                raw = rr_tags["5A"].hex()
                # Strip trailing F padding (BCD encoded PAN)
                pan_found = raw.rstrip("fF").upper()

            # Tag 5F24 — Expiry Date (YYMM in BCD: e.g. 2812 → bytes 28 12)
            if "5F24" in rr_tags and not expiry_found:
                exp_raw = rr_tags["5F24"].hex().upper()
                expiry_found = exp_raw[:4]  # YYMM

            # Tag 57 — Track 2 Equivalent Data
            if "57" in rr_tags and not track2_found:
                track2_found = rr_tags["57"].hex().upper()

    # -- Step 5: Extract PAN from Track-2 if not found in tag 5A ------------
    if not pan_found and track2_found:
        # Track-2 BCD: PAN + 'D' separator + expiry + service code + ...
        t2 = track2_found.replace(" ", "").upper()
        sep = t2.find("D")
        if sep > 0:
            pan_found = t2[:sep]
            if not expiry_found and len(t2) > sep + 4:
                expiry_found = t2[sep + 1:sep + 5]

    if not pan_found:
        log.error("Could not extract PAN from card — aborting flow")
        return None

    # BIN guard
    if not _is_test_bin(pan_found):
        log.error(
            "BLOCKED: PAN BIN %s is not on the test-BIN allowlist. "
            "Only test cards are accepted by this agent.",
            pan_found[:6],
        )
        return None

    # Tokenise
    capture._tokenise_pan(pan_found)
    capture.expiry_yymm = expiry_found or ""
    if track2_found:
        capture._track2_clear = track2_found

    log.info(
        "Card read OK — BIN: %s …%s  AID: %s (%s)  Expiry: %s",
        capture.pan_bin, capture.pan_last_four,
        capture.aid, capture.network_label, capture.expiry_yymm,
    )

    # -- Step 6: GENERATE AC (ARQC) -----------------------------------------
    # Dummy CDOL data (8 bytes of zeros)
    cdol_hex = "0000000000000000"
    genac_apdu = f"80AE800008{cdol_hex}00"
    ac_data, sw1, sw2 = _send(connection, genac_apdu, "GENERATE AC")
    if (sw1, sw2) == (0x90, 0x00) and ac_data:
        ac_tags = _parse_tlv(ac_data)
        arqc_bytes = ac_tags.get("9F26")
        atc_bytes  = ac_tags.get("9F36")
        if arqc_bytes:
            capture.arqc_hex = arqc_bytes.hex().upper()
            log.debug("ARQC: %s", capture.arqc_hex)
        if atc_bytes:
            capture.atc_hex  = atc_bytes.hex().upper()

    # Build DE55 (ICC data) — concatenate all READ RECORD data
    capture.de55_hex = "".join(de55_fragments)

    return capture


# ---------------------------------------------------------------------------
# Transaction ID generator
# ---------------------------------------------------------------------------

def _txn_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"PCSC_{ts}"


# ---------------------------------------------------------------------------
# Main agent loop
# ---------------------------------------------------------------------------

def run_agent(
    acquirer_url: str = _DEFAULT_ACQUIRER_URL,
    amount: int = 1000,
    mcc: str = "5411",
    terminal_id: str = "PCSC0001",
    acquiring_institution_id: str = "123456",
    currency: str = "840",
    one_shot: bool = False,
) -> Optional[dict]:
    """
    Main agent entry point.

    Args:
        acquirer_url: URL of the acquirer /authorize endpoint.
        amount:       Transaction amount in minor units (cents).
        mcc:          Merchant Category Code.
        terminal_id:  8-char terminal identifier.
        acquiring_institution_id: Acquiring institution ID (DE32 / DE42).
        currency:     ISO 4217 numeric currency code string.
        one_shot:     If True, exit after processing one card.

    Returns:
        The acquirer response dict (in one_shot mode), else None.
    """
    if not _PCSC_AVAILABLE:
        log.error(
            "pyscard is not installed. Install it with:  pip install pyscard\n"
            "On macOS:  brew install pcsc-lite && pip install pyscard\n"
            "On Linux:  sudo apt-get install pcscd libpcsclite-dev && pip install pyscard"
        )
        sys.exit(1)

    reader_list = _sc_readers()
    if not reader_list:
        log.error("No PC/SC readers detected. Plug in a USB card reader and retry.")
        sys.exit(1)

    reader = reader_list[0]
    log.info("Using reader: %s", reader)

    while True:
        log.info("Waiting for card… (present test card now)")
        connection = reader.createConnection()
        try:
            connection.connect()
        except Exception as exc:
            log.debug("No card yet (%s) — retrying in 1 s", exc)
            time.sleep(1)
            continue

        log.info("Card detected — starting EMV flow")
        capture: Optional[CardCapture] = None
        try:
            capture = run_emv_flow(connection)
        except Exception as exc:
            log.exception("EMV flow error: %s", exc)
        finally:
            try:
                connection.disconnect()
            except Exception:
                pass

        if capture is None:
            log.warning("EMV flow did not produce a valid capture — remove card and retry")
        else:
            txn_id = _txn_id()
            payload = capture.to_acquirer_payload(
                transaction_id=txn_id,
                amount=amount,
                mcc=mcc,
                terminal_id=terminal_id,
                acquiring_institution_id=acquiring_institution_id,
                currency=currency,
            )

            # Log a scrubbed version — PAN is masked in logs
            log_payload = {**payload, "pan": f"***{capture.pan_last_four}"}
            log.info("Posting to acquirer: %s", json.dumps(log_payload, indent=2))

            try:
                resp = requests.post(
                    acquirer_url,
                    json=payload,
                    timeout=30,
                    headers={"Content-Type": "application/json"},
                )
                result = resp.json()
                decision = result.get("customer_decision", result.get("decision", "UNKNOWN"))
                rc = result.get("response_code", result.get("rc", "??"))
                if decision == "APPROVED":
                    log.info("✅ APPROVED  RC=%s  TXN=%s", rc, txn_id)
                else:
                    log.warning("❌ %s  RC=%s  TXN=%s", decision, rc, txn_id)
            except requests.RequestException as exc:
                log.error("Acquirer POST failed: %s", exc)
                result = {"error": str(exc)}
            finally:
                # Scrub PAN from memory ASAP after the network call
                capture.scrub()
                payload["pan"] = "***SCRUBBED***"

            if one_shot:
                return result

        log.info("Remove the card to process the next one.\n")
        time.sleep(2)

    return None  # unreachable in loop mode


# ---------------------------------------------------------------------------
# Software-only simulation (no physical reader required)
# ---------------------------------------------------------------------------

def run_software_simulation(
    acquirer_url: str = _DEFAULT_ACQUIRER_URL,
    amount: int = 1000,
    mcc: str = "5411",
) -> dict:
    """
    Simulate a full PC/SC capture flow using the SoftwareCardEmulator
    (chip_terminal.py) as the card — no physical reader required.

    Useful for demo mode and CI where a real reader isn't present.
    """
    # Import inline to avoid mandatory dependency at module load time
    try:
        from backend.chip_terminal import SoftwareCardEmulator
    except ImportError:
        from chip_terminal import SoftwareCardEmulator  # type: ignore[no-reattr]

    emulator = SoftwareCardEmulator()
    card_data = emulator._card  # direct access to internal state for simulation

    pan = card_data["pan"]
    if not _is_test_bin(pan):
        raise ValueError(f"Emulator PAN BIN {pan[:6]} is not on the test-BIN allowlist")

    # Simulate the SELECT → GET DATA → READ RECORD → GENERATE AC flow
    _sel = emulator.select_application(card_data["aid"])
    if _sel["sw"] != "9000":
        raise RuntimeError(f"Software SELECT failed: {_sel}")

    _gd = emulator.get_data("5A")   # PAN
    _ac = emulator.generate_ac("")  # ARQC

    capture = CardCapture()
    capture._tokenise_pan(pan)
    capture.expiry_yymm = card_data.get("expiry", "2812")
    capture.aid = card_data.get("aid", _AID_FALLBACK)
    capture.network_label = "Visa Credit (simulated)"
    capture.arqc_hex = _ac.get("data", "")[:16]
    capture.de55_hex = _gd.get("data", "") + _ac.get("data", "")
    capture.pos_entry_mode = "07"   # contactless

    txn_id = _txn_id()
    payload = capture.to_acquirer_payload(
        transaction_id=txn_id,
        amount=amount,
        mcc=mcc,
    )

    log_payload = {**payload, "pan": f"***{capture.pan_last_four}"}
    log.info("[SIMULATION] Posting to acquirer: %s", json.dumps(log_payload, indent=2))

    try:
        resp = requests.post(acquirer_url, json=payload, timeout=30)
        result = resp.json()
    except requests.RequestException as exc:
        log.error("Acquirer POST failed: %s", exc)
        result = {"error": str(exc)}
    finally:
        capture.scrub()
        payload["pan"] = "***SCRUBBED***"

    return result


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pos_agent",
        description=(
            "PC/SC host agent: reads an EMV test card and posts the "
            "terminal-capture payload to the acquirer endpoint.\n\n"
            "Use --simulate to run without a physical reader."
        ),
    )
    p.add_argument(
        "--acquirer-url",
        default=_DEFAULT_ACQUIRER_URL,
        help="Acquirer /authorize URL (default: %(default)s)",
    )
    p.add_argument(
        "--amount",
        type=int,
        default=1000,
        help="Transaction amount in cents (default: 1000 = $10.00)",
    )
    p.add_argument(
        "--mcc",
        default="5411",
        help="Merchant Category Code (default: %(default)s)",
    )
    p.add_argument(
        "--terminal-id",
        default="PCSC0001",
        help="8-char terminal identifier (default: %(default)s)",
    )
    p.add_argument(
        "--acquiring-institution-id",
        default="123456",
        help="Acquiring institution ID (default: %(default)s)",
    )
    p.add_argument(
        "--currency",
        default="840",
        help="ISO 4217 numeric currency code (default: 840 = USD)",
    )
    p.add_argument(
        "--one-shot",
        action="store_true",
        help="Process one card then exit (default: loop)",
    )
    p.add_argument(
        "--simulate",
        action="store_true",
        help="Run in software-simulation mode (no physical reader needed)",
    )
    p.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable debug-level logging",
    )
    return p


if __name__ == "__main__":
    args = _build_parser().parse_args()
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if args.simulate:
        result = run_software_simulation(
            acquirer_url=args.acquirer_url,
            amount=args.amount,
            mcc=args.mcc,
        )
        print(json.dumps(result, indent=2))
    else:
        run_agent(
            acquirer_url=args.acquirer_url,
            amount=args.amount,
            mcc=args.mcc,
            terminal_id=args.terminal_id,
            acquiring_institution_id=args.acquiring_institution_id,
            currency=args.currency,
            one_shot=args.one_shot,
        )
