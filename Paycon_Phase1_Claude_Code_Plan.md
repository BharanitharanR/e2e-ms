# Paycon E2E-MS — Phase 1 Claude Code Plan
### Origination + multi-network + ISO→JPF mapping (no hardware)

**Repo:** `github.com/srv2go/e2e-ms`
**Outcome:** originate a transaction as the acquirer, pack it under a **selectable network
dialect** (Visa / Mastercard / Amex / Discover) via a **jPOS sidecar**, map the ISO 8583
message to the canonical **JPF** object with a config-driven engine, and run it through the
existing issuer → Customer JIT path. Pure software — runnable on any laptop and in CI.

> **Kickoff prompt (paste into Claude Code):**
> "Read this plan top to bottom and work task by task (T1 → T7). After each task run its
> Acceptance check before continuing. Don't change existing endpoint paths or the JIT
> webhook shape. Keep a CHANGELOG.md. The vertical slice in T6 (Visa vs Mastercard
> producing different ISO but identical JPF, scored end-to-end) is the definition of done —
> get there, then harden."

---

## 1. Architecture

```
backend/acquirer.py ──build 0100──▶ backend/network/ ──pack/unpack──▶ iso-engine (jPOS, Java)
   (origination)                     (BIN routing, edits,            GenericPackager XML per
                                       which DEs to populate)          network · TCP/REST
                                            │
                                       unpacked ISOMsg
                                            ▼
                              backend/mapping/  ──JPF──▶  marqeta_simulator.py ──pgfs.*──▶ customer_jit
                              (DE+TLV → canonical,                  (already built)
                               YAML specs per network)
```

- **iso-engine** (new, `iso-engine/`, Java + jPOS Q2): owns the byte-level ISO 8583
  packers. One `GenericPackager` XML per network. Exposes `POST /pack` and `POST /unpack`
  (hex ↔ field map) over HTTP so the Python side stays the orchestrator.
- **backend/network/** (new, Python): per-network *profiles* — BIN ranges, MTI map,
  which private DEs to populate, network edits/validation. Calls iso-engine to pack.
- **backend/mapping/** (new, Python): the ISO→JPF engine driven by YAML specs.
- **backend/acquirer.py** (extend): build the 0100 from a scenario/POS capture, stamp
  acquirer DEs, mint fresh STAN/RRN, hand to the selected network.

If a Java sidecar is too heavy for the first slice, T2 has a **pure-Python fallback**
using `pyiso8583` so the vertical slice works immediately; the jPOS sidecar then replaces
the packer without touching the mapping engine.

---

## 2. Tasks

### T1 — Network profiles (`backend/network/profiles/*.yaml`)
Create the four profiles below (§3). Add `backend/network/router.py` with
`select_network(pan, override=None)` → profile, routing by BIN with explicit override.
**Acceptance:** `select_network("4111...")` → visa; `select_network("5555...")` → mastercard;
override wins.

### T2 — ISO engine
Stand up `iso-engine/` (jPOS Q2 + a tiny HTTP handler) with `GenericPackager` XMLs per
network and `/pack` + `/unpack`. Add a `Dockerfile.iso_engine` and a service in
`docker-compose.yml`.
*Fallback:* if blocked on Java, implement `backend/network/packer.py` over `pyiso8583`
with the same `/pack` `/unpack` contract so T3–T6 proceed; swap to jPOS later.
**Acceptance:** packing then unpacking a known field map round-trips losslessly under each
network; private DEs from the profile appear in the packed message.

### T3 — Acquirer origination (`backend/acquirer.py`)
Build the `0100` from the scenario `request` + network profile: stamp DE7, DE11 (fresh
STAN), DE32, DE37 (fresh RRN), DE41, DE42, DE19; populate the profile's private DEs; pack
via the engine; send to the network leg; on `0110` read DE39/DE38.
**Acceptance:** a scenario yields a well-formed `0100` whose DE set differs by network
(Visa: DE44/62/63; Mastercard: DE48/61/63) while the standard DEs match.

### T4 — Mapping engine (`backend/mapping/engine.py` + `specs/*.yaml`)
Implement a spec-driven mapper: load the per-network YAML spec (§4), walk the unpacked
ISOMsg + DE55 TLV, emit the canonical **JPF** object. Enforce `pii`/`store` rules (token,
last_four, hash — never persist clear PAN). Validate chip-vs-DE agreement (DE4↔9F02,
DE49↔5F2A) and flag mismatches.
**Acceptance:** Visa and Mastercard messages for the *same* logical transaction produce an
**identical** JPF object (network name aside); a deliberately mismatched 9F02 is flagged.

### T5 — Wire into the existing path
Route mapper output (JPF) into `marqeta_simulator.py` so it emits the existing `pgfs.*`
webhook to `customer_jit`. Persist the JPF as the issuer "DB" record (reuse the Mongo /
in-memory store). Surface the ISO field set + JPF + DB record in the orchestrator response
so the UI can show all three.
**Acceptance:** `/execute/{scenario}` returns `{iso_message, jpf, db_record, webhook,
result}` and the SUT decision still scores PASS/FAIL.

### T6 — Vertical-slice test (definition of done)
See §5. Same transaction, Visa vs Mastercard: assert ISO DE sets differ, JPF is identical,
SUT decisions match expectation. Add as pytest + a GitHub Action.
**Acceptance:** the test is green and demonstrates the network-switch → remap → identical
canonical → decision story.

### T7 — UI: live network switch (`frontend/pages/`)
Add a "Network" selector (Visa/Mastercard/Amex/Discover) to the scenario/suite page; on
change, re-run and show the ISO field table (private DEs highlighted) beside the JPF
canonical and the SUT verdict — the in-app version of the demo console.
**Acceptance:** toggling the network in the UI visibly changes the ISO private fields while
the JPF panel stays constant.

---

## 3. The four network packager configs (`backend/network/profiles/`)

> Standard DEs are shared; these capture the dialect deltas. Exact byte layouts come from
> each scheme's licensed interface spec — these are representative, configurable profiles.

```yaml
# visa.yaml
network: visa
bin_ranges: ["4"]
mti: { auth_request: "0100", auth_response: "0110", reversal: "0420" }
packager: iso-engine/packagers/visa.xml
private_fields:
  - { de: 44, name: Additional response data, usage: response }   # CVV2 / CVR
  - { de: 62, name: Custom payment service,   format: "ans..255 LLLVAR" }  # Visa TID
  - { de: 63, name: Network data (V.I.P.),    format: "ans..255 LLLVAR" }
edits: { require: [2,3,4,7,11,22,32,37,41,49], cvv_field: 44 }
notes: "BASE I authorization; Visa Transaction ID in DE62; V.I.P. network data in DE63."
```
```yaml
# mastercard.yaml
network: mastercard
bin_ranges: ["5", "2221-2720"]
mti: { auth_request: "0100", auth_response: "0110", reversal: "0420" }
packager: iso-engine/packagers/mastercard.xml
private_fields:
  - { de: 48, name: Additional data (PDS), format: "ans..999 LLLVAR", subelements: pds }
  - { de: 61, name: POS data,              format: "ans..999 LLLVAR" }
  - { de: 63, name: Network data (Banknet), format: "ans..999 LLLVAR" }  # Banknet ref
edits: { require: [2,3,4,7,11,22,32,37,41,49], pds_field: 48 }
notes: "CIS authorization; PDS sub-elements in DE48; Banknet reference in DE63."
```
```yaml
# amex.yaml
network: amex
bin_ranges: ["34", "37"]
mti: { auth_request: "0100", auth_response: "0110", reversal: "0420" }
packager: iso-engine/packagers/amex.xml
private_fields:
  - { de: 47, name: Additional data (national), format: "ans..999 LLLVAR" }
  - { de: 63, name: Amex private data,          format: "ans..999 LLLVAR" }
edits: { require: [2,3,4,7,11,22,32,37,41,49] }
notes: "GCAG authorization; Amex private structures in DE47/DE63."
```
```yaml
# discover.yaml
network: discover
bin_ranges: ["6011", "64", "65"]
mti: { auth_request: "0100", auth_response: "0110", reversal: "0420" }
packager: iso-engine/packagers/discover.xml
private_fields:
  - { de: 62, name: Network reference data, format: "ans..255 LLLVAR" }
  - { de: 63, name: Network data,           format: "ans..255 LLLVAR" }
edits: { require: [2,3,4,7,11,22,32,37,41,49] }
notes: "D-Payment authorization; network reference data in DE62/DE63."
```

---

## 4. Sample mapping spec (`backend/mapping/specs/mastercard.yaml`)

```yaml
version: 1
network: mastercard
fields:
  - { canonical: transaction.amount,            source: {de: 4, transform: n12_to_cents},
      emv_source: {tag: "9F02", transform: emv_amount} }
  - { canonical: card.pan, source: {de: 2}, pii: true,
      store: {token: true, last_four: true, hash: sha256} }
  - { canonical: transaction.network.stan,      source: {de: 11} }
  - { canonical: transaction.network.rrn,       source: {de: 37} }
  - { canonical: transaction.processing_code,   source: {de: 3} }
  - { canonical: merchant.mcc,                  source: {de: 18}, emv_source: {tag: "9F15"} }
  - { canonical: pos.entry_mode,                source: {de: 22} }
  - { canonical: pos.terminal_id,               source: {de: 41} }
  - { canonical: transaction.currency_code,     source: {de: 49}, emv_source: {tag: "5F2A"} }
  - { canonical: card.expiration,               source: {de: 14} }
  - { canonical: emv.tvr,                        source: {de: 55, tag: "95"} }
  - { canonical: emv.cryptogram,                source: {de: 55, tag: "9F26"} }
  - { canonical: emv.atc,                        source: {de: 55, tag: "9F36"} }
validate:
  - { rule: equal, a: {de: 4}, b: {tag: "9F02"}, on_mismatch: flag }
  - { rule: equal, a: {de: 49}, b: {tag: "5F2A"}, on_mismatch: flag }
```
The Visa/Amex/Discover specs are the same canonical targets with their own `source` DEs.
`backend/mapping/specs/` holds one per network; `frontend/iso_mapping.py` /
`pages/04_iso_mapper.py` become the editor/visualizer for these.

---

## 5. Verification test cases (also the demo script)

The same logical transaction is sent under Visa then Mastercard.

| # | Scenario | Amount | Network | Distinct ISO private DEs | JPF identical? | SUT decision |
|---|---|---|---|---|---|---|
| 1 | Grocery, contactless | $25.00 (2500) | Visa | DE44, DE62, DE63 | — | APPROVED (≤ $50) |
| 1 | Grocery, contactless | $25.00 (2500) | Mastercard | DE48, DE61, DE63 | ✓ vs case 1 Visa | APPROVED |
| 2 | Electronics, contactless | $75.00 (7500) | Visa | DE44, DE62, DE63 | — | DECLINED (> $50) |
| 2 | Electronics, contactless | $75.00 (7500) | Mastercard | DE48, DE61, DE63 | ✓ | DECLINED |
| 3 | Online, e-commerce | €50.00 (5000) | Visa | DE44, DE62, DE63 | — | APPROVED |

Assertions for the pytest:
1. `iso_visa.fields != iso_mc.fields` (the private DE sets differ).
2. `jpf_visa == jpf_mc` ignoring `transaction.network.name` (canonical is dialect-agnostic).
3. `result.decision == scenario.expected_customer_decision` for every row.
4. STAN/RRN are unique per run (no false duplicate / HTTP 409).

This is exactly the "switch Visa↔Mastercard, watch the ISO remap, identical canonical,
same decision" narrative — wired as an automated test so the demo can't regress.

---

## 6. Definition of done
1. Network selectable by BIN or override; four profiles load.
2. Acquirer originates a valid 0100; ISO field set differs by network.
3. Mapper produces identical JPF across networks; mismatches flagged; no clear PAN stored.
4. JPF flows through the existing issuer → `pgfs.*` → SUT path and scores PASS/FAIL.
5. The §5 vertical-slice test is green in CI.
6. The UI network switch shows the live ISO-vs-JPF contrast.
