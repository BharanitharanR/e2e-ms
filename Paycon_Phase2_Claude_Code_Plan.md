# Paycon E2E-MS — Phase 2 Claude Code Plan
### From complete simulator to shippable end-to-end product

**Repo:** `github.com/srv2go/e2e-ms`
**Outcome:** turn the working full-path simulator into a product a customer can be handed
in a 2-week pilot: a **certification deliverable** they receive, a **stateful transaction
lifecycle** that links reversals/refunds/advices to their original auth, an optional
**physical POS front door** (real test card via PC/SC), and the **hardening** that makes it
shippable. Phase 1 (origination + multi-network + ISO→JPF mapping) is already done — do not
rebuild it.

> **Kickoff prompt (paste into Claude Code):**
> "Read this plan and the existing code first. Work task by task in priority order
> (P0 → P2); after each task run its Acceptance check and record it in CHANGELOG.md. Do
> NOT change existing endpoint paths, the JPF schema, or the `pgfs.*` webhook shape unless
> a task says so. Reuse what exists: `frontend/utils/html_report.py`, `/execute_suite`,
> `/environments`, `backend/mapping/engine.py`, `backend/network/originator.py`,
> `backend/chip_terminal.py`. P0 (certification) is the definition of a shippable product —
> get there first."

---

## 0. Current state — already built in Phase 1 (do not rebuild)

- Multi-network origination: `backend/network/originator.build_0100()`, profiles for
  visa/mastercard/amex/discover, BIN router.
- ISO→JPF mapping: `backend/mapping/engine.map_to_jpf()` with a BER-TLV DE55 parser, EMV
  transforms, tokenization, and a `validate` list; specs per network.
- Issuer + lifecycle events: `marqeta_simulator.py` emits `pgfs.authorization` /
  `.advice` / `.refund` / `.reversal`; `customer_jit/app.py` decisions + duplicate (RC 76).
- Chip terminal: `backend/chip_terminal.py` (software APDU emulation), `/chip/command`.
- Pluggable SUT: `/environments` CRUD + activate, `06_sandbox_config.py`.
- Suite runner + reports: `/execute_suite`, `frontend/utils/html_report.py`
  (`build_html_report`, `build_junit_xml`), analytics endpoints.
- Vertical-slice test + CI: `tests/test_vertical_slice.py`, `.github/workflows/vertical_slice.yml`.
- AI Copilot P0 fixes landed (the `db` import and the Ollama-fallback bug are fixed).

The remaining work is **product**, not path.

---

## P0 — The certification deliverable (what the customer receives)

### T0.1 — `POST /certify`
Add a certification run that executes a fixed **certification suite** against the **active
environment** (`/environments/active`): the RC matrix already in `backend/scenarios/`
(`rc_51, rc_54, rc_57, rc_61, rc_62, rc_65, rc_75, rc_91, rc_96`) plus the lifecycle happy
paths (auth approve/decline, advice/clearing, refund, reversal). Return:
```json
{ "sut": {...active env...}, "timestamp": "...", "results": [ {scenario, expected, actual, passed, latency_ms}... ],
  "coverage": { "lifecycle_events_covered": [...], "rc_codes_covered": [...], "score": 0-100 },
  "certified": true|false, "threshold": 95 }
```
Reuse `/execute_suite` internals; don't duplicate the runner.
**Acceptance:** `POST /certify` against the bundled SUT returns per-scenario PASS/FAIL, a
coverage score, and a `certified` verdict; threshold is configurable via env/body.

### T0.2 — Branded certification report
Extend `frontend/utils/html_report.py` with `build_certification_report(certify_result)`:
header "Marqeta JIT Integration — Certification Report", SUT URL + timestamp, the coverage
scorecard (per lifecycle event and per RC), aggregate score, and the PASS/FAIL verdict
banner. Render HTML; add PDF export (`weasyprint` or `playwright` headless — whichever is
already available) so it's a sharable artifact.
**Acceptance:** a certification run produces a downloadable, branded HTML **and** PDF report
that a non-engineer can read and forward.

### T0.3 — "Certify this SUT" in the UI
On `03_suite_runner.py` (or a new `09_certification.py`), add a **Certify this SUT** button
that calls `/certify`, shows the live scorecard, the verdict, and offers the report
download. Show which environment is active so the presenter can't certify the wrong target.
**Acceptance:** one click certifies the active SUT and offers the report; switching the
environment and re-certifying targets the new endpoint.

### T0.4 — Inline AI explanation on a failed cert scenario
`/ai/explain_failure` exists — surface it: any FAILED row in the certification scorecard
gets an "Explain with AI" action rendering `root_cause` / `likely_rule_triggered` /
`suggested_fix` inline.
**Acceptance:** a deliberately failing SUT yields a one-click AI explanation with a concrete
fix.

---

## P1 — Stateful lifecycle ledger (make end-to-end coherent)

### T1.1 — Issuer transaction ledger
In `marqeta_simulator.py` (persist via the existing SQLite `backend/db.py`), record each
authorization as a ledger entry keyed by `transaction_id` with `amount`, `currency`,
`network`, `state`, `remaining_amount`. Expose `GET /issuer/ledger` for the UI.
**Acceptance:** an approved auth creates a ledger entry in `PENDING` with the full amount.

### T1.2 — Link advice / clearing / refund / reversal to the original
When an event carries `original_transaction_id`, look it up in the ledger and:
- **clearing advice / completion** → move `PENDING`→`CLEARED`, support a **partial**
  completion amount (≤ original), decrement `remaining_amount`.
- **reversal** → release the held amount (full or partial), state `REVERSED`.
- **refund** → create a linked credit referencing the original.
Reject events whose `original_transaction_id` is unknown with a clear error (not a silent
pass).
**Acceptance:** a $75 auth + $50 partial clearing leaves `remaining_amount = 25`; a reversal
of an unknown original is rejected with a descriptive 4xx.

### T1.3 — Originate linked lifecycle messages
Extend `backend/network/originator.py` so advice/refund/reversal scenarios originate with
the correct processing code (already mapped) **and** the original STAN/RRN + DE90 (original
data elements) where the dialect expects it, so the issuer can match on trace, not just on
`original_transaction_id`.
**Acceptance:** a reversal message carries the original DE11/DE37; the issuer matches the
ledger entry by trace.

### T1.4 — Lifecycle scenarios + test
Add/curate a `suite_lifecycle.json` that runs auth → partial clearing → refund → reversal
as a linked chain and asserts ledger state transitions. Add a pytest.
**Acceptance:** the linked-chain test is green and shows coherent ledger transitions end to
end.

---

## P2 — Physical POS front door (PC/SC) & hardening

### T2.1 — PC/SC host agent (`pos_agent/`)
A host-run Python agent (`pyscard`) that reads a **test card** via a contactless/contact
reader and posts a terminal-capture payload to the acquirer over localhost. Reuse the EMV
tag model already in `backend/chip_terminal.py`. APDU flow: SELECT PPSE → SELECT AID → GPO →
READ RECORD (tags 57/5A/5F24) → optional GENERATE AC (9F26). Map PAN→DE2, expiry→DE14,
track2→DE35, entry mode→DE22=`07`, collected tags→DE55. Runs on the host (USB), not in a
container; document `--device` passthrough if containerized.
**Guardrails:** test cards / test BINs only; never persist a clear PAN (tokenize, last_four,
salted hash; mask in logs); don't log track/CVV/PIN. This is a QA origination harness.
**Acceptance:** tapping a test card originates a real transaction that flows through the
existing path to a SUT decision; with no reader present the agent reports a clear, graceful
error and the synthetic terminal still works.

### T2.2 — Pilot package & one-command demo
A `make demo` / `start.sh` profile that validates `ANTHROPIC_API_KEY`, brings up only the
demo services (Claude-only profile — no `ollama`/`mongodb` required), waits on `/health/all`,
and prints the UI URL. Produce a **sandbox-ready bundle** (the docker-compose + scenarios +
README quickstart) as the literal 2-week-pilot deliverable.
**Acceptance:** a fresh clone reaches a working UI with one command and only an API key set.

### T2.3 — Observability & security pass
Add structured run logging and a run **audit export** (`GET /history/export` → JSON/CSV).
Do a PAN-handling audit: confirm no clear PAN is persisted or logged anywhere on the
origination → mapping → webhook path (the mapper's `pii`/`store` rules are the enforcement
point).
**Acceptance:** an audit export downloads; a grep for raw PAN across logs/DB is clean.

### T2.4 — Extend CI
Grow `.github/workflows/vertical_slice.yml` (or add jobs) to run the certification smoke
(`/certify` against the bundled SUT, mocked Claude) and the T1.4 lifecycle chain, so neither
the product deliverable nor the lifecycle can silently regress.
**Acceptance:** CI runs vertical-slice + certification + lifecycle and is green.

---

## Conditional stretch (Phase 3 candidates — additional to this plan)
- **jPOS sidecar** to replace `pyiso8583` for byte-authentic packing (`iso-engine/` is
  scaffolded but empty). Pure authenticity/credibility upgrade; the product ships without it.
- **Clearing & settlement file generation** + **DB validation** (data-at-rest vs
  data-in-motion) and **interchange/qualification** — the deeper FIME-parity modules; larger
  build, pursue when a buyer asks.

---

## Guardrails for the agent
- Reuse existing modules; don't fork the suite runner, report builder, or mapper.
- Don't change public endpoint paths, the JPF schema, or the `pgfs.*` webhook shape outside
  a task that explicitly calls for it.
- Every task ends with its Acceptance check, recorded in CHANGELOG.md.
- Secrets via env only; test cards only on the PC/SC path; never persist or log a clear PAN.

## Definition of done
1. `POST /certify` produces a branded PASS/FAIL certification report (HTML + PDF) with a
   coverage score and verdict against the active SUT.
2. The issuer maintains a stateful ledger; reversals/refunds/advices/completions link to the
   original auth with partial-amount support; unknown originals are rejected.
3. A test card tapped on a PC/SC reader originates a real transaction end to end (and the
   synthetic path still works with no hardware).
4. One-command demo on the Claude-only profile; sandbox-ready pilot bundle produced.
5. CI covers vertical-slice + certification + lifecycle, all green.
