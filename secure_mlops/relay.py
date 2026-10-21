"""The alert relay: turns a firing drift alert into a retraining run (gate G7).

Alertmanager posts its webhook here; the relay keeps only firing alerts labelled action=retrain (the same rule as
`smlops retrain-check`) and asks GitHub to start the retrain workflow with a repository dispatch of type
drift-alert. Everything else is acknowledged and dropped.

- Alertmanager must present a bearer token; only its SHA-256 hash is stored, compared in constant time.
- Only what the workflow needs is forwarded: alert name, labels and summary, never the whole payload.
- A cooldown (default one hour) stops a long-firing alert, which Alertmanager repeats, from starting a run each time.
- The dispatcher is injected: GitHub in production, a log line on the CI cluster, where no token exists.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import threading
import time
from collections.abc import Callable

from fastapi import FastAPI, HTTPException, Request

from .retrain import retrain_reasons

EVENT_TYPE = "drift-alert"
LOG = logging.getLogger("smlops.relay")
Dispatch = Callable[[str, dict], None]          # (event type, client payload)


def forwarded(payload: dict) -> list[dict]:
    """The firing retrain alerts, reduced to what the retrain workflow reads."""
    keep = []
    for alert in payload.get("alerts", []):
        labels = alert.get("labels", {})
        if alert.get("status") == "firing" and labels.get("action") == "retrain":
            keep.append({"status": "firing",
                         "labels": {k: str(v) for k, v in labels.items() if k in ("alertname", "action", "severity",
                                                                                   "gate")},
                         "annotations": {"summary": str(alert.get("annotations", {}).get("summary", ""))[:300]}})
    return keep


def create_relay(dispatch: Dispatch, token_sha256: str, cooldown_seconds: float = 3600,
                 clock: Callable[[], float] = time.monotonic) -> FastAPI:
    app = FastAPI(title="smlops alert relay", docs_url=None, redoc_url=None, openapi_url=None)
    state = {"last": None}
    lock = threading.Lock()

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    @app.post("/alerts")
    async def alerts(request: Request) -> dict:
        header = request.headers.get("authorization", "")
        presented = header[7:].strip() if header[:7].lower() == "bearer " else ""
        if not hmac.compare_digest(hashlib.sha256(presented.encode()).hexdigest(), token_sha256):
            raise HTTPException(status_code=401, detail="a valid token is required",
                                headers={"WWW-Authenticate": "Bearer"})
        try:
            payload = json.loads(await request.body())
            if not isinstance(payload, dict):
                raise ValueError("not a JSON object")
            alerts = forwarded(payload)
        except (ValueError, AttributeError) as exc:
            raise HTTPException(status_code=400, detail=f"not an Alertmanager payload: {exc}") from None
        if not alerts:
            return {"retrain": False, "dispatched": False}
        reasons = retrain_reasons({"alerts": alerts})
        with lock:
            now = clock()
            if state["last"] is not None and now - state["last"] < cooldown_seconds:
                LOG.info(json.dumps({"event": "retrain-suppressed", "reasons": reasons}))
                return {"retrain": True, "dispatched": False, "reason": "cooldown"}
            state["last"] = now
        dispatch(EVENT_TYPE, {"alerts": alerts})
        LOG.info(json.dumps({"event": "retrain-dispatched", "reasons": reasons}))
        return {"retrain": True, "dispatched": True, "reasons": reasons}

    return app
