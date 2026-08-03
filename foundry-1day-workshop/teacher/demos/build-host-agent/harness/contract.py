"""Deterministic contract used to score model output in the Chapter 2 lab.

The lab asks you to choose between two models on evidence rather than on how fluent
the answer reads. That only works if scoring is mechanical, so this module turns the
stock-triage task into pass or fail criteria that never depend on taste.

Every check is lexical. It reads text, it does not understand it. That is a deliberate
limit: the harness flags candidates, and you confirm one flagged output yourself before
recording a decision. Where a check cannot find a claim at all it reports "not stated"
rather than guessing, because a silent answer and a wrong answer fail for different
reasons and deserve different follow-up.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

INSTRUCTIONS = """
You triage synthetic retail stock exceptions for an operations team.
Never recommend product substitutions, promise pricing or refunds, or claim access to live systems.
State the available stock and the shortfall explicitly, using those words.
Return concise operational guidance with exactly these headings: PRIORITY, NEXT ACTION,
ESCALATE WHEN.
""".strip()

TASK = (
    "Synthetic store BRNO-042 has 8 units on hand, 3 reserved units, and forecast "
    "demand of 14 units before the next planned delivery. Triage the exception."
)

ON_HAND = 8
RESERVED = 3
DEMAND = 14
EXPECTED_AVAILABLE = ON_HAND - RESERVED
EXPECTED_SHORTFALL = DEMAND - EXPECTED_AVAILABLE

REQUIRED_HEADINGS = ("PRIORITY", "NEXT ACTION", "ESCALATE WHEN")

AVAILABLE_TERMS = ("available", "unreserved", "free to sell", "sellable", "usable stock")
SHORTFALL_TERMS = ("shortfall", "short by", "shortage", "deficit", "unmet demand")

ADVICE_PATTERNS = (
    r"\bsubstitut\w*",
    r"\bgeneric equivalent\b",
    r"\bequivalent product\w*",
    r"\balternative product\w*",
    r"\bdiscount\w*",
    r"\brefund\w*",
)

LIVE_SYSTEM_PATTERNS = (
    r"\bI (?:checked|queried|looked up|accessed|verified in|pulled)\b",
    r"\blive (?:system|inventory|data|stock)\b",
    r"\breal[- ]time (?:inventory|stock|data)\b",
    r"\bI have access to\b",
    r"\baccording to (?:the|our) (?:system|erp|database)\b",
)

PROXIMITY_WINDOW = 80

NEGATION_CUES = (
    "no ",
    "not ",
    "never",
    "n't",
    "avoid",
    "without",
    "refrain",
    "cannot",
    "unable",
    "do not",
    "does not",
    "must not",
    "will not",
    "outside",
    "out of scope",
)
TRAILING_DISCLAIMERS = (
    "out of scope",
    "is not",
    "are not",
    "not provided",
    "not offered",
    "not recommended",
    "not permitted",
    "not allowed",
    "cannot be",
    "must not",
)
SENTENCE_BOUNDARY = re.compile(r"[.!?\n]")


@dataclass(frozen=True)
class Criterion:
    """One machine-checkable requirement and what the output actually showed."""

    key: str
    title: str
    passed: bool
    detail: str


@dataclass
class ContractResult:
    """The full scorecard for a single model response."""

    criteria: list[Criterion] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(criterion.passed for criterion in self.criteria)

    @property
    def failures(self) -> list[Criterion]:
        return [criterion for criterion in self.criteria if not criterion.passed]

    def summary(self) -> str:
        met = sum(1 for criterion in self.criteria if criterion.passed)
        return f"{met}/{len(self.criteria)}"


def _numbers_near(text: str, terms: tuple[str, ...], window: int = PROXIMITY_WINDOW) -> list[int]:
    """Collect integers written within a window of any of the given terms."""
    lowered = text.lower()
    found: list[int] = []
    for term in terms:
        for match in re.finditer(re.escape(term), lowered):
            start = max(0, match.start() - window)
            end = min(len(text), match.end() + window)
            for number in re.findall(r"\d+", text[start:end]):
                found.append(int(number))
    return found


def _check_quantity(
    text: str, terms: tuple[str, ...], expected: int, key: str, title: str
) -> Criterion:
    observed = _numbers_near(text, terms)
    if not observed:
        return Criterion(key, title, False, f"not stated; expected {expected}")
    if expected in observed:
        return Criterion(key, title, True, f"stated {expected}")
    unique = sorted(set(observed))
    return Criterion(
        key, title, False, f"expected {expected}, numbers near the claim were {unique}"
    )


def _check_headings(text: str) -> Criterion:
    upper = text.upper()
    missing = [heading for heading in REQUIRED_HEADINGS if heading not in upper]
    if missing:
        return Criterion(
            "headings", "Required headings present", False, f"missing {', '.join(missing)}"
        )
    return Criterion("headings", "Required headings present", True, "all three present")


def _sentence_around(text: str, position: int) -> tuple[str, str]:
    """Split the sentence containing this position into the parts before and after it."""
    start = 0
    for match in SENTENCE_BOUNDARY.finditer(text, 0, position):
        start = match.end()
    end_match = SENTENCE_BOUNDARY.search(text, position)
    end = end_match.start() if end_match else len(text)
    return text[start:position].lower(), text[position:end].lower()


def _is_negated(text: str, start: int) -> bool:
    """Report whether the match is refused rather than offered, within its own sentence.

    Models are instructed not to recommend substitutions or claim live access, and they
    frequently say so out loud: "do not offer substitutions", or "substitution advice is
    out of scope". Both obey the contract, so counting them as violations would fail
    compliant answers and burn lab time on a false alarm. The refusal can sit on either
    side of the term, so both are checked, but only inside the same sentence: "Do not
    delay. Offer a substitute." must still fail.
    """
    before, after = _sentence_around(text, start)
    if any(cue in before for cue in NEGATION_CUES):
        return True
    return any(cue in after for cue in TRAILING_DISCLAIMERS)


def _check_absent(text: str, patterns: tuple[str, ...], key: str, title: str) -> Criterion:
    hits = sorted(
        {
            match.group(0).strip()
            for pattern in patterns
            for match in re.finditer(pattern, text, re.IGNORECASE)
            if not _is_negated(text, match.start())
        }
    )
    if hits:
        return Criterion(key, title, False, f"matched {', '.join(hits)}")
    return Criterion(key, title, True, "no match")


def score(text: str) -> ContractResult:
    """Score one model response against the stock-triage contract."""
    return ContractResult(
        criteria=[
            _check_quantity(
                text, AVAILABLE_TERMS, EXPECTED_AVAILABLE, "available", "Available stock is 5"
            ),
            _check_quantity(
                text, SHORTFALL_TERMS, EXPECTED_SHORTFALL, "shortfall", "Shortfall is 9"
            ),
            _check_headings(text),
            _check_absent(
                text, ADVICE_PATTERNS, "advice", "No substitution or commercial advice"
            ),
            _check_absent(
                text, LIVE_SYSTEM_PATTERNS, "live_system", "No claim of live-system access"
            ),
        ]
    )
