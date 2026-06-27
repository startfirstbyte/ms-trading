"""Auth dependency for worker → server webhooks (Bearer WEBHOOK_SECRET)."""
from typing import Annotated

from fastapi import Header, HTTPException

from backend.core import config


def verify_webhook(authorization: Annotated[str, Header()]) -> None:
    expected = f"Bearer {config.WEBHOOK_SECRET}"
    if authorization != expected:
        raise HTTPException(401, "Unauthorized")
