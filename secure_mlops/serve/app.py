"""The HTTP server, speaking KServe's v1 inference protocol, so KServe can run it as a custom predictor.

    GET  /healthz                      liveness
    GET  /metrics                      Prometheus: requests, latency, teams, model identity and live drift
    GET  /v1/models/{name}             readiness and identity of the model being served
    POST /v1/models/{name}:predict     {"instances": [{"text": "..."}]} -> {"predictions": [{"team", "confidence"}]}
    POST /v1/models/{name}:feedback    {"request_id": "...", "teams": ["..."]}: the teams agents confirmed, in order

The app is built around an already verified ServedModel, so it cannot exist without one. Every response names the
model version and the digest of the signed files that produced it.
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from collections import Counter

import pandas as pd
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter as Metric, Gauge, Histogram, generate_latest
from prometheus_client.core import GaugeMetricFamily
from pydantic import BaseModel, ConfigDict, Field

from .auth import authenticate
from .bundle import ServedModel
from .feedback import FeedbackError, LiveAccuracy, PredictionLog
from .live_drift import LiveDrift, model_vocabulary
from .ratelimit import TicketLimiter


# Limits on what a caller can send (threat T9): bounded work per request, nothing the model was not built for.
MAX_INSTANCES = 64
MAX_TEXT = 2000
MAX_BODY_BYTES = 256 * 1024
# One JSON line per prediction: which model answered, for whom, how fast. Never the ticket text.
AUDIT = logging.getLogger("smlops.audit")
REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class Instance(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=MAX_TEXT)


class PredictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instances: list[Instance] = Field(min_length=1, max_length=MAX_INSTANCES)


class FeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(pattern=REQUEST_ID.pattern)
    teams: list[str] = Field(min_length=1, max_length=MAX_INSTANCES)


def _bearer(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    return header[7:].strip() if header[:7].lower() == "bearer " else None


class _DriftGauges:
    """Exports the live drift window at scrape time; a value not yet known (too few tickets) is left out, not 0."""
    GAUGES = {"tickets": ("smlops_live_window_tickets", "Tickets in the live drift window"),
              "psi_predicted_team": ("smlops_live_psi_predicted_team", "PSI of predicted teams against training"),
              "new_word_rate": ("smlops_live_new_word_rate", "Share of words the model's vectorizer does not know"),
              "new_word_ratio": ("smlops_live_new_word_ratio", "New-word rate as a multiple of the model's baseline")}

    def __init__(self, drift: LiveDrift):
        self.drift = drift

    def collect(self):
        for key, value in self.drift.snapshot().items():
            if value is not None:
                name, doc = self.GAUGES[key]
                yield GaugeMetricFamily(name, doc, value=value)


def default_drift(served: ServedModel) -> LiveDrift:
    reference = served.reference or {}
    return LiveDrift(reference.get("teams"), model_vocabulary(served.model),
                     baseline_new_word_rate=reference.get("new_word_rate"))


def create_app(served: ServedModel, api_keys: dict[str, str], limiter: TicketLimiter | None = None,
               drift: LiveDrift | None = None, predictions: PredictionLog | None = None,
               accuracy: LiveAccuracy | None = None) -> FastAPI:
    """api_keys: {sha256 of key: client id}; every prediction must present one of the keys. limiter: tickets per
    client; defaults to TicketLimiter() (600 per minute, bursts of 120). drift: the live drift window; defaults to
    one built from the model's signed reference profile. predictions and accuracy: the log feedback confirms
    against, and the live accuracy window measured against the profile's held-out accuracy."""
    limiter = limiter or TicketLimiter()
    drift = drift or default_drift(served)
    predictions = predictions or PredictionLog()
    accuracy = accuracy or LiveAccuracy((served.reference or {}).get("accuracy"))
    app = FastAPI(title=f"{served.name} v{served.version}", docs_url=None, redoc_url=None, openapi_url=None)
    metrics = CollectorRegistry()           # per app: what Prometheus scrapes from /metrics
    requests = Metric("smlops_predict_requests", "Prediction requests by HTTP status", ["status"], registry=metrics)
    tickets = Metric("smlops_tickets_routed", "Tickets routed, by predicted team", ["team"], registry=metrics)
    latency = Histogram("smlops_predict_latency_seconds", "Time to answer a prediction request", registry=metrics,
                        buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5))
    Gauge("smlops_model_info", "The model being served (always 1)", ["name", "version", "digest"],
          registry=metrics).labels(served.name, served.version, served.digest).set(1)
    metrics.register(_DriftGauges(drift))

    @app.middleware("http")
    async def limit_body(request: Request, call_next):
        """Refuse oversized bodies before parsing them; a missing or false Content-Length is checked on the body."""
        declared = request.headers.get("content-length")
        if declared is not None and (not declared.isdigit() or int(declared) > MAX_BODY_BYTES):
            return JSONResponse({"detail": f"request body over {MAX_BODY_BYTES} bytes"}, status_code=413)
        if request.method == "POST" and len(await request.body()) > MAX_BODY_BYTES:
            requests.labels("413").inc()
            return JSONResponse({"detail": f"request body over {MAX_BODY_BYTES} bytes"}, status_code=413)
        started = time.perf_counter()
        response = await call_next(request)
        if request.url.path.endswith(":predict"):
            requests.labels(str(response.status_code)).inc()
            latency.observe(time.perf_counter() - started)
        return response

    def identity() -> dict:
        return {"name": served.name, "version": served.version, "digest": served.digest}

    def known(name: str) -> None:
        if name != served.name:
            raise HTTPException(status_code=404, detail=f"no model named {name}; this server serves {served.name}")

    @app.get("/metrics")
    def scrape() -> PlainTextResponse:
        return PlainTextResponse(generate_latest(metrics).decode(), media_type=CONTENT_TYPE_LATEST)

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/v1/models/{name}")
    def model_ready(name: str) -> dict:
        known(name)
        return {**identity(), "ready": True}

    @app.post("/v1/models/{name}:predict")
    def predict(name: str, request: PredictRequest, raw: Request, response: Response) -> dict:
        client = authenticate(api_keys, _bearer(raw))
        if client is None:
            raise HTTPException(status_code=401, detail="a valid API key is required",
                                headers={"WWW-Authenticate": "Bearer"})
        known(name)
        allowed, wait = limiter.take(client, len(request.instances))
        if not allowed:
            if wait < 0:
                raise HTTPException(status_code=413, detail=f"more tickets than this client may send at once ({limiter.burst:g})")
            raise HTTPException(status_code=429, detail="rate limit reached", headers={"Retry-After": str(wait)})
        started = time.perf_counter()
        supplied = raw.headers.get("x-request-id", "")
        request_id = supplied if REQUEST_ID.match(supplied) else uuid.uuid4().hex
        frame = pd.DataFrame({"text": [i.text for i in request.instances]})
        teams = served.predict(frame)
        confidence = served.predict_proba(frame).max(axis=1)
        for team, n in Counter(map(str, teams)).items():
            tickets.labels(team).inc(n)
        drift.observe(frame["text"], teams)
        predictions.record(request_id, client, [str(t) for t in teams])     # for the agents' feedback later
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Model-Digest"] = served.digest
        AUDIT.info(json.dumps({"event": "predict", "request_id": request_id, "client": client, "model": served.name,
                               "version": served.version, "digest": served.digest, "tickets": len(frame),
                               "teams": dict(sorted(Counter(map(str, teams)).items())),
                               "latency_ms": round((time.perf_counter() - started) * 1000, 2)}, sort_keys=True))
        return {"predictions": [{"team": str(t), "confidence": round(float(c), 4)} for t, c in zip(teams, confidence)],
                "model": identity()}

    known_teams = set(map(str, getattr(served.model, "classes_", []))) or set((served.reference or {}).get("teams", {}))

    @app.post("/v1/models/{name}:feedback")
    def feedback(name: str, request: FeedbackRequest, raw: Request) -> dict:
        client = authenticate(api_keys, _bearer(raw))
        if client is None:
            raise HTTPException(status_code=401, detail="a valid API key is required",
                                headers={"WWW-Authenticate": "Bearer"})
        known(name)
        unknown = sorted(set(request.teams) - known_teams)
        if unknown:
            raise HTTPException(status_code=422, detail=f"not a team this model routes to: {', '.join(unknown)}")
        try:
            pairs = predictions.confirm(request.request_id, client, request.teams)
        except FeedbackError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from None
        accuracy.observe(pairs)
        correct = sum(p == c for p, c in pairs)
        AUDIT.info(json.dumps({"event": "feedback", "request_id": request.request_id, "client": client,
                               "model": served.name, "version": served.version, "digest": served.digest,
                               "tickets": len(pairs), "correct": correct}, sort_keys=True))
        return {"counted": len(pairs), "correct": correct}

    return app
