"""HTTP quote fixture: positive JSON integers return 200; invalid bodies return 422."""
from fastapi import FastAPI
from pydantic import BaseModel, Field


class QuantityError(ValueError):
    """The pricing operation received a nonpositive item count."""


class QuoteRequest(BaseModel):
    quantity: int


app = FastAPI()


def quote_total(quantity: int) -> int:
    """Price one or more items at 125 minor units each."""
    if quantity <= 0:
        raise QuantityError("Pricing requires a positive item count")
    return quantity * 125


@app.post("/quote")
def create_quote(body: QuoteRequest) -> dict:
    """Reject zero, negatives, booleans and strings at the HTTP validation boundary."""
    return {"quantity": body.quantity, "total_minor": quote_total(body.quantity)}
