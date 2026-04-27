import json
import os
from openai import OpenAI
from state import ConversationState
from tools import ToolHandler

SYSTEM_PROMPT = """You are a professional payment collection agent for a financial services company.
Your job is to guide users through paying their outstanding balance securely over chat.

CONVERSATION FLOW — follow strictly in order:
1. Greet the user and ask for their account ID.
2. Call lookup_account with the provided account ID.
3. If account found: collect their full name AND at least one secondary factor
   (date of birth in YYYY-MM-DD, Aadhaar last 4 digits, or pincode).
4. Call verify_identity once you have full name + at least one secondary factor.
5. If verified: verify_identity returns the outstanding balance — share it with the user
   and ask how much they'd like to pay (partial payment <= balance is allowed).
6. Collect all 6 card fields: card number, CVV, expiry month, expiry year,
   cardholder name, and the amount.
7. Call process_payment with all card details.
8. Communicate the outcome (transaction ID on success, clear reason on failure),
   then close the conversation warmly.

SECURITY RULES — non-negotiable:
- NEVER reveal or repeat back the account holder's DOB, Aadhaar, or pincode —
  not even to confirm what the user said.
- Do NOT share ANY balance information (including zero balance) until verify_identity
  returns verified=true. Even if the balance is ₹0, complete verification first.
- Do NOT call process_payment until verify_identity returns verified=true.
- You MUST call verify_identity and receive verified=true before sharing the balance —
  even if the user mentioned their name earlier in the conversation.
- Do NOT assume verification is complete just because you have seen the user's name.
  A secondary factor (DOB, Aadhaar last 4, or pincode) is ALWAYS required.

VERIFICATION RULES:
- ALWAYS explicitly collect BOTH: (1) full name AND (2) at least one secondary factor.
  Never skip the secondary factor, no matter what was said earlier in the conversation.
- Name matching is strict and case-sensitive. Do not coach the user on capitalisation.
- If verification fails (verified=false with attempts_remaining), say ONLY that verification
  was not successful and how many attempts remain. Ask them to try again with their full name
  and a secondary factor. Do NOT say which field was wrong. Do NOT say "I still need a
  secondary factor" or "I still need your name" — never hint at which part failed.
- After 3 failed attempts (attempts_remaining=0 or locked=true), close the session politely.
- If missing_secondary_factor is returned (no secondary given at all), ask the user for a
  secondary factor without counting it as a failed attempt.

PAYMENT RULES:
- After verification, check the balance first.
- If the balance is 0 or ₹0.00: inform the user there is no outstanding amount due
  and close the conversation warmly. Do NOT ask for card details or payment amount.
- If balance > 0: ask the user how much they want to pay before collecting card details.
- For invalid_card / invalid_cvv / invalid_expiry errors: ask the user to re-enter;
  these are retryable.
- For insufficient_balance: remind the user of the outstanding balance.
- For terminal / network errors: apologise and close cleanly.

TONE: Professional, clear, empathetic. One step at a time.
"""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "lookup_account",
            "description": (
                "Look up a user's account by their account ID. "
                "Call this as soon as the user provides their account ID."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "account_id": {
                        "type": "string",
                        "description": "The account ID provided by the user (e.g. ACC1001)",
                    }
                },
                "required": ["account_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "verify_identity",
            "description": (
                "Verify the user's identity. Requires full name and at least one secondary "
                "factor: date of birth (YYYY-MM-DD), Aadhaar last 4, or pincode. "
                "Call only when you have name + at least one secondary factor."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "full_name": {
                        "type": "string",
                        "description": "Full name as stated by the user (case-sensitive, exact)",
                    },
                    "dob": {
                        "type": "string",
                        "description": "Date of birth in YYYY-MM-DD format (optional)",
                    },
                    "aadhaar_last4": {
                        "type": "string",
                        "description": "Last 4 digits of Aadhaar (optional)",
                    },
                    "pincode": {
                        "type": "string",
                        "description": "Pincode (optional)",
                    },
                },
                "required": ["full_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "process_payment",
            "description": (
                "Process a card payment. Call ONLY after verify_identity returns verified=true. "
                "Collect all fields before calling."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "amount": {
                        "type": "number",
                        "description": "Amount to pay (positive, max 2 decimal places)",
                    },
                    "cardholder_name": {"type": "string"},
                    "card_number": {
                        "type": "string",
                        "description": "Card digits only, no spaces",
                    },
                    "cvv": {"type": "string"},
                    "expiry_month": {"type": "integer", "description": "1-12"},
                    "expiry_year": {
                        "type": "integer",
                        "description": "4-digit year e.g. 2027",
                    },
                },
                "required": [
                    "amount",
                    "cardholder_name",
                    "card_number",
                    "cvv",
                    "expiry_month",
                    "expiry_year",
                ],
            },
        },
    },
]


class Agent:
    def __init__(self):
        self.state = ConversationState()
        self.tool_handler = ToolHandler(self.state)

        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise EnvironmentError("OPENAI_API_KEY environment variable not set.")
        self.client = OpenAI(api_key=api_key)
        self.messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    def next(self, user_input: str) -> dict:
        self.messages.append({"role": "user", "content": user_input})

        for _ in range(10):
            response = self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=self.messages,
                tools=TOOLS,
            )
            msg = response.choices[0].message
            self.messages.append(msg)

            if not msg.tool_calls:
                return {"message": msg.content or ""}

            for tc in msg.tool_calls:
                args = json.loads(tc.function.arguments)
                result = self.tool_handler.execute(tc.function.name, args)
                self.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(result),
                    }
                )

        return {"message": "Something went wrong on my end. Please try again."}
