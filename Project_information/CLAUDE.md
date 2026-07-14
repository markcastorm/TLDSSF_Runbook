# CLAUDE.md — TLDSSF_Runbook Pipeline Context

This file is the complete briefing for a new AI session working on this pipeline. Read this before touching any code. Everything you need to understand state, design decisions, known bugs, and safe operating procedures is here.

---

## What This Project Is

A SIMBA data pipeline that extracts quarterly investment allocations from Thai Social Security Fund (SSO) PDF reports and outputs structured Excel files. Source: `sso.go.th`. Data: 8 asset categories + total, quarterly frequency, values in millions THB.

**Run command:** `python main.py`  
**Entry point:** `main.py` → `orchestrator.py` → `scraper.py` + `extractor.py` + `file_generator.py`

---

## File Map

| File | Purpose | Key functions |
|------|---------|---------------|
| `config.py` | All constants | `COLUMNS`, `COL_KEYS`, `META_STATIC`, `META_COLUMNS`, `THAI_MONTHS`, `MONTH_TO_QUARTER` |
| `scraper.py` | SSO site scraper | `download()`, `_try_direct_requests()`, `_build_driver()`, `_get_report_links()`, `_download_pdf_requests()` |
| `extractor.py` | PDF data extraction | `extract()`, `_extract_text_based()`, `_extract_ocr()`, `_parse_date_from_text()`, `_match_label()`, `_normalize()` |
| `file_generator.py` | Output generation | `generate()`, `append_to_master()`, `is_already_in_master()`, `generate_data_xlsx()`, `generate_meta_xlsx()`, `create_zip()`, `update_latest()` |
| `orchestrator.py` | Pipeline wiring | `run()` |
| `main.py` | Entry point | `main()` |

---

## extractor.py — Complete Function Reference

### `_normalize(text) → str`
Strips fitz BOM artefact: fitz inserts a space between a Thai base character and its following combining tone mark/vowel. For example `ค ้า` instead of `ค้า`.

```python
text = re.sub(r'(?<=[฀-๿]) (?=[ะ-๎])', '', text)
return ' '.join(text.split()).strip()
```

Also collapses all whitespace. Applied to every label before matching.

---

### `_parse_date_from_text(raw_text) → (quarter_key, date_str)`

Extracts quarter from the raw page 1 text. Handles two formats:
- **Old (image PDFs):** `ไตรมาสที่ 3 ณ 30 กันยายน 2563`
- **New (text PDFs):** `ไตรมาสที่ ณ 31 ธันวาคม 2567` (quarter number may be absent)

Logic:
1. Try `re.search(r'ไตรมาสที่\s*(\d+)', raw_text)` for Q number
2. Try `re.search(r'(\d{1,2})\s*(MONTH)\s*(\d{4})', raw_text)` for day+month+year(BE)
3. If Q number absent, derive from month via `MONTH_TO_QUARTER = {3:1, 6:2, 9:3, 12:4}`
4. Convert BE→CE: `year_ce = year_be - 543`
5. `quarter_key = f'{year_ce}-Q{q_num}'`  (e.g. `'2024-Q4'`)
6. `date_str = f'{year_ce}{end_month:02d}'` (e.g. `'202412'`)

Returns `(None, None)` if parsing fails.

---

### `_match_label(label_text) → col_key or None`

Normalizes the label, then iterates `config.COLUMNS`. For each column:
- ALL `thai_keys` must appear in the normalized label
- NONE of `exclude_keys` may appear

Returns first matching `col_key`, or `None` if unmatched. Skips the `Total` column (Total is extracted separately by regex on `ล้านบาท`).

---

### `_extract_text_based(raw_text) → dict`

Main extraction for text-based PDFs (and OCR output, which is text-normalized to the same format).

**Chart section boundaries:**
- Start: line containing `รายงานสถานะ` (skips document header above chart)
- End: first line matching `^[\d,]{5,}\s*ล้านบาท` (the total line)

**Total extraction:** Done separately before the chart loop via `re.match(r'^([\d,]{5,})\s*ล้านบาท', line)`.

**Six regex patterns (applied in order per line):**

```python
VALUE_PCT    = re.compile(r',\s*([\d,]{4,})\s*,\s*\d+\.\d+%')
VALUE_ONLY   = re.compile(r',\s*([\d,]{4,})\s*,\s*$')
VALUE_TRAIL  = re.compile(r',\s*([\d,]{4,})\s*$')
VALUE_LAXEND = re.compile(r',\s*([\d,]{4,})\s*,(?!\s*[\d%])')
PURE_VALPC   = re.compile(r'^([\d,]{4,})\s*,\s*\d+\.\d+%')
PCT_ONLY     = re.compile(r'^,?\d+\.\d+%')
```

**`label_buf` accumulator:** Lines that match no value pattern are appended to `label_buf`. When a value trigger fires, `pre` (text before the match start) is added to `label_buf`, then `_flush(label_buf, val)` is called and `label_buf` is reset to `[]`.

**`_flush(label_parts, value)`:** Joins parts with space → `_normalize()` → `_match_label()` → if matched and not duplicate, writes to `results[col_key] = value`.

**Returns:** `{col_key: int, ..., 'Total': int}` — may be partial if some labels were unmatched.

---

### `_extract_ocr(doc) → dict`

OCR fallback for image-based PDFs. `doc` is an open `fitz.Document`.

**Step-by-step:**

```python
page = doc[0]

# 1. Extract largest embedded image (the pie chart)
images = page.get_images(full=True)
for img_info in images:
    xref = img_info[0]
    base = doc.extract_image(xref)        # {'image': bytes, 'width': int, 'height': int, 'ext': str}
    area = base['width'] * base['height']
    # pick max area

# 2. Fallback: render page at 300 DPI if no embedded images
if img is None:
    pix = page.get_pixmap(dpi=300)
    img = Image.open(io.BytesIO(pix.tobytes('png')))

# 3. Grayscale + 2x LANCZOS upscale
img = img.convert('L')
img = img.resize((img.width * 2, img.height * 2), Image.LANCZOS)

# 4. OCR with PSM 11 (sparse text)
ocr_text = pytesseract.image_to_string(img, lang='tha+eng', config='--psm 11')

# 5. Normalize artefact: ';' → ','
ocr_text = ocr_text.replace(';', ',')

return _extract_text_based(ocr_text)
```

**Why each step:**
- `extract_image()` vs page render: original chart art is higher resolution (e.g. 3072×2304) than a 300 DPI page render
- Grayscale: colored label boxes suppress pytesseract confidence on RGB input → grayscale removes the color noise
- 2× upscale: larger text → more accurate OCR glyph recognition
- PSM 11 (sparse text): chart labels are scattered around the pie, not in reading-order columns
- `';'→','`: OCR frequently misreads Thai comma separators as semicolons

---

### `extract(pdf_path) → dict`

Main entry point. Returns:
```python
{
    'quarter_key': '2024-Q4',
    'date_str':    '202412',
    'data': {
        'DomesticGovernmentBonds': 1387919,
        'ForeignBonds': 368365,
        ...
        'Total': 2657245,
    }
}
```

Detection logic:
```python
thai_chars = sum(1 for c in raw_text if '฀' <= c <= '๿')
if thai_chars >= 800:
    data = _extract_text_based(raw_text)   # text-based PDF
else:
    doc2 = fitz.open(pdf_path)
    data = _extract_ocr(doc2)             # image-based PDF
    doc2.close()
```

**Important:** fitz `open()` is called twice for image-based PDFs — once for text (date) and once for OCR (image). This is intentional because `doc.close()` is called between them.

---

## config.py — Key Constants

### `COLUMNS` list — ABSOLUTE ORDER, DO NOT REORDER

Each entry:
```python
{
    'code':         'TLDSOCIALSECURITYFUND.DOMESTICGOVERNMENTBONDS.LEVEL.NONE.Q.1@TLDSOCIALSECURITYFUNDQ',
    'label':        'TLD Social Security Fund: Domestic Government Bonds',
    'col_key':      'DomesticGovernmentBonds',
    'thai_keys':    ['พันธบัตรรัฐบาล'],
    'exclude_keys': [],
}
```

`COL_KEYS = [c['col_key'] for c in COLUMNS]`  — 9 keys including Total  
`DATA_COL_KEYS = COL_KEYS[:-1]`  — 8 keys excluding Total

### Column disambiguation — critical notes

**ForeignBonds** `thai_keys=['ตราสารหนี', 'น่าเชื่อถือ']`:
- Newer text PDFs use abbreviation `ตปท` (ต่างประเทศ) — NOT matched by `ตปท` key alone because old image PDFs use the full word.
- Both old and new formats include `ตราสารหนี` (bonds) and `น่าเชื่อถือ` (credit-rated).
- `exclude_keys=['ไม่ได้รับ', 'ไม่ได้จัด']` prevent matching the 9th unrated category present in some old PDFs.

**ForeignEquities** `thai_keys=['ตราสารทุน'], exclude_keys=['ไทย']`:
- `DomesticEquities` label is `ตราสารทุนไทย`. Since `ไทย` appears in that, excluding it leaves only `ForeignEquities`.
- `ตราสารทุน` alone would also match `ตราสารทุนไทย` without the exclusion.

**PREVIOUSLY TRIED (WRONG) KEYS:**
- `ForeignBonds` was briefly given `['ตราสารหนี', 'น่าเชื่อถือ', 'ตปท']` — WRONG: `ตปท` absent in old image PDFs → ForeignBonds returned None for all pre-2022 PDFs.
- `ForeignEquities` was briefly given `['ตราสารทุน', 'ตปท']` — same problem.

### META_STATIC

All 9 series share:
```python
FREQUENCY='Q', MULTIPLIER=6, AGGREGATION_TYPE='END_OF_PERIOD',
UNIT_TYPE='LEVEL', DATA_TYPE='CURRENCY', DATA_UNIT='THB',
SEASONALLY_ADJUSTED='NSA', ANNUALIZED=False, STATE='ACTIVE',
PROVIDER='AfricaAI', SOURCE='SSO',
SOURCE_DESCRIPTION='Thailand Social Security Office',
COUNTRY='THA', DATASET='TLDSSF',
PROVIDER_MEASURE_URL=BASE_URL
```

`META_COLUMNS` is a 17-column list: `['CODE', 'DESCRIPTION', 'FREQUENCY', 'MULTIPLIER', 'AGGREGATION_TYPE', 'UNIT_TYPE', 'DATA_TYPE', 'DATA_UNIT', 'SEASONALLY_ADJUSTED', 'ANNUALIZED', 'STATE', 'PROVIDER_MEASURE_URL', 'PROVIDER', 'SOURCE', 'SOURCE_DESCRIPTION', 'COUNTRY', 'DATASET']`

---

## file_generator.py — Critical Behaviors

### Master CSV append safety

Two bugs were found and fixed during development:

**Bug 1: Missing trailing newline**  
Excel omits the trailing newline on the last row. Without the check below, the new row is concatenated onto the last row: `"3,041,974"2026-Q2,...`.

Fix:
```python
with open(path, 'rb') as f:
    f.seek(-1, 2)
    last_byte = f.read(1)
with open(path, 'a', newline='', encoding='utf-8') as f:
    if last_byte not in (b'\n', b'\r'):
        f.write('\n')
    writer = csv.writer(f)
    writer.writerow(row)
```

**Bug 2: BOM written mid-file**  
Using `encoding='utf-8-sig'` in append mode writes a BOM (byte-order mark `\xef\xbb\xbf`) at the current file position (mid-file), corrupting all subsequent reads.

Fix: always use `encoding='utf-8'` for append; use `encoding='utf-8-sig'` only when reading (it strips the existing BOM at file start).

### `generate()` flow

```
is_already_in_master(quarter_key)  → return None if yes
append_to_master(quarter_key, data)
generate_data_xlsx(output_dir, today)    # today = YYYYMMDD for filename
generate_meta_xlsx(output_dir, today)
create_zip(output_dir, timestamp, [data_path, meta_path])
update_latest(output_dir)                # rmtree + copytree
return output_dir
```

`output_dir = os.path.join(config.OUTPUT_DIR, timestamp)` where `timestamp = YYYYMMDD_HHMMSS`

### DATA xlsx structure

Row 0: codes (from `COLUMNS[*]['code']`)  
Row 1: labels (from `COLUMNS[*]['label']`)  
Row 2+: all rows from master CSV, numeric values parsed via `_parse_value()` (handles both `'1,344,682'` and `1344682`)

### META xlsx structure

Row 0: `META_COLUMNS` header  
Rows 1–9: one per `COLUMNS` entry = `[code, label] + [META_STATIC[c] for c in META_COLUMNS[2:]]`

---

## scraper.py — Behavior Notes

### Dual-mode scraping

The scraper tries `_try_direct_requests()` first (no browser, BeautifulSoup parsing). If the site is JS-rendered and returns no report divs, it falls back to `_build_driver()` → Selenium undetected Chrome.

### Report selection

`config.TARGET_DATE = None` → `max(reports, key=lambda r: r['sort_key'])` where `sort_key = (year_ce, q_num)`.

`config.TARGET_DATE = 'Q4-2024'` → normalized comparison (strips `-`, uppercased) against each `quarter_key`.

### PDF download

First tries `requests.Session` with browser cookies transferred from Selenium session. If that fails, falls back to `_selenium_click_download()` which clicks the link and polls the download directory.

### CSS selectors

```
Page listing: div.col-sm-12.mar-l20.region_716
PDF link:     div > a[href$='.pdf']
Cookie btn:   #c-p-bn
```

---

## orchestrator.py — Pipeline Safety

Two duplicate checks occur:

1. **After scraper:** `file_generator.is_already_in_master(quarter_key)` — if the quarter from the PDF URL/link text is already in master, stop before downloading and extracting.
2. **Inside `file_generator.generate()`:** `is_already_in_master()` is called again — safe redundancy in case orchestrator's check is bypassed.

**Quarter key consistency check:**  
```python
if result['quarter_key'] != quarter_key:
    raise RuntimeError(f'Quarter mismatch: scraper={quarter_key}, PDF={result["quarter_key"]}')
```
Catches the case where the scraper reads a different quarter from the link text than the extractor reads from the PDF body (e.g., if the site lists Q4 but the linked PDF is actually Q3).

---

## Test PDFs and Expected Results

All in `Project_information/sample Pdfs/`:

| Hash prefix | Quarter | Type | Cols |
|-------------|---------|------|------|
| `3072943` | 2019-Q1 | IMAGE | 9/9 |
| `57d7e5` | 2019-Q2 | IMAGE | 9/9 |
| `5e5007` | 2020-Q3 | IMAGE | **8/9** (Alternatives=None — known spatial interleave) |
| `ac5a49` | 2022-Q1 | TEXT | 9/9 |
| `39cd43` | 2024-Q2 | TEXT | 9/9 |
| `e754ad` | 2024-Q3 | TEXT | 9/9 |

**Quick verification script:**
```python
import fitz, config, extractor, os

base = r'D:\Projects\SIMBA-RUNBOOKS\TLDSSF_Runbook\Project_information\sample Pdfs'
for name in os.listdir(base):
    if not name.endswith('.pdf'):
        continue
    r = extractor.extract(os.path.join(base, name))
    hits = sum(1 for k in config.COL_KEYS if k in r['data'])
    print(r['quarter_key'], hits, '/', len(config.COL_KEYS))
```

Expected output:
```
2019-Q1 9 / 9
2019-Q2 9 / 9
2020-Q3 8 / 9
2022-Q1 9 / 9
2024-Q2 9 / 9
2024-Q3 9 / 9
```

---

## Master CSV Current State (as of 2026-07-13)

Rows present (newest last):
```
2022-Q4, 2023-Q4, 2024-Q2, 2025-Q2, 2025-Q3, 2025-Q4, 2026-Q1, 2026-Q2
```

Next expected quarter: **2026-Q3** (end-of-Q3 = September 2026).  
The pipeline will skip any already-present quarter and only append genuinely new ones.

---

## Thai Calendar Quick Reference

| Thai | Month # | Quarter |
|------|---------|---------|
| มกราคม | 1 | Q1 |
| กุมภาพันธ์ | 2 | Q1 |
| มีนาคม | 3 | Q1 |
| เมษายน | 4 | Q2 |
| พฤษภาคม | 5 | Q2 |
| มิถุนายน | 6 | Q2 |
| กรกฎาคม | 7 | Q3 |
| สิงหาคม | 8 | Q3 |
| กันยายน | 9 | Q3 |
| ตุลาคม | 10 | Q4 |
| พฤศจิกายน | 11 | Q4 |
| ธันวาคม | 12 | Q4 |

BE → CE: subtract 543. Example: 2567 − 543 = 2024.

---

## Common Failure Modes

### "Could not parse date from PDF"
The `_parse_date_from_text` regex failed. Check: does the PDF text layer contain the expected Thai date string? Use `doc[0].get_text('text')` to inspect. May indicate a new PDF format.

### Missing columns (0 extracted or partial)
- First check `thai_chars` count — if unexpectedly < 800, a text PDF is being treated as image-based.
- For text PDFs: add debug logging in `_extract_text_based` to print `line` at each step and see what the value patterns are failing to match.
- For OCR PDFs: print the `ocr_text` — if garbled, the embedded image extraction or grayscale step may have failed.

### "Quarter mismatch" error in orchestrator
The link text on the page says one quarter, but the PDF body says another. Most likely cause: the SSO site listed the wrong PDF for a given link. Check the downloaded PDF manually.

### New PDF has > 9 categories
The SSO fund may add new investment categories over time. Check the extracted text — if there's a new Thai label with a value that doesn't map to any column, it will silently be ignored (logged as "Unmapped"). Update `config.COLUMNS` and `_match_label` exclusions accordingly.

### OCR returning very few columns
Check: is `pytesseract` installed? Is the Tesseract binary in PATH? Is the `tha` language pack installed (`tessdata/tha.traineddata`)?

Run this to verify:
```python
import pytesseract
print(pytesseract.get_tesseract_version())
print(pytesseract.get_languages())  # should include 'tha'
```

---

## Design Decisions That Must Not Change

1. **`COLUMNS` order is absolute.** The master CSV column order matches `COL_KEYS`. Reordering `COLUMNS` corrupts all existing CSV data.
2. **Master CSV is append-only.** Never delete, reorder, or rewrite existing rows. If a row is wrong, add a correction mechanism in `file_generator` that either patches in-place or raises an error — do not rebuild.
3. **`encoding='utf-8-sig'` for reading, `'utf-8'` for appending.** Do not change this. `utf-8-sig` in append mode writes a BOM mid-file.
4. **Trailing newline check before append.** Do not remove the `f.seek(-1, 2)` check — Excel-saved CSVs reliably omit the final newline.
5. **Embedded image extraction before page render.** The page-render fallback is for PDFs that have no embedded images (rare). The embedded image path gives higher resolution and cleaner OCR input.
6. **PSM 11 for OCR.** Do not switch to PSM 6 — it reads the chart as two columns and interleaves left/right labels on the same line.

---

## What Has NOT Been Tested Live

- The full pipeline against the live `sso.go.th` website (scraper was built from the HTML structure seen in screenshots/reference materials, not from a live crawl).
- The `_selenium_click_download` fallback path.
- Docker/headless deployment (`HEADLESS_MODE=True` in config).
- Network failure/retry behavior in `_download_pdf_requests`.

The extraction logic (extractor.py) and file generation (file_generator.py) have been fully tested against 6 sample PDFs and the real master CSV respectively.
