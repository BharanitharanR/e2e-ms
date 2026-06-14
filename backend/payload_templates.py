# e2e-marqeta-simulator/backend/payload_templates.py
"""Marqeta-style JIT Funding gateway webhook builders.

Mirrors the payload Marqeta POSTs to a program's JIT Funding gateway (Core API
v3): a `jit_funding` object carrying the funding instruction, plus transaction,
card, user and merchant context. JIT `method` values follow Marqeta's
`pgfs.*` namespace (e.g. pgfs.authorization, pgfs.refund).
"""
import os
from datetime import datetime, timezone


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class MarqetaWebhookPayload:

    @staticmethod
    def _random_token() -> str:
        """Return a random 24-char hex token (Marqeta-style token)."""
        return os.urandom(12).hex()

    @staticmethod
    def _apply_overrides(payload: dict, overrides) -> dict:
        """Apply overrides. Keys may be flat ("state") or dot-paths
        ("transaction.state") for nested fields."""
        if not overrides:
            return payload
        for key, value in overrides.items():
            if "." in key:
                node = payload
                parts = key.split(".")
                for part in parts[:-1]:
                    node = node.setdefault(part, {})
                node[parts[-1]] = value
            else:
                payload[key] = value
        return payload

    @classmethod
    def authorization(cls, request, overrides: dict = None) -> dict:
        card_token = cls._random_token()
        user_token = cls._random_token()
        payload = {
            "event_type": "transaction.authorization",
            "timestamp": _now_iso(),
            "transaction": {
                "id": request.transaction_id,
                "type": "authorization",
                "state": "PENDING",
                "amount": request.amount,
                "currency": "USD",
                "card_token": card_token,
                "card_holder_model": {"token": user_token},
                "merchant": {
                    "name": request.merchant_name,
                    "city": request.merchant_city,
                    "state": request.merchant_state or "CA",
                    "country": request.merchant_country,
                    "mcc": request.mcc,
                },
                "pos": {
                    "entry_mode": request.pos_entry_mode,
                    "pin_present": False,
                    "terminal_attendance": "ATTENDED",
                    "terminal_id": request.terminal_id,
                },
                "acquiring_institution_id": request.acquiring_institution_id,
                "network": "VISANET",
            },
            "card": {
                "token": card_token,
                "last_four": request.pan[-4:],
                "expiry_month": "12",
                "expiry_year": "2028",
            },
            "user": {"token": user_token, "active": True},
            "jit_funding": {
                "token": cls._random_token(),
                "method": "pgfs.authorization",
                "user_token": user_token,
                "acting_user_token": user_token,
                "amount": request.amount,
                "currency_code": "USD",
            },
        }
        return cls._apply_overrides(payload, overrides)

    @classmethod
    def advice(cls, request, original_transaction_id: str,
               advice_type: str = "CLEARING", overrides: dict = None) -> dict:
        """Clearing advice. NOTE: required `original_transaction_id` comes before
        the defaulted args (the original YAML signature was invalid Python)."""
        payload = cls.authorization(request)
        payload["event_type"] = "transaction.authorization.clearing"
        payload["transaction"]["type"] = "authorization.clearing"
        payload["transaction"]["state"] = "CLEARED"
        payload["transaction"]["advice_type"] = advice_type
        payload["transaction"]["original_transaction_token"] = original_transaction_id
        payload["jit_funding"]["method"] = "pgfs.authorization.advice"
        return cls._apply_overrides(payload, overrides)

    @classmethod
    def refund(cls, request, original_transaction_id: str, overrides: dict = None) -> dict:
        payload = cls.authorization(request)
        payload["event_type"] = "transaction.refund"
        payload["transaction"]["type"] = "refund"
        payload["transaction"]["state"] = "COMPLETION_PENDING"
        payload["transaction"]["original_transaction_token"] = original_transaction_id
        payload["transaction"]["refund"] = {"amount": request.amount, "currency": "USD"}
        payload["jit_funding"]["method"] = "pgfs.refund"
        return cls._apply_overrides(payload, overrides)

    @classmethod
    def reversal(cls, request, original_transaction_id: str, overrides: dict = None) -> dict:
        payload = cls.authorization(request)
        payload["event_type"] = "transaction.reversal"
        payload["transaction"]["type"] = "reversal"
        payload["transaction"]["state"] = "PENDING"
        payload["transaction"]["original_transaction_token"] = original_transaction_id
        payload["jit_funding"]["method"] = "pgfs.authorization.reversal"
        return cls._apply_overrides(payload, overrides)
