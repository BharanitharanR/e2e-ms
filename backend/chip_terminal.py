# e2e-marqeta-simulator/backend/chip_terminal.py
"""Software-only NFC/EMV chip card emulator.

Simulates APDU (Application Protocol Data Unit) exchanges as a PC/SC reader
would perform them against a contactless EMV chip card. No physical hardware
is required — the entire card state is maintained in memory.

Status Words (SW1 SW2):
  9000 = Success / No Error
  6A82 = File or Application Not Found
  6982 = Security Status Not Satisfied (select first)
  63Cx = PIN Wrong, x tries remaining (x = 0, 1, 2)
  6983 = Authentication Method Blocked (PIN blocked)
  6700 = Wrong Length
  6D00 = Instruction Not Supported
"""

import os
import hashlib
from typing import Optional


# --------------------------------------------------------------------------- #
# Default personalized card data
# --------------------------------------------------------------------------- #
DEFAULT_CARD_DATA: dict = {
    "aid":               "A0000000031010",   # Visa Credit AID
    "application_label": "VISA CREDIT",
    "pan":               "4111111111111111",
    "pan_sequence":      "01",
    "expiry":            "2812",             # YYMM format
    "service_code":      "201",
    "cvv2":              "737",
    "cardholder_name":   "JOHN DOE",
    # PIN = "1234"  (stored as SHA-256 hash)
    "pin_hash":          hashlib.sha256(b"1234").hexdigest(),
    "pin_tries_remaining": 3,
    "card_status":       "ACTIVE",           # ACTIVE | BLOCKED | EXPIRED
}

# EMV tag number → logical field name
EMV_TAGS: dict = {
    "4F":   "application_identifier",
    "50":   "application_label",
    "57":   "track2_equivalent",
    "5A":   "pan",
    "5F20": "cardholder_name",
    "5F24": "expiry",
    "5F25": "effective_date",
    "5F28": "issuer_country_code",
    "5F2D": "language_preference",
    "5F30": "service_code",
    "84":   "dedicated_file_name",
    "8E":   "cdol1",
    "9F10": "issuer_application_data",
    "9F26": "application_cryptogram",
    "9F27": "cryptogram_information_data",
    "9F36": "atc",
}

# Known AID prefixes → network/application name
KNOWN_AIDS: dict = {
    "A0000000031010": "Visa Credit",
    "A0000000032010": "Visa Debit",
    "A0000000033010": "Visa Electron",
    "A0000000041010": "Mastercard Credit",
    "A0000000043060": "Mastercard Maestro",
    "A000000025010801": "American Express",
    "A000000065":     "JCB",
}


# --------------------------------------------------------------------------- #
# APDU response builder
# --------------------------------------------------------------------------- #
class APDUResponse:
    """Static factory for APDU response dicts."""

    @staticmethod
    def ok(data_hex: str = "") -> dict:
        return {
            "data":   data_hex.upper(),
            "sw1":    "90",
            "sw2":    "00",
            "sw":     "9000",
            "status": "SUCCESS",
        }

    @staticmethod
    def not_found() -> dict:
        return {
            "data":   "",
            "sw1":    "6A",
            "sw2":    "82",
            "sw":     "6A82",
            "status": "FILE_NOT_FOUND",
        }

    @staticmethod
    def security_error() -> dict:
        return {
            "data":   "",
            "sw1":    "69",
            "sw2":    "82",
            "sw":     "6982",
            "status": "SECURITY_STATUS_NOT_SATISFIED",
        }

    @staticmethod
    def wrong_pin(tries_left: int) -> dict:
        sw2 = f"C{tries_left}"
        return {
            "data":   "",
            "sw1":    "63",
            "sw2":    sw2,
            "sw":     f"63{sw2}",
            "status": f"WRONG_PIN_{tries_left}_TRIES_LEFT",
        }

    @staticmethod
    def pin_blocked() -> dict:
        return {
            "data":   "",
            "sw1":    "69",
            "sw2":    "83",
            "sw":     "6983",
            "status": "PIN_BLOCKED",
        }

    @staticmethod
    def not_supported() -> dict:
        return {
            "data":   "",
            "sw1":    "6D",
            "sw2":    "00",
            "sw":     "6D00",
            "status": "INSTRUCTION_NOT_SUPPORTED",
        }


# --------------------------------------------------------------------------- #
# BER-TLV length helper
# --------------------------------------------------------------------------- #
def _tlv_len(byte_count: int) -> str:
    """Encode length as BER-TLV hex string (single-byte for ≤127, 81xx for ≤255)."""
    if byte_count <= 127:
        return f"{byte_count:02X}"
    return f"81{byte_count:02X}"


# --------------------------------------------------------------------------- #
# Software Card Emulator
# --------------------------------------------------------------------------- #
class SoftwareCardEmulator:
    """
    Emulates a contactless EMV chip card (NFC/PC-SC).

    Card state persists in instance variables and survives across multiple
    APDU commands within the same backend session. Call reset_card() to
    return to factory-fresh personalized state.

    All methods return an APDUResponse-style dict:
      {"data": <hex>, "sw1": <hex>, "sw2": <hex>, "sw": <4-hex>, "status": <str>}
    """

    def __init__(self, card_data: Optional[dict] = None):
        self._card: dict = dict(DEFAULT_CARD_DATA)
        if card_data:
            self._card.update(card_data)
        self._selected_aid: Optional[str] = None
        self._atc: int = 0   # Application Transaction Counter
        self._session_pin_verified: bool = False

    # ------------------------------------------------------------------ #
    # Public: state inspection
    # ------------------------------------------------------------------ #
    def get_card_state(self) -> dict:
        """Return safe card state for UI display (PAN is masked)."""
        pan = self._card.get("pan", "")
        if len(pan) >= 8:
            masked = pan[:4] + " **** **** " + pan[-4:]
        else:
            masked = pan
        return {
            "aid":               self._card.get("aid"),
            "application_label": self._card.get("application_label"),
            "pan_masked":        masked,
            "expiry":            self._card.get("expiry"),
            "service_code":      self._card.get("service_code"),
            "cardholder_name":   self._card.get("cardholder_name"),
            "card_status":       self._card.get("card_status"),
            "pin_tries_remaining": self._card.get("pin_tries_remaining"),
            "selected_aid":      self._selected_aid,
            "atc":               self._atc,
        }

    # ------------------------------------------------------------------ #
    # SELECT Application by AID
    # APDU: 00 A4 04 00 [Lc] [AID bytes] 00
    # ------------------------------------------------------------------ #
    def select_application(self, aid: str) -> dict:
        """
        Select an EMV application by its AID.
        Returns FCI (File Control Information) TLV on success.
        """
        if self._card.get("card_status") == "BLOCKED":
            return APDUResponse.security_error()

        aid_upper = aid.upper().replace(" ", "")

        # Partial AID match: card AID must start with requested AID prefix
        card_aid = self._card.get("aid", "").upper()
        # Accept if the submitted AID is a prefix of the card's AID, or exact
        matched = card_aid.startswith(aid_upper[:min(len(aid_upper), 8)])
        if not matched:
            return APDUResponse.not_found()

        # Also verify it's a known network AID family
        known = any(card_aid.startswith(k[:8]) for k in KNOWN_AIDS)
        if not known:
            return APDUResponse.not_found()

        self._selected_aid = card_aid
        self._session_pin_verified = False

        # Build simplified FCI TLV response
        label_bytes = self._card.get("application_label", "VISA").encode("ascii").hex().upper()
        aid_hex = card_aid
        # 84 = DF Name (AID), 50 = Application Label, A5 = Proprietary template, 6F = FCI
        inner_84 = f"84{_tlv_len(len(aid_hex) // 2)}{aid_hex}"
        inner_50 = f"50{_tlv_len(len(label_bytes) // 2)}{label_bytes}"
        a5_body  = inner_50
        inner_a5 = f"A5{_tlv_len(len(a5_body) // 2)}{a5_body}"
        fci_body = inner_84 + inner_a5
        fci      = f"6F{_tlv_len(len(fci_body) // 2)}{fci_body}"
        return APDUResponse.ok(fci)

    # ------------------------------------------------------------------ #
    # GET DATA
    # APDU: 80 CA [P1 = tag_byte1] [P2 = tag_byte2] 00
    # ------------------------------------------------------------------ #
    def get_data(self, tag: str) -> dict:
        """Read a specific EMV data object by its tag number (hex string)."""
        if self._selected_aid is None:
            return APDUResponse.security_error()

        tag_upper = tag.upper().replace(" ", "")
        field_name = EMV_TAGS.get(tag_upper)
        if field_name is None:
            return APDUResponse.not_found()

        if field_name == "pan":
            pan_hex = self._card.get("pan", "").encode("ascii").hex().upper()
            return APDUResponse.ok(f"5A{_tlv_len(len(pan_hex) // 2)}{pan_hex}")

        if field_name == "expiry":
            exp = self._card.get("expiry", "2812")
            exp_hex = exp.encode("ascii").hex().upper()
            return APDUResponse.ok(f"5F24{_tlv_len(len(exp_hex) // 2)}{exp_hex}")

        if field_name == "service_code":
            svc_hex = self._card.get("service_code", "201").encode("ascii").hex().upper()
            return APDUResponse.ok(f"5F30{_tlv_len(len(svc_hex) // 2)}{svc_hex}")

        if field_name == "cardholder_name":
            name_hex = self._card.get("cardholder_name", "").encode("ascii").hex().upper()
            return APDUResponse.ok(f"5F20{_tlv_len(len(name_hex) // 2)}{name_hex}")

        if field_name == "atc":
            atc_hex = f"{self._atc:04X}"
            return APDUResponse.ok(f"9F3602{atc_hex}")

        if field_name in ("application_identifier", "dedicated_file_name"):
            aid_hex = self._selected_aid or self._card.get("aid", "")
            return APDUResponse.ok(f"4F{_tlv_len(len(aid_hex) // 2)}{aid_hex}")

        if field_name == "application_label":
            label_hex = self._card.get("application_label", "").encode("ascii").hex().upper()
            return APDUResponse.ok(f"50{_tlv_len(len(label_hex) // 2)}{label_hex}")

        return APDUResponse.not_found()

    # ------------------------------------------------------------------ #
    # VERIFY PIN
    # APDU: 00 20 00 80 [Lc] [PIN block]
    # ------------------------------------------------------------------ #
    def verify_pin(self, pin: str) -> dict:
        """
        Verify offline PIN. Accepts plaintext PIN (simulated).
        Decrements tries on failure; blocks card at 0 tries.
        """
        if self._selected_aid is None:
            return APDUResponse.security_error()

        if self._card.get("card_status") == "BLOCKED":
            return APDUResponse.pin_blocked()

        tries = self._card.get("pin_tries_remaining", 0)
        if tries <= 0:
            self._card["card_status"] = "BLOCKED"
            return APDUResponse.pin_blocked()

        submitted_hash = hashlib.sha256(pin.encode()).hexdigest()
        if submitted_hash == self._card.get("pin_hash"):
            self._card["pin_tries_remaining"] = 3   # reset counter on success
            self._session_pin_verified = True
            return APDUResponse.ok()

        # Wrong PIN
        self._card["pin_tries_remaining"] = tries - 1
        remaining = self._card["pin_tries_remaining"]
        if remaining <= 0:
            self._card["card_status"] = "BLOCKED"
            return APDUResponse.pin_blocked()
        return APDUResponse.wrong_pin(remaining)

    # ------------------------------------------------------------------ #
    # READ RECORD
    # APDU: 00 B2 [RecordNum] [SFI<<3 | 4] 00
    # ------------------------------------------------------------------ #
    def read_record(self, sfi: int, record_num: int) -> dict:
        """
        Read an EMV application record.
        SFI=1, Record=1 → Application Data (PAN, expiry, service code).
        """
        if self._selected_aid is None:
            return APDUResponse.security_error()

        if sfi == 1 and record_num == 1:
            pan_hex  = self._card.get("pan", "").encode("ascii").hex().upper()
            exp_hex  = self._card.get("expiry", "2812").encode("ascii").hex().upper()
            svc_hex  = self._card.get("service_code", "201").encode("ascii").hex().upper()
            # Construct simplified EMV record under tag 70 (Record Template)
            elem_5a   = f"5A{_tlv_len(len(pan_hex) // 2)}{pan_hex}"
            elem_5f24 = f"5F24{_tlv_len(len(exp_hex) // 2)}{exp_hex}"
            elem_5f30 = f"5F30{_tlv_len(len(svc_hex) // 2)}{svc_hex}"
            record_body = elem_5a + elem_5f24 + elem_5f30
            record = f"70{_tlv_len(len(record_body) // 2)}{record_body}"
            return APDUResponse.ok(record)

        return APDUResponse.not_found()

    # ------------------------------------------------------------------ #
    # GENERATE AC (Application Cryptogram)
    # APDU: 80 AE [RefCtrl] 00 [Lc] [CDOL data] 00
    # RefCtrl: 80=ARQC (online auth), 40=TC (offline approve), 00=AAC (decline)
    # ------------------------------------------------------------------ #
    def generate_ac(self, cdol_data: str = "") -> dict:
        """
        Generate Application Cryptogram (simplified ARQC — not cryptographically valid).
        Increments the Application Transaction Counter (ATC).
        """
        if self._selected_aid is None:
            return APDUResponse.security_error()

        self._atc += 1
        atc_hex = f"{self._atc:04X}"

        # Pseudo-ARQC: SHA-256 of card identity data + ATC + CDOL
        seed = (
            self._card.get("pan", "")
            + self._card.get("expiry", "")
            + atc_hex
            + cdol_data
        )
        arqc = hashlib.sha256(seed.encode()).hexdigest()[:16].upper()
        iad  = os.urandom(18).hex().upper()

        # Build simplified response: 77 (Response Message Template Format 2)
        elem_9f27 = "9F270180"          # CID: ARQC
        elem_9f36 = f"9F3602{atc_hex}"  # ATC
        elem_9f26 = f"9F2608{arqc}"     # Application Cryptogram (8 bytes / 16 hex)
        elem_9f10 = f"9F1012{iad}"      # Issuer Application Data (18 bytes / 36 hex)
        body = elem_9f27 + elem_9f36 + elem_9f26 + elem_9f10
        resp_data = f"77{_tlv_len(len(body) // 2)}{body}"
        return APDUResponse.ok(resp_data)

    # ------------------------------------------------------------------ #
    # PUT DATA (issuer script command)
    # APDU: 04 DA [P1] [P2] [Lc] [data]
    # ------------------------------------------------------------------ #
    def put_data(self, tag: str, value_hex: str) -> dict:
        """
        Issuer script / personalisation command.
        Supported tags:
          5F24  = update expiry date (YYMM hex)
          5F20  = update cardholder name (ASCII hex)
          PINCHG = change PIN (plaintext ASCII hex of new PIN)
        """
        if self._selected_aid is None:
            return APDUResponse.security_error()

        tag_upper = tag.upper()

        if tag_upper == "5F24":
            # Expiry update: value_hex is already in hex (e.g. "3238313200" for "2812\x00")
            # Accept raw YYMM string or hex
            try:
                decoded = bytes.fromhex(value_hex).decode("ascii").strip("\x00")
                self._card["expiry"] = decoded if len(decoded) == 4 else value_hex[:4]
            except Exception:
                self._card["expiry"] = value_hex[:4]
            return APDUResponse.ok()

        if tag_upper == "5F20":
            try:
                self._card["cardholder_name"] = bytes.fromhex(value_hex).decode("ascii").strip()
            except Exception:
                self._card["cardholder_name"] = value_hex
            return APDUResponse.ok()

        if tag_upper == "PINCHG":
            try:
                new_pin = bytes.fromhex(value_hex).decode("ascii")
            except Exception:
                new_pin = value_hex
            self._card["pin_hash"] = hashlib.sha256(new_pin.encode()).hexdigest()
            self._card["pin_tries_remaining"] = 3
            return APDUResponse.ok()

        return APDUResponse.not_supported()

    # ------------------------------------------------------------------ #
    # RESET CARD
    # ------------------------------------------------------------------ #
    def reset_card(self) -> None:
        """Return card to factory-fresh personalized state."""
        self._card = dict(DEFAULT_CARD_DATA)
        self._selected_aid = None
        self._atc = 0
        self._session_pin_verified = False
