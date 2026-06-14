# e2e-marqeta-simulator/backend/terminal.py
"""Simulated POS terminal -- the entry point of the payment flow.

In the real world the cardholder taps/dips/swipes at a terminal, which builds the
ISO 8583 authorization message and hands it to the acquirer. Here the Terminal
normalises the request field-set, stamps the ISO trace identifiers (STAN/RRN),
and optionally mints a unique transaction id per run so the demo is repeatable.
"""
import random
from datetime import datetime, timezone


class Terminal:
    @staticmethod
    def _stan() -> str:
        # DE11 - 6-digit System Trace Audit Number
        return f"{random.randint(0, 999999):06d}"

    @staticmethod
    def _rrn() -> str:
        # DE37 - 12-char Retrieval Reference Number (yDDDhhmm + 4 random)
        ts = datetime.now(timezone.utc).strftime("%y%j%H%M")
        return f"{ts}{random.randint(0, 9999):04d}"

    @classmethod
    def swipe(cls, request: dict, unique: bool = True) -> dict:
        """Return a normalised copy of `request` ready for the acquirer.

        unique=True appends a short hex suffix to transaction_id so repeated runs
        do not collide with the customer JIT service's duplicate detection.
        Set unique=False to deliberately replay and trigger DUPLICATE handling.
        """
        req = dict(request)  # never mutate the caller's dict
        req["datetime"] = datetime.now(timezone.utc).isoformat()
        req.setdefault("stan", cls._stan())
        req.setdefault("rrn", cls._rrn())
        req.setdefault("pos_entry_mode", "051")  # 051 = chip card, PIN
        if unique:
            suffix = f"{random.randint(0, 0xFFFFFF):06X}"
            base = req.get("transaction_id", "TXN")
            req["transaction_id"] = f"{base}-{suffix}"
        return req
