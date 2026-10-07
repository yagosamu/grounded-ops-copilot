"""Small authenticated contracts used by the web interface."""

from typing import Annotated, Literal, Protocol

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from interfaces.http.auth import PrincipalDependency
from modules.policy.authorizer import Principal


class FeedbackStore(Protocol):
    def record(self, principal: Principal, answer_id: str, rating: str) -> None: ...


class FeedbackBody(BaseModel):
    answer_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    rating: Literal["helpful", "not_helpful"]


def create_ui_support_router(
    authenticate: PrincipalDependency, feedback_store: FeedbackStore | None = None
) -> APIRouter:
    router = APIRouter(prefix="/v1", tags=["web"])

    @router.get("/session")
    def session(
        principal: Annotated[Principal, Depends(authenticate)],
    ) -> dict[str, str]:
        return {"principal_id": principal.id, "tenant_id": principal.tenant_id}

    if feedback_store is not None:

        @router.post("/feedback", status_code=status.HTTP_201_CREATED)
        def feedback(
            body: FeedbackBody,
            principal: Annotated[Principal, Depends(authenticate)],
        ) -> dict[str, bool]:
            try:
                feedback_store.record(principal, body.answer_id, body.rating)
            except Exception as error:
                raise HTTPException(
                    status_code=503, detail="feedback temporarily unavailable"
                ) from error
            return {"recorded": True}

    return router
