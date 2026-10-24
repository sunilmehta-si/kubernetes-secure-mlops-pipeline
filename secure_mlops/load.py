"""Load the endpoint the way clients do and hold it to the serving objectives (configs/serving.yaml).

A fixed number of prediction requests, sent concurrently with the client's API key, each timed end to end.
The report gives p50/p95/p99 latency, throughput and the error rate, and the verdict compares p95 and the error
rate with the objectives. A run can be paced (`rate`, requests a second) to stay inside the client's ticket
budget: a throttled request (429) means the run measured the rate limiter and not the server, so the verdict
refuses it instead of passing it (the first cluster run of the drill was 77% 429s and still looked green). Prometheus measures the same latencies from inside the server; the cluster drill checks
that the two views agree, so neither the client nor the server can hide a slow path.
"""
from __future__ import annotations

import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, fields
from pathlib import Path

import requests
import yaml

TICKETS = ("please refund the $40 charge from last week", "my parcel never arrived",
           "I cannot log in to my account since the update", "the app crashes when I open settings",
           "someone used my card without my permission")


class LoadError(ValueError):
    pass


@dataclass(frozen=True)
class ServingObjectives:
    p95_ms: float = 250.0
    max_error_rate: float = 0.01
    min_requests: int = 200


def load_objectives(path: str | Path) -> ServingObjectives:
    data = yaml.safe_load(Path(path).read_text()) or {}
    unknown = sorted(set(data) - {f.name for f in fields(ServingObjectives)})
    if unknown:
        raise LoadError(f"{path}: unknown serving objective(s) {', '.join(unknown)}")
    return ServingObjectives(**data)


def _one(session: requests.Session, url: str, headers: dict, body: dict, timeout: float) -> tuple[float, int]:
    """(seconds, status); status 0 means no answer at all."""
    started = time.perf_counter()
    try:
        response = session.post(url, json=body, headers=headers, timeout=timeout)
        status = response.status_code
    except requests.RequestException:
        status = 0
    return time.perf_counter() - started, status


def run(url: str, api_key: str, requests_total: int, concurrency: int, tickets_per_request: int = 2,
        timeout: float = 10.0, rate: float | None = None) -> dict:
    """Send requests_total predictions with `concurrency` workers, at most `rate` a second if given; returns
    the measurements."""
    if requests_total < 1 or concurrency < 1 or not 1 <= tickets_per_request <= 64:
        raise LoadError("requests and concurrency must be positive, tickets per request between 1 and 64")
    if rate is not None and rate <= 0:
        raise LoadError("rate must be positive (requests a second), or left unset to go as fast as the workers go")
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    bodies = [{"instances": [{"text": TICKETS[(i + j) % len(TICKETS)]} for j in range(tickets_per_request)]}
              for i in range(requests_total)]
    started = time.perf_counter()

    def paced(i: int) -> tuple[float, int]:
        if rate:                                       # request i is due i/rate seconds after the start
            time.sleep(max(0.0, started + i / rate - time.perf_counter()))
        return _one(session, url, headers, bodies[i], timeout)
    with requests.Session() as session, ThreadPoolExecutor(max_workers=concurrency) as pool:
        results = list(pool.map(paced, range(requests_total)))
    elapsed = time.perf_counter() - started
    latencies = sorted(seconds * 1000 for seconds, _ in results)
    statuses = [status for _, status in results]
    errors = sum(1 for s in statuses if s == 0 or s >= 500)

    def pct(p: float) -> float:
        return round(latencies[min(len(latencies) - 1, int(round(p / 100 * len(latencies) + 0.5)) - 1)], 1)
    return {"requests": requests_total, "concurrency": concurrency, "tickets_per_request": tickets_per_request,
            "rate": rate,
            "seconds": round(elapsed, 2), "requests_per_second": round(requests_total / elapsed, 1),
            "latency_ms": {"p50": pct(50), "p95": pct(95), "p99": pct(99), "max": round(latencies[-1], 1),
                           "mean": round(statistics.fmean(latencies), 1)},
            "errors": errors, "error_rate": round(errors / requests_total, 4),
            "status_counts": {str(s): statuses.count(s) for s in sorted(set(statuses))}}


def judge(report: dict, objectives: ServingObjectives) -> dict:
    """{passed, checks: [{name, value, limit, passed}]} against the objectives."""
    if report["requests"] < objectives.min_requests:
        raise LoadError(f"{report['requests']} requests are too few to judge; the objectives need "
                        f"{objectives.min_requests}")
    throttled = report.get("status_counts", {}).get("429", 0)
    if throttled:
        raise LoadError(f"{throttled} of {report['requests']} requests were throttled (429): the run measured the "
                        "client's ticket budget, not the server; pace it with a rate the budget allows")
    checks = [{"name": "p95 latency (ms)", "value": report["latency_ms"]["p95"], "limit": objectives.p95_ms,
               "passed": report["latency_ms"]["p95"] <= objectives.p95_ms},
              {"name": "error rate", "value": report["error_rate"], "limit": objectives.max_error_rate,
               "passed": report["error_rate"] <= objectives.max_error_rate}]
    return {"passed": all(c["passed"] for c in checks), "checks": checks}


def markdown(report: dict, verdict: dict) -> str:
    lines = [f"## Load: {report['requests']} requests, {report['concurrency']} at a time, "
             f"{report['tickets_per_request']} tickets each"
             + (f", paced at {report['rate']:g} a second" if report.get("rate") else "") + f", {report['seconds']} s "
             f"({report['requests_per_second']} req/s) **{'passed' if verdict['passed'] else 'failed'}**", "",
             "| Check | Value | Limit | Result |", "|---|---|---|---|"]
    for c in verdict["checks"]:
        lines.append(f"| {c['name']} | {c['value']} | <= {c['limit']} | {'pass' if c['passed'] else '**FAIL**'} |")
    lat = report["latency_ms"]
    lines += ["", f"Latency ms: p50 {lat['p50']}, p95 {lat['p95']}, p99 {lat['p99']}, max {lat['max']}, "
                  f"mean {lat['mean']}. Status codes: "
                  + ", ".join(f"{k}: {v}" for k, v in report["status_counts"].items())]
    return "\n".join(lines) + "\n"
