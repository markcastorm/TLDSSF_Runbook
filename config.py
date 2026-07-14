import os

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR = os.path.join(BASE_DIR, 'downloads')
OUTPUT_DIR   = os.path.join(BASE_DIR, 'output')
MASTER_CSV   = os.path.join(BASE_DIR, 'Master Data', 'Master_Data_TLDSSF_Q_DATA.csv')

# ── Source ────────────────────────────────────────────────────────────────────
BASE_URL = (
    'https://www.sso.go.th/wpr/main/knowledge/'
    '%E0%B8%A3%E0%B8%B2%E0%B8%A2%E0%B8%87%E0%B8%B2%E0%B8%99'
    '%E0%B8%AA%E0%B8%A0%E0%B8%B2%E0%B8%9E%E0%B8%81%E0%B8%B2'
    '%E0%B8%A3%E0%B8%9A%E0%B8%A3%E0%B8%B4%E0%B8%AB%E0%B8%B2'
    '%E0%B8%A3%E0%B9%80%E0%B8%87%E0%B8%B4%E0%B8%99%E0%B8%A5'
    '%E0%B8%87%E0%B8%97%E0%B8%B8%E0%B8%99'
    '_category_list-label_1_134_0'
)
# Decoded Thai path: รายงานสภาพการบริหารเงินลงทุน_category_list-label_1_134_0

# ── Target date ───────────────────────────────────────────────────────────────
# None  → always pick the latest available report
# "Q4-2024" / "2024-Q4" → pick specific quarter
TARGET_DATE = None

# ── Browser ───────────────────────────────────────────────────────────────────
HEADLESS_MODE = True   # True for Docker/prod; False for local debugging
WAIT_TIMEOUT  = 60     # seconds

# ── Job identity ──────────────────────────────────────────────────────────────
JOB_NAME = 'TLDSSF'

# ── Thai calendar ─────────────────────────────────────────────────────────────
THAI_MONTHS = {
    'มกราคม': 1,   # มกราคม
    'กุมภาพันธ์': 2,  # กุมภาพันธ์
    'มีนาคม': 3,   # มีนาคม
    'เมษายน': 4,   # เมษายน
    'พฤษภาคม': 5,  # พฤษภาคม
    'มิถุนายน': 6,  # มิถุนายน
    'กรกฎาคม': 7,  # กรกฎาคม
    'สิงหาคม': 8,  # สิงหาคม
    'กันยายน': 9,  # กันยายน
    'ตุลาคม': 10,  # ตุลาคม
    'พฤศจิกายน': 11,  # พฤศจิกายน
    'ธันวาคม': 12,  # ธันวาคม
}
MONTH_TO_QUARTER = {3: 1, 6: 2, 9: 3, 12: 4}

# ── Column definitions (ABSOLUTE — do not reorder) ───────────────────────────
#
# Each entry:
#   code        → SIMBA series code (used in CSV row 0)
#   label       → human label (used in CSV row 1)
#   thai_keys   → list of Thai keyword fragments; ALL must appear in label text
#                 for a positive match. Order: most-specific first.
#   exclude_keys→ if any of these appear, this mapping is REJECTED (avoids ambiguity)
#
COLUMNS = [
    {
        'code':  'TLDSOCIALSECURITYFUND.DOMESTICGOVERNMENTBONDS.LEVEL.NONE.Q.1@TLDSOCIALSECURITYFUNDQ',
        'label': 'TLD Social Security Fund: Domestic Government Bonds',
        'col_key': 'DomesticGovernmentBonds',
        # พันธบัตรรัฐบาล is unique to this category (vs พันธบัตรรัฐวิสาหกิจ for OtherBonds)
        'thai_keys':   ['พันธบัตรรัฐบาล'],
        'exclude_keys': [],
    },
    {
        'code':  'TLDSOCIALSECURITYFUND.FOREIGNBONDS.LEVEL.NONE.Q.1@TLDSOCIALSECURITYFUNDQ',
        'label': 'TLD Social Security Fund: Foreign Bonds',
        'col_key': 'ForeignBonds',
        # ตปท (abbrev) appears in newer text PDFs; older OCR PDFs use ต่างประเทศ in full.
        # Matching on ตราสารหนี + น่าเชื่อถือ covers both formats.
        # The 9th unrated-foreign-bonds category also has both keys but is caught by exclude_keys.
        'thai_keys':   ['ตราสารหนี', 'น่าเชื่อถือ'],
        'exclude_keys': ['ไม่ได้รับ', 'ไม่ได้จัด'],
    },
    {
        'code':  'TLDSOCIALSECURITYFUND.DEPOSITS.LEVEL.NONE.Q.1@TLDSOCIALSECURITYFUNDQ',
        'label': 'TLD Social Security Fund: Deposits',
        'col_key': 'Deposits',
        'thai_keys':   ['เงินฝาก'],  # เงินฝาก
        'exclude_keys': [],
    },
    {
        'code':  'TLDSOCIALSECURITYFUND.CORPORATEBONDS.LEVEL.NONE.Q.1@TLDSOCIALSECURITYFUNDQ',
        'label': 'TLD Social Security Fund: Corporate Bonds',
        'col_key': 'CorporateBonds',
        'thai_keys':   ['หุ้นกู้เอกชน'],  # หุ้นกู้เอกชน
        'exclude_keys': [],
    },
    {
        'code':  'TLDSOCIALSECURITYFUND.OTHERBONDS.LEVEL.NONE.Q.1@TLDSOCIALSECURITYFUNDQ',
        'label': 'TLD Social Security Fund: Other Bonds',
        'col_key': 'OtherBonds',
        # พันธบัตรรัฐวิสาหกิจ is unique to this category (state enterprise bonds)
        'thai_keys':   ['พันธบัตรรัฐวิสาหกิจ'],
        'exclude_keys': [],
    },
    {
        'code':  'TLDSOCIALSECURITYFUND.DOMESTICEQUITIES.LEVEL.NONE.Q.1@TLDSOCIALSECURITYFUNDQ',
        'label': 'TLD Social Security Fund: Domestic Equities',
        'col_key': 'DomesticEquities',
        'thai_keys':   ['ตราสารทุนไทย'],  # ตราสารทุนไทย
        'exclude_keys': [],
    },
    {
        'code':  'TLDSOCIALSECURITYFUND.FOREIGNEQUITIES.LEVEL.NONE.Q.1@TLDSOCIALSECURITYFUNDQ',
        'label': 'TLD Social Security Fund: Foreign Equities',
        'col_key': 'ForeignEquities',
        # ตปท (abbrev) appears in newer text PDFs; older OCR PDFs use ต่างประเทศ.
        # ตราสารทุน alone + exclude ไทย is sufficient to distinguish from DomesticEquities.
        'thai_keys':   ['ตราสารทุน'],
        'exclude_keys': ['ไทย'],
    },
    {
        'code':  'TLDSOCIALSECURITYFUND.ALTERNATIVES.LEVEL.NONE.Q.1@TLDSOCIALSECURITYFUNDQ',
        'label': 'TLD Social Security Fund: Alternatives',
        'col_key': 'Alternatives',
        'thai_keys':   ['อสังหา'],  # อสังหา
        'exclude_keys': [],
    },
    {
        'code':  'TLDSOCIALSECURITYFUND.TOTAL.LEVEL.NONE.Q.1@TLDSOCIALSECURITYFUNDQ',
        'label': 'TLD Social Security Fund: Total',
        'col_key': 'Total',
        'thai_keys':   ['ล้านบาท'],  # ล้านบาท
        'exclude_keys': [],
    },
]

# Column key order for CSV/Excel output
COL_KEYS = [c['col_key'] for c in COLUMNS]
# Data column keys only (exclude Total for individual sum check)
DATA_COL_KEYS = COL_KEYS[:-1]  # everything except 'Total'

# ── META static fields ────────────────────────────────────────────────────────
# Matches the actual TLDSSF_Q_META_20250124.xlsx column structure exactly.
META_STATIC = {
    'FREQUENCY':           'Q',
    'MULTIPLIER':          6,
    'AGGREGATION_TYPE':    'END_OF_PERIOD',
    'UNIT_TYPE':           'LEVEL',
    'DATA_TYPE':           'CURRENCY',
    'DATA_UNIT':           'THB',
    'SEASONALLY_ADJUSTED': 'NSA',
    'ANNUALIZED':          False,
    'STATE':               'ACTIVE',
    'PROVIDER_MEASURE_URL': BASE_URL,
    'PROVIDER':            'AfricaAI',
    'SOURCE':              'SSO',
    'SOURCE_DESCRIPTION':  'Thailand Social Security Office',
    'COUNTRY':             'THA',
    'DATASET':             'TLDSSF',
}

META_COLUMNS = [
    'CODE', 'DESCRIPTION', 'FREQUENCY', 'MULTIPLIER', 'AGGREGATION_TYPE',
    'UNIT_TYPE', 'DATA_TYPE', 'DATA_UNIT', 'SEASONALLY_ADJUSTED', 'ANNUALIZED',
    'STATE', 'PROVIDER_MEASURE_URL', 'PROVIDER', 'SOURCE', 'SOURCE_DESCRIPTION',
    'COUNTRY', 'DATASET',
]
