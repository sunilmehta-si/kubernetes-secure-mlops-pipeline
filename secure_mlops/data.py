"""Synthetic support tickets: text in, owning team out.

The generator is seeded, so the same spec always gives the same tickets, and it is built to be realistically hard:
classes share vocabulary ("my payment failed after I reset my password"), some tickets are vague, a few are
mislabelled, and text has typos. `drift` shifts the data the way production traffic shifts: a new product line
with words the model has never seen, more tickets arriving by chat, and more of them about one team.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

LABELS = ("billing", "technical", "account", "shipping", "security")
CHANNELS = ("email", "chat", "web")
TIERS = ("free", "pro", "enterprise")

PRODUCTS = ("Atlas router", "Nimbus camera", "Orbit speaker", "Pulse watch", "Vega laptop")
NEW_PRODUCTS = ("Quasar drone", "Helix headset", "Zephyr scooter")      # appear only under drift

TEMPLATES = {
    "billing": [
        "I was charged twice for my {product} subscription this month",
        "my invoice shows {amount} but my plan costs less",
        "please refund the {amount} charge from last week",
        "payment failed when I tried to renew my plan",
        "how do I change the card used for billing",
        "the discount code was not applied to my order of {product}",
    ],
    "technical": [
        "my {product} keeps disconnecting from wifi",
        "the app crashes with error {code} when I open settings",
        "{product} firmware update is stuck at 60 percent",
        "the {product} will not turn on after charging overnight",
        "sync between the app and my {product} stopped working",
        "getting error {code} every time I pair the {product}",
    ],
    "account": [
        "I cannot log in after resetting my password",
        "please change the email address on my account",
        "how do I add a second user to my account",
        "I want to delete my account and all my data",
        "my account is locked after too many attempts",
        "the verification email never arrives",
    ],
    "shipping": [
        "my {product} order has not arrived after two weeks",
        "tracking number for my {product} shows no updates",
        "the {product} box arrived damaged",
        "can I change the delivery address for order {order}",
        "I received the wrong item instead of my {product}",
        "when will my {product} be back in stock and shipped",
    ],
    "security": [
        "I got an email asking for my password that looks like phishing",
        "someone logged into my account from another country",
        "I see a login I do not recognise and a {amount} charge",
        "how do I turn on two factor authentication",
        "my {product} camera feed was accessed by someone else",
        "please report a suspicious message pretending to be support",
    ],
}
# Phrases that belong to no team: they make tickets vaguer and blur the classes.
FILLER = ("hi team", "please help", "this is urgent", "thanks in advance", "not sure who to ask",
          "second time writing about this", "", "", "")


@dataclass(frozen=True)
class DataSpec:
    n: int = 3000
    seed: int = 7
    label_noise: float = 0.03       # share of tickets whose label is replaced by another team's
    typo_rate: float = 0.02         # chance per word of a swapped-letter typo
    drift: float = 0.0              # 0 = training distribution; 1 = fully shifted production traffic

    def __post_init__(self):
        if self.n < len(LABELS):
            raise ValueError(f"n must be at least {len(LABELS)} so every label appears")
        for name in ("label_noise", "typo_rate", "drift"):
            if not 0.0 <= getattr(self, name) <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")


def _typos(text: str, rate: float, rng: random.Random) -> str:
    words = []
    for w in text.split():
        if len(w) > 3 and rng.random() < rate:
            i = rng.randrange(len(w) - 1)
            w = w[:i] + w[i + 1] + w[i] + w[i + 2:]
        words.append(w)
    return " ".join(words)


def _label_weights(drift: float) -> list[float]:
    """Equal teams in training; under drift, technical tickets grow to a third of traffic (a bad firmware release)."""
    base = [1.0] * len(LABELS)
    base[LABELS.index("technical")] += 1.5 * drift
    return base


def generate(spec: DataSpec) -> list[dict]:
    rng = random.Random(spec.seed)
    weights = _label_weights(spec.drift)
    channel_weights = [0.5 - 0.3 * spec.drift, 0.3 + 0.4 * spec.drift, 0.2 - 0.1 * spec.drift]
    rows = []
    for i in range(spec.n):
        label = LABELS[i] if i < len(LABELS) else rng.choices(LABELS, weights)[0]   # every label at least once
        products = PRODUCTS + NEW_PRODUCTS if rng.random() < spec.drift else PRODUCTS
        text = rng.choice(TEMPLATES[label]).format(
            product=rng.choice(products), amount=f"${rng.randint(5, 400)}", code=f"E{rng.randint(100, 999)}",
            order=f"#{rng.randint(10000, 99999)}")
        if rng.random() < 0.35:                    # some tickets mention a second topic
            other = rng.choice([lab for lab in LABELS if lab != label])
            text += " and also " + rng.choice(TEMPLATES[other]).format(
                product=rng.choice(products), amount=f"${rng.randint(5, 400)}", code=f"E{rng.randint(100, 999)}",
                order=f"#{rng.randint(10000, 99999)}")
        filler = rng.choice(FILLER)
        text = f"{filler} {text}".strip() if filler and rng.random() < 0.5 else (f"{text} {filler}".strip())
        text = _typos(text, spec.typo_rate, rng)
        if rng.random() < spec.label_noise:
            label = rng.choice([lab for lab in LABELS if lab != label])
        rows.append({"ticket_id": f"T{spec.seed:03d}-{i:06d}", "text": text,
                     "channel": rng.choices(CHANNELS, channel_weights)[0],
                     "tier": rng.choices(TIERS, [0.6, 0.3, 0.1])[0], "label": label})
    return rows
