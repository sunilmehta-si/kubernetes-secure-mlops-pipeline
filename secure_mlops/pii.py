"""Find personal data in ticket text before it reaches training (gate G1, threat T2).

A deliberately small, dependency-free scanner for the identifiers that most often leak into support tickets: email
addresses, phone numbers, payment card numbers and IBANs. Card numbers must pass the Luhn checksum and IBANs the
mod-97 check, so order numbers and ticket references are not mistaken for them. Findings are always masked: a
report about personal data must not become a copy of it.

Names are found where tickets usually carry them: a self-introduction ("my name is Priya Shah", "this is Tom"),
a greeting ("Dear Ms Okafor") and a sign-off ("Regards, Jan de Vries"). A name mentioned in passing ("ask Priya")
and street addresses still need a statistical tool such as Presidio and are not covered.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
# +country code and 7-14 more digits, or 10-11 digits written as a phone number (with spaces, dots or dashes)
PHONE = re.compile(r"(?<![\w#])(?:\+\d{1,3}[\s.-]?\d(?:[\s.-]?\d){6,13}|\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4})(?!\w)")
CARD = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,3})?\b")
# A capitalised word, allowing O'Brien, Jean-Luc and particles inside a name (van, de, da, al, bin)
_NAME = r"[A-Z][a-z]+(?:['-][A-Z]?[a-z]+)?(?:\s+(?:(?:van|von|de|da|del|di|al|bin|der|den)\s+)?[A-Z][a-z]+(?:['-][A-Z]?[a-z]+)?){0,2}"
NAME_CUES = re.compile(
    r"(?:\b(?:[Mm]y name is|[Tt]his is|[Ii] am|[Ii]'m)\s+(?P<intro>" + _NAME + r")"
    r"|\b(?:Dear|Hi|Hello)\s+(?:(?:Mr|Mrs|Ms|Dr)\.?\s+)?(?P<greeting>" + _NAME + r")"
    r"|\b(?:[Rr]egards|[Tt]hanks|[Tt]hank you|[Cc]heers|[Ss]incerely|[Bb]est)\s*,\s*(?P<signoff>" + _NAME + r"))")
# Words that follow those cues without being a person: "this is Urgent", "Thanks, Support Team", "Hi All"
NOT_A_NAME = {"all", "team", "support", "there", "everyone", "again", "urgent", "still", "not", "unable", "the",
              "a", "an", "sorry", "happy", "billing", "shipping", "security", "account", "technical", "customer",
              "service", "sir", "madam", "you", "your", "it", "my", "our", "we", "please", "regarding", "about",
              "writing", "contacting", "following", "very", "so", "really", "just", "now"}


@dataclass(frozen=True)
class Finding:
    kind: str
    masked: str


def _luhn(digits: str) -> bool:
    total, double = 0, False
    for d in reversed(digits):
        n = int(d) * (2 if double else 1)
        total += n - 9 if n > 9 else n
        double = not double
    return total % 10 == 0


def _iban_ok(value: str) -> bool:
    s = value.replace(" ", "")
    if not 15 <= len(s) <= 34:
        return False
    rearranged = s[4:] + s[:4]
    return int("".join(str(int(ch, 36)) for ch in rearranged)) % 97 == 1


def _mask(value: str, keep: int = 2) -> str:
    chars = [c for c in value if not c.isspace()]
    return "".join(chars[:keep]) + "*" * max(3, len(chars) - 2 * keep) + "".join(chars[-keep:])


def scan(text: str) -> list[Finding]:
    found = [Finding("email", _mask(m.group(), 1)) for m in EMAIL.finditer(text)]
    for m in CARD.finditer(text):
        digits = re.sub(r"\D", "", m.group())
        if 13 <= len(digits) <= 19 and _luhn(digits):
            found.append(Finding("payment card", _mask(digits)))
    for m in IBAN.finditer(text):
        if _iban_ok(m.group()):
            found.append(Finding("IBAN", _mask(m.group())))
    for m in NAME_CUES.finditer(text):
        name = next(g for g in (m["intro"], m["greeting"], m["signoff"]) if g)
        if name.split()[0].lower() not in NOT_A_NAME:
            found.append(Finding("name", _mask(name, 1)))
    cards = {f.masked for f in found if f.kind == "payment card"}
    for m in PHONE.finditer(text):
        digits = re.sub(r"\D", "", m.group())
        if _mask(digits) not in cards:                          # a card number is not also a phone number
            found.append(Finding("phone", _mask(digits)))
    return found


def summary(texts) -> tuple[int, Counter, list[str]]:
    """(tickets with personal data, findings by kind, up to three masked examples)."""
    rows, kinds, examples = 0, Counter(), []
    for text in texts:
        found = scan(text)
        if found:
            rows += 1
            kinds.update(f.kind for f in found)
            examples += [f"{f.kind} {f.masked}" for f in found][: max(0, 3 - len(examples))]
    return rows, kinds, examples
