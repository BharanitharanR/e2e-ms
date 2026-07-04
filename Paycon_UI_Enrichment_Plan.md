# Paycon e2ePS — UI Enrichment Plan (Five-Lens)

**Repo:** `github.com/srv2go/e2e-ms`
**Goal:** enrich the Streamlit UI into a branded, fast, first-impression-grade product —
evaluated and built through five lenses: Prototyper → Builder → Sweeper → Grower → Maintainer.

> **Kickoff prompt (paste into Claude Code):**
> "Read this plan and the existing `frontend/` first. Execute in the recommended order
> (P0 cross-cutting → WS-2 → WS-4 → WS-5). After each task run its Acceptance check and
> record it in CHANGELOG.md. Don't change backend endpoint paths or response shapes. Never
> render or log a raw API key. Keep one shared theme; don't add new per-page `<style>` blocks."

---

## 0. Current UI snapshot (grounded — don't re-discover)
- 12 pages (`01_home` … `12_enrichment_trace`) + `utils/{api_client,demo_mode,html_report,session_state}.py`.
- **No caching anywhere** (`st.cache_*` count = 0) — every rerun re-hits the backend.
- **CSS scattered** across 10+ pages as inline `st.markdown(<style>)`; no shared theme, no `.streamlit/config.toml`.
- Shell still branded **"e2MS — Marqeta E2E Simulator"** (`app.py`); nav is a markdown table duplicating the sidebar.
- Terminology debt: **"JPBOS" / "JCF"** typos (should be JPOS / JPF).
- `demo_mode.render_node_diagram()` already renders a horizontal node flow — reuse it.
- Phase 3–5 pages exist: `10_transaction_builder`, `11_ai_settings`, `12_enrichment_trace`, `09_certification`.

---

## WS-1 · PROTOTYPER — idea backlog (ship a few, park the rest)
Generate broadly; commit narrowly. **Ship now** (feed WS-2): branded theme; Mission-Control
home; horizontal enrichment trace as the run hero; certification-readiness gauge; provider
badge everywhere. **Park** (revisit post-PMF): Cmd-K command palette; audit-trail replay
scrubber; always-on AI chat dock; mandate before/after diff viewer; run permalinks;
card-tap animation. Record the parked list in `docs/ui_backlog.md` so ideas aren't lost.
**Acceptance:** `docs/ui_backlog.md` lists shipped vs parked with a one-line rationale each.

## WS-2 · BUILDER — build the winners (P0)
### T2.1 — Shared Paycon design system
Create `.streamlit/config.toml` with the brand theme (base dark; primary teal `#1fb7ac`;
bg navy `#0a1730`; text `#eaf1ff`) and `frontend/utils/theme.py: inject_theme()` holding the
single CSS block (cards, chips, mono, gradients). Call it once per page; **delete the inline
`<style>` blocks** from all pages.
**Acceptance:** every page shares one theme; `grep -rn "<style" frontend/pages` returns nothing.

### T2.2 — Rebrand the shell
`app.py` → "Paycon · e2ePS — End-to-End Payment Simulator", Paycon mark, page_icon. Replace
the markdown nav table with a compact branded header; let the sidebar own navigation.
**Acceptance:** the app reads "Paycon e2ePS" everywhere; no "e2MS/Marqeta" or "JPBOS/JCF" strings remain.

### T2.3 — Mission-Control home (`01_home.py`)
A real landing: service health (`/health/all`), AI provider status (`/ai/status`),
certification-readiness gauge (last `/certify` score), recent runs (`/history`), and a
prominent **Run demo** + use-case presets (AUTH / PRE-AUTH / ATM).
**Acceptance:** home shows live health + provider + last cert score + recent runs, and a
one-click demo run from the landing.

### T2.4 — Horizontal enrichment trace as the run hero
Promote `render_node_diagram` into a reusable `utils/flow.py: render_flow(trace)` that lays
Acquirer → Network → Issuer → JIT left-to-right with per-hop "added fields" popovers; replace
the **vertical** audit trail in `02_scenario_lab.py` with it.
**Acceptance:** runs render as a horizontal flow with per-hop payloads; no vertical step-list remains.

## WS-3 · SWEEPER — simplify, unship, optimize
### T3.1 — Cache read-only API calls
Add `@st.cache_data(ttl=…)` in `utils/api_client.py` for `scenarios`, `suites`,
`environments`, `analytics`, `test_cards`; invalidate on writes.
**Acceptance:** navigating pages no longer re-fetches static lists every rerun (verify via backend logs).

### T3.2 — Collapse page count
Merge overlaps: ISO Mapper → into the Enrichment page; Terminal Emulator + Transaction
Builder → one **Originate** page. Target ~7 pages. Remove the duplicate markdown nav.
**Acceptance:** page count drops to ≤8 with no lost function; nav is sidebar-only.

### T3.3 — Terminology + dead-code sweep
Fix JPBOS→JPOS, JCF→JPF, e2MS→e2ePS across `frontend/`; remove unused session_state keys and dead helpers.
**Acceptance:** a terminology grep is clean; `vulture`/manual pass finds no dead UI helpers.

## WS-4 · GROWER — iterate for PMF
### T4.1 — First-run onboarding to the "aha"
A guided first-run path (banner/wizard) that takes a new user from landing to a scored
PASS/FAIL in under 60 seconds; dismissible, shown only when no runs exist.
**Acceptance:** a fresh session reaches a scored run in ≤3 clicks.

### T4.2 — Certification report as the shareable artifact
Make `09_certification` produce a one-click branded HTML/PDF (reuse `html_report.py`) with a
copy/download/share affordance, since the report is what spreads inside a buyer's org.
**Acceptance:** one click yields a downloadable branded report from the active SUT.

### T4.3 — Empty states + provider badge everywhere
Every list has an empty state with a next-step CTA ("no scenarios → generate with AI"); the
`/ai/status` badge appears on every AI surface so the copilot never silently fails.
**Acceptance:** empty pages guide the next action; the provider badge is visible app-wide.

## WS-5 · MAINTAINER — secure, reliable, fast at scale
### T5.1 — Secret hygiene
Audit `11_ai_settings.py`: keys shown only as detected/`••••`, never rendered or logged;
entries go to the server-side encrypted store, never session_state in plain text.
**Acceptance:** `grep` for a test key across logs/session is clean; UI shows masked status only.

### T5.2 — Error boundaries + async states
Wrap every API call in `api_client` with timeout + try/except → friendly inline error (never a
raw stack trace or raw JSON); add spinners/skeletons on every fetch.
**Acceptance:** killing the backend yields friendly messages app-wide, not stack traces.

### T5.3 — Performance budget + framework-ceiling note
Paginate `/history` and analytics; avoid re-rendering heavy components on every rerun. Document
in `docs/ui_architecture.md` that Streamlit is right for the pilot, with a React/Next migration
as a **future** option **only** if/when this becomes multi-tenant SaaS — not now.
**Acceptance:** history/analytics paginate; the architecture note exists with the migration trigger spelled out.

---

## Recommended execution order
1. **P0 foundation:** T2.1 (theme) + T2.2 (rebrand) + T3.1 (caching) + T5.1 (secret hygiene) — fast, cross-cutting, unblock everything.
2. **First impression:** T2.3 (home) + T2.4 (horizontal trace).
3. **Adoption:** T4.1–T4.3.
4. **Hardening + simplify:** T3.2–T3.3 + T5.2–T5.3.
WS-1 is continuous — park ideas, don't build them speculatively.

## Guardrails
- One shared theme; no new per-page `<style>` blocks.
- Never render or log a raw API key.
- Don't change backend endpoint paths or response shapes.
- Every task ends with its Acceptance check, recorded in CHANGELOG.md.

## Definition of done
1. Branded Paycon e2ePS shell, one theme, no e2MS/Marqeta/JPBOS/JCF debt.
2. Mission-Control home with live health/provider/cert/runs and one-click demo.
3. Runs render as a horizontal enrichment flow with per-hop payloads.
4. Read-only calls cached; ≤8 pages; friendly error/loading states everywhere.
5. Keys never leak; onboarding reaches a scored run in ≤3 clicks; report is one-click shareable.
