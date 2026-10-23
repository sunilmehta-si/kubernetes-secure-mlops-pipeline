"""Summarise the server's audit log (one JSON line per prediction or feedback request, never ticket text).

The questions on-call asks after an accuracy alert: which model digests answered, which clients sent how many
tickets, and for each client how many of the tickets agents confirmed the model got right. One client whose
confirmations disagree with everyone else's is the first thing to look at: a broken integration, or feedback
being used to poison the next training run.
"""
from __future__ import annotations

import json
from collections import defaultdict


def summarize(lines) -> dict:
    clients: dict[str, dict] = defaultdict(lambda: {"requests": 0, "tickets": 0, "confirmed": 0, "correct": 0})
    models: dict[str, int] = defaultdict(int)
    skipped = 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line[line.index("{"):])                 # tolerate a timestamp or logger prefix
            kind, client = event["event"], str(event["client"])
            tickets = int(event["tickets"])
        except (ValueError, KeyError, TypeError):
            skipped += 1
            continue
        if kind == "predict":
            clients[client]["requests"] += 1
            clients[client]["tickets"] += tickets
            models[str(event.get("digest", "unknown"))] += 1
        elif kind == "feedback":
            clients[client]["confirmed"] += tickets
            clients[client]["correct"] += int(event.get("correct", 0))
        else:
            skipped += 1
    for numbers in clients.values():
        numbers["accuracy"] = round(numbers["correct"] / numbers["confirmed"], 4) if numbers["confirmed"] else None
    return {"clients": dict(sorted(clients.items())), "models": dict(sorted(models.items())), "skipped": skipped}


def markdown(summary: dict) -> str:
    rows = ["| Client | Requests | Tickets | Confirmed | Confirmed correct |", "|---|---|---|---|---|"]
    for client, n in summary["clients"].items():
        accuracy = "-" if n["accuracy"] is None else f"{n['correct']} ({n['accuracy']:.1%})"
        rows.append(f"| {client} | {n['requests']} | {n['tickets']} | {n['confirmed']} | {accuracy} |")
    rows += ["", "| Model digest | Requests |", "|---|---|"]
    rows += [f"| `{digest}` | {n} |" for digest, n in summary["models"].items()]
    if summary["skipped"]:
        rows += ["", f"{summary['skipped']} line(s) were not audit events and were skipped."]
    return "\n".join(rows) + "\n"
