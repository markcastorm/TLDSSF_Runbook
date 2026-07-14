# TLDSSF_Runbook — SIMBA Pipeline

Automated pipeline that scrapes the Thailand Social Security Office (SSO) website, extracts quarterly investment allocation data from PDF reports, and outputs structured Excel files for the SIMBA platform.

---

## Quick Start

```bash
python main.py
```

Runs the full pipeline: scrape → extract → write outputs. Idempotent — if the latest quarter is already in the master CSV, it exits cleanly with no changes.

---

## Project Purpose

The Thailand Social Security Fund (SSO) publishes quarterly "รายงานสถานะการบริหารเงินลงทุน" (Investment Status Reports) as PDFs. Each PDF contains a pie chart showing fund allocation across 8 asset categories. This pipeline:

1. Navigates to the SSO website, finds the latest report, and downloads the PDF
2. Detects whether the pie chart is text-embedded or image-based, and extracts the 8 category values + total accordingly
3. Appends the new row to a master CSV (append-only, never overwritten)
4. Generates DATA xlsx, META xlsx, a ZIP of both, and copies to `output/latest/`

---

## Directory Structure

```
TLDSSF_Runbook/
├── main.py                          # Entry point — run this
├── orchestrator.py                  # Wires scraper → extractor → file_generator
├── scraper.py                       # Selenium/requests scraper for sso.go.th
├── extractor.py                     # PDF text extraction + OCR fallback
├── config.py                        # All constants: URLs, columns, Thai months
├── file_generator.py                # CSV append, xlsx generation, ZIP, latest/
│
├── Master Data/
│   └── Master_Data_TLDSSF_Q_DATA.csv   # APPEND-ONLY master CSV (never delete rows)
│
├── output/
│   └── latest/                      # Always the most recent run's output
│       ├── TLDSSF_Q_DATA_YYYYMMDD.xlsx
│       ├── TLDSSF_Q_META_YYYYMMDD.xlsx
│       └── TLDSSF_Q_YYYYMMDD_HHMMSS.zip
│
├── downloads/                       # Timestamped per-run download folders
│   └── YYYYMMDD_HHMMSS/
│       └── <hash>.pdf
│
└── Project_information/             # Reference materials (not used at runtime)
    ├── sample Pdfs/                 # 6 test PDFs (3 text-based, 3 image-based)
    │   ├── 3072943dd758...pdf       # IMAGE 2019-Q1  → 9/9 columns
    │   ├── 57d7e5ccf468...pdf       # IMAGE 2019-Q2  → 9/9 columns
    │   ├── 5e5007a504ac...pdf       # IMAGE 2020-Q3  → 8/9 (Alternatives interleave bug)
    │   ├── ac5a494b2b74...pdf       # TEXT  2022-Q1  → 9/9 columns
    │   ├── 39cd43571bea...pdf       # TEXT  2024-Q2  → 9/9 columns
    │   └── e754ad1b2c13...pdf       # TEXT  2024-Q3  → 9/9 columns
    ├── TLDSSF_Q_META_20250124.xlsx  # Reference META structure
    ├── TLDSSF_Q_DATA_20260713.xlsx  # Reference DATA structure
    └── information.txt              # Original project specification
```

---

## Data Source

**URL:** `https://www.sso.go.th/wpr/main/knowledge/รายงานสภาพการบริหารเงินลงทุน_category_list-label_1_134_0`

The page lists all quarterly reports as `<div class="col-sm-12 mar-l20 region_716">` elements. Each contains an `<a>` tag whose `href` ends in `.pdf` and whose text contains the Thai quarter/date string.

**Sample link text:**
```
รายงานสถานะการบริหารเงินลงทุนกองทุนประกันสังคม ไตรมาสที่ 4 ณ 31 ธันวาคม 2567
```

---

## The 8 Data Columns

Columns are fixed and ordered. `col_key` is the internal Python identifier; `code` is the SIMBA series code written to the CSV/xlsx.

| # | col_key | Label | Primary thai_keys | exclude_keys |
|---|---------|-------|-------------------|--------------|
| 1 | DomesticGovernmentBonds | Domestic Government Bonds | `['พันธบัตรรัฐบาล']` | — |
| 2 | ForeignBonds | Foreign Bonds | `['ตราสารหนี', 'น่าเชื่อถือ']` | `['ไม่ได้รับ', 'ไม่ได้จัด']` |
| 3 | Deposits | Deposits | `['เงินฝาก']` | — |
| 4 | CorporateBonds | Corporate Bonds | `['หุ้นกู้เอกชน']` | — |
| 5 | OtherBonds | Other Bonds (State Enterprise) | `['พันธบัตรรัฐวิสาหกิจ']` | — |
| 6 | DomesticEquities | Domestic Equities | `['ตราสารทุนไทย']` | — |
| 7 | ForeignEquities | Foreign Equities | `['ตราสารทุน']` | `['ไทย']` |
| 8 | Alternatives | Alternatives (RE/infra/gold funds) | `['อสังหา']` | — |
| 9 | Total | Total (ล้านบาท) | — | — |

**Disambiguation notes:**
- `ForeignBonds` uses `['ตราสารหนี', 'น่าเชื่อถือ']` because newer text PDFs abbreviate `ตปท` while older image PDFs spell out `ต่างประเทศ`. The exclude_keys `['ไม่ได้รับ', 'ไม่ได้จัด']` prevent the 9th "unrated foreign bonds" category (which appears in some older PDFs) from matching.
- `ForeignEquities` uses a single key `['ตราสารทุน']` plus `exclude_keys=['ไทย']` because `DomesticEquities` is `ตราสารทุนไทย`. The exclusion separates them.
- `DomesticGovernmentBonds` (`พันธบัตรรัฐบาล`) vs `OtherBonds` (`พันธบัตรรัฐวิสาหกิจ`) — both start with `พันธบัตรรัฐ`; the longer key `พันธบัตรรัฐวิสาหกิจ` (state enterprise) uniquely identifies OtherBonds.

---

## PDF Types and Extraction Logic

### Detection: Thai character count

```python
thai_chars = sum(1 for c in raw_text if '฀' <= c <= '๿')
# Thai Unicode block: U+0E00–U+0E7F
if thai_chars >= 800:
    data = _extract_text_based(raw_text)   # embedded text
else:
    data = _extract_ocr(doc)               # image-based PDF
```

**Threshold 800:** Text PDFs have 800+ Thai chars on page 1 (full label text). Image PDFs have < 800 (just heading/footer text, chart is a raster image).

### Text-based PDFs (2022-Q1 onwards)

Raw text from `page.get_text('text')` contains lines like:

```
เงินฝาก, 65,084 , 2.45%          ← label + value + pct on ONE line
ประกัน, 1,387,919 ,              ← label + value + trailing comma
52.23%                            ← pct-only continuation line
267,567                           ← value-only (no Thai)
,10.34%                           ← pct-only with leading comma
368,365 , 13.86%                  ← pure value + pct (no label)
2,657,245 ล้านบาท                  ← total
```

**fitz BOM artefact:** fitz inserts a space before Thai combining marks/tone marks:
```
ค ้า → ค้า      (ค + space + ้ + า)
```
Fixed by `_normalize()` via: `re.sub(r'(?<=[฀-๿]) (?=[ะ-๎])', '', text)`

### Image-based PDFs (pre-2022, roughly)

The pie chart is a raster image embedded in the PDF. The OCR path:
1. `page.get_images(full=True)` → list all embedded images
2. `doc.extract_image(xref)` on each, pick the one with the largest pixel area (the chart)
3. Fallback to `page.get_pixmap(dpi=300)` if no embedded images found
4. Convert to grayscale + 2× LANCZOS upscale
5. `pytesseract.image_to_string(img, lang='tha+eng', config='--psm 11')`
6. Normalize `';'` → `','` (OCR frequently misreads Thai commas as semicolons)

**Why grayscale?** The colored label boxes (yellow, blue) on the chart suppress pytesseract's confidence scoring on RGB input. Grayscale removes color noise and dramatically improves word detection.

**Why extract embedded image vs render the page?** The embedded image is the original high-res chart art (e.g., 3072×2304). A page render at 300 DPI is typically lower resolution and includes surrounding non-chart page area. Using the extracted image gives cleaner, higher-res input for OCR.

**Why PSM 11?** PSM 11 = "Sparse text. Find as much text as possible in no particular order." The pie chart labels are scattered around the chart rather than in a reading-order block. PSM 6 (single uniform block) reads the chart as two columns and interleaves left/right labels on the same line, breaking the parser.

**Known limitation (2020-Q3 → 8/9):** The `Alternatives` label in this PDF is physically split into two text regions (`หน่วยลงทุนอสังหา, โครงสร้าง` top + `พื้นฐาน, ทองคำ , 77,515 ,` bottom), and PSM 11 reads the `ForeignEquities` value between them in Y-order. The label buffer is flushed on ForeignEquities, leaving only the bottom half for Alternatives, which no longer contains `อสังหา`. Result: Alternatives = None for this quarter.

---

## Regex Patterns (extractor.py `_extract_text_based`)

All patterns applied to each line **in order**, with a `label_buf` accumulating label-only lines between value triggers:

```python
VALUE_PCT    = re.compile(r',\s*([\d,]{4,})\s*,\s*\d+\.\d+%')
# Matches: ', VALUE , XX.XX%'  — label+value+pct all on one line
# Example: 'เงินฝาก, 65,084 , 2.45%'

VALUE_ONLY   = re.compile(r',\s*([\d,]{4,})\s*,\s*$')
# Matches: ', VALUE , '  at end of line — pct on next line
# Example: 'ประกัน, 1,387,919 , '

VALUE_TRAIL  = re.compile(r',\s*([\d,]{4,})\s*$')
# Matches: ', VALUE'  at end of line — no trailing comma; continuation on next line
# Example: 'ตราสารทุนไทย, 267,567'  followed by  ',10.34%'

VALUE_LAXEND = re.compile(r',\s*([\d,]{4,})\s*,(?!\s*[\d%])')
# Matches: ', VALUE ,' followed by non-numeric OCR garbage (column bleed artefact)
# Negative lookahead prevents stealing VALUE_PCT lines

PURE_VALPC   = re.compile(r'^([\d,]{4,})\s*,\s*\d+\.\d+%')
# Matches: 'VALUE , XX.XX%'  at line START — label was in label_buf from prior lines
# Example: '368,365 , 13.86%'

PCT_ONLY     = re.compile(r'^,?\d+\.\d+%')
# Skip lines that are just percentages (continuation of value lines)
# Example: '52.23%' or ',10.34%'
```

**Processing order per line:**
1. Skip empty lines
2. Skip `PCT_ONLY` lines
3. Try `VALUE_PCT` → flush `label_buf`
4. Try `VALUE_ONLY` → flush `label_buf`
5. Try `VALUE_TRAIL` → flush `label_buf`
6. Try `VALUE_LAXEND` → flush `label_buf`
7. Try `PURE_VALPC` → flush `label_buf`
8. Otherwise: `label_buf.append(line)`

**Chart section boundaries:** The text region `รายงานสถานะ` marks the start of the chart data. The first line matching `^[\d,]{5,}\s*ล้านบาท` marks the total and ends the chart section.

**`_flush(label_parts, value)`:** Joins label_buf parts with spaces, normalizes with `_normalize()`, then calls `_match_label()`. First match wins; duplicates are ignored.

---

## Master CSV Format

File: `Master Data/Master_Data_TLDSSF_Q_DATA.csv`  
Encoding: UTF-8 with BOM (`utf-8-sig`)  
Written by Excel originally; read with `encoding='utf-8-sig'` to strip BOM.

```
Row 0: ' ', CODE1, CODE2, ..., CODE9
Row 1: '',  LABEL1, LABEL2, ..., LABEL9
Row 2+: QUARTER_KEY, VAL1, ..., VAL9
```

**Example:**
```csv
 ,TLDSOCIALSECURITYFUND.DOMESTICGOVERNMENTBONDS...,TLDSOCIALSECURITYFUND.FOREIGNBONDS...,...
,TLD Social Security Fund: Domestic Government Bonds,...,...
2022-Q4,"1,361,056","226,383","76,801","63,398","9,542","251,027","196,462","87,149","2,271,818"
2023-Q4,"1,347,138","307,193","81,763","78,464","1,884","241,522","276,859","105,089","2,439,912"
```

**Append safety rules (critical):**
- Excel saves without trailing newline on the last row. Before appending, read the last byte; if it is not `\n` or `\r`, write `\n` first, then append.
- Use `encoding='utf-8'` (NOT `utf-8-sig`) for the append open — `utf-8-sig` writes a BOM at the file start only, but in append mode it would write a BOM mid-file, corrupting subsequent reads.
- Values appended by the pipeline are plain integers (no commas). Existing rows may have comma-formatted values — `_parse_value()` in file_generator handles both.

---

## Output Files

Each run creates `output/YYYYMMDD_HHMMSS/` containing:

| File | Description |
|------|-------------|
| `TLDSSF_Q_DATA_YYYYMMDD.xlsx` | Full master CSV as xlsx: row 0 = codes, row 1 = labels, row 2+ = all data |
| `TLDSSF_Q_META_YYYYMMDD.xlsx` | One row per series (9 rows): CODE, DESCRIPTION + 15 static META columns |
| `TLDSSF_Q_YYYYMMDD_HHMMSS.zip` | ZIP of both xlsx files |

`output/latest/` is always replaced (rmtree + copytree) with the current run's folder.

**META static fields** (all 9 series share these):
```
FREQUENCY=Q, MULTIPLIER=6, AGGREGATION_TYPE=END_OF_PERIOD, UNIT_TYPE=LEVEL,
DATA_TYPE=CURRENCY, DATA_UNIT=THB, SEASONALLY_ADJUSTED=NSA, ANNUALIZED=False,
STATE=ACTIVE, PROVIDER=AfricaAI, SOURCE=SSO,
SOURCE_DESCRIPTION=Thailand Social Security Office, COUNTRY=THA, DATASET=TLDSSF
```

---

## Dependencies

```
fitz (PyMuPDF)          PDF reading + image extraction
pytesseract             OCR engine wrapper
Pillow (PIL)            Image conversion and upscaling
selenium                Browser automation (fallback)
undetected_chromedriver Chrome driver with anti-bot bypass
selenium_stealth        Additional stealth headers
requests                HTTP download
beautifulsoup4          HTML parsing (direct requests path)
openpyxl                xlsx generation
```

Tesseract OCR must be installed separately with Thai language pack (`tha`).

---

## Thai Calendar Notes

Thai Buddhist Era (BE) year = CE year + 543.  
Example: `ธันวาคม 2567` → month=12, year_BE=2567 → year_CE=2024 → Q4 → `quarter_key='2024-Q4'`, `date_str='202412'`

Quarter end months: Q1→03, Q2→06, Q3→09, Q4→12

---

## Scraper Logic

1. **Fast path:** Try direct `requests.get()` + BeautifulSoup to parse `div.col-sm-12.mar-l20.region_716` links. If reports found, skip browser.
2. **Fallback:** Launch `undetected_chromedriver` with stealth options + accept cookie banner → wait for `div.region_716` → collect links.
3. **PDF download:** Use `requests.Session` with browser cookies. If that fails, fall back to `ActionChains` click + wait for file to appear in download dir.
4. **Target selection:** `TARGET_DATE=None` → pick `max(reports, key=sort_key)` (latest). `TARGET_DATE='2024-Q4'` → filter by normalized quarter string.

---

## Running Tests Against Sample PDFs

```python
import extractor, config

# Test a single PDF
result = extractor.extract(r'path/to/file.pdf')
# Returns: {'quarter_key': '2024-Q3', 'date_str': '202409', 'data': {...}}

# Check hit count
hits = sum(1 for k in config.COL_KEYS if k in result['data'])
print(hits, '/', len(config.COL_KEYS))  # should be 9/9

# Test OCR path directly (for image-based PDFs)
import fitz
doc = fitz.open(r'path/to/image_pdf.pdf')
data = extractor._extract_ocr(doc)
doc.close()
```

---

## Known Issues / Future Work

| Issue | Details |
|-------|---------|
| 2020-Q3 Alternatives = None | PSM 11 reads ForeignEquities value between two halves of Alternatives label. Affects only this one historical quarter. Would need spatial OCR (HOCR bounding boxes at paragraph level, not word level) to fix. |
| Thai `image_to_data` character-level detection | pytesseract returns individual Thai characters (not words) from `image_to_data`, making spatial clustering by word impossible. `image_to_string` correctly assembles characters via the language model. |
| Scraper not live-tested | The scraper was built from the site structure but has not been run end-to-end against the live site. First live run may need minor selector adjustments if the site layout changed. |
| `_selenium_click_download` untested | The fallback Selenium click-download path has not been exercised in tests. |
