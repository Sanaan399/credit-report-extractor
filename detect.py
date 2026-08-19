"""Figures out which of the three known report formats a PDF is, by looking
for signature phrases on its first few pages. This only decides how the
output gets grouped/labelled later -- Claude always reads the whole PDF
directly regardless of the detected format.
"""

from pathlib import Path

from pypdf import PdfReader

import config

_SIGNATURES: dict[str, list[str]] = {
    config.FORMAT_CREDITSAFE: ["CREDITSAFE", "SAFE NUMBER"],
    config.FORMAT_CRIF_INTL: ["CREDIT RISK RATING", "GIVEN DETAILS BY CUSTOMER", "BASIS OF CREDIT RATING"],
    config.FORMAT_GLADTRUST: ["GLADTRUST", "ORDER DETAILS & INVESTIGATION RESULTS"],
}

_PAGES_TO_SCAN = 3


def detect_format(pdf_path: Path) -> str:
    text = _extract_leading_text(pdf_path).upper()

    scores = {fmt: sum(1 for sig in sigs if sig in text) for fmt, sigs in _SIGNATURES.items()}
    best_format, best_score = max(scores.items(), key=lambda kv: kv[1])
    return best_format if best_score > 0 else config.FORMAT_UNKNOWN


def _extract_leading_text(pdf_path: Path) -> str:
    reader = PdfReader(str(pdf_path))
    pages = reader.pages[: min(_PAGES_TO_SCAN, len(reader.pages))]
    return "\n".join(page.extract_text() or "" for page in pages)
