"""
Flight information extraction and parsing.

Extracts flight data (airports, dates, confirmation codes, etc.) from email content.
Uses dateutil for robust date parsing instead of fragile regex patterns.
"""

import re
import hashlib
from datetime import datetime
from html.parser import HTMLParser
from html import unescape

from .airports import VALID_AIRPORT_CODES, EXCLUDED_CODES

# Try to import deps module (may not exist in very old versions before auto-update)
try:
    from .deps import get_dateutil_parser
except ImportError:
    # Fallback: try to import dateutil directly, or return None
    def get_dateutil_parser():
        try:
            from dateutil import parser
            return parser
        except ImportError:
            return None


# Pre-compile regex patterns for performance
_CONFIRMATION_PATTERNS = [
    re.compile(r'confirmation[:\s]+(?:code[:\s]+)?([A-Z0-9]{6})\b', re.IGNORECASE),
    re.compile(r'booking[:\s]+(?:reference[:\s]+)?([A-Z0-9]{6})\b', re.IGNORECASE),
    re.compile(r'record[:\s]+locator[:\s]+([A-Z0-9]{6})\b', re.IGNORECASE),
    re.compile(r'PNR[:\s]+([A-Z0-9]{6})\b', re.IGNORECASE),
    re.compile(r'code\s+is\s+([A-Z0-9]{6})\b', re.IGNORECASE),
    re.compile(r'reservation[:\s]+([A-Z0-9]{6})\b', re.IGNORECASE),
]
_SUBJECT_CONF_PATTERN = re.compile(r'[–-]\s*([A-Z0-9]{6})\s*$')
_GENERIC_CONF_PATTERN = re.compile(r'\b([A-Z0-9]{6})\b')

# Common English words that are 6 characters - these are NOT confirmation codes
# These cause false positives when parsing email text
_EXCLUDED_CONFIRMATION_CODES = {
    # Common words that appear in flight emails
    'WINDOW', 'BEFORE', 'CHANGE', 'FLIGHT', 'NUMBER', 'PLEASE', 'THANKS',
    'TRAVEL', 'RETURN', 'ARRIVE', 'DEPART', 'BOSTON', 'DALLAS', 'DENVER',
    'OTHERS', 'REVIEW', 'UPDATE', 'MANAGE', 'CANCEL', 'MODIFY', 'CREDIT',
    'POINTS', 'MEMBER', 'STATUS', 'SELECT', 'CHOOSE', 'OPTION', 'DOUBLE',
    'SINGLE', 'UNITED', 'VIRGIN', 'SPIRIT', 'AMOUNT', 'DOLLAR', 'CHARGE',
    'REFUND', 'POLICY', 'NOTICE', 'DETAIL', 'ITINER', 'RECEPT', 'INVOIC',
    'TICKET', 'RESERV', 'CONFIR', 'AIRPOR', 'TERMIN', 'CHECKIN',
    # More common words
    'EITHER', 'STREET', 'WITHIN', 'DURING', 'ACROSS', 'AROUND', 'BEHIND',
    'BEYOND', 'TOWARD', 'SHOULD', 'THOUGH', 'REALLY', 'ALWAYS', 'ALMOST',
    'PEOPLE', 'THINGS', 'HAPPEN', 'COMING', 'MAKING', 'TAKING', 'HAVING',
    'FAMILY', 'FRIEND', 'SUMMER', 'WINTER', 'SPRING', 'MONDAY', 'FRIDAY',
    'AUGUST', 'JANUAR', 'OCTOBE', 'NOVEMB', 'DECEMB', 'SEPTEM',
    # Technical/email words
    'IMAGES', 'CHROME', 'SAFARI', 'MOBILE', 'ONLINE', 'ACCESS', 'SECURE',
    'SUBMIT', 'BUTTON', 'FOOTER', 'HEADER', 'EMAILX', 'DOMAIN', 'SERVER',
}

# Airport extraction patterns (pre-compiled)
_CITY_CODE_PATTERN = re.compile(r'([A-Za-z\s]+)\s*\(([A-Z]{3})\)')
_ROUTE_PATTERN = re.compile(r'\b([A-Z]{3})\s*(?:→|->|►|to|–|-)\s*([A-Z]{3})\b')
_CONTEXT_PATTERN = re.compile(r'(?:depart|arrive|from|to|origin|destination)[:\s]+([A-Z]{3})\b', re.IGNORECASE)

# Flight number patterns (pre-compiled)
_FLIGHT_NUM_PATTERNS = [
    re.compile(r'[Ff]light\s*#?\s*(\d{1,4})\b'),
    re.compile(r'\b(?:B6|DL|UA|AA|WN|AS|NK|F9|HA|AC|BA|LH|EK)\s*(\d{1,4})\b'),
]

# Time pattern (pre-compiled)
_TIME_PATTERN = re.compile(r'\b(\d{1,2}:\d{2}\s*[AaPp][Mm])\b')

# HTML tag removal (pre-compiled)
_HTML_TAG_PATTERN = re.compile(r'<[^>]+>')
_WHITESPACE_PATTERN = re.compile(r'\s+')


class _TextExtractor(HTMLParser):
    """Fast HTML text extractor - extracts only visible text content."""

    __slots__ = ('text_parts', 'skip_depth')

    SKIP_TAGS = frozenset({'script', 'style', 'head', 'meta', 'link', 'noscript'})

    def __init__(self):
        super().__init__()
        self.text_parts = []
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag.lower() in self.SKIP_TAGS:
            self.skip_depth += 1

    def handle_endtag(self, tag):
        if tag.lower() in self.SKIP_TAGS and self.skip_depth > 0:
            self.skip_depth -= 1

    def handle_data(self, data):
        if self.skip_depth == 0:
            text = data.strip()
            if text:
                self.text_parts.append(text)

    def get_text(self):
        return ' '.join(self.text_parts)


def strip_html_tags(html_text):
    """Remove HTML tags and return only visible text content.

    Uses Python's built-in html.parser for proper extraction.

    Args:
        html_text: HTML string to parse

    Returns:
        Plain text with HTML removed
    """
    if not html_text:
        return ""

    try:
        parser = _TextExtractor()
        parser.feed(html_text)
        text = parser.get_text()
        text = unescape(text)
        text = _WHITESPACE_PATTERN.sub(' ', text)
        return text.strip()
    except Exception:
        # Fallback: simple regex strip if parser fails
        text = _HTML_TAG_PATTERN.sub(' ', html_text)
        text = _WHITESPACE_PATTERN.sub(' ', text)
        return text.strip()


def _is_valid_confirmation_code(code):
    """Check if a code looks like a valid confirmation code.

    Real confirmation codes are typically alphanumeric with mixed letters/numbers.
    Pure English words are not valid codes.
    """
    if not code:
        return False
    code = code.upper()

    # Check against excluded words
    if code in _EXCLUDED_CONFIRMATION_CODES:
        return False
    if code in EXCLUDED_CODES:  # Also check 3-letter exclusions (shouldn't match 6-char but be safe)
        return False

    # All digits is not a confirmation code (more likely a date or amount)
    if code.isdigit():
        return False

    # All letters with common word patterns - likely not a code
    # Real codes typically have at least one number OR unusual letter combos
    if code.isalpha():
        # Check for common word endings that indicate English words
        word_endings = ('ING', 'TED', 'LLY', 'ION', 'ATE', 'ENT', 'OUS', 'URE', 'BLE')
        for ending in word_endings:
            if code.endswith(ending):
                return False

    return True


def extract_confirmation_code(subject, body):
    """Extract confirmation code from email subject or body.

    Args:
        subject: Email subject line
        body: Email body text

    Returns:
        6-character confirmation code or None
    """
    # First try subject line - often has format "... - ABCDEF"
    match = _SUBJECT_CONF_PATTERN.search(subject)
    if match:
        code = match.group(1).upper()
        if _is_valid_confirmation_code(code):
            return code

    # Try to find confirmation code in context
    for pattern in _CONFIRMATION_PATTERNS:
        match = pattern.search(body)
        if match:
            code = match.group(1).upper()
            if _is_valid_confirmation_code(code):
                return code

    # Try subject with generic pattern (less reliable)
    match = _GENERIC_CONF_PATTERN.search(subject)
    if match:
        code = match.group(1).upper()
        if _is_valid_confirmation_code(code):
            return code

    return None


def _is_valid_flight_year(year):
    """Check if a year is reasonable for a flight date.

    Flight dates can be historical (past trips) or future bookings.
    We accept 1900 to 2 years in the future.
    """
    current_year = datetime.now().year
    return 1900 <= year <= (current_year + 2)


def _extract_dates_dateutil(text, base_year=None):
    """Extract dates using dateutil parser (much more robust than regex).

    Args:
        text: Text to search for dates
        base_year: Year to use for dates without year specified

    Returns:
        List of formatted date strings
    """
    dateutil_parser = get_dateutil_parser()
    if not dateutil_parser:
        return _extract_dates_fallback(text, base_year)

    if base_year is None:
        base_year = datetime.now().year

    dates = []
    seen = set()

    # Split text into potential date chunks
    # Look for patterns that might contain dates
    date_indicators = [
        'january', 'february', 'march', 'april', 'may', 'june',
        'july', 'august', 'september', 'october', 'november', 'december',
        'jan', 'feb', 'mar', 'apr', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec',
    ]

    text_lower = text.lower()

    # Find positions of date indicators (month names only, not day names which cause false positives)
    positions = []
    for indicator in date_indicators:
        start = 0
        while True:
            pos = text_lower.find(indicator, start)
            if pos == -1:
                break
            positions.append(pos)
            start = pos + 1

    # Also find numeric date patterns (MM/DD/YYYY, YYYY-MM-DD)
    numeric_date_pattern = re.compile(r'\b\d{1,4}[-/]\d{1,2}[-/]\d{2,4}\b')
    for match in numeric_date_pattern.finditer(text):
        positions.append(match.start())

    # Sort and deduplicate positions
    positions = sorted(set(positions))

    # Extract date strings around each position
    for pos in positions:
        # Get a window around the position
        start = max(0, pos - 5)
        end = min(len(text), pos + 30)
        chunk = text[start:end]

        try:
            # Use dateutil to parse
            parsed = dateutil_parser.parse(chunk, fuzzy=True, default=datetime(base_year, 1, 1))

            # Validate the year is reasonable for a flight
            if not _is_valid_flight_year(parsed.year):
                continue

            # Format consistently
            formatted = parsed.strftime("%B %d, %Y")

            if formatted not in seen:
                seen.add(formatted)
                dates.append(formatted)

                if len(dates) >= 3:
                    break
        except (ValueError, OverflowError, TypeError):
            continue

    return dates


def _extract_dates_fallback(text, base_year=None):
    """Fallback date extraction using regex (if dateutil unavailable).

    Args:
        text: Text to search for dates
        base_year: Year to use for dates without year

    Returns:
        List of date strings
    """
    if base_year is None:
        base_year = datetime.now().year

    dates = []

    # Pattern: "December 7, 2025" or "Dec 7, 2025"
    pattern1 = re.compile(
        r'\b((?:January|February|March|April|May|June|July|August|September|October|November|December|'
        r'Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},?\s+\d{4})\b',
        re.IGNORECASE
    )
    for m in pattern1.findall(text):
        if m.strip() not in dates:
            dates.append(m.strip())

    # Pattern: numeric dates with 4-digit year
    pattern2 = re.compile(r'\b(\d{1,2}[/-]\d{1,2}[/-]\d{4})\b')
    for m in pattern2.findall(text):
        if m.strip() not in dates:
            dates.append(m.strip())

    # Pattern: ISO format
    pattern3 = re.compile(r'\b(\d{4}-\d{2}-\d{2})\b')
    for m in pattern3.findall(text):
        if m.strip() not in dates:
            dates.append(m.strip())

    # Pattern without year - add base_year
    pattern4 = re.compile(
        r'\b((?:January|February|March|April|May|June|July|August|September|October|November|December|'
        r'Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2})(?!\d|,?\s*\d{4})\b',
        re.IGNORECASE
    )
    for m in pattern4.findall(text):
        date_with_year = f"{m.strip()}, {base_year}"
        if date_with_year not in dates and len(dates) < 3:
            dates.append(date_with_year)

    return dates[:3]


def extract_flight_info(body, email_date=None):
    """Extract flight information from email body.

    Args:
        body: Email body text (can be HTML or plain text)
        email_date: datetime when email was sent (for year inference)

    Returns:
        Dict with keys: airports, flight_numbers, dates, times
    """
    info = {
        "airports": [],
        "flight_numbers": [],
        "dates": [],
        "times": []
    }

    if not body:
        return info

    # Strip HTML to get only visible text
    body = strip_html_tags(body)

    # Determine base year for dates without year
    base_year = email_date.year if email_date and hasattr(email_date, 'year') else datetime.now().year

    # Extract airport codes using pre-compiled patterns
    airports = []

    # Pattern 1: City (CODE) format
    for city, code in _CITY_CODE_PATTERN.findall(body):
        if code in VALID_AIRPORT_CODES and code not in airports:
            airports.append(code)

    # Pattern 2: CODE → CODE routes
    for origin, dest in _ROUTE_PATTERN.findall(body):
        if origin in VALID_AIRPORT_CODES and origin not in airports:
            airports.append(origin)
        if dest in VALID_AIRPORT_CODES and dest not in airports:
            airports.append(dest)

    # Pattern 3: Contextual (depart/arrive/from/to)
    for match in _CONTEXT_PATTERN.findall(body):
        code = match.upper()
        if code in VALID_AIRPORT_CODES and code not in airports:
            airports.append(code)

    info["airports"] = airports[:4]

    # Extract flight numbers
    flight_nums = []
    for pattern in _FLIGHT_NUM_PATTERNS:
        for num in pattern.findall(body):
            if num not in flight_nums:
                flight_nums.append(num)
    info["flight_numbers"] = flight_nums[:4]

    # Extract dates using dateutil
    info["dates"] = _extract_dates_dateutil(body, base_year)

    # Extract times
    times = _TIME_PATTERN.findall(body)
    info["times"] = list(dict.fromkeys(times))[:4]  # Dedupe while preserving order

    return info


def generate_content_hash(subject, body):
    """Generate a hash of the email content for deduplication.

    Args:
        subject: Email subject
        body: Email body

    Returns:
        16-character hex hash string
    """
    normalized = _WHITESPACE_PATTERN.sub(' ', (subject + body).lower().strip())
    return hashlib.md5(normalized.encode()).hexdigest()[:16]


def create_flight_fingerprint(flight_info):
    """Create a fingerprint to identify unique flight itineraries.

    Args:
        flight_info: Dict from extract_flight_info()

    Returns:
        Fingerprint string or None if not enough info
    """
    parts = []

    if flight_info.get("airports"):
        parts.append("-".join(flight_info["airports"]))

    if flight_info.get("dates"):
        parts.append(flight_info["dates"][0].lower())

    if flight_info.get("flight_numbers"):
        parts.append("-".join(sorted(flight_info["flight_numbers"])))

    return "|".join(parts) if parts else None
