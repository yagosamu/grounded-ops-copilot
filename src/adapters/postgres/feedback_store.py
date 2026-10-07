"""Append-only answer ratings without prompt, answer or token storage."""

import hmac
from hashlib import sha256
from uuid import uuid4

from sqlalchemy import Engine, text

from modules.policy.authorizer import Principal


class PostgresFeedbackStore:
    def __init__(self, engine: Engine, redaction_key: bytes) -> None:
        if not redaction_key:
            raise ValueError("redaction key is required")
        self._engine = engine
        self._redaction_key = redaction_key

    def _ref(self, label: str, value: str) -> str:
        return hmac.new(
            self._redaction_key, f"{label}\0{value}".encode(), sha256
        ).hexdigest()

    def record(self, principal: Principal, answer_id: str, rating: str) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                text("""
                    INSERT INTO answer_feedback (
                        feedback_id, tenant_ref, principal_ref, answer_id, rating
                    ) VALUES (
                        :feedback_id, :tenant_ref, :principal_ref, :answer_id, :rating
                    )
                """),
                {
                    "feedback_id": uuid4().hex,
                    "tenant_ref": self._ref("tenant", principal.tenant_id),
                    "principal_ref": self._ref("actor", principal.id),
                    "answer_id": answer_id,
                    "rating": rating,
                },
            )
