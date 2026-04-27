#!/usr/bin/env python3
"""Automated evaluation framework for the payment collection agent."""

import json
import os
import re
import time
from dataclasses import dataclass, field

from openai import OpenAI
from agent import Agent


@dataclass
class Turn:
    user: str
    must_contain: list = field(default_factory=list)      # ANY of these words must appear
    must_not_contain: list = field(default_factory=list)  # NONE of these words must appear


@dataclass
class Scenario:
    name: str
    description: str
    expected_outcome: str
    turns: list


SCENARIOS = [
    Scenario(
        name="happy_path_dob",
        description="Successful payment verified via date of birth",
        expected_outcome="payment_success",
        turns=[
            Turn("Hi", must_contain=["account"]),
            Turn("ACC1001", must_contain=["name"]),
            Turn("Nithin Jain", must_contain=["date", "aadhaar", "pincode", "secondary", "verify factor"]),
            Turn(
                "My date of birth is 1990-05-14",
                must_contain=["verified", "balance"],
                must_not_contain=["1990-05-14"],
            ),
            Turn("I want to pay the full amount of 1250.75", must_contain=["card"]),
            Turn(
                "Card: 4532015112830366, CVV: 123, Expiry: 12/2027, Name: Nithin Jain",
                must_contain=["transaction"],
            ),
        ],
    ),
    Scenario(
        name="happy_path_aadhaar",
        description="Successful payment verified via Aadhaar last 4",
        expected_outcome="payment_success",
        turns=[
            Turn("Hello"),
            Turn("ACC1001"),
            Turn("Nithin Jain"),
            Turn("Aadhaar last 4 is 4321", must_contain=["verified"]),
            Turn("Pay 500"),
            Turn(
                "4532015112830366, CVV 123, expires 12 2027, cardholder Nithin Jain",
                must_contain=["transaction"],
            ),
        ],
    ),
    Scenario(
        name="partial_payment",
        description="User makes a partial payment less than the balance",
        expected_outcome="payment_success",
        turns=[
            Turn("Hi"),
            Turn("My account ID is ACC1001"),
            Turn("Nithin Jain"),
            Turn("DOB 1990-05-14", must_contain=["verified"]),
            Turn("I want to pay only 500"),
            Turn(
                "4532015112830366, 123, 12, 2027, Nithin Jain",
                must_contain=["transaction"],
            ),
        ],
    ),
    Scenario(
        name="verification_lockout",
        description="User fails verification 3 times (name+DOB wrong each time) — session locks",
        expected_outcome="verification_locked",
        turns=[
            Turn("Hello"),
            Turn("ACC1001"),
            Turn("My full name is John Smith and my date of birth is 1990-01-01",
                 must_contain=["remaining", "attempt", "not successful", "verification"]),
            Turn("My full name is Jane Doe and my date of birth is 1990-01-01",
                 must_contain=["remaining", "attempt", "not successful", "verification"]),
            Turn(
                "My full name is Bob Jones and my date of birth is 1990-01-01",
                must_contain=["locked", "exhausted exceeded maximum", "support", "contact reach out", "session", "security"],
                must_not_contain=["card", "balance"],
            ),
        ],
    ),
    Scenario(
        name="invalid_account",
        description="Account ID does not exist",
        expected_outcome="account_not_found",
        turns=[
            Turn("Hi"),
            Turn(
                "ACC9999",
                must_contain=["no account", "account", "check", "double-check", "try again", "associated"],
            ),
        ],
    ),
    Scenario(
        name="zero_balance",
        description="Account with zero balance — verified then told no payment needed",
        expected_outcome="zero_balance",
        turns=[
            Turn("Hi"),
            Turn("ACC1003"),
            Turn("Priya Agarwal"),
            Turn(
                "My date of birth is 1992-08-10",
                # Agent may say "balance is ₹0" OR "no outstanding amount due" OR "no payment needed" — all valid
                must_contain=["verified", "balance outstanding payment", "no 0 zero"],
                must_not_contain=["1992-08-10"],
            ),
        ],
    ),
    Scenario(
        name="leap_year_dob",
        description="ACC1004 with exact leap year DOB 1988-02-29 — must verify",
        expected_outcome="verification_pass",
        turns=[
            Turn("Hi"),
            Turn("ACC1004"),
            Turn("Rahul Mehta"),
            Turn("My DOB is 1988-02-29", must_contain=["verified"]),
        ],
    ),
    Scenario(
        name="wrong_leap_year_dob",
        description="ACC1004 with wrong DOB 1988-02-28. Agent should fail the attempt and show attempts remaining. Session should NOT be locked yet (only 1 of 3 attempts used). Balance must NOT be shown.",
        expected_outcome="One verification attempt fails. Agent tells user attempts remaining. Does not show balance or proceed to payment.",
        turns=[
            Turn("Hi"),
            Turn("ACC1004"),
            Turn("Rahul Mehta"),
            Turn(
                "My DOB is 1988-02-28",
                must_contain=["not successful", "remaining", "attempt", "verification"],
                must_not_contain=["verified successfully", "balance"],
            ),
        ],
    ),
    Scenario(
        name="invalid_card",
        description="User provides a card number that fails Luhn check",
        expected_outcome="payment_failed_invalid_card",
        turns=[
            Turn("Hi"),
            Turn("ACC1001"),
            Turn("Nithin Jain"),
            Turn("DOB 1990-05-14", must_contain=["verified"]),
            Turn("Pay 500"),
            # Provide card details in unambiguous structured format
            Turn(
                "card number 1234567890123456 cvv 123 expiry month 12 expiry year 2027 cardholder Nithin Jain",
                must_contain=["invalid", "card", "valid", "number"],
                must_not_contain=["transaction", "success"],
            ),
        ],
    ),
    Scenario(
        name="expired_card",
        description="User provides an expired card",
        expected_outcome="payment_failed_expired",
        turns=[
            Turn("Hi"),
            Turn("ACC1001"),
            Turn("Nithin Jain"),
            Turn("DOB 1990-05-14", must_contain=["verified"]),
            Turn("Pay 500"),
            Turn(
                "Card 4532015112830366, CVV 123, expiry 01/2020, Nithin Jain",
                must_contain=["expir"],
                must_not_contain=["transaction", "success"],
            ),
        ],
    ),
    Scenario(
        name="out_of_order_info",
        description="User volunteers name early — agent must still ask for secondary factor",
        expected_outcome="secondary_factor_still_required",
        turns=[
            Turn("Hi, I'm Nithin Jain and I want to pay my bill"),
            Turn("My account ID is ACC1001"),
            Turn(
                "What next",
                must_contain=["date", "aadhaar", "pincode", "secondary", "date of birth", "verify"],
                must_not_contain=["balance", "1250"],
            ),
        ],
    ),
]


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #

def _check_turn(msg: str, turn: Turn) -> list:
    checks = []
    for kw in turn.must_contain:
        # Support OR matching: any word in a space-separated group counts
        words = kw.lower().split()
        passed = any(w in msg.lower() for w in words)
        checks.append({"type": "must_contain", "keyword": kw, "passed": passed})
    for kw in turn.must_not_contain:
        checks.append({
            "type": "must_not_contain",
            "keyword": kw,
            "passed": kw.lower() not in msg.lower(),
        })
    return checks


def run_scenario(scenario: Scenario) -> dict:
    agent = Agent()
    turn_results = []
    conversation = []

    for i, turn in enumerate(scenario.turns):
        response = None
        for attempt in range(4):
            try:
                response = agent.next(turn.user)
                break
            except Exception as e:
                if "429" in str(e) or "rate" in str(e).lower() or "quota" in str(e).lower():
                    wait = 30 * (attempt + 1)
                    print(f"    Rate limit hit, waiting {wait}s...", flush=True)
                    time.sleep(wait)
                else:
                    raise
        if response is None:
            print(f"    SKIPPED — rate limit not resolved after retries")
            return {"scenario": scenario.name, "description": scenario.description,
                    "expected_outcome": scenario.expected_outcome, "turn_results": [],
                    "keyword_score": 0, "llm_judge": {"pass": False, "score": 0,
                    "reasoning": "Rate limit", "issues": []}, "overall_pass": False}
        msg = response["message"]
        conversation.append({"user": turn.user, "agent": msg})
        checks = _check_turn(msg, turn)
        turn_results.append({"turn": i + 1, "user": turn.user, "agent": msg, "checks": checks})

    all_checks = [c for r in turn_results for c in r["checks"]]
    keyword_score = (
        sum(1 for c in all_checks if c["passed"]) / len(all_checks) if all_checks else 1.0
    )

    judge = _llm_judge(scenario, conversation)
    judge_ok = judge.get("pass", False) or judge.get("score", 0) >= 7
    overall = keyword_score >= 0.75 and judge_ok

    return {
        "scenario": scenario.name,
        "description": scenario.description,
        "expected_outcome": scenario.expected_outcome,
        "turn_results": turn_results,
        "keyword_score": round(keyword_score, 3),
        "llm_judge": judge,
        "overall_pass": overall,
    }


def _llm_judge(scenario: Scenario, conversation: list) -> dict:
    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    conv_text = "\n".join(f"User: {t['user']}\nAgent: {t['agent']}" for t in conversation)

    prompt = f"""You are evaluating a payment collection AI agent. Your job is to check whether the agent achieved the SPECIFIC expected outcome for this scenario.

Scenario: {scenario.description}
Expected outcome: {scenario.expected_outcome}

Conversation:
{conv_text}

Evaluation rules:
1. Judge ONLY against the "Expected outcome" above — not a generic ideal flow. If the expected outcome is that verification FAILS, then the agent correctly FAILING verification is a PASS, not a FAIL.
2. The agent should NOT reveal sensitive data (DOB, Aadhaar, pincode) verbatim in responses.
3. The agent should communicate clearly and professionally.
4. The agent may combine multiple steps in one response — this is acceptable, do not penalise.
5. If the expected outcome says "verification attempt fails and agent shows attempts remaining" — the agent PASSING this means it said something like "verification was not successful, X attempts remaining." That IS the correct behavior.
6. Do not penalise the agent for behaviour that is consistent with the expected outcome even if it seems unusual (e.g., rejecting a wrong DOB is correct when testing a wrong DOB scenario).

Respond in JSON only (no markdown):
{{"pass": true/false, "score": 0-10, "reasoning": "brief explanation", "issues": ["issue1", "issue2"]}}"""

    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=400,
        )
        text = resp.choices[0].message.content
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group())
    except Exception as e:
        return {"pass": False, "score": 0, "reasoning": f"Judge error: {e}", "issues": []}
    return {"pass": False, "score": 0, "reasoning": "Could not parse judge response", "issues": []}


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def main():
    print("=" * 70)
    print("  Payment Agent — Evaluation Suite")
    print("=" * 70)

    all_results = []
    passed = 0

    for scenario in SCENARIOS:
        print(f"\nRunning: {scenario.name} ...", flush=True)
        time.sleep(3)  # avoid rate limits on new accounts
        result = run_scenario(scenario)
        all_results.append(result)

        status = "PASS" if result["overall_pass"] else "FAIL"
        if result["overall_pass"]:
            passed += 1

        judge = result["llm_judge"]
        print(f"  [{status}]  keyword: {result['keyword_score']:.0%}  "
              f"llm-judge: {judge.get('score', '?')}/10  — {judge.get('reasoning', '')[:80]}")

        failed_checks = [
            c for r in result["turn_results"] for c in r["checks"] if not c["passed"]
        ]
        for fc in failed_checks:
            print(f"    ✗ turn {[r['turn'] for r in result['turn_results'] if fc in r['checks']][0]}"
                  f" | {fc['type']}: '{fc['keyword']}'")

        if judge.get("issues"):
            for issue in judge["issues"]:
                print(f"    ⚠  {issue}")

    print("\n" + "=" * 70)
    print(f"  Total: {passed}/{len(SCENARIOS)} passed  ({passed/len(SCENARIOS):.0%})")
    print("=" * 70)

    with open("eval_results.json", "w") as f:
        json.dump(all_results, f, indent=2, default=str)
    print("\nDetailed results → eval_results.json")


if __name__ == "__main__":
    main()
