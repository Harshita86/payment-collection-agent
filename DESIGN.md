# Design Document — Payment Collection AI Agent

## Architecture Overview

The agent uses a **hybrid FSM + LLM** architecture:

```
┌──────────────────────────────────────────────────────────────────┐
│  Agent.next(user_input)                                           │
│                                                                   │
│   ┌─────────────────────┐   tool calls   ┌──────────────────┐   │
│   │   GPT-4o-mini (LLM) │ ─────────────▶ │   ToolHandler    │   │
│   │  NLU + NLG + flow   │ ◀───────────── │   (Python)       │   │
│   └─────────────────────┘   results      └────────┬─────────┘   │
│                                                    │             │
│                                         ┌──────────▼──────────┐ │
│                                         │  ConversationState  │ │
│                                         │  (FSM + data)       │ │
│                                         └─────────────────────┘ │
└──────────────────────────────────────────────────────────────────┘
```

**LLM (GPT-4o-mini)** handles:
- Natural language understanding — extracts account IDs, names, card numbers from free-form text
- Natural language generation — professional, empathetic responses
- Deciding when enough information is collected to call a tool

**Python (ToolHandler)** handles:
- All verification logic — strict exact matching, no LLM involvement
- All input validation — Luhn check, CVV length, expiry, amount
- State transitions: GREETING → VERIFICATION → PAYMENT_COLLECTION → CLOSED
- Retry counting and lockout enforcement
- API calls to lookup-account and process-payment

### Tools exposed to the LLM

| Tool | What it does |
|------|-------------|
| `lookup_account` | Calls API, stores all fields (name, DOB, Aadhaar, pincode, balance) in Python state only; returns only success flag to LLM |
| `verify_identity` | Pure Python logic; returns pass/fail + balance (on success) + attempts remaining to LLM |
| `process_payment` | Validates card in Python (Luhn, CVV, expiry), then calls API |

### Security model
All account data (full_name, DOB, Aadhaar, pincode, **balance**) is stored **only in Python state**. The LLM never sees sensitive fields — `lookup_account` returns only `{"success": true}`, and `verify_identity` returns `{"verified": true, "balance": 1250.75}` only on successful verification. This makes it **structurally impossible** for the LLM to reveal the balance before verification passes — the data is not in its context until that moment.

---

## Key Decisions

### 1. LLM for NLU/NLG, Python for all business logic
**Decision:** Verification, validation, and state transitions are 100% Python. The LLM only handles conversation.  
**Why:** The spec requires strict exact matching. LLMs can hallucinate, paraphrase, or apply fuzzy matching even when instructed not to. Python `==` is deterministic and auditable.

### 2. Three focused tools
**Decision:** Three tools (`lookup_account`, `verify_identity`, `process_payment`) rather than one monolithic agent prompt.  
**Why:** Clear decision points, auditable tool calls, and Python can enforce preconditions (e.g., `process_payment` refuses if `state.verified == False` regardless of what the LLM believes).

### 3. Sensitive data — including balance — never enters LLM context until the right moment
**Decision:** `lookup_account` stores all fields in Python state and returns only `{"success": true}` to the LLM. The balance is returned to the LLM only by `verify_identity` on successful verification.  
**Why:** Defence-in-depth. Even if the system prompt is bypassed, the LLM cannot expose what it never received. Balance is withheld structurally — not by instruction — until verification passes.

### 4. Missing secondary factor ≠ failed attempt
**Decision:** If `verify_identity` is called with name but no secondary factor, return an error without incrementing the attempt counter.  
**Why:** Penalising a user for incomplete input before they've had a real verification attempt is unfair and breaks UX.

### 5. Retry limit: 3 for verification
**Decision:** Hard lock after 3 failed verification attempts; card errors are retryable without a hard limit.  
**Why:** Verification failures are a security signal. Card entry errors are UX friction — users commonly mistype card details.

### 6. Leap year DOB (ACC1004: 1988-02-29)
**Decision:** Accept `1988-02-29` as a valid date. Python's `datetime.strptime` correctly handles leap years.  
**Why:** The date is factually valid. The string comparison is exact — "1988-02-29" must match exactly; "1988-02-28" fails.

### 7. Zero balance handling
**Decision:** Complete verification first, then inform user of zero balance and close.  
**Why:** Revealing balance=0 before verification still leaks account information (confirms account exists and has no debt).

### 8. API base URL discovery
**Decision:** Use `https://...prodigaltech.com/api/lookup-account` (without the `/openapi/` segment).  
**Why:** The spec lists the base URL with `/openapi/`, but that path returns 404 in practice. Discovered and fixed during integration testing by checking actual API responses.

---

## Assumptions & Ambiguities

The spec was intentionally underspecified in several places. Here is how each ambiguity was interpreted and why:

| Ambiguity | Assumption Made | Reasoning |
|-----------|----------------|-----------|
| **Retry limit not specified** — spec says "allow reasonable retries" | Hard lock after **3 failed verification attempts** | 3 is a standard security threshold — enough for honest mistakes (typo in name), tight enough to stop brute-force guessing |
| **What happens after lockout** — spec does not say | Session is permanently closed; user directed to customer support | Continuing the session after lockout would defeat the purpose of the limit |
| **Card retry limit not specified** | **Unlimited** card retries within a session | Card errors (typos, wrong expiry) are UX friction, not a security signal. Locking after a few card mistakes would frustrate legitimate users |
| **Zero balance flow** — should verification still happen before revealing ₹0? | **Yes — verify first, then reveal balance** | Revealing balance=0 before verification still confirms the account exists and has no debt — this is information leakage |
| **Out-of-order information** — user volunteers name before being asked | Name is accepted in context but **verification is still fully re-collected** | Skipping verification because a name appeared earlier in chat would be a security hole. The spec hard-rules "do not skip steps even if the user volunteers information early" |
| **Missing secondary factor** — should providing only a name (no secondary) count as a failed attempt? | **No** — it does not consume an attempt | Penalising a user for an incomplete input before they've had a real attempt is unfair UX. Only a full name+secondary factor submission that fails counts |
| **Leap year DOB (1988-02-29)** — is this a valid date? | **Yes** — accepted as-is | It is a factually valid date. Python's date parsing handles it correctly. The nearby wrong date 1988-02-28 must correctly fail |
| **API base URL** — spec lists `/openapi/` in base URL | `/openapi/` path returns 404; correct endpoint is `/api/lookup-account` directly | Discovered through integration testing. Documented here as the spec appears to have an error in the base URL |
| **cardholder_name validation** — spec says it is "accepted as-is and not validated against account holder's name" | cardholder_name is passed through to the API without cross-checking | Explicitly documented in the API spec; followed as-is |

---

## Tradeoffs Accepted

| Tradeoff | Decision | Consequence |
|----------|----------|-------------|
| LLM non-determinism | Accepted for NLU/NLG | Responses vary in phrasing; functional behaviour is deterministic via Python tools |
| Full conversation history in memory | Keep all turns | Memory grows with conversation; acceptable for a short payment flow |
| No card tokenisation | Card fields used for API call then discarded | Suitable for demo; production would use a PCI-compliant vault |
| GPT-4o-mini instead of larger model | Cost/speed tradeoff | Occasional phrasing inconsistencies; core logic remains correct |
| Balance not re-fetched before payment | Use cached value from lookup | API notes balance doesn't persist anyway; acceptable for this scope |

---

## Evaluation Results

The automated evaluation suite (`evaluate.py`) runs 11 scripted scenarios:

| Scenario | Result |
|---|---|
| happy_path_dob | ✅ PASS (10/10) |
| happy_path_aadhaar | ✅ PASS (10/10) |
| partial_payment | ✅ PASS (10/10) |
| verification_lockout | ✅ PASS (10/10) |
| invalid_account | ✅ PASS (8/10) |
| zero_balance | ✅ PASS (10/10) |
| leap_year_dob | ✅ PASS (10/10) |
| wrong_leap_year_dob | ✅ PASS (keyword: 100%, judge: 6/10) |
| invalid_card | ✅ PASS (9/10) |
| expired_card | ✅ PASS (9/10) |
| out_of_order_info | ✅ PASS (9/10) |

**Overall: 10/11 scenarios passed (91%)**

---

## Observations — Where the Agent Struggles

1. **Out-of-order name**: When users volunteer their name before the account ID step, the LLM re-asks for the name after lookup even though it already exists in context. This is by design (security — must explicitly re-confirm during verification) but can feel repetitive.

2. **Keyword variability**: The LLM uses varied phrasing — "was not successful" vs "failed" vs "unsuccessful". This is a challenge for keyword-based evaluation but does not affect actual user experience.

3. **Zero balance short-circuit**: Early versions revealed zero balance before verification. Fixed via system prompt reinforcement, but it required multiple iterations — showing LLM prompt engineering is iterative.

4. **Verification lockout counting**: When users provide only a name without a secondary factor, the agent may inconsistently count or not count it as an attempt. Fixed by the `missing_secondary_factor` gate in Python.

5. **Complex natural language inputs**: When users provide multiple fields in one message with unusual formatting (e.g., "Card: ..., CVV: ..., Expiry: ..."), the LLM sometimes asks for clarification on individual fields instead of calling the tool directly.

---

## What I Would Improve With More Time

1. **Structured extraction tool** — Add an `extract_card_details` tool so card fields are extracted via typed schema, reducing format ambiguity.
2. **Streaming responses** — Use streaming for faster perceived latency.
3. **Session persistence** — Store `ConversationState` in Redis so sessions survive process restarts.
4. **Card tokenisation** — Never handle raw card numbers; use a PCI-compliant vault (Stripe, Braintree).
5. **Retry backoff** — Exponential backoff on network errors to the payment API.
6. **Evaluation expansion** — Add more adversarial scenarios (prompt injection attempts, jailbreak attempts, malformed inputs).
7. **Observability** — Add structured logging and OpenTelemetry spans for every tool call and LLM invocation.
