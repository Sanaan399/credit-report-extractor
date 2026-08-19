"""Central place to tune models and paths. Change EXTRACTION_MODEL to a
stronger model (e.g. "claude-sonnet-5") if Haiku's accuracy isn't good enough
on messy reports -- everything else in the pipeline stays the same. If you
do, add its price to PRICING_PER_M_TOKENS below so cost reporting stays
accurate.

Reads ANTHROPIC_API_KEY from a local .env file if present (see .env.example)
-- copy .env.example to .env and fill in your key; .env is gitignored so it
never leaves your machine."""

from dotenv import load_dotenv

load_dotenv()

EXTRACTION_MODEL = "claude-haiku-4-5"
CROSS_CHECK_MODEL = "claude-haiku-4-5"

EXTRACTION_MAX_TOKENS = 8000
CROSS_CHECK_MAX_TOKENS = 1024

OUTPUT_JSON_DIRNAME = "json"
REVIEW_FLAGS_FILENAME = "review_flags.csv"
CORE_CSV_FILENAME = "all_reports_core.csv"
ERRORS_FILENAME = "errors.csv"

FORMAT_CREDITSAFE = "creditsafe"
FORMAT_CRIF_INTL = "crif_intl"
FORMAT_GLADTRUST = "gladtrust"
FORMAT_UNKNOWN = "unknown"

# USD per 1M tokens, as of 2026-08. Used only for the cost estimate main.py
# prints after a run -- update if you change EXTRACTION_MODEL/CROSS_CHECK_MODEL.
PRICING_PER_M_TOKENS = {
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00},
}


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float | None:
    prices = PRICING_PER_M_TOKENS.get(model)
    if prices is None:
        return None
    return (input_tokens / 1_000_000) * prices["input"] + (output_tokens / 1_000_000) * prices["output"]


# --- Constants for the pre-run cost estimate in preflight.py ---------------
#
# Calibrated against one real API call measured 2026-08-10 (a 25-page report:
# 48,580 input tokens against a ~50-token system prompt => ~1,941 tokens per
# page of PDF content). Output tokens are held flat at that same call's
# figures; output is a small share of spend, so the approximation moves the
# total very little. These drive the *estimate* shown before a run -- the
# actual cost printed after a run comes from the API's own usage numbers.
EST_PDF_TOKENS_PER_PAGE = 1941
EST_EXTRACTION_OUTPUT_TOKENS = 1678
EST_CROSS_CHECK_OUTPUT_TOKENS = 82
