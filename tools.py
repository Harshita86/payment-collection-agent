import requests
from state import ConversationState, Stage, AccountData
from validators import luhn_check, is_amex, validate_cvv, validate_expiry, validate_amount

BASE_URL = "https://se-payment-verification-api.service.external.usea2.aws.prodigaltech.com"


class ToolHandler:
    def __init__(self, state: ConversationState):
        self.state = state

    def execute(self, tool_name: str, tool_input: dict) -> dict:
        if tool_name == "lookup_account":
            return self._lookup_account(tool_input.get("account_id", ""))
        if tool_name == "verify_identity":
            return self._verify_identity(tool_input)
        if tool_name == "process_payment":
            return self._process_payment(tool_input)
        return {"error": f"Unknown tool: {tool_name}"}

    # ------------------------------------------------------------------ #

    def _lookup_account(self, account_id: str) -> dict:
        try:
            resp = requests.post(
                f"{BASE_URL}/api/lookup-account",
                json={"account_id": account_id},
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                self.state.account_data = AccountData(
                    account_id=data["account_id"],
                    full_name=data["full_name"],
                    dob=data["dob"],
                    aadhaar_last4=data["aadhaar_last4"],
                    pincode=data["pincode"],
                    balance=data["balance"],
                )
                # Reset verification state for fresh account lookup
                self.state.verified = False
                self.state.verification_attempts = 0
                self.state.payment_complete = False
                self.state.stage = Stage.VERIFICATION
                return {
                    "success": True,
                    "message": "Account found. Please collect the user's full name and one secondary factor.",
                }
            if resp.status_code == 404:
                return {
                    "success": False,
                    "error_code": "account_not_found",
                    "message": "No account found with that ID.",
                }
            return {
                "success": False,
                "error_code": "api_error",
                "message": "Service temporarily unavailable.",
            }
        except requests.RequestException:
            return {
                "success": False,
                "error_code": "network_error",
                "message": "Could not reach account service. Please try again.",
            }

    def _verify_identity(self, inputs: dict) -> dict:
        if not self.state.account_data:
            return {"verified": False, "message": "Account not loaded. Please provide your account ID first."}

        if self.state.verified:
            return {"verified": True, "message": "Already verified."}

        if self.state.verification_attempts >= self.state.MAX_VERIFICATION_ATTEMPTS:
            return {
                "verified": False,
                "locked": True,
                "attempts_remaining": 0,
                "message": "Maximum verification attempts exceeded.",
            }

        # Require at least one secondary factor before counting as an attempt
        has_secondary = any([inputs.get("dob"), inputs.get("aadhaar_last4"), inputs.get("pincode")])
        if not has_secondary:
            return {
                "verified": False,
                "error": "missing_secondary_factor",
                "message": "Please provide at least one: date of birth (YYYY-MM-DD), Aadhaar last 4, or pincode.",
            }

        acc = self.state.account_data
        name_match = inputs.get("full_name", "") == acc.full_name

        secondary_match = False
        if inputs.get("dob") and inputs["dob"] == acc.dob:
            secondary_match = True
        if not secondary_match and inputs.get("aadhaar_last4") and inputs["aadhaar_last4"] == acc.aadhaar_last4:
            secondary_match = True
        if not secondary_match and inputs.get("pincode") and inputs["pincode"] == acc.pincode:
            secondary_match = True

        if name_match and secondary_match:
            self.state.verified = True
            self.state.stage = Stage.PAYMENT_COLLECTION
            return {
                "verified": True,
                "balance": self.state.account_data.balance,
                "message": "Identity verified successfully.",
            }

        self.state.verification_attempts += 1
        remaining = self.state.MAX_VERIFICATION_ATTEMPTS - self.state.verification_attempts

        if remaining <= 0:
            self.state.stage = Stage.CLOSED
            return {
                "verified": False,
                "locked": True,
                "attempts_remaining": 0,
                "message": "Maximum verification attempts exceeded. Session locked.",
            }

        return {
            "verified": False,
            "attempts_remaining": remaining,
            "message": f"Verification failed. {remaining} attempt(s) remaining.",
        }

    def _process_payment(self, inputs: dict) -> dict:
        if not self.state.verified:
            return {
                "success": False,
                "error_code": "not_verified",
                "message": "Identity must be verified before processing payment.",
            }

        amount = inputs.get("amount")
        valid, msg = validate_amount(amount)
        if not valid:
            return {"success": False, "error_code": "invalid_amount", "message": msg}

        if float(amount) > self.state.account_data.balance:
            return {
                "success": False,
                "error_code": "insufficient_balance",
                "message": f"Amount exceeds outstanding balance of ₹{self.state.account_data.balance:.2f}.",
            }

        card_number = str(inputs.get("card_number", "")).replace(" ", "").replace("-", "")
        if not luhn_check(card_number):
            return {"success": False, "error_code": "invalid_card", "message": "Card number is invalid."}

        cvv = str(inputs.get("cvv", ""))
        amex = is_amex(card_number)
        if not validate_cvv(cvv, amex):
            expected = 4 if amex else 3
            return {
                "success": False,
                "error_code": "invalid_cvv",
                "message": f"CVV must be {expected} digits.",
            }

        expiry_month = inputs.get("expiry_month")
        expiry_year = inputs.get("expiry_year")
        if not validate_expiry(expiry_month, expiry_year):
            return {
                "success": False,
                "error_code": "invalid_expiry",
                "message": "Card expiry is invalid or the card has expired.",
            }

        try:
            payload = {
                "account_id": self.state.account_data.account_id,
                "amount": round(float(amount), 2),
                "payment_method": {
                    "type": "card",
                    "card": {
                        "cardholder_name": inputs["cardholder_name"],
                        "card_number": card_number,
                        "cvv": cvv,
                        "expiry_month": int(expiry_month),
                        "expiry_year": int(expiry_year),
                    },
                },
            }
            resp = requests.post(
                f"{BASE_URL}/api/process-payment",
                json=payload,
                timeout=10,
            )
            data = resp.json()

            if resp.status_code == 200 and data.get("success"):
                self.state.payment_complete = True
                self.state.stage = Stage.CLOSED
                return {
                    "success": True,
                    "transaction_id": data["transaction_id"],
                    "amount_paid": round(float(amount), 2),
                    "message": "Payment processed successfully.",
                }

            error_map = {
                "insufficient_balance": "The amount exceeds the outstanding balance.",
                "invalid_amount": "The payment amount is invalid.",
                "invalid_card": "The card number is invalid.",
                "invalid_cvv": "The CVV is incorrect.",
                "invalid_expiry": "The card expiry is invalid or the card has expired.",
            }
            error_code = data.get("error_code", "unknown_error")
            return {
                "success": False,
                "error_code": error_code,
                "message": error_map.get(error_code, "Payment failed. Please try again."),
            }

        except requests.RequestException:
            return {
                "success": False,
                "error_code": "network_error",
                "message": "Could not reach payment service. Please try again.",
            }
