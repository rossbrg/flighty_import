"""
Flight Email Parser - Simplified for JetBlue

Extracts flight segments from JetBlue confirmation emails.
Each segment: (origin, destination, flight_number, date)
"""

import re
from datetime import datetime
from html import unescape
from typing import Optional, List, Dict

from .airports import is_valid_airport

# Marketing keywords - emails with these are promotional
MARKETING_KEYWORDS = [
    'unsubscribe', 'opt out', 'manage preferences',
    'trueblue points', 'earn points', 'bonus points',
    'limited time', 'book now', 'sale ends',
    'exclusive offer', 'special offer',
    'credit card', 'apply now',
]

# Words that look like confirmation codes but aren't
EXCLUDED_CODES = {
    'FLIGHT', 'TRAVEL', 'TICKET', 'BOOKING', 'CONFIRM', 'NUMBER',
    'DETAIL', 'STATUS', 'CHANGE', 'UPDATE', 'CANCEL', 'AMOUNT',
    'CREDIT', 'MANAGE', 'REVIEW', 'MEMBER', 'RETURN', 'DEPART',
    'ARRIVE', 'CENTER', 'MOBILE', 'ONLINE', 'SUBMIT', 'BUTTON',
    'SELECT', 'POLICY', 'POINTS', 'ACCOUNT', 'WINDOW', 'MIDDLE',
    'FLYING', 'OFFERS', 'EXTRAS', 'HOTELS', 'SOCIAL', 'FOLLOW',
    'DATES', 'TIMES', 'PRICES', 'ROUTES', 'FARES', 'CHANNEL',
    'EMAILS',  # False positive from "receive their emails"
}

# Common hex colors that look like PNR codes
HEX_COLOR_PNRS = {
    '000000', 'FFFFFF', 'EEEEEE', 'CCCCCC', 'AAAAAA', 'DDDDDD', 'BBBBBB',
    '111111', '222222', '333333', '444444', '555555', '666666', '777777',
    '888888', '999999', 'F0F0F0', 'E0E0E0', 'D0D0D0', 'C0C0C0', 'B0B0B0',
}


def is_valid_pnr(code: str) -> bool:
    """Check if a 6-character code is a valid PNR (not a false positive).

    Filters out:
    - Known excluded words (FLIGHT, TRAVEL, etc.)
    - Hex colors (000000, FFFFFF, etc.)
    - Repeated characters (AAAAAA)
    - Pure hex codes that look like colors
    """
    if not code or len(code) != 6:
        return False

    code = code.upper()

    # Check excluded words
    if code in EXCLUDED_CODES:
        return False

    # Check known hex colors
    if code in HEX_COLOR_PNRS:
        return False

    # Check repeated characters (AAAAAA, BBBBBB, etc.)
    if len(set(code)) == 1:
        return False

    # Check if it's a valid hex color pattern (all hex chars)
    if re.match(r'^[0-9A-F]{6}$', code):
        # If it's pure hex AND has no letters OR no digits, likely a color
        has_letters = any(c.isalpha() for c in code)
        has_digits = any(c.isdigit() for c in code)
        if not (has_letters and has_digits):
            return False

    return True


MONTH_MAP = {
    'jan': 1, 'january': 1,
    'feb': 2, 'february': 2,
    'mar': 3, 'march': 3,
    'apr': 4, 'april': 4,
    'may': 5,
    'jun': 6, 'june': 6,
    'jul': 7, 'july': 7,
    'aug': 8, 'august': 8,
    'sep': 9, 'sept': 9, 'september': 9,
    'oct': 10, 'october': 10,
    'nov': 11, 'november': 11,
    'dec': 12, 'december': 12,
}

# City name to airport code mapping for Delta emails
CITY_TO_AIRPORT = {
    'atlanta': 'ATL', 'detroit': 'DTW', 'minneapolis': 'MSP',
    'salt lake city': 'SLC', 'seattle': 'SEA', 'los angeles': 'LAX',
    'new york': 'JFK', 'boston': 'BOS', 'chicago': 'ORD',
    'dallas': 'DFW', 'denver': 'DEN', 'san francisco': 'SFO',
    'miami': 'MIA', 'phoenix': 'PHX', 'houston': 'IAH',
    'orlando': 'MCO', 'las vegas': 'LAS', 'philadelphia': 'PHL',
    'charlotte': 'CLT', 'washington': 'DCA', 'tampa': 'TPA',
    'fort lauderdale': 'FLL', 'san diego': 'SAN', 'austin': 'AUS',
    'nashville': 'BNA', 'raleigh': 'RDU', 'portland': 'PDX',
    'honolulu': 'HNL', 'anchorage': 'ANC', 'newark': 'EWR',
    'laguardia': 'LGA', 'reagan': 'DCA', 'dulles': 'IAD',
}


def strip_html(html: str) -> str:
    """Convert HTML to plain text."""
    if not html:
        return ""
    # Remove style and script blocks
    text = re.sub(r'<style[^>]*>.*?</style>', ' ', html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<script[^>]*>.*?</script>', ' ', text, flags=re.DOTALL | re.IGNORECASE)
    # Remove HTML tags
    text = re.sub(r'<[^>]+>', ' ', text)
    # Decode HTML entities
    text = unescape(text)
    # Handle non-breaking spaces
    text = text.replace('\xa0', ' ')
    # Normalize whitespace
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def is_marketing_email(text: str, subject: str) -> bool:
    """Check if email is marketing/promotional."""
    combined = (text + " " + subject).lower()

    # Check for marketing keywords
    marketing_count = sum(1 for kw in MARKETING_KEYWORDS if kw in combined)
    if marketing_count >= 2:
        return True

    # Marketing subjects
    marketing_subjects = [
        'earn', 'bonus', 'points', 'sale', 'offer', 'save',
        'win', 'deals', 'discount', 'reward',
    ]
    subject_lower = subject.lower()
    if any(kw in subject_lower for kw in marketing_subjects):
        if 'confirmation' not in subject_lower and 'itinerary' not in subject_lower:
            return True

    return False


def get_email_type(text: str, subject: str, has_confirmation: bool = False) -> str:
    """Classify email type."""
    subject_lower = subject.lower()
    text_lower = text.lower()

    # Check for cancellation
    if 'cancel' in subject_lower or 'cancelled' in subject_lower:
        return 'cancellation'
    if 'has been cancelled' in text_lower:
        return 'cancellation'

    # Check for marketing
    if is_marketing_email(text, subject):
        return 'marketing'

    # Check for booking confirmation
    if 'confirmation' in subject_lower or 'itinerary' in subject_lower:
        return 'booking'
    if has_confirmation:
        return 'booking'

    return 'unknown'


def extract_confirmation_code(text: str, subject: str) -> Optional[str]:
    """Extract confirmation code from email."""

    # Pattern 1: JetBlue subject "NAME - XXXXXX"
    match = re.search(r'\s+-\s+([A-Z0-9]{6})\s*$', subject)
    if match:
        code = match.group(1).upper()
        if is_valid_pnr(code):
            return code

    # Pattern 2: "confirmation code is XXXXXX"
    match = re.search(r'confirmation\s+code\s+is\s+([A-Z0-9]{6})\b', text, re.IGNORECASE)
    if match:
        code = match.group(1).upper()
        if is_valid_pnr(code):
            return code

    # Pattern 3: "Confirmation: XXXXXX" or "Confirmation #XXXXXX"
    match = re.search(r'confirmation[:\s#]+([A-Z0-9]{6})\b', text, re.IGNORECASE)
    if match:
        code = match.group(1).upper()
        if is_valid_pnr(code):
            return code

    # Pattern 4: "Confirmation Number XXXXXX" (Delta format)
    match = re.search(r'confirmation\s+number\s+([A-Z0-9]{6})\b', text, re.IGNORECASE)
    if match:
        code = match.group(1).upper()
        if is_valid_pnr(code):
            return code

    # Pattern 5: "Record Locator: XXXXXX" (receipt format)
    match = re.search(r'record\s+locator[:\s]+([A-Z0-9]{6})\b', text, re.IGNORECASE)
    if match:
        code = match.group(1).upper()
        if is_valid_pnr(code):
            return code

    return None


def parse_date_with_year(month_str: str, day: int, email_year: int) -> str:
    """Convert month name and day to ISO date, inferring year from email."""
    month = MONTH_MAP.get(month_str.lower())
    if not month:
        return None

    # Use email year
    year = email_year

    return f"{year}-{month:02d}-{day:02d}"


def extract_flight_segments(text: str, email_year: int) -> List[Dict]:
    """Extract flight segments from JetBlue confirmation email.

    Pattern 1: ORIGIN DEST Flight NUMBER DAY, MONTH DATE TIME
    Example: BOS SAV Flight 349 Wed, Nov 12 3:50pm

    Pattern 1b: ORIGIN DEST [duration] Flight NUMBER DAY, MONTH DATE
    Example: BOS MCO 10hr 30min Flight 451 Tue, Jun 11 3:40pm

    Pattern 2: Cape Air codeshare - ORIGIN DEST Flight N ... Sold as B6 NUMBER ... DAY, MONTH DATE
    Example: MVY BOS Flight 1 9K 3261 1 Sold as B6 5924 ... Thu, Jul 17 6:10pm

    Pattern 4: Old JetBlue format (2015-2017) with city names
    Example: Wed, Oct 14 03:15 PM 06:09 PM PROVIDENCE, RI (PVD) to ORLANDO, FL (MCO) 1075
    """
    segments = []
    seen_keys = set()  # Track (origin, dest, date) to avoid duplicates

    # Pattern 4: Old JetBlue format (2015-2017) - must run first as it's very specific
    # Format: Day, Month DD HH:MM AM/PM HH:MM AM/PM CITY, ST (ORG) to CITY, ST (DST) FLIGHTNUM
    pattern4 = r'(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*,?\s*(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\s+(\d{1,2})\s+\d{1,2}:\d{2}\s*[AP]M\s+\d{1,2}:\d{2}\s*[AP]M\s+[A-Z][A-Za-z\s]+,\s*[A-Z]{2}\s+\(([A-Z]{3})\)\s+to\s+[A-Z][A-Za-z\s]+,\s*[A-Z]{2}\s+\(([A-Z]{3})\)\s+(\d+)'

    for match in re.finditer(pattern4, text, re.IGNORECASE):
        month_str = match.group(1)
        day = int(match.group(2))
        origin = match.group(3).upper()
        dest = match.group(4).upper()
        flight_num = match.group(5)

        if not is_valid_airport(origin) or not is_valid_airport(dest):
            continue
        if origin == dest:
            continue

        date = parse_date_with_year(month_str, day, email_year)
        if not date:
            continue

        key = (origin, dest, date)
        if key not in seen_keys:
            seen_keys.add(key)
            segments.append({
                "origin": origin,
                "destination": dest,
                "flight_number": f"B6{flight_num}",
                "date": date,
            })

    # Pattern 1: Standard JetBlue flight format (airports directly before Flight)
    pattern1 = r'\b([A-Z]{3})\s+([A-Z]{3})\s+Flight\s+(\d+)\s+(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*,?\s*(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\s+(\d{1,2})'

    # Pattern 1b: JetBlue format with duration between airports and Flight
    # Example: BOS MCO 10hr 30min Flight 451 Tue, Jun 11 3:40pm
    pattern1b = r'\b([A-Z]{3})\s+([A-Z]{3})\s+\d+hr\s*\d*min\s+Flight\s+(\d+)\s+(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*,?\s*(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\s+(\d{1,2})'

    for match in re.finditer(pattern1b, text, re.IGNORECASE):
        origin = match.group(1).upper()
        dest = match.group(2).upper()
        flight_num = match.group(3)
        month_str = match.group(4)
        day = int(match.group(5))

        if not is_valid_airport(origin) or not is_valid_airport(dest):
            continue
        if origin == dest:
            continue

        date = parse_date_with_year(month_str, day, email_year)
        if not date:
            continue

        key = (origin, dest, date)
        if key not in seen_keys:
            seen_keys.add(key)
            segments.append({
                "origin": origin,
                "destination": dest,
                "flight_number": f"B6{flight_num}",
                "date": date,
            })

    for match in re.finditer(pattern1, text, re.IGNORECASE):
        origin = match.group(1).upper()
        dest = match.group(2).upper()
        flight_num = match.group(3)
        month_str = match.group(4)
        day = int(match.group(5))

        # Validate airports
        if not is_valid_airport(origin) or not is_valid_airport(dest):
            continue
        if origin == dest:
            continue

        # Parse date
        date = parse_date_with_year(month_str, day, email_year)
        if not date:
            continue

        key = (origin, dest, date)
        if key not in seen_keys:
            seen_keys.add(key)
            segments.append({
                "origin": origin,
                "destination": dest,
                "flight_number": f"B6{flight_num}",
                "date": date,
            })

    # Pattern 2: Cape Air/partner codeshare - "Sold as B6 XXXX"
    # Format: ORIGIN DEST Flight N ... Sold as B6 NUMBER ... Day, Month Date
    pattern2 = r'\b([A-Z]{3})\s+([A-Z]{3})\s+Flight\s+\d+.*?Sold\s+as\s+B6\s+(\d+).*?(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*,?\s*(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\s+(\d{1,2})'

    for match in re.finditer(pattern2, text, re.IGNORECASE | re.DOTALL):
        origin = match.group(1).upper()
        dest = match.group(2).upper()
        flight_num = match.group(3)
        month_str = match.group(4)
        day = int(match.group(5))

        # Validate airports
        if not is_valid_airport(origin) or not is_valid_airport(dest):
            continue
        if origin == dest:
            continue

        # Parse date
        date = parse_date_with_year(month_str, day, email_year)
        if not date:
            continue

        key = (origin, dest, date)
        if key not in seen_keys:
            seen_keys.add(key)
            segments.append({
                "origin": origin,
                "destination": dest,
                "flight_number": f"B6{flight_num}",
                "date": date,
            })

    # Pattern 1c: JetBlue format with "Flights" header (first segment)
    # Example: Flights BOS LAX Boston, MA ... Date Tue, Feb 11 Departs 6:50am ... Flight 287
    pattern1c = r'Flights\s+([A-Z]{3})\s+([A-Z]{3}).*?Date\s+(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*,?\s*(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\s+(\d{1,2}).*?Flight\s+(\d+)'

    for match in re.finditer(pattern1c, text, re.IGNORECASE | re.DOTALL):
        origin = match.group(1).upper()
        dest = match.group(2).upper()
        month_str = match.group(3)
        day = int(match.group(4))
        flight_num = match.group(5)

        if not is_valid_airport(origin) or not is_valid_airport(dest):
            continue
        if origin == dest:
            continue

        date = parse_date_with_year(month_str, day, email_year)
        if not date:
            continue

        key = (origin, dest, date)
        if key not in seen_keys:
            seen_keys.add(key)
            segments.append({
                "origin": origin,
                "destination": dest,
                "flight_number": f"B6{flight_num}",
                "date": date,
            })

    # Pattern 1d: JetBlue continuation segment (after first segment, no "Flights" prefix)
    # Example: MCI BOS Kansas City ... Date Mon, Sep 04 ... Flight 2364
    # Match: ORIGIN DEST City ... Date Day, Month DD ... Flight NUM
    pattern1d = r'\b([A-Z]{3})\s+([A-Z]{3})\s+[A-Z][a-z]+[^F]*?Date\s+(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*,?\s*(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\s+(\d{1,2})\s+Departs.*?Flight\s+(\d+)'

    for match in re.finditer(pattern1d, text, re.IGNORECASE | re.DOTALL):
        origin = match.group(1).upper()
        dest = match.group(2).upper()
        month_str = match.group(3)
        day = int(match.group(4))
        flight_num = match.group(5)

        if not is_valid_airport(origin) or not is_valid_airport(dest):
            continue
        if origin == dest:
            continue

        date = parse_date_with_year(month_str, day, email_year)
        if not date:
            continue

        key = (origin, dest, date)
        if key not in seen_keys:
            seen_keys.add(key)
            segments.append({
                "origin": origin,
                "destination": dest,
                "flight_number": f"B6{flight_num}",
                "date": date,
            })

    # Pattern 5: Expedia format - "Departure Day, Month DD ... Airline FlightNum ... City (ORG) ... City (DST)"
    # Example: "Departure Thu, Jul 5 United 2155 Houston (IAH) 6:05pm Terminal: C Chicago (ORD) 8:47pm"
    # Airline code mapping for non-JetBlue carriers
    AIRLINE_CODES = {
        'united': 'UA', 'delta': 'DL', 'american': 'AA', 'southwest': 'WN',
        'jetblue': 'B6', 'alaska': 'AS', 'spirit': 'NK', 'frontier': 'F9',
    }

    expedia_pattern = r'Departure\s+(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*,?\s*(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\s+(\d{1,2})\s+(\w+)\s+(\d+)\s+[A-Za-z\s]+\(([A-Z]{3})\).*?[A-Za-z\s]+\(([A-Z]{3})\)'

    for match in re.finditer(expedia_pattern, text, re.IGNORECASE | re.DOTALL):
        month_str = match.group(1)
        day = int(match.group(2))
        airline_name = match.group(3).lower()
        flight_num = match.group(4)
        origin = match.group(5).upper()
        dest = match.group(6).upper()

        if not is_valid_airport(origin) or not is_valid_airport(dest):
            continue
        if origin == dest:
            continue

        date = parse_date_with_year(month_str, day, email_year)
        if not date:
            continue

        # Get airline code
        airline_code = AIRLINE_CODES.get(airline_name, airline_name.upper()[:2])

        key = (origin, dest, date)
        if key not in seen_keys:
            seen_keys.add(key)
            segments.append({
                "origin": origin,
                "destination": dest,
                "flight_number": f"{airline_code}{flight_num}",
                "date": date,
            })

    # Pattern 3: Delta format - "Day, DDMON ... DELTA XXXX ... CITY TIME CITY TIME"
    # Example: "Tue, 17APR...DELTA 2971...DETROIT 8:11pm BOSTON, MA 10:09pm"
    # Simplified pattern that works with various Delta email formats
    delta_pattern = r'(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*,?\s*(\d{1,2})(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC).*?DELTA\s+(\d+).*?([A-Z][A-Z]+)\s+\d{1,2}:\d{2}[ap]m\s+([A-Z][A-Z]+)'

    for match in re.finditer(delta_pattern, text, re.IGNORECASE | re.DOTALL):
        day = int(match.group(1))
        month_str = match.group(2)
        flight_num = match.group(3)
        origin_city = match.group(4).strip().lower()
        dest_city = match.group(5).strip().lower()

        # Map cities to airport codes
        origin = CITY_TO_AIRPORT.get(origin_city)
        dest = CITY_TO_AIRPORT.get(dest_city)

        if not origin or not dest:
            continue
        if origin == dest:
            continue

        # Parse date
        date = parse_date_with_year(month_str, day, email_year)
        if not date:
            continue

        key = (origin, dest, date)
        if key not in seen_keys:
            seen_keys.add(key)
            segments.append({
                "origin": origin,
                "destination": dest,
                "flight_number": f"DL{flight_num}",
                "date": date,
            })

    return segments


def format_date_display(iso_date: str) -> str:
    """Convert ISO date to display format like 'December 15, 2025'."""
    if not iso_date:
        return ""
    try:
        dt = datetime.strptime(iso_date, "%Y-%m-%d")
        return dt.strftime("%B %d, %Y").replace(" 0", " ")
    except (ValueError, TypeError):
        return iso_date


def extract_flight_info(html_content: str, text_content: str = "",
                        subject: str = "", from_addr: str = "",
                        email_date: datetime = None) -> dict:
    """Extract flight information from email.

    Returns:
        Dict with confirmation, segments, email_type, and legacy fields.
    """
    # Convert HTML to text
    text = strip_html(html_content) if html_content else ""
    if text_content:
        text = text + " " + strip_html(text_content)

    # Get year from email date (validate it's a reasonable year)
    email_year = email_date.year if email_date and email_date.year > 2000 else datetime.now().year

    # Extract confirmation code
    confirmation = extract_confirmation_code(text, subject)

    # Determine email type
    email_type = get_email_type(text, subject, has_confirmation=bool(confirmation))

    # Extract flight segments
    segments = extract_flight_segments(text, email_year)

    # Build legacy format fields
    airports = []
    flight_numbers = []
    dates = []

    for seg in segments:
        if seg["origin"] not in airports:
            airports.append(seg["origin"])
        if seg["destination"] not in airports:
            airports.append(seg["destination"])
        if seg["flight_number"] and seg["flight_number"] not in flight_numbers:
            flight_numbers.append(seg["flight_number"])
        if seg["date"]:
            formatted = format_date_display(seg["date"])
            if formatted not in dates:
                dates.append(formatted)

    # Get route from first segment
    route = None
    if segments:
        route = (segments[0]["origin"], segments[0]["destination"])

    return {
        "confirmation": confirmation,
        "segments": segments,
        "email_type": email_type,
        # Legacy format
        "airports": airports,
        "flight_numbers": flight_numbers,
        "dates": dates,
        "route": route,
    }


