# Payment Collection AI Agent

A production-ready conversational AI agent that handles end-to-end payment collection with strict identity verification.

## Setup

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Set your OpenAI API key
```bash
export OPENAI_API_KEY=sk-...
```

### 3. Run interactively
```bash
python3 cli.py
```

### 4. Run the evaluation suite
```bash
python3 evaluate.py
```

---

## Project Structure

```
agent.py          # Agent class — required interface: Agent.next()
state.py          # ConversationState + Stage FSM
tools.py          # ToolHandler (API calls, verification logic)
validators.py     # Luhn check, CVV, expiry, amount validation
cli.py            # Interactive CLI runner
evaluate.py       # Automated evaluation suite (11 scenarios, LLM judge)
requirements.txt
README.md
DESIGN.md         # Architecture, decisions, tradeoffs
conversations.md  # Real sample conversation transcripts
eval_results.json # Latest evaluation run output
```

---

## Required Interface

```python
from agent import Agent

agent = Agent()
response = agent.next("Hi")
# → {"message": "Hello! Could you please provide your account ID?"}

response = agent.next("ACC1001")
# → {"message": "Thank you. Could you please provide your full name?"}

response = agent.next("Nithin Jain")
# → {"message": "Could you provide a secondary factor: DOB, Aadhaar last 4, or pincode?"}

response = agent.next("DOB is 1990-05-14")
# → {"message": "Identity verified. Your outstanding balance is ₹1,250.75..."}
```

- `Agent()` initialises a fresh conversation — no external setup needed
- Each `next(user_input)` call processes one turn and returns `{"message": str}`
- All state is maintained internally between calls

---

## Test Accounts

| Account ID | Full Name | DOB | Aadhaar Last 4 | Pincode | Balance |
|---|---|---|---|---|---|
| ACC1001 | Nithin Jain | 1990-05-14 | 4321 | 400001 | ₹1,250.75 |
| ACC1002 | Rajarajeswari Balasubramaniam | 1985-11-23 | 9876 | 400002 | ₹540.00 |
| ACC1003 | Priya Agarwal | 1992-08-10 | 2468 | 400003 | ₹0.00 |
| ACC1004 | Rahul Mehta | 1988-02-29 | 1357 | 400004 | ₹3,200.50 |

> ACC1004's DOB (1988-02-29) is a valid leap year date and is accepted exactly as-is.

---

## Evaluation

`evaluate.py` runs 11 scripted scenarios through the agent automatically:

| Scenario | What it tests |
|---|---|
| happy_path_dob | Full flow verified via DOB |
| happy_path_aadhaar | Full flow verified via Aadhaar |
| partial_payment | Amount < balance |
| verification_lockout | 3 wrong attempts → locked |
| invalid_account | Non-existent account ID |
| zero_balance | ACC1003, ₹0 balance |
| leap_year_dob | ACC1004 DOB = 1988-02-29 |
| wrong_leap_year_dob | ACC1004 wrong DOB → fails |
| invalid_card | Luhn check failure → re-prompt |
| expired_card | Expired card → re-prompt |
| out_of_order_info | Name given early → secondary factor still required |

Each scenario uses:
1. **Keyword checks** — must/must-not-contain assertions per turn
2. **LLM judge** — GPT-4o-mini evaluator scores flow, security, clarity 0–10

Results written to `eval_results.json`.

**Latest run: 11/11 passed (100%)**

---

## Security Design

- Sensitive account fields (DOB, Aadhaar, pincode) are **never sent to the LLM** — stored only in Python state
- Verification is pure Python `==` comparison — zero LLM involvement
- Raw card data is used for a single API call then not retained
- Session locks after 3 failed verification attempts
