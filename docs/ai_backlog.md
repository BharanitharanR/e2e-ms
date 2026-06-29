# Paycon e2ePS — AI Feature Backlog

Ideas generated during the Improvements sprint (P4).
**Shipped** = integrated and active.  **Parked** = deferred until post-PMF.

---

## ✅ Shipped (AI Features — Active)

| Feature | Where | Rationale |
|---------|-------|-----------|
| **AI Mandate → Scenario generation** | `07_ai_copilot.py`, `backend/ai_routes.py` | Headline differentiator — operator describes a business mandate in plain English; AI generates a compliant scenario JSON. |
| **"Explain this DE" inline Copilot** | `04_iso_mapper.py` (DE Reference tab) | Operators can ask the AI to explain any ISO 8583 data element in context; reduces friction for non-ISO experts. |
| **AI anomaly detection** | `08_analytics.py`, `backend/ai_routes.py` | Copilot scans the last N transactions and flags unusual RC patterns (e.g. unexpected surge in RC 05, RC 91 spikes). |
| **AI test-data synthesis** | `07_ai_copilot.py`, `backend/ai_routes.py` | Given a target certification profile, AI generates a minimal scenario set that covers all required lifecycle events and RC codes. |
| **AI provider badge (sidebar)** | `01_home.py`, `12_enrichment_trace.py` | Always-visible badge shows which AI provider is active (Claude / GPT-4o / Ollama) and key status; Copilot never silently fails. |
| **Multi-provider support** | `backend/ai_config.py`, `backend/ai_routes.py` | Claude (Anthropic) primary, GPT-4o secondary, local Ollama fallback; switchable from **AI Settings** page. |

---

## 🅿️ Parked (Post-PMF)

| Feature | Rationale for Parking |
|---------|----------------------|
| **Conversational AI chat dock** | Requires WebSocket or Streamlit fragment streaming; add when backend has a persistent streaming endpoint. |
| **Audit-trail replay scrubber** | Grouped playback (shipped) covers 90% of the use case; scrubber is additive polish for v2. |
| **Canonical-model diff timeline** | AI could compare two transaction traces side-by-side and call out field-level deltas; complex UX, park for post-PMF. |
| **AI-powered RC root-cause advisor** | When a suite fails, AI suggests root cause from JIT webhook logs + RC code; high value but requires enriched log streaming. |
| **Natural-language query over history** | "Show me all Mastercard declines this week over $100" — requires vector index on transaction history. |
| **AI mandate compliance checker** | Upload a regulatory mandate PDF; AI generates a coverage heatmap against the current scenario suite. |
| **Auto-generated certification narrative** | AI drafts the human-readable certification letter from the `/certify` JSON result; park until cert workflow is customer-facing. |
| **Federated learning anomaly baseline** | Train per-customer baseline on their own transaction patterns for personalised anomaly alerts; requires persistent model store. |

---

## 📌 Implementation Notes

### Mandate → Implementation (Headline Flow)

```
User types: "My JIT must approve all contactless transactions under $50"
     ↓
AI extracts:  event_type=authorization, pos_entry_mode=071, amount≤5000, expected_decision=APPROVED
     ↓
Backend:      /ai/mandate → MandateProposal JSON
     ↓
UI review:    Operator approves/edits proposal
     ↓
Backend:      /generate → saves scenario
     ↓
UI:           One-click "Run now" → /execute/{id}
```

### Provider Priority Order

```
1. Claude (Anthropic) — primary; requires ANTHROPIC_API_KEY
2. OpenAI GPT-4o      — secondary; requires OPENAI_API_KEY
3. Ollama (local)     — offline fallback; requires OLLAMA_URL
```

### Security / PII

- AI prompts **never** include clear PAN. All PAN values are tokenised before leaving the backend.
- API keys are stored in Fernet AES-GCM encrypted store at `~/.paycon/secrets` (see `backend/ai_config.py`).
- Keys are never logged, never returned in API responses, never included in AI prompts.
