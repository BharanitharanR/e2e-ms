# Paycon E2E-MS — Transaction Origination & Canonical Mapping Expansion

A design specification for four new capabilities that turn Paycon from a JIT webhook
simulator into a full **origination-to-issuer** simulation product:

1. Acquirer Test Simulator — originate a transaction as the acquirer
2. Network Test Simulator — select Visa / Mastercard / Amex / Discover dialects
3. JPOS → JPF canonical conversion with a flexible ISO mapper
4. POS Simulator driving a real **PC/SC** reader (tap a test card)

> **Terminology used here:** **JPOS** = the jPOS `ISOMsg` representation of an ISO 8583
> message (data elements + the DE55 EMV TLV blob). **JPF** = the canonical *JIT Payment
> Funding* object — the normalized JSON an issuer processor persists as DB fields and
> from which it emits the `pgfs.*` JIT webhook. The product's core IP is the controlled,
> auditable transformation **JPOS → JPF → DB → webhook**.

---

## 0. Why this closes the FIME gap

FIME HTS sells a "transaction generator" (inject ISO 8583 / API JSON / XML),
"authorization simulation" (any node in the ecosystem), multi-brand support, and EMV /
ISO field interpretation. Paycon today starts the flow *after* the network leg. These
four modules give Paycon the **left-hand side of the rail** — origination, network
dialects, and EMV — which is exactly the scope FIME charges for, but delivered as a
local, containerized, AI-assisted harness rather than an enterprise platform. You reach
capability parity on origination while keeping your differentiation (local, CI/CD,
AI copilot, Marqeta-JIT focus).

---

## 1. Extended architecture

```
 POS agent (host) ──tap──▶ Acquirer sim ──ISO──▶ Network sim ──ISO──▶ ISO→JPF mapper
   PC/SC reader            (builds 0100)         (Visa/MC/Amex/Disc)   (DE+TLV→canonical)
        │                                                                     │
   test card                                                            JPF canonical
                                                                              ▼
                                          Customer JIT (SUT) ◀─pgfs.*─ Issuer processor
                                          approve / decline             (persists JPF as
                                                                         DB fields)
```

Map onto the current repo:

| New / changed | Repo target | Role |
|---|---|---|
| POS agent (new, host-run) | `pos_agent/` | pyscard / javax.smartcardio; reads a test card, posts a terminal-capture payload to the acquirer over localhost |
| Acquirer simulator (extend) | `backend/acquirer.py` → real ISO builder, **or** a jPOS sidecar `iso-engine/` | builds the 0100/0200, stamps acquirer DEs, manages STAN/RRN, handles reversals/advices |
| Network simulator (generalize) | `backend/visa.py` → `backend/network/` | per-network packager configs + BIN router + network edits |
| ISO → JPF mapper (new) | `backend/mapping/` (+ existing `frontend/iso_mapping.py`, `pages/04_iso_mapper.py`) | config-driven DE/TLV → canonical engine |
| Issuer processor (keep) | `backend/marqeta_simulator.py` | JPF → `pgfs.*` webhook (already implemented) |

**Engine choice:** use **jPOS** as the ISO 8583 engine (a small Java/Q2 sidecar exposed
over TCP/REST). It is the industry standard, ships `GenericPackager` XML so each network
is a config file, and produces authentic bitmaps/packing that a payments buyer will
recognize on sight. The Python orchestrator stays the brain; jPOS owns the wire format.
(A pure-Python path with `pyiso8583` is viable if you want a single language, at the cost
of authenticity and the "JPOS" story your buyers expect.)

---

## 2. Module 1 — Acquirer Test Simulator

**Goal:** originate a financial message exactly as an acquirer would, from manual input,
a saved scenario, or a POS capture.

- **Message types (MTI):** `0100/0110` (auth), `0200/0210` (financial),
  `0120/0220` (advice), `0420/0430` (reversal). Start with 0100/0110 and 0420.
- **Acquirer-stamped fields:** DE7 (transmission date/time), DE11 (STAN — unique per
  run), DE32 (acquiring institution id), DE37 (RRN), DE41 (terminal id), DE42 (card
  acceptor id), DE19 (acquirer country). The STAN/RRN uniqueness you already added is the
  idempotency backbone — keep minting fresh trace ids per run.
- **Response handling:** match `0110` to the request on STAN+RRN, read DE39 (response
  code) and DE38 (auth code), score against the scenario.
- **Reversal flow:** on timeout / forced reversal, emit `0420` carrying the original
  DE11/DE37 and DE90 (original data elements) so the issuer can unwind the auth — this is
  a high-value demo of "the full path," not just the happy auth.

**Acceptance:** given a scenario or POS capture, the acquirer emits a well-formed ISO
0100, receives a 0110, and the orchestrator scores it — with a reversal variant.

---

## 3. Module 2 — Network Test Simulator (multi-network)

**Goal:** route the ISO message through the selected scheme's dialect.

- **Selection:** explicit pick in the UI, or auto-route by BIN
  (`4*` → Visa, `5* / 2221–2720` → Mastercard, `34 / 37` → Amex, `6011 / 64 / 65` →
  Discover).
- **Per-network packagers:** one `GenericPackager` XML per scheme. The standard DEs are
  shared; the differences live in private fields and edits, e.g.:
  - **Visa** (BASE I / V.I.P.): private use in DE62/DE63, CVV/iCVV handling, field-44
    additional response data.
  - **Mastercard** (CIS / Banknet): heavy use of DE48 sub-elements (PDS), DE61 POS data,
    Banknet reference in DE63.
  - **Amex** (GCAG): Amex-specific private DEs and SE structures.
  - **Discover** (D-Payment): network reference data in DE62/DE63, POS data layout.
  - The exact field layouts come from each scheme's interface spec (licensed); the
    simulator ships **representative, configurable** packagers, not the proprietary specs.
- **Network edits / STIP:** mandatory-field checks, format validation, and optional
  Stand-In Processing so the network can approve/decline without the issuer (useful for
  testing timeout behavior).

**Acceptance:** the same logical transaction, packed and unpacked correctly under each
selected network, routed by BIN, with at least one network-specific private field
populated.

---

## 4. Module 3 — JPOS → JPF canonical mapping (the core)

This is the centerpiece: the bidirectional, auditable mapping from the jPOS `ISOMsg`
(plus the DE55 EMV TLV parse) into the canonical JPF object that the issuer processor
persists and from which it emits the `pgfs.*` webhook.

### 4.1 Canonical JPF schema (what the issuer stores)

```json
{
  "transaction": {
    "id": "...", "type": "authorization", "amount": 2500, "currency_code": "840",
    "local_time": "...", "transmitted_at": "...",
    "network": { "name": "VISA", "mti": "0100", "stan": "...", "rrn": "...",
                 "processing_code": "000000" }
  },
  "card":     { "token": "...", "pan_last_four": "1111", "pan_hash": "...",
                "exp_month": "12", "exp_year": "2028", "sequence_number": "01" },
  "merchant": { "id": "...", "name": "...", "city": "...", "state": "CA",
                "country": "USA", "mcc": "5411" },
  "pos":      { "entry_mode": "071", "condition_code": "00", "terminal_id": "TERM0001",
                "pin_present": false, "attendance": "ATTENDED" },
  "acquirer": { "institution_id": "123456", "country": "840",
                "forwarding_institution_id": "123456" },
  "emv":      { "aip": "...", "atc": "...", "tvr": "...", "cryptogram": "...",
                "cid": "...", "issuer_app_data": "...", "unpredictable_number": "...",
                "cvm_results": "...", "aid": "...", "terminal_country": "840" },
  "jit_funding": { "method": "pgfs.authorization", "token": "...", "user_token": "...",
                   "amount": 2500, "currency_code": "840" },
  "response": { "response_code": "00", "auth_code": "ABC123", "decision": "APPROVED" }
}
```

### 4.2 Master mapping table — ISO 8583 DE → EMV tag → JPF → issuer DB field

| ISO DE | Field | EMV tag (if chip) | JPF canonical path | Typical issuer DB column |
|---|---|---|---|---|
| DE2  | PAN | 5A / 57 | `card.pan` → token | `card_token`, `pan_last_four`, `pan_hash` |
| DE3  | Processing code | 9C | `transaction.processing_code` / `.type` | `processing_code`, `txn_type` |
| DE4  | Amount, transaction | 9F02 | `transaction.amount`, `jit_funding.amount` | `amount_cents` |
| DE7  | Transmission date/time | — | `transaction.transmitted_at` | `transmission_ts` |
| DE11 | STAN | — | `transaction.network.stan` | `stan` |
| DE12 | Local time | 9F21 | `transaction.local_time` | `local_time` |
| DE13 | Local date | 9A | `transaction.local_date` | `local_date` |
| DE14 | Expiration date | 5F24 | `card.exp_month` / `.exp_year` | `exp_month`, `exp_year` |
| DE18 | MCC | 9F15 | `merchant.mcc` | `mcc` |
| DE19 | Acquirer country | 9F1A | `acquirer.country` | `acquirer_country` |
| DE22 | POS entry mode | 9F39 | `pos.entry_mode` | `pos_entry_mode` |
| DE25 | POS condition code | — | `pos.condition_code` | `pos_condition_code` |
| DE32 | Acquiring institution id | — | `acquirer.institution_id` | `acquiring_institution_id` |
| DE35 | Track 2 data | 57 | `card.track2` *(sensitive)* | `track2_hash` |
| DE37 | RRN | — | `transaction.network.rrn` | `rrn` |
| DE38 | Auth id response | — | `response.auth_code` | `auth_code` |
| DE39 | Response code | — | `response.response_code` | `response_code` |
| DE41 | Card acceptor terminal id | 9F1C | `pos.terminal_id` | `terminal_id` |
| DE42 | Card acceptor id (merchant) | — | `merchant.id` | `card_acceptor_id` |
| DE43 | Card acceptor name / location | 9F4E | `merchant.name/city/state/country` | `merchant_name`, `merchant_city`, `merchant_country` |
| DE48 | Additional data (private) | — | `transaction.network.private` | network-specific |
| DE49 | Currency, transaction | 5F2A | `transaction.currency_code` | `currency_code` |
| DE52 | PIN data | — | `pos.pin_present` *(verify only)* | `pin_verified` *(never store raw block)* |
| DE55 | ICC / EMV data | *(TLV container)* | `emv.*` (sub-parse below) | `emv_*` columns |

**DE55 sub-parse (EMV TLV → `emv.*`):**

| Tag | Name | JPF path | DB column |
|---|---|---|---|
| 82   | Application Interchange Profile | `emv.aip` | `emv_aip` |
| 9F36 | Application Transaction Counter | `emv.atc` | `emv_atc` |
| 95   | Terminal Verification Results | `emv.tvr` | `emv_tvr` |
| 9F26 | Application Cryptogram (ARQC) | `emv.cryptogram` | `emv_arqc` |
| 9F27 | Cryptogram Information Data | `emv.cid` | `emv_cid` |
| 9F10 | Issuer Application Data | `emv.issuer_app_data` | `emv_iad` |
| 9F37 | Unpredictable Number | `emv.unpredictable_number` | `emv_un` |
| 9F34 | CVM Results | `emv.cvm_results` | `emv_cvm` |
| 84 / 4F | Dedicated File / AID | `emv.aid` | `emv_aid` |
| 9F1A | Terminal Country Code | `emv.terminal_country` | `term_country` |

**Final hop (JPF → webhook):** `jit_funding.method` resolves to `pgfs.authorization` /
`pgfs.authorization.advice` / `pgfs.refund` / `pgfs.authorization.reversal` and the
issuer emits the Marqeta-shaped webhook your `marqeta_simulator.py` already builds.

### 4.3 ISO Mapper flexibility — config-driven specs

Mappings live in versioned YAML per network/customer, so fields can be re-pointed without
code. Your existing `iso_mapping.py` / `04_iso_mapper.py` becomes the editor/visualizer
for these specs.

```yaml
version: 1
network: mastercard
fields:
  - canonical: transaction.amount
    source:     { de: 4, transform: n12_to_cents }
    emv_source: { tag: "9F02", transform: emv_amount }   # cross-check chip vs DE4
  - canonical: card.pan
    source: { de: 2 }
    pii: true
    store: { token: true, last_four: true, hash: sha256 }  # never persist clear PAN
  - canonical: transaction.network.stan
    source: { de: 11 }
  - canonical: merchant.mcc
    source:     { de: 18 }
    emv_source: { tag: "9F15" }
  - canonical: emv.tvr
    source: { de: 55, tag: "95" }
  - canonical: pos.entry_mode
    source: { de: 22 }
```

The mapper validates that chip-sourced and DE-sourced values agree (e.g. DE4 vs 9F02,
DE49 vs 5F2A) and flags mismatches — a genuinely useful test signal FIME-class tools
surface as "ISO field interpretation."

---

## 5. Module 4 — POS Simulator + PC/SC reader

**Goal:** originate a real transaction from a physical tap of a **test card**.

### 5.1 Two modes
- **Synthetic** (no hardware): generate the terminal capture in software — works in CI
  and on any laptop. Build this first.
- **Live PC/SC** (hardware): read a test card via a contactless/contact reader and build
  the field set from real chip data.

### 5.2 APDU flow (contactless EMV read)
1. Connect to the reader and poll for a card.
2. **SELECT PPSE** (`2PAY.SYS.DDF01`) → FCI → candidate AID(s) from tag `4F`.
3. **SELECT AID**.
4. **GET PROCESSING OPTIONS** (with PDOL) → AIP (`82`) + AFL (`94`).
5. **READ RECORD** per AFL → `57` (Track 2 equivalent: PAN | expiry | service code),
   `5A`, `5F24`, optionally `5F20` (cardholder name, often absent on contactless).
6. *(optional)* **GENERATE AC** → ARQC (`9F26`) to drive online-auth simulation.

Build: PAN → DE2, expiry → DE14, track2 → DE35, entry mode → DE22 = `07` (contactless
chip), collected tags → DE55.

### 5.3 Stack & constraints
- **Libraries:** Python `pyscard` (`smartcard` module) or Java `javax.smartcardio`.
- **Readers:** ACR1252U / ACR122U (contactless), Identiv uTrust — PC/SC class devices.
- **Host constraint:** USB access means the POS agent runs **on the host** (or a
  container with explicit `--device` / USB passthrough), bridging to the Dockerized
  acquirer over localhost REST. Streamlit/browser cannot touch PC/SC directly — you need
  the small local agent.

### 5.4 Guardrails (keep it a test tool, not a liability)
- **Test cards / test BINs only.** This is a QA origination harness, not a capture tool.
- **Never persist a clear PAN.** Tokenize, keep `last_four`, store a salted hash; mask
  in logs and the UI. The mapper's `pii: true` / `store:` directives enforce this.
- **Stay PCI-aware:** scope the agent, don't log sensitive authentication data (track
  data, CVV, PIN blocks), and keep the reader path off any system handling real cardholder
  data.

---

## 6. ROI-ordered roadmap

| Phase | Scope | Why first |
|---|---|---|
| **1** | ISO 8583 builder in the acquirer + JPF mapping engine + config specs + Visa/MC selector | Pure software, no hardware; closes the FIME "transaction generator" + "multi-brand" gap and lights up the mapper demo |
| **2** | DE55 EMV TLV parse + EMV scenarios; reversal/advice MTIs; network edits / STIP | Adds chip realism and the reversal "full path" story |
| **3** | POS simulator with PC/SC — synthetic first, then live test-card read via the host agent | Hardware differentiator; heavier, narrower, do once 1–2 are demoable |
| **4** | Clearing/settlement file generation + DB-validation view | Full FIME parity; larger build, pursue only if a buyer asks |

---

## 7. The demo this unlocks

Tap a test card on the reader → the PAN and EMV tags flow into an ISO 8583 0100 → pick
**Mastercard** and watch the message re-pack into that dialect → the **ISO → JPF mapper**
lights up each DE / TLV as it lands in a canonical field and a DB column → the issuer
emits `pgfs.authorization` → your SUT decides → the AI copilot explains the result. A
live, physical tap that traces all the way to issuer DB fields is something a static API
sandbox structurally cannot show — and it's the moment that wins the room.
