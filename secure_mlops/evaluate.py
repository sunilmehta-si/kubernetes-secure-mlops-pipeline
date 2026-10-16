"""Evaluate a model the way the gate needs it: overall, per team, per slice, calibration and latency.

A single accuracy number hides the failures that matter in routing: one team's tickets misrouted, or one channel
(chat, where tickets are short) degrading. Slices make those visible, and the gate checks each one.
"""
from __future__ import annotations

import statistics
import time

import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, f1_score, recall_score

from .data import LABELS
from .train import features

SLICE_COLUMNS = ("channel", "tier", "length")
SHORT_WORDS = 8         # tickets of at most this many words are "short"


def _length_bucket(text: pd.Series) -> pd.Series:
    return np.where(text.str.split().str.len() <= SHORT_WORDS, "short", "long")


def calibration_error(proba: np.ndarray, truth: np.ndarray, classes: list[str], bins: int = 10) -> float:
    """Expected calibration error: how far the model's confidence is from how often it is right."""
    confidence = proba.max(axis=1)
    correct = np.array(classes)[proba.argmax(axis=1)] == truth
    edges = np.linspace(0.0, 1.0, bins + 1)
    ece = 0.0
    for lo, hi in zip(edges[:-1], edges[1:]):
        mask = (confidence > lo) & (confidence <= hi)
        if mask.any():
            ece += mask.mean() * abs(correct[mask].mean() - confidence[mask].mean())
    return float(ece)


def latency_ms(model, frame: pd.DataFrame, samples: int = 200) -> dict:
    """Wall time to route one ticket at a time, the way a serving endpoint sees traffic."""
    picks = [features(frame.iloc[[i]]) for i in range(min(samples, len(frame)))]
    model.predict(picks[0])                       # warm up
    times = []
    for t in picks:
        start = time.perf_counter()
        model.predict(t)
        times.append((time.perf_counter() - start) * 1000)
    times.sort()
    return {"p50": round(statistics.median(times), 3), "p95": round(times[int(0.95 * (len(times) - 1))], 3)}


def _scores(truth: pd.Series, pred: np.ndarray) -> dict:
    return {"n": int(len(truth)), "accuracy": round(float(accuracy_score(truth, pred)), 4),
            "macro_f1": round(float(f1_score(truth, pred, labels=list(LABELS), average="macro", zero_division=0)), 4)}


def evaluate(model, frame: pd.DataFrame, measure_latency: bool = True) -> dict:
    pred = model.predict(features(frame))
    proba = model.predict_proba(features(frame))
    out = {**_scores(frame["label"], pred),
           "recall_by_label": {lab: round(float(r), 4) for lab, r in
                               zip(LABELS, recall_score(frame["label"], pred, labels=list(LABELS), average=None,
                                                        zero_division=0))},
           "calibration_error": round(calibration_error(proba, frame["label"].to_numpy(), list(model.classes_)), 4),
           "slices": {}}
    framed = frame.assign(length=_length_bucket(frame["text"]), _pred=pred)
    for column in SLICE_COLUMNS:
        for value, group in sorted(framed.groupby(column), key=lambda kv: kv[0]):
            out["slices"][f"{column}={value}"] = _scores(group["label"], group["_pred"].to_numpy())
    if measure_latency:
        out["latency_ms"] = latency_ms(model, frame)
    return out
