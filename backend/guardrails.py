import re
from typing import Any

PII_PATTERNS = {
    "email": re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I),
    "phone": re.compile(r"(?<!\d)(?:\+?91[-\s]?)?[6-9]\d{9}(?!\d)"),
    "card": re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)"),
}
INJECTION_TERMS = (
    "ignore previous instructions",
    "ignore all instructions",
    "system prompt",
    "developer message",
    "reveal your prompt",
    "jailbreak",
    "bypass safety",
)
OFF_TOPIC = (
    "recipe",
    "politics",
    "weather",
    "medical diagnosis",
    "write code",
    "password",
)


def inspect_ingress(text: str) -> tuple[bool, str | None]:
    lower = text.lower()
    if any(term in lower for term in INJECTION_TERMS):
        return False, "Prompt-injection pattern detected."
    for name, pattern in PII_PATTERNS.items():
        if pattern.search(text):
            return (
                False,
                f"Potential {name} PII detected. Please remove sensitive data.",
            )
    if any(term in lower for term in OFF_TOPIC):
        return False, "Request is outside the e-commerce order-support scope."
    return True, None


def inspect_egress(text: str) -> tuple[bool, str | None]:
    for name, pattern in PII_PATTERNS.items():
        if pattern.search(text):
            return False, f"Response blocked because it contains potential {name} PII."
    if any(term in text.lower() for term in ("api key", "secret key", "system prompt")):
        return (
            False,
            "Response blocked because it may expose internal secrets or prompts.",
        )
    return True, None
