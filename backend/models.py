"""Pydantic request models for webhook payloads."""
from pydantic import BaseModel


class BarData(BaseModel):
    time:   float
    open:   float
    high:   float
    low:    float
    close:  float
    volume: int


class QuoteData(BaseModel):
    bid:  float
    ask:  float
    time: float


class TickPayload(BaseModel):
    live:   dict[str, BarData]
    closed: dict[str, BarData] | None = None
    quote:  QuoteData | None = None


class BatchBarsPayload(BaseModel):
    symbol:     str
    resolution: str
    bars:       list[BarData]
