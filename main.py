"""Entry point for the full pipeline: extract, cross-check, consolidate.

    python main.py "C:\\path\\to\\folder\\of\\reports"

Run `python main.py --help` for all options. There is also a browser UI --
run `python app.py` instead if you would rather not use the command line.

Needs an Anthropic API key: either set ANTHROPIC_API_KEY in the environment,
or copy .env.example to .env and put the key in there.

Before spending anything, the run does a free pre-flight check (how many
PDFs, how many pages, roughly what it will cost, whether any output file is
locked by Excel) and asks for confirmation. Pass --yes to skip the prompt.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path

import config
import consolidate
import extract
import preflight
import validate

_ERROR_FIELDNAMES = ["source_file", "error"]


def _write_errors(errors: list[dict], path: Path, log=print) -> None:
    if not errors:
        return
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=_ERROR_FIELDNAMES)
        writer.writeheader()
        writer.writerows(errors)
    log(f"{len(errors)} failure(s) -> {path}")


def _format_cost(label: str, model: str, usage: dict) -> tuple[str, float | None]:
    cost = config.estimate_cost(model, usage["input_tokens"], usage["output_tokens"])
    cost_str = f"${cost:.4f}" if cost is not None else "unknown (add pricing for this model to config.py)"
    line = f"{label}: {usage['input_tokens']:,} in + {usage['output_tokens']:,} out tokens -> {cost_str}"
    return line, cost


def run(
    input_dir: Path,
    output_dir: Path,
    *,
    log=print,
    force: bool = False,
    skip_validate: bool = False,
) -> dict:
    """Runs the pipeline. Assumes pre-flight has already passed.

    Returns a summary dict (also useful to the web UI)."""
    json_dir = output_dir / config.OUTPUT_JSON_DIRNAME
    output_dir.mkdir(parents=True, exist_ok=True)
    errors: list[dict] = []

    log("=== Extracting ===")
    extraction = extract.extract_all(input_dir, json_dir, log=log, force=force)
    errors.extend(extraction["errors"])
    extract_usage = extraction["usage"]

    validate_usage = {"input_tokens": 0, "output_tokens": 0}
    flags = None
    if skip_validate:
        log("\n=== Cross-checking (skipped) ===")
    else:
        log("\n=== Cross-checking ===")
        validation = validate.validate_all(
            input_dir, json_dir, output_dir / config.REVIEW_FLAGS_FILENAME, log=log
        )
        errors.extend(validation["errors"])
        validate_usage = validation["usage"]
        flags = validation["flags"]

    log("\n=== Consolidating ===")
    report_count = consolidate.consolidate(json_dir, output_dir / config.CORE_CSV_FILENAME, log=log)
    _write_errors(errors, output_dir / config.ERRORS_FILENAME, log=log)

    log("\n=== Summary ===")
    log(f"Extracted this run: {len(extraction['succeeded'])}   Skipped (already done): {extraction['skipped']}")
    log(f"Reports in output:  {report_count}")
    if flags is not None:
        log(f"Review flags:       {flags}  (see {config.REVIEW_FLAGS_FILENAME})")
    if errors:
        log(f"Failures:           {len(errors)}  (see {config.ERRORS_FILENAME})")

    extract_line, extract_cost = _format_cost("Extraction ", config.EXTRACTION_MODEL, extract_usage)
    log(extract_line)
    total_cost = extract_cost
    if not skip_validate:
        validate_line, validate_cost = _format_cost("Cross-check", config.CROSS_CHECK_MODEL, validate_usage)
        log(validate_line)
        if total_cost is not None and validate_cost is not None:
            total_cost += validate_cost
    if total_cost is not None:
        log(f"Actual cost this run: ${total_cost:.4f}")

    log(f"\nOutput folder: {output_dir}")
    log("Open the .xlsx files in Excel (not the .csv) so long ID numbers keep their digits.")

    return {
        "extracted": len(extraction["succeeded"]),
        "skipped": extraction["skipped"],
        "reports": report_count,
        "flags": flags,
        "errors": errors,
        "cost": total_cost,
        "output_dir": str(output_dir),
    }


def default_input_dir() -> Path:
    """The bundled `reports/` folder, where a new user is told to drop their
    PDFs. Falls back to a sibling `reports/` next to the project if that is
    where they already live."""
    here = Path(__file__).parent
    bundled = here / "reports"
    sibling = here.parent / "reports"
    if not any(bundled.glob("*.pdf")) and any(sibling.glob("*.pdf")):
        return sibling.resolve()
    return bundled.resolve()


def _parse_args(argv: list[str]) -> argparse.Namespace:
    default_input = default_input_dir()
    default_output = Path(__file__).parent / "output"

    parser = argparse.ArgumentParser(
        description="Extract structured data from business credit report PDFs.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            '  python main.py "C:\\reports\\batch-a"\n'
            '  python main.py "C:\\reports\\batch-a" -o "C:\\results\\batch-a"\n'
            "  python main.py --skip-validate      (extraction only, ~44% cheaper)\n"
            "  python main.py --dry-run            (cost estimate only, no API calls)\n"
        ),
    )
    parser.add_argument(
        "input_dir",
        nargs="?",
        default=str(default_input),
        help=f"folder of PDFs to process, non-recursive (default: {default_input})",
    )
    parser.add_argument(
        "-o",
        "--output",
        default=str(default_output),
        help=f"folder to write results into (default: {default_output})",
    )
    parser.add_argument("--force", action="store_true", help="re-extract reports that already have output")
    parser.add_argument(
        "--skip-validate", action="store_true", help="skip the cross-check pass (cheaper, no review_flags.csv)"
    )
    parser.add_argument("-y", "--yes", action="store_true", help="skip the confirmation prompt")
    parser.add_argument(
        "--dry-run", action="store_true", help="show the pre-flight check and cost estimate, then exit"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    input_dir = Path(args.input_dir).expanduser()
    output_dir = Path(args.output).expanduser()
    include_cross_check = not args.skip_validate

    check = preflight.check(
        input_dir,
        output_dir,
        api_key=os.environ.get("ANTHROPIC_API_KEY"),
        include_cross_check=include_cross_check,
        force=args.force,
    )
    print("=== Pre-flight (no API calls yet) ===")
    print(preflight.render(check, include_cross_check=include_cross_check))

    if not check.ok:
        print("\nFix the problem(s) above and try again. Nothing was sent to the API.")
        return 1
    if args.dry_run:
        print("\nDry run -- nothing sent to the API.")
        return 0
    if check.to_process == 0:
        print("\nEverything in that folder has already been extracted. Use --force to redo it.")
        return 0

    if not args.yes:
        answer = input("\nProceed? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("Cancelled. Nothing was sent to the API.")
            return 0

    print()
    summary = run(
        input_dir,
        output_dir,
        force=args.force,
        skip_validate=args.skip_validate,
    )
    return 1 if summary["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
