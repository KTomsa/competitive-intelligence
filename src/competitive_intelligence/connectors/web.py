"""Web Scout – načte schválenou stránku dodavatele a převede ji na čistý text."""
from __future__ import annotations

import hashlib
import re

from bs4 import BeautifulSoup

from .http import fetch


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def html_to_lines(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "template"]):
        tag.decompose()
    lines = []
    for raw in soup.get_text("\n").split("\n"):
        line = normalize(raw)
        if len(line) > 2:
            lines.append(line)
    return lines


def contains(page_text: str, needle: str) -> bool:
    return normalize(needle).casefold() in page_text.casefold()


def load(supplier: dict, live: bool = True) -> dict:
    fetched = fetch(supplier["source_url"], f"web_{supplier['id']}.html", live=live)
    lines = html_to_lines(fetched.content.decode("utf-8", "replace"))
    text = " ".join(lines)
    source = fetched.meta()
    source.update({
        "name": f"Web dodavatele {supplier['name']}",
        "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()[:16],
    })
    return {"lines": lines, "text": text, "source": source, "_fetched": fetched}
