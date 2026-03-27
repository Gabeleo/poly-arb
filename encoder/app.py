"""Cross-encoder scoring service using stsb-TinyBERT-L-4."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from functools import partial

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sentence_transformers import CrossEncoder

_model: CrossEncoder | None = None


class ScoreRequest(BaseModel):
    pairs: list[list[str]]


class ScoreResponse(BaseModel):
    scores: list[float]


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _model
    _model = CrossEncoder("cross-encoder/stsb-TinyBERT-L-4")
    yield
    _model = None


app = FastAPI(lifespan=lifespan)


@app.get("/health")
async def health():
    if _model is None:
        raise HTTPException(503, "model not loaded")
    return {"status": "ok"}


@app.post("/score", response_model=ScoreResponse)
async def score(req: ScoreRequest):
    if _model is None:
        raise HTTPException(503, "model not loaded")
    if not req.pairs:
        return ScoreResponse(scores=[])
    loop = asyncio.get_running_loop()
    raw = await loop.run_in_executor(None, partial(_model.predict, req.pairs))
    # stsb-TinyBERT-L-4 outputs 0-5 (STS-B scale); normalise to 0.0-1.0
    scores = [round(max(0.0, min(1.0, float(s) / 5.0)), 4) for s in raw]
    return ScoreResponse(scores=scores)
