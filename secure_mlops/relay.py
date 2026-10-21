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
import re
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request

from .retrain import retrain_reasons

EVENT_TYPE = "drift-alert"
LOG = logging.getLogger("smlops.relay")
Dispatch = Callable[[str, dict], None]          # (event type, client payload)
REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class DispatchError(RuntimeError):
    pass


def github_dispatcher(repo: str, token_file: str | Path, api: str = "https://api.github.com",
                      timeout: float = 10) -> Dispatch:
    """POST /repos/{repo}/dispatches. The token (fine-grained, Contents: write on this one repository) is read from
    a file at each call, so a rotated Secret takes effect without a restart, and never appears in arguments."""
    if not REPO.match(repo):
        raise ValueError(f"repository must be owner/name, got {repo!r}")
    url = f"{api.rstrip('/')}/repos/{repo}/dispatches"

    def dispatch(event_type: str, client_payload: dict) -> None:
        token = Path(token_file).read_text().strip()
        body = json.dumps({"event_type": event_type, "client_payload": client_payload}).encode()
        request = urllib.request.Request(url, data=body, method="POST", headers={
            "Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:     # noqa: S310 (https URL built above)
                if response.status != 204:
                    raise DispatchError(f"GitHub answered {response.status}, expected 204")
        except urllib.error.HTTPError as exc:
            raise DispatchError(f"GitHub refused the dispatch: HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError) as exc:
            raise DispatchError(f"GitHub could not be reached: {exc}") from None

    return dispatch


def log_dispatcher(event_type: str, client_payload: dict) -> None:
    """For clusters without a GitHub token (CI): record what would have been sent."""
    LOG.info(json.dumps({"event": "dry-run-dispatch", "event_type": event_type, "payload": client_payload},
                        sort_keys=True))


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
            previous, state["last"] = state["last"], now
        try:
            dispatch(EVENT_TYPE, {"alerts": alerts})
        except DispatchError as exc:
            with lock:
                state["last"] = previous       # a failed dispatch must not hold back the next alert
            LOG.error(json.dumps({"event": "retrain-dispatch-failed", "error": str(exc)}))
            raise HTTPException(status_code=502, detail="the retraining trigger failed; Alertmanager will retry") \
                from None
        LOG.info(json.dumps({"event": "retrain-dispatched", "reasons": reasons}))
        return {"retrain": True, "dispatched": True, "reasons": reasons}

    return app
