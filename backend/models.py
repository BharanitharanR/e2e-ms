# e2e-marqeta-simulator/backend/models.py
"""Pydantic models for the network messages and scenario schema.

These models describe the ISO 8583-style message that travels acquirer -> issuer,
the response that travels back, and the JSON test-scenario schema. Pydantic v2.
"""
from typing import Optional
from pydantic import BaseModel, Field


class NetworkAuthRequest(BaseModel):
    """Authorisation request flowing from the acquirer to the issuer processor.

    `amount` is in minor units (cents). `currency` is the ISO 4217 *numeric*
    code (840 = USD) as it would appear on the network leg.
    """
    transaction_id: str
    pan: str
    amount: int                      # minor units (cents)
    currency: str = "840"            # ISO 4217 numeric; 840 = USD
    mcc: str = "0000"
    merchant_name: str = ""
    merchant_city: str = ""
    merchant_state: str = ""
    merchant_country: str = "USA"
    pos_entry_mode: str = "000"
    terminal_id: str = ""
    acquiring_institution_id: str = ""
    forwarding_institution_id: str = ""
    datetime: str = ""               # ISO8601 string

    # ISO 8583 trace identifiers stamped by the Terminal layer
    stan: Optional[str] = None       # DE11 - System Trace Audit Number
    rrn: Optional[str] = None        # DE37 - Retrieval Reference Number

    # Routing helpers used for non-authorization events. These are carried in the
    # same request body and consumed by the Marqeta simulator (not part of a real
    # ISO field set, but convenient for this POC).
    event_type: Optional[str] = None
    advice_type: Optional[str] = None
    original_transaction_id: Optional[str] = None

    # Ignore any stray fields rather than 422-ing the whole chain.
    model_config = {"extra": "ignore"}


class NetworkAuthResponse(BaseModel):
    """Response from the issuer processor back through the network."""
    transaction_id: str
    response_code: str                       # "00" approve, "05" decline, etc.
    auth_code: Optional[str] = None
    customer_decision: Optional[str] = None  # added by the Marqeta sim for trace
    customer_status_code: Optional[int] = None
    network: str = "VISANET"
    stan: Optional[str] = None
    rrn: Optional[str] = None


class Scenario(BaseModel):
    """A test scenario loaded from / written to JSON."""
    id: str
    name: str
    description: str = ""
    event_type: str = "authorization"        # authorization | advice | refund | reversal
    request: dict                            # kept as dict for flexibility
    expected_network_response_code: str = "00"
    expected_customer_decision: Optional[str] = None   # e.g. "APPROVED" / "DECLINED"
    webhook_overrides: dict = Field(default_factory=dict)
    original_transaction_id: Optional[str] = None       # advice/refund/reversal only
