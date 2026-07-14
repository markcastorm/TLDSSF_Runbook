"""
Orchestrates the TLDSSF pipeline:
  scraper → extractor → file_generator

Always produces a timestamped output folder and updates output/latest/.
Master CSV is only appended to when the quarter is genuinely new.
"""

import logging

import scraper
import extractor
import file_generator

logger = logging.getLogger(__name__)


def run():
    """
    Execute the full pipeline end-to-end.

    Returns:
      output_dir (str) if new data was written.
      None          if the quarter was already in the master CSV.
    Raises on unrecoverable errors.
    """
    # ── Step 1: Download ──────────────────────────────────────────────────────
    logger.info('=== Step 1: Scraping ===')
    dl = scraper.download()
    quarter_key = dl['quarter_key']
    pdf_path    = dl['pdf_path']
    logger.info(f'Downloaded: {quarter_key} → {pdf_path}')

    # ── Step 2: Early duplicate check (fast path before PDF extraction) ───────
    if file_generator.is_already_in_master(quarter_key):
        logger.info(f'Quarter {quarter_key} already in master — nothing to do')
        return None

    # ── Step 3: Extract ───────────────────────────────────────────────────────
    logger.info('=== Step 2: Extracting ===')
    result = extractor.extract(pdf_path)
    logger.info(f'Extracted: {result["quarter_key"]} | cols: {list(result["data"].keys())}')

    # Sanity-check: extracted quarter must match what the scraper found
    if result['quarter_key'] != quarter_key:
        raise RuntimeError(
            f'Quarter mismatch: scraper={quarter_key}, PDF={result["quarter_key"]}'
        )

    # ── Step 4: Generate outputs ──────────────────────────────────────────────
    logger.info('=== Step 3: Generating files ===')
    output_dir = file_generator.generate(
        quarter_key=result['quarter_key'],
        date_str=result['date_str'],
        data=result['data'],
    )

    return output_dir
