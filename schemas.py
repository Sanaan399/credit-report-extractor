"""Pydantic models describing what we ask Claude to extract from a report.

One schema is used for every report format. Fields that don't apply to a
given format are simply left null by the model -- this keeps the extraction
call, the prompt, and the API schema identical across all three formats,
and consolidate.py decides which columns matter per format when writing CSVs.

Field descriptions double as extraction instructions: they are compiled into
the JSON schema Claude sees, so keep them precise.

How each model reaches the API (verified empirically 2026-08-10):

* CreditReport is far too large for the strict structured-output feature --
  the API rejects schemas with >24 optional parameters, >16 nullable/union
  parameters per object, or a large total compiled grammar, and every
  restructuring of this schema (nested groups, required-but-nullable fields,
  Money collapsed to strings) still tripped the total-grammar cap. So
  extract.py instead embeds CreditReport.model_json_schema() in the prompt,
  asks for plain JSON, and validates the reply with this model client-side.

* CriticalFields IS small enough for strict structured outputs, so
  validate.py uses messages.parse(output_format=CriticalFields) and gets
  grammar-enforced JSON. Keep this model small (it shares Money, whose three
  fields count toward the limits) or the cross-check pass will start failing
  with 400 errors.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Money(BaseModel):
    """extract.py recomputes `value` deterministically from `raw` (see
    _parse_amount) rather than trusting the model's own arithmetic -- get
    `raw` exactly right, character for character, including the currency
    symbol/code; `value` is a best-effort fallback for when that parse fails.
    """

    raw: str | None = Field(
        None,
        description=(
            "The amount EXACTLY as printed, including every digit, separator, and currency symbol/code -- "
            "e.g. '£2.5K', 'CNY 3,000,000.00', '3,000 US$', 'UAE Dh 150,000'. Never normalize, translate, "
            "or drop the currency symbol; transcribe it character for character."
        ),
    )
    value: float | None = Field(
        None, description="The same amount as a plain number with K/M expanded, e.g. 2500 for '£2.5K'."
    )
    currency: str | None = Field(
        None, description="Best-guess currency code or symbol, e.g. GBP, EUR, CNY, AED, USD."
    )


class Person(BaseModel):
    name: str | None = None
    title: str | None = Field(None, description="e.g. Mr, Mrs, Dr")
    role: str | None = Field(
        None, description="Their function, e.g. Director, Company Secretary, General Manager, Legal Representative."
    )
    nationality: str | None = None
    date_of_birth: str | None = Field(None, description="ISO date YYYY-MM-DD if a full date is given, else as printed.")
    appointment_date: str | None = Field(None, description="ISO date YYYY-MM-DD if given, else as printed.")
    notes: str | None = None


class Shareholder(BaseModel):
    name: str | None = None
    nationality: str | None = None
    share_percentage: str | None = Field(None, description="e.g. '100%'")


class FinancialYear(BaseModel):
    year: str | None = Field(None, description="The fiscal year this row of figures covers, e.g. '2024'.")
    unit_multiplier: float | None = Field(
        1,
        description=(
            "The scale factor implied by any unit label printed near this financials table -- e.g. "
            "'UNIT: CNY 1,000' -> 1000, 'Currency In Ten Thousand CNY' / \"RMB'0,000\" -> 10000, "
            "'in millions' -> 1000000. Use 1 if the table states no such label and figures are already "
            "absolute amounts. This is read from the table header/caption, not from any individual cell -- "
            "look above and around the table, not just at the numbers themselves."
        ),
    )
    turnover: Money | None = None
    pre_tax_profit: Money | None = None
    net_worth: Money | None = Field(None, description="Shareholders' funds / net assets / net worth for the year.")
    total_assets: Money | None = None
    total_liabilities: Money | None = None
    employees: str | None = None


class CreditReport(BaseModel):
    # --- Identity (present in every format) ---
    company_name: str | None = Field(None, description="The company's registered/legal name.")
    name_local: str | None = Field(None, description="Company name in its local/original language, if shown separately.")
    country: str | None = None
    registration_number: str | None = Field(
        None, description="Company registration / incorporation number (not the tax/VAT number)."
    )
    tax_vat_number: str | None = Field(
        None,
        description=(
            "VAT number, unified social credit code, or tax ID. ALWAYS populate this when the report states "
            "one -- even if the same value is also captured in a format-specific field like "
            "unified_social_credit_code. Do not rely on that other field alone; this field must independently "
            "hold the same value whenever one is printed anywhere in the report."
        ),
    )
    incorporation_date: str | None = Field(None, description="ISO date YYYY-MM-DD if a full date is given, else as printed.")
    legal_form: str | None = Field(None, description="e.g. 'Private limited with Share Capital', 'LLC-SO'.")
    company_status: str | None = Field(None, description="e.g. 'Active - Accounts Filed', 'Operational'.")
    registered_address: str | None = None
    trading_address: str | None = None
    phone: str | None = None
    email: str | None = None
    website: str | None = None
    industry_code: str | None = Field(None, description="e.g. SIC07, NACE, or industry classification code.")
    industry_description: str | None = None
    employees: str | None = Field(None, description="Number or range of employees, as printed, e.g. '1-9' or '6.7'.")
    report_date: str | None = Field(None, description="ISO date YYYY-MM-DD the report itself was printed/generated, if shown.")

    # --- Risk / score (present in every format, but on different scales) ---
    credit_score: str | None = Field(None, description="The primary numeric or letter score/rating shown, as printed.")
    score_scale: str | None = Field(None, description="Description of the scale used, e.g. '0-100', 'A-F', '1-100'.")
    risk_level: str | None = Field(None, description="Plain-English risk band, e.g. 'Low Risk', 'Higher than average'.")
    credit_rating: str | None = Field(None, description="Letter/band rating if separate from credit_score, e.g. 'C', 'BB', 'D'.")
    credit_limit: Money | None = Field(
        None,
        description=(
            "The report's headline credit limit / recommended credit figure, under whatever label it uses -- "
            "'Credit Limit', 'Base Credit Limit', 'Contract Limit', etc. ALWAYS populate this whenever the "
            "report states any such figure, even if the same amount is also captured in a format-specific "
            "field like base_credit_limit or contract_limit. Leave null only if no credit-limit figure is "
            "printed anywhere -- never estimate or infer one."
        ),
    )

    # --- Repeating groups ---
    directors: list[Person] = Field(default_factory=list)
    shareholders: list[Shareholder] = Field(default_factory=list)
    financials: list[FinancialYear] = Field(default_factory=list)
    adverse_findings: list[str] = Field(
        default_factory=list,
        description="Short entries for CCJs, sanctions hits, payment defaults, litigation, adverse press. Empty list if none found.",
    )
    commentary: list[str] = Field(
        default_factory=list, description="Any bullet-point commentary/notes the report gives about the company."
    )

    # --- Creditsafe-specific (leave null for other formats) ---
    safe_number: str | None = None
    probability_of_default: str | None = None
    contract_limit: Money | None = None
    dbt: str | None = Field(None, description="Days Beyond Terms, if shown.")
    enquiries_past_12_months: str | None = None
    share_capital: Money | None = None

    # --- International/CRIF-style rating report specific (leave null for other formats) ---
    points_allocated: str | None = Field(None, description="Raw credit-risk points, e.g. '45' out of 100.")
    rating_band: str | None = Field(None, description="Letter grade band, e.g. 'A+', 'BB', 'C'.")
    payment_terms: str | None = Field(None, description="Summary of stated payment/selling terms, as printed.")
    corruption_index_summary: str | None = None

    # --- GLADTRUST-specific (leave null for other formats) ---
    gladtrust_rating: str | None = Field(None, description="GLADTRUST letter rating A-F.")
    gladtrust_suggestion: str | None = None
    base_credit_limit: Money | None = None
    unified_social_credit_code: str | None = None
    business_scope: str | None = None


class CriticalFields(BaseModel):
    """Small subset used for the independent cross-check pass in validate.py.

    Fields are required-but-nullable (no defaults): this shape is verified to
    compile in the strict structured-output grammar.

    tax_vat_number and risk_level are included specifically so the model has
    somewhere correct to put the unified social credit code / VAT number and
    the plain-English risk band -- confirmed empirically 2026-08-10 that
    without them, the model stuffs the USCC into registration_number and the
    risk_level into credit_rating instead, producing false-positive flags in
    review_flags.csv against an already-correct primary extraction.
    """

    company_name: str | None
    registration_number: str | None
    tax_vat_number: str | None
    credit_score: str | None
    credit_rating: str | None
    risk_level: str | None
    credit_limit: Money | None
    incorporation_date: str | None
