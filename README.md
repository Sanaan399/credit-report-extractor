# Credit Report Extractor

Reads business credit report PDFs and produces one spreadsheet row per report,
plus a full JSON record of everything found in each one.

Handles three report formats automatically — **Creditsafe**, a **CRIF-style
international** report, and **GLADTRUST** — by detecting which one each PDF is
and using extraction instructions tuned for it.

---

## Quick start

You need **Python 3.10+** (<https://www.python.org/downloads/> — tick *"Add
Python to PATH"* during install) and an **Anthropic API key**
(<https://console.anthropic.com> → API keys).

1. Download this folder.
2. Put your PDFs in the **`reports/`** folder (or pick any folder later).
3. Double-click **`start.bat`** — on Mac/Linux run `./start.sh` instead.
4. Your browser opens. Paste your API key, check the folders, click
   **Check & estimate cost**, then **Run**.

That's it — `start.bat` installs everything needed the first time and opens the
page for you.

> ### ⚠️ Don't open `web/index.html` by double-clicking it
>
> The page needs the local helper running to read your folders and do the
> extraction. Opened directly, the fields stay empty and **Browse…** does
> nothing. Always start it with `start.bat` (or `python app.py`) — that opens
> the correct page automatically. If you land on the page and see a red
> "not connected" warning, that's what happened.

### Optional: save your API key

So you don't paste it every time, copy `.env.example` to `.env` and put your
key in it:

```
ANTHROPIC_API_KEY=sk-ant-...
```

`.env` is gitignored, so the key never leaves your machine.

---

## Running it

### Option A — browser (easiest)

`start.bat`, or if you prefer a terminal:

```bash
pip install -r requirements.txt   # first time only
python app.py
```

Paste your API key, pick the folder of PDFs and the folder to save results
into, then click **Check & estimate cost**. It tells you how many reports it
found and roughly what the run will cost *before* you commit; click **Run** to
go ahead and watch progress live.

The server only listens on your own computer — nothing is exposed to your
network.

### Option B — command line

```bash
python main.py "C:\path\to\folder\of\reports"
```

Useful options:

| Option | What it does |
|---|---|
| `-o FOLDER` | where to save results (default: `output/` next to the script) |
| `--dry-run` | show the cost estimate and checks, then stop — **makes no API calls** |
| `--skip-validate` | extraction only, no cross-check — about 44% cheaper |
| `--force` | re-do reports that already have results (default is to skip them) |
| `-y` | don't ask for confirmation |

```bash
python main.py "C:\reports\batch-a" -o "C:\results\batch-a"   # keep batches separate
python main.py "C:\reports\batch-a" --dry-run                 # just the estimate
```

**Point it at a new output folder for each batch**, otherwise the next run
overwrites the previous batch's spreadsheets.

---

## What you get

Everything lands in the output folder:

| File | What it is |
|---|---|
| **`all_reports_core.xlsx`** | One row per report, the fields common to all formats. **Start here.** |
| `all_reports_creditsafe.xlsx`<br>`all_reports_crif_intl.xlsx`<br>`all_reports_gladtrust.xlsx` | Same rows, split by format, plus that format's own extra fields |
| `review_flags.csv` | Fields where the two passes disagreed — your manual-review list |
| `errors.csv` | Any reports that failed (only written if something failed) |
| `json/*.json` | Full detail per report: every director, every year of financials, every adverse finding. This is the source of truth; the spreadsheets are a summary. |

### ⚠️ Open the `.xlsx` files, not the `.csv` files

Both are written with identical data. But opening a `.csv` in Excel lets Excel
guess each column's type, and it guesses wrong on this data in two damaging
ways:

- An employee count of `1-9` becomes the date **1 September**.
- An 18-digit registration or tax number becomes `9.13E+17` — and if you then
  **save**, those digits are gone for good (Excel keeps only 15 significant
  digits).

The `.xlsx` files pin every column to Text, so Excel leaves them alone. The
`.csv` files are there for feeding into other software.

---

## Understanding `review_flags.csv`

Every report is read **twice**, by two independent passes. Where they disagree
on a key field, you get a row here:

| Column | Meaning |
|---|---|
| `source_file` | which report |
| `field` | which field they disagreed on |
| `primary_value` | what the main extraction found (this is what's in the spreadsheets) |
| `cross_check_value` | what the second pass found |

**A flag is not proof of an error** — it's a "look at this one" list. In testing,
most flags turned out to be cosmetic (`LLC` vs `L.L.C.`, title case vs caps).
Open the source PDF and see which value is right.

Equally, **no flag is not proof of correctness**: both passes use the same model
and can share a blind spot. For a new batch, spot-check a few reports against
the original PDFs before trusting the output wholesale.

---

## Costs

Roughly **5–9 cents per report**, scaling with page count. The 11-report test
set (213 pages) costs about **$0.96** with cross-check, or **$0.54** for
extraction only.

You always see an estimate before a run starts, and the exact figure (from the
API's own usage numbers) when it finishes. `--dry-run` gives you the estimate
without spending anything.

Two ways to spend less:

- `--skip-validate` drops the cross-check pass (~44% of the cost) if you don't
  need the review flags.
- Reports that already have results are **skipped by default**, so re-running
  after a partial failure only pays for what's missing.

---

## Accuracy: what's been verified, and what hasn't

This has been checked field-by-field against the source PDFs for a sample of
reports, which found and fixed several real bugs:

- **Unit-scaled financial tables.** A table captioned `UNIT: CNY 1,000` prints
  `663,294` to mean 663,294,000. The scale factor is now read from the caption
  and applied in code.
- **Money values are computed, not guessed.** Every amount's numeric value is
  parsed in Python from the text as printed, rather than trusting the model's
  arithmetic. Currency symbols are preserved exactly.
- **Ambiguous dates.** `03/01/2019` is resolved by finding an unambiguous date
  elsewhere in the same report (e.g. `07/28/2026` proves it's US-style) and
  applying that convention consistently.
- **ID numbers.** A company registration number and a tax/unified social credit
  code are kept in separate columns rather than conflated.

`python test_extract.py` runs regression tests over these (no API calls, no
cost). Run it after changing anything in `extract.py`.

**Known limits:**

- Only PDFs sitting **directly** in the folder you point at — sub-folders are
  ignored (it tells you if it spots PDFs one level down).
- A PDF that isn't one of the three known formats is labelled `unknown` and
  extracted with a generic fallback prompt that has **no accuracy history**.
  You'll get a warning listing them — check those against the source.
- Accuracy on a genuinely new batch hasn't been measured. The fixes above are
  general mechanisms, not tuned to specific reports, but spot-check a few
  before trusting a large run.

---

## If something goes wrong

| Symptom | Cause and fix |
|---|---|
| Fields are blank and **Browse…** does nothing | You opened `web/index.html` directly. Close the tab and run `start.bat` (or `python app.py`) instead. |
| Browse dialog doesn't appear | It sometimes opens *behind* the browser window — check your taskbar. You can always type or paste the path instead. |
| "These output files are open in another program" | A results file is open in Excel. Close it. (This check runs *before* any spending.) |
| "No PDF files directly inside…" | PDFs are in sub-folders — point at the sub-folder, or move them up. |
| "No API key" | Set `ANTHROPIC_API_KEY`, or copy `.env.example` to `.env` and fill it in. |
| A few reports failed but others worked | Normal — one bad PDF no longer kills the batch. See `errors.csv`, fix or remove those files, and re-run; completed reports are skipped so you don't pay twice. |
| Employee counts look like dates | You opened the `.csv`. Open the `.xlsx` instead. |

---

## How it works

```
start.bat / start.sh      one-click launcher (installs deps, opens the UI)
main.py / app.py          entry points (command line / browser)
  └─ preflight.py         free checks + cost estimate, before any spending
  └─ detect.py            which of the three formats is this PDF?
  └─ extract.py           full extraction  → output/json/*.json
  └─ validate.py          independent second pass → review_flags.csv
  └─ consolidate.py       JSON → the .xlsx / .csv spreadsheets
     config.py            models, pricing, filenames — tune here
     schemas.py           the fields extracted, and their descriptions
     prompts/*.txt        per-format extraction instructions
     web/index.html       the browser UI (served by app.py — don't open directly)
     test_extract.py      regression tests, no API calls
```

The whole PDF is sent to Claude, which reads it directly — there's no separate
OCR step. `detect.py` only chooses which instructions to use.

**To change what gets extracted:** add the field to `schemas.py` (the field
description is itself the instruction the model sees) and add it to the column
list in `consolidate.py`.

**To use a stronger model:** change `EXTRACTION_MODEL` in `config.py` and add
its price to `PRICING_PER_M_TOKENS` in the same file so cost reporting stays
accurate.

### If you scale to thousands of reports

Switch to the [Batch API](https://platform.claude.com/docs/en/build-with-claude/batch-processing)
— identical model and accuracy at 50% of the price, in exchange for results
arriving within a few hours instead of immediately. It's the single biggest
saving available and needs no other changes.
