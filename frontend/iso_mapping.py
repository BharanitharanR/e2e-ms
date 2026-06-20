# frontend/iso_mapping.py
"""ISO 8583 ↔ JPBOS ↔ JCF canonical field mapping definitions.

DEFAULT_ISO_JCF_MAPPING provides the default DE→JCF mapping table.
extract_iso_jcf_values() hydrates the mapping with actual values from a trace.
"""

DEFAULT_ISO_JCF_MAPPING = [
    {
        "de": "DE2",
        "iso_name": "Primary Account Number",
        "jcf_field": "pan",
        "description": "PAN → Marqeta card_token (tokenised)",
        "transform": "tokenize",
    },
    {
        "de": "DE4",
        "iso_name": "Amount, Transaction",
        "jcf_field": "amount",
        "description": "Minor units (cents)",
        "transform": "passthrough",
    },
    {
        "de": "DE7",
        "iso_name": "Transmission Date & Time",
        "jcf_field": "datetime",
        "description": "ISO8601 UTC timestamp",
        "transform": "format_iso8601",
    },
    {
        "de": "DE11",
        "iso_name": "STAN",
        "jcf_field": "stan",
        "description": "6-digit System Trace Audit Number",
        "transform": "passthrough",
    },
    {
        "de": "DE12",
        "iso_name": "Local Transaction Time",
        "jcf_field": "local_transaction_time",
        "description": "Extracted HHMMSS from datetime",
        "transform": "extract_time",
    },
    {
        "de": "DE18",
        "iso_name": "Merchant Type (MCC)",
        "jcf_field": "mcc",
        "description": "4-digit Merchant Category Code",
        "transform": "passthrough",
    },
    {
        "de": "DE22",
        "iso_name": "POS Entry Mode",
        "jcf_field": "pos_entry_mode",
        "description": "How card was read (051=chip+PIN, 071=contactless)",
        "transform": "passthrough",
    },
    {
        "de": "DE37",
        "iso_name": "Retrieval Reference Number",
        "jcf_field": "rrn",
        "description": "12-char network reference (DE37)",
        "transform": "passthrough",
    },
    {
        "de": "DE41",
        "iso_name": "Terminal ID",
        "jcf_field": "terminal_id",
        "description": "8-char terminal identifier",
        "transform": "passthrough",
    },
    {
        "de": "DE42",
        "iso_name": "Card Acceptor ID Code",
        "jcf_field": "acquiring_institution_id",
        "description": "Merchant/acquirer institution code",
        "transform": "passthrough",
    },
    {
        "de": "DE43",
        "iso_name": "Card Acceptor Name/Location",
        "jcf_field": "merchant_name",
        "description": "Merchant name (≤25 chars)",
        "transform": "truncate_25",
    },
    {
        "de": "DE49",
        "iso_name": "Currency Code, Transaction",
        "jcf_field": "currency",
        "description": "ISO 4217 numeric (840=USD, 978=EUR, 826=GBP)",
        "transform": "numeric_to_alpha",
    },
    {
        "de": "DE63",
        "iso_name": "Network Data",
        "jcf_field": "network",
        "description": "Network identifier label",
        "transform": "passthrough",
    },
]

# ISO 4217 numeric → alpha lookup (common codes)
_CURRENCY_NUMERIC_TO_ALPHA = {
    "840": "USD",
    "978": "EUR",
    "826": "GBP",
    "124": "CAD",
    "036": "AUD",
    "392": "JPY",
    "756": "CHF",
    "356": "INR",
    "986": "BRL",
    "484": "MXN",
}


def _apply_transform(transform: str, value: str, field: str) -> str:
    """Apply a named transform to a field value."""
    if transform == "passthrough" or not transform:
        return value
    if transform == "tokenize":
        # Show as tokenised (mask middle digits)
        v = str(value)
        if len(v) > 8:
            return v[:4] + "****" + v[-4:]
        return "****" + v[-4:] if len(v) >= 4 else v
    if transform == "format_iso8601":
        return value  # already ISO8601 in our system
    if transform == "extract_time":
        # Extract time from ISO8601 datetime string YYYY-MM-DDTHH:MM:SS...
        try:
            return str(value).split("T")[1][:8].replace(":", "")
        except Exception:
            return value
    if transform == "truncate_25":
        return str(value)[:25]
    if transform == "numeric_to_alpha":
        return _CURRENCY_NUMERIC_TO_ALPHA.get(str(value), str(value))
    return value


def extract_iso_jcf_values(
    mapping_rows: list,
    request_sent: dict,
    response_received: dict,
) -> list:
    """Hydrate mapping rows with actual values from a transaction trace.

    Returns a list of dicts with keys:
        de, iso_name, iso_value, jcf_field, jcf_value, transform, transformed
    """
    all_vals = {}
    all_vals.update(response_received or {})
    all_vals.update(request_sent or {})   # request takes priority

    out = []
    for row in mapping_rows:
        jcf_field = row.get("jcf_field", "")
        transform = row.get("transform", "passthrough")
        raw_value = all_vals.get(jcf_field, "")
        raw_str   = str(raw_value) if raw_value != "" else ""
        transformed_value = _apply_transform(transform, raw_str, jcf_field) if raw_str else "(none)"

        out.append({
            "de":          row.get("de", ""),
            "iso_name":    row.get("iso_name", ""),
            "iso_value":   raw_str if raw_str else "(none)",
            "jcf_field":   jcf_field,
            "jcf_value":   transformed_value,
            "transform":   transform,
            "transformed": transform not in ("passthrough", ""),
        })

    return out
