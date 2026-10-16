"""Train the ticket-routing classifier: TF-IDF word n-grams into a logistic regression.

A deliberately simple, strong baseline for routing short text. It trains on CPU in seconds, so the full pipeline
runs in CI, and the same seed and data always give the same model.

The model takes a DataFrame with a `text` column, the shape a serving endpoint receives. A bare TfidfVectorizer
given a DataFrame iterates over its column names and would route the word "text" instead of the ticket.
"""
from __future__ import annotations

import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.compose import ColumnTransformer
from sklearn.pipeline import Pipeline

from .config import TrainConfig
from .data import LABELS

FEATURES = ["text"]


def features(frame: pd.DataFrame) -> pd.DataFrame:
    return frame[FEATURES]


def build(config: TrainConfig) -> Pipeline:
    tfidf = TfidfVectorizer(ngram_range=(1, config.ngram_max), min_df=config.min_df, max_features=config.max_features,
                            sublinear_tf=config.sublinear_tf, lowercase=True, strip_accents="unicode")
    return Pipeline([
        ("features", ColumnTransformer([("tfidf", tfidf, "text")])),
        ("clf", LogisticRegression(C=config.c, class_weight=config.class_weight, max_iter=config.max_iter,
                                   random_state=config.seed)),
    ])


def train(frame: pd.DataFrame, config: TrainConfig) -> Pipeline:
    missing = sorted(set(LABELS) - set(frame["label"]))
    if missing:
        raise ValueError(f"training data has no tickets for: {', '.join(missing)}")
    model = build(config)
    model.fit(features(frame), frame["label"])
    return model
