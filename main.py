"""
Entry point for the TLDSSF quarterly investment report pipeline.

Usage:
  python main.py
"""

import logging
import sys

import orchestrator

# Windows console defaults to cp1252 which can't encode Thai text or arrows.
# Reconfigure stdout to UTF-8 before the logging handler is attached.
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(levelname)-8s  %(name)s  %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[logging.StreamHandler(sys.stdout)],
)

logger = logging.getLogger('main')


def main():
    logger.info('TLDSSF pipeline starting')
    try:
        output_dir = orchestrator.run()
        if output_dir:
            logger.info(f'Pipeline complete. Output: {output_dir}')
        else:
            logger.info('Pipeline complete. No new data (quarter already in master).')
    except Exception as exc:
        logger.error(f'Pipeline failed: {exc}', exc_info=True)
        sys.exit(1)


if __name__ == '__main__':
    main()
