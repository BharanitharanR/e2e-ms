# Paycon e2ePS — UI Idea Backlog (WS-1)

Ideas generated during the UI Enrichment sprint.
**Shipped** = merged to main.  **Parked** = deferred until post-PMF.

---

## ✅ Shipped (UI Enrichment Sprint)

| Idea | Where | Rationale |
|------|-------|-----------|
| Shared Paycon dark theme (`.streamlit/config.toml` + `utils/theme.py`) | All pages | Foundation for brand consistency; unblocks everything else |
| Rebrand shell → "Paycon e2ePS" | `app.py`, all pages | Eliminates e2MS / Marqeta / JPBOS / JCF terminology debt |
| Mission-Control home | `01_home.py` | Live health + cert gauge + one-click presets = first impression |
| AI provider badge in sidebar | `07_ai_copilot.py`, `12_enrichment_trace.py` | Copilot never silently fails — key status visible at all times |
| Cached read-only API calls | `utils/api_client.py` | Eliminates repeated backend hits on every Streamlit rerun |
| Friendly error boundaries | `utils/api_client.py` | Killing the backend now shows a CTA, not a raw stack trace |
| Terminology sweep: JPBOS→JPOS, JCF→JPF | All pages + `iso_mapping.py` + `html_report.py` | Clean language throughout |
| Paycon-branded cert HTML report | `utils/html_report.py` | Shareable artefact now carries the Paycon brand |
| Empty states with next-step CTAs | `01_home.py` | New users are guided to the next action instead of blank pages |

---

## 🅿️ Parked (post-PMF)

| Idea | Rationale for parking |
|------|-----------------------|
| **Cmd-K command palette** | High implementation cost; Streamlit has no native shortcut support; revisit if we go React |
| **Audit-trail replay scrubber** | Nice-to-have UX polish; grouped playback (already shipped) covers 90% of the use case |
| **Always-on AI chat dock** | Needs WebSocket or fragment support; add when backend has a streaming endpoint |
| **Mandate before/after diff viewer** | Mandate workflow exists; a visual diff is additive — park for v2 |
| **Run permalinks (shareable trace URLs)** | Requires persistent trace storage / UUID routing; not needed for single-operator pilot |
| **Card-tap animation (EMV APDU)** | Pure cosmetic; terminal emulator already covers the function |
| **React / Next.js migration** | Only warranted if this becomes multi-tenant SaaS; Streamlit is correct for the pilot phase |
| **T3.2 — Collapse page count to ≤8** | ISO Mapper and Terminal Emulator are used by different personas; merging risks confusion; revisit with user interviews |
| **T4.1 — First-run onboarding wizard** | Preset buttons on Home cover the 60-second path; a full wizard is MVP+ |
| **T5.3 — Paginate analytics / history** | History endpoint already paginates; UI pagination is a polish item for when history grows beyond 1k rows |
