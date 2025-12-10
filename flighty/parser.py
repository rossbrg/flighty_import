"""
Flight information extraction and parsing.

Extracts flight data (airports, dates, confirmation codes, etc.) from email content.
"""

import re
import hashlib
from datetime import datetime
from html.parser import HTMLParser
from html import unescape

from .airports import VALID_AIRPORT_CODES, EXCLUDED_CODES


def strip_html_tags(html_text):
    """Remove HTML tags and return only visible text content.

    Uses Python's built-in html.parser to properly extract visible text,
    preventing regex from matching CSS class names, HTML comments,
    and other non-visible content like 'New Copy 11' or 'Banner 04'.

    Args:
        html_text: HTML string to parse

    Returns:
        Plain text with HTML removed
    """
    if not html_text:
        return ""

    class TextExtractor(HTMLParser):
        def __init__(self):
            super().__init__()
            self.text_parts = []
            self.skip_tags = {'script', 'style', 'head', 'meta', 'link'}
            self.current_skip = False

        def handle_starttag(self, tag, attrs):
            if tag.lower() in self.skip_tags:
                self.current_skip = True

        def handle_endtag(self, tag):
            if tag.lower() in self.skip_tags:
                self.current_skip = False

        def handle_data(self, data):
            if not self.current_skip:
                text = data.strip()
                if text:
                    self.text_parts.append(text)

    try:
        parser = TextExtractor()
        parser.feed(html_text)
        text = ' '.join(parser.text_parts)
        # Decode any remaining HTML entities
        text = unescape(text)
        # Collapse multiple whitespace
        text = re.sub(r'\s+', ' ', text)
        return text.strip()
    except Exception:
        # Fallback: simple regex strip if parser fails
        text = re.sub(r'<[^>]+>', ' ', html_text)
        text = re.sub(r'\s+', ' ', text)
        return text.strip()


def extract_confirmation_code(subject, body):
    """Extract confirmation code from email subject or body.

    Args:
        subject: Email subject line
        body: Email body text

    Returns:
        6-character confirmation code or None
    """
    # First try subject line - often has format "... - ABCDEF"
    subject_match = re.search(r'[–-]\s*([A-Z0-9]{6})\s*$', subject)
    if subject_match:
        return subject_match.group(1)

    # Try to find confirmation code in context
    patterns = [
        r'confirmation[:\s]+(?:code[:\s]+)?([A-Z0-9]{6})\b',
        r'booking[:\s]+(?:reference[:\s]+)?([A-Z0-9]{6})\b',
        r'record[:\s]+locator[:\s]+([A-Z0-9]{6})\b',
        r'PNR[:\s]+([A-Z0-9]{6})\b',
        r'code\s+is\s+([A-Z0-9]{6})\b',
        r'reservation[:\s]+([A-Z0-9]{6})\b',
    ]

    for pattern in patterns:
        match = re.search(pattern, body, re.IGNORECASE)
        if match:
            return match.group(1).upper()

    # Try subject with generic pattern
    match = re.search(r'\b([A-Z0-9]{6})\b', subject)
    if match:
        code = match.group(1)
        if code not in EXCLUDED_CODES and not code.isdigit():
            return code

    return None


def extract_flight_info(body, email_date=None):
    """Extract flight information from email body with error handling.

    Args:
        body: Email body text (can be HTML or plain text)
        email_date: datetime when email was sent (used to determine year for dates without year)

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

    # Strip HTML to get only visible text - prevents matching CSS/HTML artifacts
    body = strip_html_tags(body)

    try:
        # Extract airport codes - ONLY accept codes from our whitelist
        # Pattern 1: City (CODE) format - e.g., "Orlando (MCO)" or "Boston (BOS)"
        try:
            city_code_pattern = r'([A-Za-z\s]+)\s*\(([A-Z]{3})\)'
            city_matches = re.findall(city_code_pattern, body)
            for city, code in city_matches:
                if code in VALID_AIRPORT_CODES and code not in info["airports"]:
                    info["airports"].append(code)
        except Exception:
            pass

        # Pattern 2: CODE → CODE or CODE to CODE (arrow/to between codes)
        try:
            route_pattern = r'\b([A-Z]{3})\s*(?:→|->|►|to|–|-)\s*([A-Z]{3})\b'
            route_matches = re.findall(route_pattern, body)
            for origin, dest in route_matches:
                if origin in VALID_AIRPORT_CODES and origin not in info["airports"]:
                    info["airports"].append(origin)
                if dest in VALID_AIRPORT_CODES and dest not in info["airports"]:
                    info["airports"].append(dest)
        except Exception:
            pass

        # Pattern 3: Departs/Arrives CODE or From/To CODE
        try:
            context_pattern = r'(?:depart|arrive|from|to|origin|destination)[:\s]+([A-Z]{3})\b'
            context_matches = re.findall(context_pattern, body, re.IGNORECASE)
            for code in context_matches:
                code = code.upper()
                if code in VALID_AIRPORT_CODES and code not in info["airports"]:
                    info["airports"].append(code)
        except Exception:
            pass

        info["airports"] = info["airports"][:4]  # Limit to 4 airports

        # Extract flight numbers - "Flight 123" or "Flight # 652" or "B6 652"
        try:
            flight_patterns = [
                r'[Ff]light\s*#?\s*(\d{1,4})\b',
                r'\b(?:B6|DL|UA|AA|WN|AS|NK|F9|HA|AC|BA|LH|EK)\s*(\d{1,4})\b',  # Airline codes
            ]
            for pattern in flight_patterns:
                matches = re.findall(pattern, body)
                for m in matches:
                    if m not in info["flight_numbers"]:
                        info["flight_numbers"].append(m)
            info["flight_numbers"] = info["flight_numbers"][:4]
        except Exception:
            pass

        # Extract dates - ONLY accept dates with valid month names
        try:
            # Use email date's year if available, otherwise current year
            if email_date and hasattr(email_date, 'year'):
                base_year = email_date.year
            else:
                base_year = datetime.now().year

            valid_dates = []

            # Pattern 1: "December 7, 2025" or "Dec 7, 2025" (Month Day, Year)
            pattern1 = r'\b((?:January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},?\s+\d{4})\b'
            for m in re.findall(pattern1, body, re.IGNORECASE):
                if m.strip() not in valid_dates:
                    valid_dates.append(m.strip())

            # Pattern 2: "Sun, Dec 07, 2025" or "Sunday, December 7, 2025" (Day, Month Day, Year)
            pattern2 = r'\b((?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|Mon|Tue|Wed|Thu|Fri|Sat|Sun),?\s+(?:January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},?\s+\d{4})\b'
            for m in re.findall(pattern2, body, re.IGNORECASE):
                if m.strip() not in valid_dates:
                    valid_dates.append(m.strip())

            # Pattern 3: "07 Dec 2025" or "7 December 2025" (Day Month Year)
            pattern3 = r'\b(\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec),?\s+\d{4})\b'
            for m in re.findall(pattern3, body, re.IGNORECASE):
                if m.strip() not in valid_dates:
                    valid_dates.append(m.strip())

            # Pattern 4: "12/07/2025" or "12-07-2025" (numeric with 4-digit year)
            pattern4 = r'\b(\d{1,2}[/-]\d{1,2}[/-]\d{4})\b'
            for m in re.findall(pattern4, body):
                if m.strip() not in valid_dates:
                    valid_dates.append(m.strip())

            # Pattern 5: "2025-12-07" (ISO format)
            pattern5 = r'\b(\d{4}-\d{2}-\d{2})\b'
            for m in re.findall(pattern5, body):
                if m.strip() not in valid_dates:
                    valid_dates.append(m.strip())

            # Patterns WITHOUT year - will add base_year
            # Pattern 6: "December 7" or "Dec 07" (Month Day, no year)
            pattern6 = r'\b((?:January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2})(?!\d|,?\s*\d{4})\b'
            for m in re.findall(pattern6, body, re.IGNORECASE):
                date_with_year = f"{m.strip()}, {base_year}"
                if date_with_year not in valid_dates and len(valid_dates) < 4:
                    valid_dates.append(date_with_year)

            # Pattern 7: "Sun, Dec 07" (Day, Month Day, no year)
            pattern7 = r'\b((?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|Mon|Tue|Wed|Thu|Fri|Sat|Sun),?\s+(?:January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2})(?!\d|,?\s*\d{4})\b'
            for m in re.findall(pattern7, body, re.IGNORECASE):
                date_with_year = f"{m.strip()}, {base_year}"
                if date_with_year not in valid_dates and len(valid_dates) < 4:
                    valid_dates.append(date_with_year)

            info["dates"] = valid_dates[:3]
        except Exception:
            pass

        # Extract times with AM/PM - "6:00 PM" or "18:00" or "6:00pm"
        try:
            time_patterns = [
                r'\b(\d{1,2}:\d{2}\s*[AaPp][Mm])\b',  # 6:00 PM or 6:00pm
                r'\b(\d{1,2}:\d{2})\s*(?=[A-Za-z]|$|\s)',  # 18:00 followed by text/end
            ]
            for pattern in time_patterns:
                matches = re.findall(pattern, body)
                for m in matches:
                    if m not in info["times"]:
                        info["times"].append(m)
            info["times"] = info["times"][:4]
        except Exception:
            pass

    except Exception:
        # If anything goes wrong, return what we have
        pass

    return info


def generate_content_hash(subject, body):
    """Generate a hash of the email content for deduplication.

    Args:
        subject: Email subject
        body: Email body

    Returns:
        16-character hex hash string
    """
    normalized = re.sub(r'\s+', ' ', (subject + body).lower().strip())
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
