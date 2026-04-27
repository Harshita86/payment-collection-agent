from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Stage(Enum):
    GREETING = "greeting"
    VERIFICATION = "verification"
    PAYMENT_COLLECTION = "payment_collection"
    CLOSED = "closed"


@dataclass
class AccountData:
    account_id: str
    full_name: str
    dob: str
    aadhaar_last4: str
    pincode: str
    balance: float


@dataclass
class ConversationState:
    stage: Stage = Stage.GREETING
    account_data: Optional[AccountData] = None
    verified: bool = False
    verification_attempts: int = 0
    MAX_VERIFICATION_ATTEMPTS: int = 3
    payment_complete: bool = False
