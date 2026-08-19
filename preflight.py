"""Checks run BEFORE any billable API call, so a run fails fast and free
rather than part-way through at real cost.

Everything here is local and free: counting PDFs and pages with pypdf,
detecting each report's format, measuring prompt sizes by reading the prompt
files, and probing whether the output files are locked by another program.

The headline output is `summarize()`, which both the CLI and the web UI call
to show "N PDFs, ~M pages, roughly $X.XX" plus any blocking problems, before
asking the user to confirm.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from pypdf import PdfReader

import config
import detect
import extract


@dataclass
class PdfInfo:
    path: Path
    pages: int
    report_format: str
    already_extracted: bool


@dataclass
class Preflight:
    input_dir: Path
    output_dir: Path
    pdfs: list[PdfInfo] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    estimated_cost: float | None = None
    to_process: int = 0
    skipped: int = 0

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def total_pages(self) -> int:
        return sum(p.pages for p in self.pdfs)

    def format_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for pdf in self.pdfs:
            counts[pdf.report_format] = counts.get(pdf.report_format, 0) + 1
        return counts


def _prompt_tokens(report_format: str) -> int:
    """Rough token count of the extraction system prompt for a format, from
    the prompt files on disk. ~4 characters per token is close enough for a
    cost estimate and costs nothing to compute."""
    prompt = extract.system_prompt(report_format)
    return len(prompt) // 4


def _cross_check_prompt_tokens() -> int:
    import validate

    return len(validate.SYSTEM_PROMPT) // 4


def estimate_cost_for(pdfs: list[PdfInfo], include_cross_check: bool = True) -> float | None:
    """Estimated USD for extracting (and optionally cross-checking) these PDFs."""
    extraction_input = 0
    extraction_output = 0
    cross_check_input = 0
    cross_check_output = 0

    cc_prompt = _cross_check_prompt_tokens()
    for pdf in pdfs:
        pdf_tokens = pdf.pages * config.EST_PDF_TOKENS_PER_PAGE
        extraction_input += _prompt_tokens(pdf.report_format) + pdf_tokens
        extraction_output += config.EST_EXTRACTION_OUTPUT_TOKENS
        if include_cross_check:
            cross_check_input += cc_prompt + pdf_tokens
            cross_check_output += config.EST_CROSS_CHECK_OUTPUT_TOKENS

    extraction = config.estimate_cost(config.EXTRACTION_MODEL, extraction_input, extraction_output)
    if extraction is None:
        return None
    if not include_cross_check:
        return extraction

    cross_check = config.estimate_cost(config.CROSS_CHECK_MODEL, cross_check_input, cross_check_output)
    if cross_check is None:
        return None
    return extraction + cross_check


def locked_files(output_dir: Path) -> list[Path]:
    """Output files currently held open by another program (usually Excel).

    Writing to these fails with PermissionError -- which, before this check
    existed, happened only at the very END of a run, after every API call had
    already been paid for."""
    candidates = [
        output_dir / config.CORE_CSV_FILENAME,
        output_dir / config.REVIEW_FLAGS_FILENAME,
        output_dir / config.ERRORS_FILENAME,
        Path(str(output_dir / config.CORE_CSV_FILENAME).replace(".csv", ".xlsx")),
    ]
    for fmt in (config.FORMAT_CREDITSAFE, config.FORMAT_CRIF_INTL, config.FORMAT_GLADTRUST):
        candidates.append(output_dir / f"all_reports_{fmt}.csv")
        candidates.append(output_dir / f"all_reports_{fmt}.xlsx")

    locked = []
    for path in candidates:
        if not path.exists():
            continue
        try:
            with path.open("a"):
                pass
        except PermissionError:
            locked.append(path)
    return locked


def check(
    input_dir: Path,
    output_dir: Path,
    *,
    api_key: str | None = None,
    include_cross_check: bool = True,
    force: bool = False,
) -> Preflight:
    """Scans the input folder and output folder and reports whether a run can
    proceed, what it would do, and roughly what it would cost. Makes no API
    calls."""
    result = Preflight(input_dir=input_dir, output_dir=output_dir)

    if not api_key:
        result.errors.append(
            "No API key. Set ANTHROPIC_API_KEY in your environment, or copy "
            ".env.example to .env and put your key in it."
        )

    if not input_dir.exists():
        result.errors.append(f"Input folder does not exist: {input_dir}")
        return result
    if not input_dir.is_dir():
        result.errors.append(f"Input path is not a folder: {input_dir}")
        return result

    pdf_paths = sorted(input_dir.glob("*.pdf"))
    if not pdf_paths:
        nested = list(input_dir.glob("*/*.pdf"))
        hint = (
            f" Found {len(nested)} PDF(s) in sub-folders -- this tool only reads PDFs sitting "
            "directly in the folder you point it at, so move them up one level or point at "
            "the sub-folder."
            if nested
            else ""
        )
        result.errors.append(f"No PDF files directly inside {input_dir}.{hint}")
        return result

    json_dir = output_dir / config.OUTPUT_JSON_DIRNAME
    unreadable = []
    for path in pdf_paths:
        try:
            pages = len(PdfReader(str(path)).pages)
        except Exception as e:  # noqa: BLE001 - surfacing any unreadable PDF is the point
            unreadable.append(f"{path.name}: {type(e).__name__}: {e}")
            continue
        already = (json_dir / f"{path.stem}.json").exists()
        result.pdfs.append(
            PdfInfo(
                path=path,
                pages=pages,
                report_format=detect.detect_format(path),
                already_extracted=already,
            )
        )

    if unreadable:
        result.warnings.append(
            f"{len(unreadable)} PDF(s) could not be opened and will be skipped: " + "; ".join(unreadable)
        )
    if not result.pdfs:
        result.errors.append("None of the PDFs in that folder could be opened.")
        return result

    pending = [p for p in result.pdfs if force or not p.already_extracted]
    result.to_process = len(pending)
    result.skipped = len(result.pdfs) - len(pending)
    result.estimated_cost = estimate_cost_for(pending, include_cross_check=include_cross_check)

    unknown = [p.path.name for p in result.pdfs if p.report_format == config.FORMAT_UNKNOWN]
    if unknown:
        result.warnings.append(
            f"{len(unknown)} report(s) did not match any known format (Creditsafe, CRIF, GLADTRUST) "
            "and will be extracted with the generic fallback prompt, which has no accuracy history. "
            "Spot-check these against the source PDF: " + ", ".join(unknown)
        )

    if result.skipped:
        result.warnings.append(
            f"{result.skipped} report(s) already have output in {json_dir} and will be skipped "
            "(so you are not billed for them twice). Use --force to re-extract them."
        )

    locked = locked_files(output_dir)
    if locked:
        result.errors.append(
            "These output files are open in another program (usually Excel) and cannot be written. "
            "Close them first: " + ", ".join(p.name for p in locked)
        )

    return result


def render(result: Preflight, include_cross_check: bool = True) -> str:
    """Human-readable pre-flight summary for the terminal."""
    lines = [f"Input:  {result.input_dir}", f"Output: {result.output_dir}"]

    if result.pdfs:
        counts = ", ".join(f"{fmt}: {n}" for fmt, n in sorted(result.format_counts().items()))
        lines.append(f"Found:  {len(result.pdfs)} PDF(s), {result.total_pages} pages ({counts})")
        passes = "extraction + cross-check" if include_cross_check else "extraction only"
        lines.append(f"To do:  {result.to_process} report(s), {passes}")
        if result.estimated_cost is not None:
            lines.append(f"Cost:   roughly ${result.estimated_cost:.2f} (estimate, not a quote)")

    for warning in result.warnings:
        lines.append(f"\n  ! {warning}")
    for error in result.errors:
        lines.append(f"\n  X {error}")

    return "\n".join(lines)


def to_dict(result: Preflight, include_cross_check: bool = True) -> dict:
    """JSON-serializable form, for the web UI."""
    return {
        "ok": result.ok,
        "input_dir": str(result.input_dir),
        "output_dir": str(result.output_dir),
        "pdf_count": len(result.pdfs),
        "total_pages": result.total_pages,
        "format_counts": result.format_counts(),
        "to_process": result.to_process,
        "skipped": result.skipped,
        "estimated_cost": result.estimated_cost,
        "include_cross_check": include_cross_check,
        "warnings": result.warnings,
        "errors": result.errors,
    }


if __name__ == "__main__":
    import os
    import sys

    input_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent.parent / "reports"
    output_dir = Path(__file__).parent / "output"
    print(render(check(input_dir, output_dir, api_key=os.environ.get("ANTHROPIC_API_KEY"))))
