# e2e-marqeta-simulator/frontend/iso_mapping.py
"""ISO 8583 Data Element → Marqeta JCF (JIT Common Fields) mapping defaults.

These mappings represent how the JPBOS (JPMorgan Banking OS) driven JCF
conversion translates ISO 8583 Data Elements (DEs) into the Marqeta
JIT Funding webhook / JCF fields used by the issuer processor.

The mapping table is editable in the Streamlit UI (session-scoped).
"""

DEFAULT_ISO_JCF_MAPPING = [
    {
        "de":          "DE2",
        "iso_name":    "Primary Account Number (PAN)",
        "jcf_field":   "pan",
        "description": "PAN → Marqeta card_token (tokenised in production)",
        "transform":   "tokenize",
    },
    {
        "de":          "DE4",
        "iso_name":    "Amount, Transaction",
        "jcf_field":   "amount",
        "description": "Transaction amount in minor units (cents/pence)",
        "transform":   "passthrough",
    },
    {
        "de":          "DE7",
        "iso_name":    "Transmission Date & Time",
        "jcf_field":   "datetime",
        "description": "ISO 8601 UTC timestamp of transmission",
        "transform":   "format_iso8601",
    },
    {
        "de":          "DE11",
        "iso_name":    "System Trace Audit Number (STAN)",
        "jcf_field":   "stan",
        "description": "6-digit terminal-assigned audit number (DE11)",
        "transform":   "passthrough",
    },
    {
        "de":          "DE12",
        "iso_name":    "Local Transaction Time",
        "jcf_field":   "local_transaction_time",
        "description": "HHMMSS extracted from transaction datetime",
        "transform":   "extract_time",
    },
    {
        "de":          "DE18",
        "iso_name":    "Merchant Type (MCC)",
        "jcf_field":   "mcc",
        "description": "4-digit ISO 18245 Merchant Category Code",
        "transform":   "passthrough",
    },
    {
        "de":          "DE22",
        "iso_name":    "POS Entry Mode",
        "jcf_field":   "pos_entry_mode",
        "description": "How the card was read: 051=chip+PIN, 011=magnetic stripe, 071=contactless",
        "transform":   "passthrough",
    },
    {
        "de":          "DE37",
        "iso_name":    "Retrieval Reference Number (RRN)",
        "jcf_field":   "rrn",
        "description": "12-character network retrieval reference (yDDDhhmm + 4 digits)",
        "transform":   "passthrough",
    },
    {
        "de":          "DE41",
        "iso_name":    "Card Acceptor Terminal ID",
        "jcf_field":   "terminal_id",
        "description": "8-character terminal identifier assigned by acquirer",
        "transform":   "passthrough",
    },
    {
        "de":          "DE42",
        "iso_name":    "Card Acceptor ID Code",
        "jcf_field":   "acquiring_institution_id",
        "description": "Merchant / acquirer institution identification code",
        "transform":   "passthrough",
    },
    {
        "de":          "DE43",
        "iso_name":    "Card Acceptor Name / Location",
        "jcf_field":   "merchant_name",
        "description": "Merchant name (≤25 chars); city & state packed in same DE in ISO",
        "transform":   "truncate_25",
    },
    {
        "de":          "DE49",
        "iso_name":    "Currency Code, Transaction",
        "jcf_field":   "currency",
        "description": "ISO 4217 numeric code: 840=USD, 978=EUR, 826=GBP, 392=JPY",
        "transform":   "numeric_to_alpha",
    },
    {
        "de":          "DE63",
        "iso_name":    "Network Data",
        "jcf_field":   "network",
        "description": "Network identifier (e.g. VISANET)",
        "transform":   "passthrough",
    },
]


def extract_iso_jcf_values(
    mapping_rows: list,
    request_sent: dict,
    response_received: dict,
) -> list:
    """
    Given live mapping table rows and a transaction trace, produce a
    translation table showing ISO field values alongside JCF field values.

    Returns a list of dicts with keys:
      de, iso_name, iso_value, jcf_field, jcf_value, transform, transformed
    """
    # Flatten all available values (request takes precedence for input fields)
    all_vals: dict = {}
    all_vals.update(response_received or {})
    all_vals.update(request_sent or {})

    result = []
    for row in mapping_rows:
        jcf_field = row.get("jcf_field", "")
        transform  = row.get("transform", "passthrough")

        # ISO value = raw field from the outbound request
        iso_val = (request_sent or {}).get(jcf_field, "")
        # JCF value = same field from combined dict (may be enriched in response)
        jcf_val = all_vals.get(jcf_field, "")

        result.append({
            "de":          row.get("de", ""),
            "iso_name":    row.get("iso_name", ""),
            "iso_value":   str(iso_val) if iso_val not in ("", None) else "(none)",
            "jcf_field":   jcf_field,
            "jcf_value":   str(jcf_val) if jcf_val not in ("", None) else "(none)",
            "transform":   transform,
            "transformed": transform not in ("passthrough", ""),
        })
    return result
