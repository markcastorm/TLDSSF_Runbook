import os
import re
import logging

import fitz  # PyMuPDF

import config

logger = logging.getLogger(__name__)

# ── Thai text helpers ─────────────────────────────────────────────────────────

def _has_thai(text):
    return any('฀' <= c <= '๿' for c in text)


def _normalize(text):
    """
    Collapse whitespace AND remove spaces that fitz inserts before Thai
    combining marks (tone marks, above-vowels). These appear as 'ค ้า'
    instead of 'ค้า' due to PDF font encoding artefacts.
    """
    # Remove space between a Thai character and a following Thai
    # combining/tone mark / vowel sign
    # Thai combining range roughly: U+0E30–U+0E4E
    text = re.sub(r'(?<=[฀-๿]) (?=[ะ-๎])', '', text)
    return ' '.join(text.split()).strip()


# ── Date / quarter parsing ────────────────────────────────────────────────────

def _parse_date_from_text(raw_text):
    """
    Extract (quarter_key, date_str) from raw PDF text.
    Handles both:
      Old:  'ไตรมาสที่ 3 ณ  30  กันยายน  2563'
      New:  'ไตรมาสที่  ณ 31 ธันวาคม 2567'  or  ' ณ 31 ธันวาคม 2567'
    Returns ('2024-Q4', '202412') or (None, None).
    """
    # Quarter number (may be missing in some PDFs)
    q_match = re.search(r'ไตรมาสที่\s*(\d+)', raw_text)
    q_num = int(q_match.group(1)) if q_match else None

    # Month + year (BE)
    month_pattern = '|'.join(re.escape(m) for m in config.THAI_MONTHS)
    date_match = re.search(
        r'(\d{1,2})\s*(' + month_pattern + r')\s*(\d{4})',
        raw_text,
    )
    if not date_match:
        return None, None

    month_thai = date_match.group(2)
    year_be    = int(date_match.group(3))
    year_ce    = year_be - 543
    month_num  = config.THAI_MONTHS[month_thai]

    # Infer quarter from end-of-quarter month when Q number absent
    if q_num is None:
        q_num = config.MONTH_TO_QUARTER.get(month_num)
    if q_num is None:
        logger.warning(f'Cannot determine quarter from month {month_num}')
        return None, None

    end_month   = {1: 3, 2: 6, 3: 9, 4: 12}[q_num]
    quarter_key = f'{year_ce}-Q{q_num}'
    date_str    = f'{year_ce}{end_month:02d}'
    return quarter_key, date_str


# ── Label → column matching ───────────────────────────────────────────────────

def _match_label(label_text):
    """
    Match a Thai label string to a config.COLUMNS entry.
    Returns col_key or None.
    """
    norm = _normalize(label_text)
    for col in config.COLUMNS:
        if col['col_key'] == 'Total':
            continue  # Total is extracted separately
        required = col['thai_keys']
        excluded = col.get('exclude_keys', [])
        if all(k in norm for k in required) and not any(k in norm for k in excluded):
            return col['col_key']
    return None


# ── Value extraction: text-based PDFs ────────────────────────────────────────

def _extract_text_based(raw_text):
    """
    Parse pie chart label+value pairs from the raw text of a text-based PDF.

    Observed line patterns from probe analysis (Q4 2024 example):
      'เงินฝาก, 65,084 , 2.45%'       → label+value+pct on ONE line
      'ประกัน, 1,387,919 , '           → label+value, pct on NEXT line
      '52.23%'                          → pct-only line (skip)
      '368,365 , 13.86%'               → pure value+pct (no Thai)
      '46,324 , 1.74%'                 → pure value+pct (unmapped category)
      '2,657,245 ล้านบาท '             → standalone total

    Three trigger patterns flush the label buffer:
      VALUE_PCT   : ', VALUE , XX.XX%' — all on same line
      VALUE_ONLY  : ', VALUE , '       — value+trailing comma, pct on next line
      PURE_VALPC  : '^VALUE , XX.XX%' — line starts with value (no Thai label)

    Returns dict: {col_key: int_value, 'Total': int_value}
    """
    lines = [l.strip() for l in raw_text.split('\n')]

    # ── Total ─────────────────────────────────────────────────────────────────
    total_val = None
    for line in lines:
        m = re.match(r'^([\d,]{5,})\s*ล้านบาท', line)
        if m:
            total_val = int(m.group(1).replace(',', ''))
            break
    if total_val is None:
        m = re.search(r'([\d,]{5,})\s*ล้านบาท', raw_text)
        if m:
            total_val = int(m.group(1).replace(',', ''))

    # ── Chart section boundaries ──────────────────────────────────────────────
    chart_start = 0
    for i, line in enumerate(lines):
        if 'รายงานสถานะ' in line and i > 0:
            chart_start = i + 1
            break

    chart_end = len(lines)
    for i in range(chart_start, len(lines)):
        if re.match(r'^[\d,]{5,}\s*ล้านบาท', lines[i]):
            chart_end = i + 1
            break

    chart_lines = lines[chart_start:chart_end]

    # ── Regex patterns ────────────────────────────────────────────────────────
    # VALUE_PCT:   ', VALUE , XX.XX%' (pct on same line)
    VALUE_PCT   = re.compile(r',\s*([\d,]{4,})\s*,\s*\d+\.\d+%')
    # VALUE_ONLY:  ', VALUE , '  at end of line (trailing comma; pct on next line)
    VALUE_ONLY  = re.compile(r',\s*([\d,]{4,})\s*,\s*$')
    # VALUE_TRAIL:  ', VALUE'     at end of line (no trailing comma; pct/comma on next line)
    # e.g. 'ตราสารทุนไทย, 267,567'  followed by  ',10.34%'
    VALUE_TRAIL  = re.compile(r',\s*([\d,]{4,})\s*$')
    # VALUE_LAXEND: ', VALUE ,' followed by non-numeric garbage (OCR column bleed artefact)
    # e.g. 'ความน่าเชื่อถือ , 89,256 ,                 cee  va 8'
    # Lookahead prevents stealing lines that VALUE_PCT should handle (no digit/% follows).
    VALUE_LAXEND = re.compile(r',\s*([\d,]{4,})\s*,(?!\s*[\d%])')
    # PURE_VALPC:   'VALUE , XX.XX%' at line start (no leading Thai)
    PURE_VALPC   = re.compile(r'^([\d,]{4,})\s*,\s*\d+\.\d+%')
    # PCT_ONLY:     line is just 'XX.XX%' or ',XX.XX%' (follows value line)
    PCT_ONLY     = re.compile(r'^,?\d+\.\d+%')

    label_buf = []
    results   = {}

    def _flush(label_parts, value):
        full_label = _normalize(' '.join(label_parts))
        col_key = _match_label(full_label)
        if col_key and col_key not in results:
            results[col_key] = value
            logger.debug(f"  '{full_label[:50]}' → {col_key} = {value:,}")
        else:
            logger.debug(f"  Unmapped: '{full_label[:50]}' = {value:,}")

    for line in chart_lines:
        if not line:
            continue

        # Skip pure percentage lines
        if PCT_ONLY.match(line):
            continue

        # 1. PURE_VALPC: VALUE , PCT% — line starts with value (no Thai label prefix).
        # Must be checked BEFORE VALUE_PCT: VALUE_PCT uses .search() and would otherwise
        # match mid-number commas (e.g. captures '315,539' from '1,315,539 , 43.34%').
        m = PURE_VALPC.match(line)
        if m:
            val = int(m.group(1).replace(',', ''))
            _flush(label_buf, val)
            label_buf = []
            continue

        # 2. VALUE_PCT: label_chunk, VALUE , PCT% — all on this line
        m = VALUE_PCT.search(line)
        if m:
            val = int(m.group(1).replace(',', ''))
            pre = line[:m.start()].strip().strip(',').strip()
            if pre:
                label_buf.append(pre)
            _flush(label_buf, val)
            label_buf = []
            continue

        # 3. VALUE_ONLY: label_chunk, VALUE , <end-of-line> — trailing comma; pct on next line
        m = VALUE_ONLY.search(line)
        if m:
            val = int(m.group(1).replace(',', ''))
            pre = line[:m.start()].strip().strip(',').strip()
            if pre:
                label_buf.append(pre)
            _flush(label_buf, val)
            label_buf = []
            continue

        # 4. VALUE_TRAIL: label_chunk, VALUE<end-of-line> — no trailing comma; ',PCT%' on next line
        m = VALUE_TRAIL.search(line)
        if m:
            val = int(m.group(1).replace(',', ''))
            pre = line[:m.start()].strip().strip(',').strip()
            if pre:
                label_buf.append(pre)
            _flush(label_buf, val)
            label_buf = []
            continue

        # 5. VALUE_LAXEND: ', VALUE ,' followed by non-numeric OCR garbage
        m = VALUE_LAXEND.search(line)
        if m:
            val = int(m.group(1).replace(',', ''))
            pre = line[:m.start()].strip().strip(',').strip()
            if pre:
                label_buf.append(pre)
            _flush(label_buf, val)
            label_buf = []
            continue

        # 6. Label-only line: accumulate
        label_buf.append(line)

    if total_val is not None:
        results['Total'] = total_val

    return results


# ── OCR fallback for image-based PDFs ─────────────────────────────────────────

def _extract_ocr(doc):
    """
    OCR-based extraction for PDFs where the pie chart is an embedded image.

    Strategy (proven 9/9 on 2019-Q1 and 2019-Q2):
      1. Extract the largest embedded image from page 1 via doc.extract_image()
         — same bytes as the original art, avoids re-rendering at lower DPI.
      2. Fall back to a 300-DPI full-page render if no embedded images exist.
      3. Convert to grayscale + 2x upscale.
         Colored label-box backgrounds suppress OCR confidence on RGB input.
      4. pytesseract PSM 11 (sparse text) finds labels scattered around the
         pie rather than assuming a single contiguous text block.
      5. Normalize ';' → ',' (common OCR misread for Thai comma separator).
    """
    try:
        from PIL import Image
        import pytesseract
        import io as _io

        page = doc[0]

        # ── Step 1: try embedded image extraction ─────────────────────────────
        img = None
        images = page.get_images(full=True)
        if images:
            best_bytes = None
            best_area  = 0
            for img_info in images:
                xref = img_info[0]
                try:
                    base = doc.extract_image(xref)
                    area = base['width'] * base['height']
                    if area > best_area:
                        best_area  = area
                        best_bytes = base['image']
                except Exception:
                    continue
            if best_bytes:
                img = Image.open(_io.BytesIO(best_bytes))
                logger.info(f'Extracted embedded chart image: {img.size}')

        # ── Step 2: fall back to page render ──────────────────────────────────
        if img is None:
            pix = page.get_pixmap(dpi=300)
            img = Image.open(_io.BytesIO(pix.tobytes('png')))
            logger.info(f'No embedded images; rendered page at 300 DPI: {img.size}')

        # ── Step 3: grayscale + 2x upscale ────────────────────────────────────
        img = img.convert('L')
        img = img.resize((img.width * 2, img.height * 2), Image.LANCZOS)

        # ── Step 4: OCR ───────────────────────────────────────────────────────
        ocr_text = pytesseract.image_to_string(img, lang='tha+eng', config='--psm 11')
        logger.info('OCR completed (grayscale 2x, psm 11)')
        logger.debug(f'OCR text snippet: {ocr_text[:300]}')

        # ── Step 5: normalize artefacts ───────────────────────────────────────
        ocr_text = ocr_text.replace(';', ',')

        return _extract_text_based(ocr_text)

    except ImportError:
        logger.warning('pytesseract or Pillow not available; OCR skipped')
        return {}
    except Exception as e:
        logger.warning(f'OCR failed: {e}')
        return {}


# ── Main extraction entry point ───────────────────────────────────────────────

def extract(pdf_path):
    """
    Extract data from a TLDSSF quarterly PDF.

    Returns dict:
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
    Raises on critical failure.
    """
    logger.info(f'Extracting from: {pdf_path}')
    doc      = fitz.open(pdf_path)
    page0    = doc[0]
    raw_text = page0.get_text('text')
    doc.close()

    # ── Date/quarter ──────────────────────────────────────────────────────────
    quarter_key, date_str = _parse_date_from_text(raw_text)
    if not quarter_key:
        raise ValueError(f'Could not parse date from PDF: {pdf_path}')
    logger.info(f'Quarter: {quarter_key} | Date: {date_str}')

    # ── Decide extraction method ──────────────────────────────────────────────
    thai_chars = sum(1 for c in raw_text if '฀' <= c <= '๿')
    logger.info(f'Thai chars on page 1: {thai_chars}')

    if thai_chars >= 800:
        # Enough Thai text — the chart labels are embedded as text
        data = _extract_text_based(raw_text)
        logger.info(f'Text extraction: {len(data)} fields')
    else:
        # Sparse Thai text — chart is an image, try OCR
        logger.info('Chart appears image-based; attempting OCR...')
        doc2 = fitz.open(pdf_path)
        data = _extract_ocr(doc2)
        doc2.close()
        logger.info(f'OCR extraction: {len(data)} fields')

    # ── Validate ──────────────────────────────────────────────────────────────
    missing = [k for k in config.DATA_COL_KEYS if k not in data]
    if missing:
        logger.warning(f'Missing columns: {missing}')

    found_cols = [k for k in config.COL_KEYS if k in data]
    logger.info(
        f'Extracted {len(found_cols)}/{len(config.COL_KEYS)} columns: '
        f'{found_cols}'
    )

    return {
        'quarter_key': quarter_key,
        'date_str':    date_str,
        'data':        data,
    }
