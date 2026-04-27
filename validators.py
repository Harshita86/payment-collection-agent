from datetime import date
from decimal import Decimal, InvalidOperation


def luhn_check(card_number: str) -> bool:
    digits = card_number.replace(" ", "").replace("-", "")
    if not digits.isdigit() or not (13 <= len(digits) <= 19):
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        n = int(d)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def is_amex(card_number: str) -> bool:
    digits = card_number.replace(" ", "").replace("-", "")
    return digits.startswith("34") or digits.startswith("37")


def validate_cvv(cvv: str, amex: bool = False) -> bool:
    if not cvv.isdigit():
        return False
    return len(cvv) == (4 if amex else 3)


def validate_expiry(month, year) -> bool:
    try:
        month, year = int(month), int(year)
        if not (1 <= month <= 12):
            return False
        today = date.today()
        if year < today.year:
            return False
        if year == today.year and month < today.month:
            return False
        return True
    except (TypeError, ValueError):
        return False


def validate_amount(amount) -> tuple:
    try:
        d = Decimal(str(amount))
        if d <= 0:
            return False, "Amount must be greater than zero."
        if d.as_tuple().exponent < -2:
            return False, "Amount cannot have more than 2 decimal places."
        return True, ""
    except (InvalidOperation, TypeError):
        return False, "Invalid amount format."
