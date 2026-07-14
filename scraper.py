import os
import sys
import re
import time
import logging
import subprocess
import requests

import config

logger = logging.getLogger(__name__)


# ── Chrome version detection ──────────────────────────────────────────────────

def _get_chrome_version():
    """Detect Chrome major version on Windows (registry) and Linux (CLI)."""
    if sys.platform == 'win32':
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r'Software\Google\Chrome\BLBeacon',
            )
            ver = winreg.QueryValueEx(key, 'version')[0]
            return ver.split('.')[0]
        except Exception:
            pass
    for cmd in ['google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser']:
        try:
            out = subprocess.check_output(
                [cmd, '--version'], stderr=subprocess.DEVNULL,
            ).decode()
            return out.strip().split()[-1].split('.')[0]
        except Exception:
            continue
    return None


# ── Thai date / quarter helpers ───────────────────────────────────────────────

def _parse_report_text(text):
    """
    Parse a report link text like:
      'รายงานสถานะการบริหารเงินลงทุนกองทุนประกันสังคม ไตรมาสที่ 4 ณ 31 ธันวาคม 2567'
    Returns (quarter_key, sort_key) or (None, None).
    quarter_key: e.g. '2024-Q4'
    sort_key: (year_ce, quarter_num) for sorting latest-first
    """
    # Quarter number
    q_match = re.search(r'ไตรมาสที่\s*(\d+)', text)
    q_num = int(q_match.group(1)) if q_match else None

    # Month + year (Buddhist Era)
    date_match = re.search(
        r'(\d+)\s*(' + '|'.join(config.THAI_MONTHS.keys()) + r')\s*(\d{4})',
        text,
    )
    if not date_match:
        return None, None

    month_thai = date_match.group(2)
    year_be    = int(date_match.group(3))
    year_ce    = year_be - 543
    month_num  = config.THAI_MONTHS[month_thai]

    # Derive quarter from month if not found
    if q_num is None:
        q_num = config.MONTH_TO_QUARTER.get(month_num)
    if q_num is None:
        return None, None

    quarter_key = f'{year_ce}-Q{q_num}'
    sort_key    = (year_ce, q_num)
    return quarter_key, sort_key


def _target_matches(quarter_key):
    """
    Returns True if this quarter_key is the one we want.
    When TARGET_DATE is None, caller picks the highest sort_key.
    """
    if config.TARGET_DATE is None:
        return True
    # Normalize target: accept '2024-Q4' or 'Q4-2024' or '2024Q4'
    t = config.TARGET_DATE.upper().replace('-', '').replace('Q', 'Q')
    k = quarter_key.upper().replace('-', '').replace('Q', 'Q')
    return t == k


# ── Selenium stealth browser ──────────────────────────────────────────────────

def _build_driver(download_dir):
    """Build a Selenium undetected_chromedriver with stealth."""
    import undetected_chromedriver as uc
    from selenium_stealth import stealth

    options = uc.ChromeOptions()
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-blink-features=AutomationControlled')
    options.add_argument('--window-size=1920,1080')
    options.add_argument('--lang=th-TH,th;q=0.9,en-US;q=0.8,en;q=0.7')

    if config.HEADLESS_MODE:
        options.add_argument('--headless=new')

    prefs = {
        'download.default_directory': download_dir,
        'download.prompt_for_download': False,
        'plugins.always_open_pdf_externally': True,
    }
    options.add_experimental_option('prefs', prefs)

    ver = _get_chrome_version()
    kwargs = {'options': options, 'use_subprocess': True}
    if ver:
        try:
            kwargs['version_main'] = int(ver)
        except ValueError:
            pass

    driver = uc.Chrome(**kwargs)

    stealth(
        driver,
        languages=['th-TH', 'th', 'en-US', 'en'],
        vendor='Google Inc.',
        platform='Win32',
        webgl_vendor='Intel Inc.',
        renderer='Intel Iris OpenGL Engine',
        fix_hairline=True,
    )
    driver.set_page_load_timeout(config.WAIT_TIMEOUT)
    return driver


def _accept_cookies(driver):
    """Click 'Accept all' cookie button if visible."""
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    try:
        btn = WebDriverWait(driver, 8).until(
            EC.element_to_be_clickable((By.ID, 'c-p-bn'))
        )
        # Human-like: scroll into view, small pause, click
        driver.execute_script('arguments[0].scrollIntoView(true);', btn)
        time.sleep(0.5 + 0.3 * (hash(str(time.time())) % 10) / 10)
        btn.click()
        time.sleep(1.0)
        logger.info('Cookie consent accepted')
    except Exception:
        logger.debug('No cookie banner found or already dismissed')


def _get_report_links(driver):
    """
    Return list of dicts: {'quarter_key', 'sort_key', 'pdf_url', 'text'}
    from all region_716 report divs on the page.
    """
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    # Wait for at least one report div to appear
    WebDriverWait(driver, config.WAIT_TIMEOUT).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, 'div.region_716'))
    )

    reports = []
    divs = driver.find_elements(By.CSS_SELECTOR, 'div.col-sm-12.mar-l20.region_716')
    for div in divs:
        try:
            a = div.find_element(By.TAG_NAME, 'a')
            href = a.get_attribute('href') or ''
            text = a.text.strip()
            if not href.lower().endswith('.pdf'):
                continue
            quarter_key, sort_key = _parse_report_text(text)
            if quarter_key:
                reports.append({
                    'quarter_key': quarter_key,
                    'sort_key':    sort_key,
                    'pdf_url':     href,
                    'text':        text,
                })
        except Exception:
            continue
    return reports


def _download_pdf_requests(pdf_url, dest_path, cookies=None):
    """Download PDF via requests, optionally using browser cookies."""
    headers = {
        'User-Agent': (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/124.0.0.0 Safari/537.36'
        ),
        'Referer': config.BASE_URL,
    }
    session = requests.Session()
    if cookies:
        for ck in cookies:
            session.cookies.set(ck['name'], ck['value'], domain=ck.get('domain', ''))
    resp = session.get(pdf_url, headers=headers, timeout=60, stream=True)
    resp.raise_for_status()
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    with open(dest_path, 'wb') as f:
        for chunk in resp.iter_content(chunk_size=65536):
            f.write(chunk)
    size = os.path.getsize(dest_path)
    logger.info(f'Downloaded {os.path.basename(dest_path)} ({size:,} bytes)')
    return dest_path


# ── Direct requests scrape (no browser needed if site is not JS-gated) ────────

def _try_direct_requests():
    """
    Attempt to fetch the listing page and find PDF links without a browser.
    Returns list of report dicts or None on failure.
    """
    try:
        from bs4 import BeautifulSoup
        headers = {
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/124.0.0.0 Safari/537.36'
            ),
            'Accept-Language': 'th-TH,th;q=0.9,en-US;q=0.8,en;q=0.7',
        }
        resp = requests.get(config.BASE_URL, headers=headers, timeout=30)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, 'html.parser')
        reports = []
        for div in soup.select('div.col-sm-12.mar-l20.region_716'):
            a = div.find('a')
            if not a:
                continue
            href = a.get('href', '').strip()
            text = a.get_text(' ', strip=True)
            if not href.lower().endswith('.pdf'):
                continue
            quarter_key, sort_key = _parse_report_text(text)
            if quarter_key:
                reports.append({
                    'quarter_key': quarter_key,
                    'sort_key':    sort_key,
                    'pdf_url':     href,
                    'text':        text,
                })
        if reports:
            logger.info(f'Direct requests: found {len(reports)} reports')
            return reports
    except Exception as e:
        logger.warning(f'Direct requests failed: {e}')
    return None


# ── Main entry point ──────────────────────────────────────────────────────────

def download():
    """
    Scrape the SSO Thailand site, find the target quarterly report,
    download its PDF, and return:
      {'quarter_key': '2024-Q4', 'pdf_path': '/path/to/file.pdf', 'date_str': '202412'}
    """
    timestamp = time.strftime('%Y%m%d_%H%M%S')
    run_dir   = os.path.join(config.DOWNLOAD_DIR, timestamp)
    os.makedirs(run_dir, exist_ok=True)

    # ── Step 1: Get report listing ────────────────────────────────────────────
    reports = _try_direct_requests()

    driver = None
    if not reports:
        logger.info('Falling back to Selenium stealth browser...')
        driver = _build_driver(run_dir)
        try:
            logger.info(f'Navigating to {config.BASE_URL}')
            driver.get(config.BASE_URL)
            time.sleep(2)

            _accept_cookies(driver)
            time.sleep(1)

            reports = _get_report_links(driver)
            logger.info(f'Selenium: found {len(reports)} reports')
        except Exception as e:
            if driver:
                driver.quit()
            raise RuntimeError(f'Failed to get report listing: {e}') from e

    if not reports:
        if driver:
            driver.quit()
        raise RuntimeError('No reports found on the page')

    # ── Step 2: Select target report ──────────────────────────────────────────
    if config.TARGET_DATE is None:
        # Pick the latest (highest sort_key)
        target = max(reports, key=lambda r: r['sort_key'])
        logger.info(f"Latest report: {target['quarter_key']} → {target['text']}")
    else:
        matches = [r for r in reports if _target_matches(r['quarter_key'])]
        if not matches:
            available = [r['quarter_key'] for r in reports]
            if driver:
                driver.quit()
            raise RuntimeError(
                f"TARGET_DATE '{config.TARGET_DATE}' not found. "
                f"Available: {available}"
            )
        target = matches[0]
        logger.info(f"Target report: {target['quarter_key']} → {target['text']}")

    # ── Step 3: Download the PDF ──────────────────────────────────────────────
    pdf_filename = os.path.basename(target['pdf_url'].split('?')[0])
    pdf_path     = os.path.join(run_dir, pdf_filename)

    cookies = None
    if driver:
        cookies = driver.get_cookies()

    try:
        _download_pdf_requests(target['pdf_url'], pdf_path, cookies=cookies)
    except Exception as e:
        if driver:
            # Last resort: let Selenium click the link and handle download
            logger.warning(f'requests download failed ({e}), trying browser click...')
            pdf_path = _selenium_click_download(driver, target, run_dir)
        else:
            raise

    if driver:
        try:
            driver.quit()
        except Exception:
            pass

    # ── Build result ──────────────────────────────────────────────────────────
    quarter_key = target['quarter_key']  # e.g. '2024-Q4'
    # Derive date_str from quarter_key for file naming
    year_ce, q_num_str = quarter_key.split('-Q')
    q_num = int(q_num_str)
    # End month of quarter: Q1→03, Q2→06, Q3→09, Q4→12
    end_month = {1: 3, 2: 6, 3: 9, 4: 12}[q_num]
    date_str = f'{year_ce}{end_month:02d}'

    return {
        'quarter_key': quarter_key,
        'pdf_path':    pdf_path,
        'date_str':    date_str,
    }


def _selenium_click_download(driver, target, run_dir):
    """Click the PDF link with Selenium and wait for download to complete."""
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.action_chains import ActionChains
    import glob

    before = set(glob.glob(os.path.join(run_dir, '*.pdf')))

    # Find the link by partial href
    pdf_filename = os.path.basename(target['pdf_url'])
    links = driver.find_elements(By.PARTIAL_LINK_TEXT, '')
    clicked = False
    for lnk in links:
        href = lnk.get_attribute('href') or ''
        if pdf_filename in href:
            driver.execute_script('arguments[0].scrollIntoView(true);', lnk)
            time.sleep(0.5)
            ActionChains(driver).move_to_element(lnk).pause(0.3).click(lnk).perform()
            clicked = True
            break

    if not clicked:
        raise RuntimeError('Could not find PDF link to click')

    # Wait for download (up to 60s)
    deadline = time.time() + 60
    while time.time() < deadline:
        time.sleep(2)
        after = set(glob.glob(os.path.join(run_dir, '*.pdf')))
        new_files = after - before
        if new_files:
            return new_files.pop()

    raise RuntimeError('PDF download via browser click timed out')
