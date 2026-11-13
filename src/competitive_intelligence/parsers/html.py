"""HTML -> canonical text.

The canonical text is what claims are quoted from and what the change hash is
computed over, so the transformation is versioned: a new PARSER_VERSION
invalidates every baseline and snapshot made with an older one.
"""

import hashlib
import re

from bs4 import BeautifulSoup

PARSER_VERSION = "html-v1"

# Elements that never carry a marketing claim.
_DROP_TAGS = ("script", "style", "noscript", "template", "svg", "iframe", "nav", "form", "button")
# Consent and cookie layers change between visits and would make every hash differ.
_NOISE = re.compile(r"cookie|consent|gdpr|onetrust", re.IGNORECASE)
_SPACES = re.compile(r"[ \t\r\f\v ]+")


def canonicalize(html: bytes | str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(_DROP_TAGS):
        tag.decompose()
    for tag in soup.find_all(True):
        if tag.decomposed:
            continue  # already removed together with a parent
        marker = " ".join([tag.get("id") or "", *(tag.get("class") or [])])
        if marker and _NOISE.search(marker):
            tag.decompose()
    root = soup.find("main") or soup.body or soup
    lines: list[str] = []
    for raw in root.get_text(separator="\n").split("\n"):
        line = _SPACES.sub(" ", raw).strip()
        if line and (not lines or lines[-1] != line):
            lines.append(line)
    return "\n".join(lines)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
