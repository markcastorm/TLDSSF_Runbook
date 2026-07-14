"""
Manages master CSV append, DATA/META xlsx output, ZIP, and latest/ copy.

Master CSV format (never modify existing rows):
  Row 0: ' ', CODE1, CODE2, ...
  Row 1: '',  LABEL1, LABEL2, ...
  Row 2+: QUARTER_KEY, VAL1, VAL2, ...

DATA xlsx: same 2-header-row structure as master CSV.
META xlsx: one row per series, header row + 9 data rows.
ZIP:       contains DATA xlsx + META xlsx.
latest/:   always replaced with the current run's output.
"""

import csv
import os
import re
import shutil
import logging
import zipfile
from datetime import datetime

import openpyxl

import config

logger = logging.getLogger(__name__)


# ── Master CSV helpers ────────────────────────────────────────────────────────

def _read_master_quarters():
    """Return set of quarter_keys already in the master CSV."""
    path = config.MASTER_CSV
    if not os.path.exists(path):
        return set()
    quarters = set()
    with open(path, newline='', encoding='utf-8-sig') as f:
        reader = csv.reader(f)
        rows = list(reader)
    # Rows 0 and 1 are headers; data starts at row 2
    for row in rows[2:]:
        if row and row[0].strip():
            quarters.add(row[0].strip())
    return quarters


def is_already_in_master(quarter_key):
    """Return True if this quarter_key is already recorded in the master CSV."""
    exists = quarter_key in _read_master_quarters()
    if exists:
        logger.info(f'Quarter {quarter_key} already in master CSV — skipping')
    return exists


def append_to_master(quarter_key, data):
    """
    Append one new data row to the master CSV.
    Values are written as plain integers (no commas, no quotes).
    """
    path = config.MASTER_CSV
    row = [quarter_key] + [data.get(k, '') for k in config.COL_KEYS]

    # Ensure the file ends with a newline before appending so the new row
    # is not concatenated onto the last existing row (Excel often omits the
    # trailing newline on the final line).
    try:
        with open(path, 'rb') as f:
            f.seek(-1, 2)
            last_byte = f.read(1)
    except OSError:
        last_byte = b'\n'

    with open(path, 'a', newline='', encoding='utf-8') as f:
        if last_byte not in (b'\n', b'\r'):
            f.write('\n')
        writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
        writer.writerow(row)
    logger.info(f'Appended {quarter_key} to master CSV')


# ── DATA xlsx ─────────────────────────────────────────────────────────────────

def _read_master_rows():
    """
    Read master CSV and return (header_rows, data_rows).
    header_rows: list of 2 raw CSV rows (each a list of strings)
    data_rows:   list of data rows (each a list of strings/values)
    """
    path = config.MASTER_CSV
    with open(path, newline='', encoding='utf-8-sig') as f:
        rows = list(csv.reader(f))
    return rows[:2], rows[2:]


def _parse_value(val_str):
    """Convert a CSV value string like '1,344,682' or '1344682' to int, or return as-is."""
    if isinstance(val_str, str):
        cleaned = val_str.replace(',', '').strip()
        if cleaned.lstrip('-').isdigit():
            return int(cleaned)
    return val_str


def generate_data_xlsx(output_dir, date_str):
    """
    Write DATA xlsx to output_dir.
    Filename: TLDSSF_Q_DATA_{YYYYMMDD}.xlsx
    Structure: 2 header rows (codes, labels) then all master data rows.
    """
    header_rows, data_rows = _read_master_rows()
    filename = f'TLDSSF_Q_DATA_{date_str}.xlsx'
    filepath = os.path.join(output_dir, filename)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = f'TLDSSF_Q_DATA_{date_str}'[:31]

    # Write 2 header rows exactly as in master CSV
    for hrow in header_rows:
        ws.append(hrow)

    # Write data rows (parse numeric values)
    for drow in data_rows:
        if not drow or not drow[0].strip():
            continue
        parsed = [drow[0]] + [_parse_value(v) for v in drow[1:]]
        ws.append(parsed)

    wb.save(filepath)
    logger.info(f'Generated DATA xlsx: {filepath}')
    return filepath


# ── META xlsx ─────────────────────────────────────────────────────────────────

def generate_meta_xlsx(output_dir, date_str):
    """
    Write META xlsx to output_dir.
    Filename: TLDSSF_Q_META_{YYYYMMDD}.xlsx
    Structure: 1 header row + 1 data row per series (9 rows total).
    """
    filename = f'TLDSSF_Q_META_{date_str}.xlsx'
    filepath = os.path.join(output_dir, filename)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = f'TLDSSF_Q_META_{date_str}'[:31]

    ws.append(config.META_COLUMNS)

    for col in config.COLUMNS:
        row = [
            col['code'],                        # CODE
            col['label'],                        # DESCRIPTION
        ] + [
            config.META_STATIC[c]
            for c in config.META_COLUMNS[2:]    # remaining static fields
        ]
        ws.append(row)

    wb.save(filepath)
    logger.info(f'Generated META xlsx: {filepath}')
    return filepath


# ── ZIP ───────────────────────────────────────────────────────────────────────

def create_zip(output_dir, timestamp, file_paths):
    """
    Create a ZIP of the given file_paths inside output_dir.
    Filename: TLDSSF_Q_{timestamp}.zip
    """
    zip_name = f'TLDSSF_Q_{timestamp}.zip'
    zip_path = os.path.join(output_dir, zip_name)
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for fp in file_paths:
            zf.write(fp, os.path.basename(fp))
    logger.info(f'Created ZIP: {zip_path}')
    return zip_path


# ── latest/ copy ──────────────────────────────────────────────────────────────

def update_latest(output_dir):
    """Replace output/latest/ with the contents of output_dir."""
    latest_dir = os.path.join(config.OUTPUT_DIR, 'latest')
    if os.path.exists(latest_dir):
        shutil.rmtree(latest_dir)
    shutil.copytree(output_dir, latest_dir)
    logger.info(f'Updated latest/ → {latest_dir}')


# ── Main entry point ──────────────────────────────────────────────────────────

def generate(quarter_key, date_str, data):
    """
    Full output generation for one extracted quarter.

    1. Check master CSV — skip if already present (duplicate guard).
    2. Append new row to master CSV.
    3. Write DATA xlsx (full master content).
    4. Write META xlsx.
    5. ZIP both.
    6. Copy to output/latest/.

    Returns output_dir path, or None if skipped.
    """
    if is_already_in_master(quarter_key):
        return None

    # Derive YYYYMMDD from date_str (already 'YYYYMM' → pad to 'YYYYMM01')
    # Use today's date for output file names (matches existing naming convention)
    today = datetime.now().strftime('%Y%m%d')
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

    output_dir = os.path.join(config.OUTPUT_DIR, timestamp)
    os.makedirs(output_dir, exist_ok=True)

    # 1. Append to master
    append_to_master(quarter_key, data)

    # 2. DATA xlsx
    data_path = generate_data_xlsx(output_dir, today)

    # 3. META xlsx
    meta_path = generate_meta_xlsx(output_dir, today)

    # 4. ZIP
    create_zip(output_dir, timestamp, [data_path, meta_path])

    # 5. latest/
    update_latest(output_dir)

    logger.info(f'Output complete: {output_dir}')
    return output_dir
