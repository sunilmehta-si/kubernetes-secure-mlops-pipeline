"""The HTTP server, speaking KServe's v1 inference protocol, so KServe can run it as a custom predictor.

    GET  /healthz                      liveness
    GET  /v1/models/{name}             readiness and identity of the model being served
    POST /v1/models/{name}:predict     {"instances": [{"text": "..."}]} -> {"predictions": [{"team", "confidence"}]}

The app is built around an already verified ServedModel, so it cannot exist without one. Every response names the
model version and the digest of the signed files that produced it.
"""
from __future__ import annotations

import pandas as pd
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .bundle import ServedModel


class Instance(BaseModel):
    text: str


class PredictRequest(BaseModel):
    instances: list[Instance]


def create_app(served: ServedModel) -> FastAPI:
    app = FastAPI(title=f"{served.name} v{served.version}", docs_url=None, redoc_url=None, openapi_url=None)

    def identity() -> dict:
        return {"name": served.name, "version": served.version, "digest": served.digest}

    def known(name: str) -> None:
        if name != served.name:
            raise HTTPException(status_code=404, detail=f"no model named {name}; this server serves {served.name}")

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/v1/models/{name}")
    def model_ready(name: str) -> dict:
        known(name)
        return {**identity(), "ready": True}

    @app.post("/v1/models/{name}:predict")
    def predict(name: str, request: PredictRequest) -> dict:
        known(name)
        frame = pd.DataFrame({"text": [i.text for i in request.instances]})
        teams = served.predict(frame)
        confidence = served.predict_proba(frame).max(axis=1)
        return {"predictions": [{"team": str(t), "confidence": round(float(c), 4)} for t, c in zip(teams, confidence)],
                "model": identity()}

    return app
