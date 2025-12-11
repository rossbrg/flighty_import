"""
Flight information extraction and parsing.

Strategy (in order of reliability):
1. Schema.org JSON-LD structured data (most reliable - airlines embed this)
2. Schema.org microdata (older format)
3. Native Python parsing with dateutil
4. Regex fallback for specific patterns

All airport codes are validated against our 9,800+ IATA database.
"""

import re
import hashlib
import json
from datetime import datetime
from html.parser import HTMLParser
from html import unescape

from .airports import VALID_AIRPORT_CODES, EXCLUDED_CODES, AIRPORT_NAMES, ALL_AIRPORT_CODES, city_to_airport_code, CITY_TO_AIRPORT, FRIENDLY_NAMES

# Try to import dateutil
try:
    from .deps import get_dateutil_parser
except ImportError:
    def get_dateutil_parser():
        try:
            from dateutil import parser
            return parser
        except ImportError:
            return None


# ============================================================================
# MARKETING / PROMOTIONAL EMAIL DETECTION
# ============================================================================

# Keywords that strongly indicate a marketing/promotional email
_MARKETING_KEYWORDS = {
    # Direct promotional language
    'book now', 'book today', 'book your', 'reserve now', 'reserve today',
    'limited time', 'limited offer', 'special offer', 'exclusive offer',
    'deal alert', 'flash sale', 'sale ends', 'ends soon', 'act now',
    'don\'t miss', 'dont miss', 'hurry', 'last chance', 'final call',
    # Pricing promotions
    'starting at $', 'starting from $', 'fares from $', 'flights from $',
    'as low as', 'from only $', 'just $', 'only $', 'save up to',
    'save $', '% off', 'percent off', 'discount', 'promo code',
    'coupon', 'voucher', 'credit offer',
    # Exploration/discovery language (not booked)
    'explore', 'discover', 'dream destination', 'getaway', 'escape to',
    'vacation deal', 'holiday deal', 'travel deal', 'trip idea',
    'where to go', 'top destination', 'popular destination',
    'trending destination', 'bucket list', 'wanderlust',
    # Call to action for booking
    'plan your trip', 'plan your next', 'plan your getaway',
    'start planning', 'book a trip', 'book a flight', 'find flights',
    'search flights', 'search fares', 'compare fares', 'see deals',
    'view deals', 'check availability', 'browse destinations',
    # Newsletter/subscription language
    'unsubscribe', 'email preferences', 'manage preferences',
    'weekly deals', 'daily deals', 'member exclusive', 'subscriber',
    # Reward program promotions
    'earn miles', 'earn points', 'bonus miles', 'bonus points',
    'double miles', 'double points', 'triple miles', 'triple points',
    'redeem miles', 'redeem points', 'miles offer', 'points offer',
}

# Keywords that indicate an actual flight booking/confirmation
_BOOKING_CONFIRMATION_KEYWORDS = {
    # Confirmation language
    'confirmed', 'confirmation', 'your confirmation', 'booking confirmed',
    'reservation confirmed', 'itinerary confirmed', 'trip confirmed',
    'e-ticket', 'eticket', 'electronic ticket', 'ticket number',
    'ticket confirmation', 'booking reference', 'record locator',
    'confirmation code', 'confirmation number', 'pnr',
    # Receipt/purchase language
    'receipt', 'purchase confirmation', 'payment received',
    'payment confirmed', 'order confirmed', 'booking complete',
    'transaction complete', 'successfully booked', 'successfully purchased',
    # Specific itinerary language
    'your flight', 'your trip', 'your itinerary', 'your reservation',
    'your booking', 'your upcoming', 'travel itinerary',
    'trip details', 'flight details', 'itinerary details',
    # Check-in and boarding
    'check-in', 'checkin', 'check in now', 'boarding pass',
    'seat assignment', 'seat selection confirmed',
    # Cancellation/change (still indicates a real booking)
    'flight cancelled', 'flight canceled', 'schedule change',
    'itinerary change', 'booking cancelled', 'booking canceled',
    'flight delayed', 'gate change',
}

# Subject line patterns that strongly indicate marketing
_MARKETING_SUBJECT_PATTERNS = [
    re.compile(r'(?:from|starting at|as low as|only)\s*\$\d+', re.IGNORECASE),
    re.compile(r'\d+%\s*off', re.IGNORECASE),
    re.compile(r'(?:sale|deal|offer)\s*(?:ends?|expires?)', re.IGNORECASE),
    re.compile(r'(?:book|reserve|plan)\s+(?:now|today|your)', re.IGNORECASE),
    re.compile(r'(?:discover|explore|escape)\s+', re.IGNORECASE),
    re.compile(r'dream\s+(?:destination|vacation|getaway)', re.IGNORECASE),
    re.compile(r'weekly\s+(?:deals?|offers?|newsletter)', re.IGNORECASE),
    re.compile(r'(?:earn|bonus|double|triple)\s+(?:miles|points)', re.IGNORECASE),
]

# Subject line patterns that indicate actual bookings
_BOOKING_SUBJECT_PATTERNS = [
    re.compile(r'(?:confirmation|confirmed|receipt)', re.IGNORECASE),
    re.compile(r'(?:e-?ticket|itinerary)', re.IGNORECASE),
    re.compile(r'(?:your|trip)\s+(?:booking|reservation|flight)', re.IGNORECASE),
    re.compile(r'check.?in\s+(?:now|available|open|reminder)', re.IGNORECASE),
    re.compile(r'boarding\s+pass', re.IGNORECASE),
    re.compile(r'(?:flight|schedule|gate)\s+(?:change|update|cancelled|canceled|delayed)', re.IGNORECASE),
]


def is_marketing_email(subject, body):
    """Detect if an email is marketing/promotional rather than a flight confirmation.

    Returns:
        Tuple of (is_marketing, confidence, reason)
        - is_marketing: True if this appears to be a marketing email
        - confidence: 'high', 'medium', or 'low'
        - reason: Explanation of why it was flagged
    """
    subject_lower = (subject or '').lower()
    body_lower = (body or '').lower()
    text_lower = subject_lower + ' ' + body_lower

    marketing_score = 0
    booking_score = 0
    reasons = []

    # Check subject line patterns
    for pattern in _MARKETING_SUBJECT_PATTERNS:
        if pattern.search(subject_lower):
            marketing_score += 3
            reasons.append(f"Subject matches marketing pattern")
            break

    for pattern in _BOOKING_SUBJECT_PATTERNS:
        if pattern.search(subject_lower):
            booking_score += 3
            reasons.append(f"Subject matches booking pattern")
            break

    # Count marketing keywords in text
    marketing_hits = []
    for keyword in _MARKETING_KEYWORDS:
        if keyword in text_lower:
            marketing_score += 2
            marketing_hits.append(keyword)
            if len(marketing_hits) >= 5:
                break  # Enough evidence

    if marketing_hits:
        reasons.append(f"Marketing keywords: {', '.join(marketing_hits[:3])}")

    # Count booking confirmation keywords
    booking_hits = []
    for keyword in _BOOKING_CONFIRMATION_KEYWORDS:
        if keyword in text_lower:
            booking_score += 2
            booking_hits.append(keyword)
            if len(booking_hits) >= 5:
                break  # Enough evidence

    if booking_hits:
        reasons.append(f"Booking keywords: {', '.join(booking_hits[:3])}")

    # Check for multiple destinations (marketing emails often list many)
    # Real bookings typically have 1-2 destinations
    destination_mentions = len(re.findall(
        r'(?:fly\s+to|trips?\s+to|visit|explore|discover)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)',
        body or '', re.IGNORECASE
    ))
    if destination_mentions >= 4:
        marketing_score += 3
        reasons.append(f"Multiple destinations promoted ({destination_mentions})")

    # Check for price listings (marketing emails list many prices)
    price_count = len(re.findall(r'\$\d+', body or ''))
    if price_count >= 5:
        marketing_score += 2
        reasons.append(f"Multiple prices listed ({price_count})")

    # Strong booking indicators
    # Has a confirmation code pattern in expected location
    if re.search(r'(?:confirmation|booking|reference|pnr|locator)[:\s#]+[A-Z0-9]{5,8}', text_lower, re.IGNORECASE):
        booking_score += 5
        reasons.append("Has confirmation code format")

    # Has specific flight date (not date range or "travel by")
    if re.search(r'(?:departs?|departing|departure)[:\s]+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)', body or '', re.IGNORECASE):
        booking_score += 2
        reasons.append("Has specific departure date")

    # Has seat assignment
    if re.search(r'seat[:\s]+\d+[A-F]', body or '', re.IGNORECASE):
        booking_score += 3
        reasons.append("Has seat assignment")

    # Calculate final determination
    net_score = marketing_score - booking_score

    if net_score >= 6:
        return True, 'high', '; '.join(reasons)
    elif net_score >= 3:
        return True, 'medium', '; '.join(reasons)
    elif net_score >= 1 and booking_score == 0:
        return True, 'low', '; '.join(reasons)
    else:
        return False, None, '; '.join(reasons) if reasons else 'No marketing indicators'


def get_email_type(subject, body, has_confirmation_code=False):
    """Classify an email as booking, marketing, or unknown.

    Args:
        subject: Email subject line
        body: Email body text
        has_confirmation_code: Whether a confirmation code was found

    Returns:
        String: 'booking', 'marketing', or 'unknown'
    """
    is_marketing, confidence, reason = is_marketing_email(subject, body)

    # If we found a confirmation code, it's very likely a real booking
    if has_confirmation_code:
        if is_marketing and confidence == 'high':
            # Rare case: marketing email that happens to contain a code-like string
            # Still treat as unknown to be safe
            return 'unknown'
        return 'booking'

    if is_marketing:
        if confidence in ('high', 'medium'):
            return 'marketing'
        # Low confidence - could go either way
        return 'unknown'

    return 'unknown'


# ============================================================================
# CONFIRMATION CODE EXTRACTION
# ============================================================================

# Patterns for finding confirmation codes in context (most reliable)
_CONFIRMATION_CONTEXT_PATTERNS = [
    # "Confirmation Code: ABC123" or "Confirmation: ABC123"
    re.compile(r'confirmation\s*(?:code|number|#)?[:\s]+([A-Z0-9]{5,8})\b', re.IGNORECASE),
    # "Booking Reference: ABC123"
    re.compile(r'booking\s*(?:reference|code|number|#)?[:\s]+([A-Z0-9]{5,8})\b', re.IGNORECASE),
    # "Record Locator: ABC123"
    re.compile(r'record\s*locator[:\s]+([A-Z0-9]{5,8})\b', re.IGNORECASE),
    # "PNR: ABC123"
    re.compile(r'\bPNR[:\s]+([A-Z0-9]{5,8})\b', re.IGNORECASE),
    # "Reservation Code: ABC123"
    re.compile(r'reservation\s*(?:code|number|#)?[:\s]+([A-Z0-9]{5,8})\b', re.IGNORECASE),
    # "Itinerary #ABC123" or "Itinerary: ABC123"
    re.compile(r'itinerary\s*[#:\s]+([A-Z0-9]{5,8})\b', re.IGNORECASE),
    # "Trip ID: ABC123"
    re.compile(r'trip\s*(?:id|#)[:\s]+([A-Z0-9]{5,8})\b', re.IGNORECASE),
    # "Your code is ABC123"
    re.compile(r'(?:your\s+)?code\s+is[:\s]+([A-Z0-9]{5,8})\b', re.IGNORECASE),
    # "Ref #: ABC123" or "Ref: ABC123"
    re.compile(r'\bref(?:erence)?[:\s#]+([A-Z0-9]{5,8})\b', re.IGNORECASE),
    # "Locator: ABC123"
    re.compile(r'\blocator[:\s]+([A-Z0-9]{5,8})\b', re.IGNORECASE),
]

# Subject line patterns
_SUBJECT_CONF_PATTERN = re.compile(r'[–\-]\s*([A-Z0-9]{6})\s*$')
_SUBJECT_CONF_PATTERN2 = re.compile(r'#\s*([A-Z0-9]{6})\b')

# Common English words that are NOT confirmation codes
_EXCLUDED_CONFIRMATION_CODES = {
    # Common 6-letter words
    'WINDOW', 'BEFORE', 'CHANGE', 'FLIGHT', 'NUMBER', 'PLEASE', 'THANKS',
    'TRAVEL', 'RETURN', 'ARRIVE', 'DEPART', 'BOSTON', 'DALLAS', 'DENVER',
    'OTHERS', 'REVIEW', 'UPDATE', 'MANAGE', 'CANCEL', 'MODIFY', 'CREDIT',
    'POINTS', 'MEMBER', 'STATUS', 'SELECT', 'CHOOSE', 'OPTION', 'DOUBLE',
    'SINGLE', 'UNITED', 'VIRGIN', 'SPIRIT', 'AMOUNT', 'DOLLAR', 'CHARGE',
    'REFUND', 'POLICY', 'NOTICE', 'DETAIL', 'TICKET',
    'EITHER', 'STREET', 'WITHIN', 'DURING', 'ACROSS', 'AROUND', 'BEHIND',
    'BEYOND', 'TOWARD', 'SHOULD', 'THOUGH', 'REALLY', 'ALWAYS', 'ALMOST',
    'PEOPLE', 'THINGS', 'HAPPEN', 'COMING', 'MAKING', 'TAKING', 'HAVING',
    'FAMILY', 'FRIEND', 'SUMMER', 'WINTER', 'SPRING', 'MONDAY', 'FRIDAY',
    'IMAGES', 'CHROME', 'SAFARI', 'MOBILE', 'ONLINE', 'ACCESS', 'SECURE',
    'SUBMIT', 'BUTTON', 'FOOTER', 'HEADER', 'DOMAIN', 'SERVER',
    # Common 5-letter words
    'DELTA', 'AFTER', 'WHICH', 'THEIR', 'ABOUT', 'WOULD', 'THERE', 'COULD',
    'OTHER', 'THESE', 'FIRST', 'BEING', 'WHERE', 'SINCE', 'UNDER', 'PRICE',
    # Common 7-letter words
    'AIRPORT', 'BOOKING', 'ACCOUNT', 'SERVICE', 'CONTACT', 'RECEIPT',
    # Common 8-letter words
    'AMERICAN', 'AIRLINES', 'TERMINAL', 'CUSTOMER', 'BOARDING',
}


def _is_valid_confirmation_code(code):
    """Check if a code looks like a valid confirmation code."""
    if not code or len(code) < 5 or len(code) > 8:
        return False

    code = code.upper()

    # Check against excluded words
    if code in _EXCLUDED_CONFIRMATION_CODES:
        return False

    # All digits is rarely a confirmation code (more likely a date, amount, etc.)
    if code.isdigit():
        return False

    # All same character is not valid
    if len(set(code)) == 1:
        return False

    # Must be alphanumeric
    if not code.isalnum():
        return False

    # Real confirmation codes typically have mixed letters and numbers
    # or at least unusual letter combinations
    if code.isalpha():
        # Check for common word patterns
        word_endings = ('ING', 'TED', 'LLY', 'ION', 'ATE', 'ENT', 'OUS', 'URE', 'BLE', 'TION', 'MENT')
        for ending in word_endings:
            if code.endswith(ending):
                return False

    return True


def extract_confirmation_code(subject, body):
    """Extract confirmation code from email subject or body.

    Uses multiple strategies:
    1. Look for code in contextual patterns (most reliable)
    2. Check subject line for common formats
    3. Look in HTML attributes (data-confirmation, etc.)

    Args:
        subject: Email subject line
        body: Email body text (can be HTML)

    Returns:
        Confirmation code string or None
    """
    # Strategy 1: Subject line patterns (often most reliable)
    if subject:
        # Pattern: "Subject - ABC123"
        match = _SUBJECT_CONF_PATTERN.search(subject)
        if match:
            code = match.group(1).upper()
            if _is_valid_confirmation_code(code):
                return code

        # Pattern: "Subject #ABC123"
        match = _SUBJECT_CONF_PATTERN2.search(subject)
        if match:
            code = match.group(1).upper()
            if _is_valid_confirmation_code(code):
                return code

    # Strategy 2: Contextual patterns in body (high confidence)
    for pattern in _CONFIRMATION_CONTEXT_PATTERNS:
        matches = pattern.findall(body)
        for match in matches:
            code = match.upper()
            if _is_valid_confirmation_code(code):
                return code

    # Strategy 3: Look in HTML attributes
    html_patterns = [
        re.compile(r'data-confirmation["\s=:]+([A-Z0-9]{5,8})', re.IGNORECASE),
        re.compile(r'confirmation-code["\s=:]+([A-Z0-9]{5,8})', re.IGNORECASE),
        re.compile(r'booking-ref["\s=:]+([A-Z0-9]{5,8})', re.IGNORECASE),
        re.compile(r'pnr["\s=:]+([A-Z0-9]{5,8})', re.IGNORECASE),
    ]
    for pattern in html_patterns:
        match = pattern.search(body)
        if match:
            code = match.group(1).upper()
            if _is_valid_confirmation_code(code):
                return code

    # Strategy 4: Look for 6-char codes near key words (medium confidence)
    # Find all potential codes and score them by proximity to keywords
    keywords = ['confirmation', 'booking', 'reference', 'locator', 'pnr', 'reservation', 'itinerary']
    body_lower = body.lower()

    # Find all 6-char alphanumeric sequences
    potential_codes = re.findall(r'\b([A-Z0-9]{6})\b', body.upper())

    for code in potential_codes:
        if not _is_valid_confirmation_code(code):
            continue

        # Check if any keyword appears near this code (within 100 chars)
        code_lower = code.lower()
        for pos in [m.start() for m in re.finditer(re.escape(code_lower), body_lower)]:
            context_start = max(0, pos - 100)
            context_end = min(len(body_lower), pos + 100)
            context = body_lower[context_start:context_end]

            if any(kw in context for kw in keywords):
                return code

    # Strategy 5: Subject line generic 6-char code (lower confidence)
    if subject:
        codes = re.findall(r'\b([A-Z0-9]{6})\b', subject.upper())
        for code in codes:
            if _is_valid_confirmation_code(code):
                return code

    return None


# ============================================================================
# HTML TEXT EXTRACTION (using native Python html.parser)
# ============================================================================

class _TextExtractor(HTMLParser):
    """Extract visible text from HTML using Python's native html.parser."""

    SKIP_TAGS = frozenset({'script', 'style', 'head', 'meta', 'link', 'noscript', 'svg', 'path'})

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
    """Remove HTML tags and return only visible text content."""
    if not html_text:
        return ""

    try:
        parser = _TextExtractor()
        parser.feed(html_text)
        text = parser.get_text()
        text = unescape(text)
        # Normalize whitespace
        text = re.sub(r'\s+', ' ', text)
        return text.strip()
    except Exception:
        # Fallback: simple regex strip
        text = re.sub(r'<[^>]+>', ' ', html_text)
        text = re.sub(r'\s+', ' ', text)
        return text.strip()


# ============================================================================
# SCHEMA.ORG STRUCTURED DATA EXTRACTION (most reliable source)
# ============================================================================

def extract_schema_org_flights(html_body):
    """Extract flight info from schema.org JSON-LD or microdata.

    Many airline emails include structured data that can be parsed reliably.
    This is the MOST RELIABLE source when available.
    """
    if not html_body:
        return None

    # Try JSON-LD first (modern format)
    json_ld_pattern = re.compile(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        re.DOTALL | re.IGNORECASE
    )

    for match in json_ld_pattern.finditer(html_body):
        try:
            data = json.loads(match.group(1).strip())
            items = data if isinstance(data, list) else [data]

            for item in items:
                item_type = item.get('@type', '')
                if 'FlightReservation' in item_type or 'Flight' in item_type:
                    result = _parse_schema_flight(item)
                    if result:
                        return result

                # Check nested reservationFor
                if 'reservationFor' in item:
                    res = item['reservationFor']
                    if isinstance(res, dict) and 'Flight' in res.get('@type', ''):
                        result = _parse_schema_flight(item)
                        if result:
                            return result
        except (json.JSONDecodeError, KeyError, TypeError):
            continue

    # Try microdata format (older)
    if 'FlightReservation' in html_body or 'itemtype' in html_body.lower():
        return _extract_microdata_flights(html_body)

    return None


def _parse_schema_flight(schema_data):
    """Parse a schema.org FlightReservation object."""
    info = {
        "airports": [],
        "flight_numbers": [],
        "dates": [],
        "times": [],
        "route": None,
        "confirmation": None,
        "airline": None
    }

    try:
        # Confirmation code
        conf = schema_data.get('reservationNumber', '')
        if conf and 5 <= len(conf) <= 8 and conf.isalnum():
            info["confirmation"] = conf.upper()

        # Flight details
        flight = schema_data.get('reservationFor', schema_data)

        # Airline
        airline = flight.get('provider') or flight.get('airline')
        if isinstance(airline, dict):
            info["airline"] = airline.get('name', '')
        elif isinstance(airline, str):
            info["airline"] = airline

        # Flight number
        flight_num = flight.get('flightNumber')
        if flight_num:
            info["flight_numbers"].append(str(flight_num))

        # Departure airport
        dep = flight.get('departureAirport', {})
        if isinstance(dep, dict):
            code = dep.get('iataCode', '').upper()
            if code and code in VALID_AIRPORT_CODES:
                info["airports"].append(code)

        # Arrival airport
        arr = flight.get('arrivalAirport', {})
        if isinstance(arr, dict):
            code = arr.get('iataCode', '').upper()
            if code and code in VALID_AIRPORT_CODES:
                info["airports"].append(code)

        # Set route
        if len(info["airports"]) == 2:
            info["route"] = (info["airports"][0], info["airports"][1])

        # Parse dates/times with dateutil
        dateutil_parser = get_dateutil_parser()
        if dateutil_parser:
            for time_field in ['departureTime', 'arrivalTime']:
                time_str = flight.get(time_field)
                if time_str:
                    try:
                        dt = dateutil_parser.parse(time_str)
                        date_str = dt.strftime("%B %d, %Y")
                        time_str = dt.strftime("%I:%M %p")
                        if date_str not in info["dates"]:
                            info["dates"].append(date_str)
                        if time_str not in info["times"]:
                            info["times"].append(time_str)
                    except (ValueError, TypeError):
                        pass

    except (KeyError, TypeError, AttributeError):
        pass

    return info if (info["airports"] or info["confirmation"]) else None


def _extract_microdata_flights(html_body):
    """Extract flight info from HTML microdata attributes."""
    info = {
        "airports": [],
        "flight_numbers": [],
        "dates": [],
        "times": [],
        "route": None,
        "confirmation": None,
        "airline": None
    }

    # Extract iataCode
    for match in re.finditer(r'itemprop=["\']iataCode["\'][^>]*>([A-Z]{3})<', html_body, re.IGNORECASE):
        code = match.group(1).upper()
        if code in VALID_AIRPORT_CODES and code not in info["airports"]:
            info["airports"].append(code)

    # Extract flightNumber
    for match in re.finditer(r'itemprop=["\']flightNumber["\'][^>]*>(\d+)<', html_body, re.IGNORECASE):
        num = match.group(1)
        if num not in info["flight_numbers"]:
            info["flight_numbers"].append(num)

    # Extract reservationNumber
    for match in re.finditer(r'itemprop=["\']reservationNumber["\'][^>]*>([A-Z0-9]{5,8})<', html_body, re.IGNORECASE):
        code = match.group(1).upper()
        if _is_valid_confirmation_code(code):
            info["confirmation"] = code
            break

    if len(info["airports"]) == 2:
        info["route"] = (info["airports"][0], info["airports"][1])

    return info if (info["airports"] or info["confirmation"]) else None


# ============================================================================
# AIRPORT CODE EXTRACTION AND VALIDATION
# ============================================================================

def validate_airport_code(code):
    """Validate an airport code against our IATA database.

    Args:
        code: 3-letter code to validate

    Returns:
        Tuple of (is_valid, airport_name) or (False, None)
    """
    if not code or len(code) != 3:
        return False, None

    code = code.upper()

    # Check if it's in our exclusion list (common words)
    if code in EXCLUDED_CODES:
        return False, None

    # Check if it's a valid IATA code
    if code in VALID_AIRPORT_CODES:
        name = AIRPORT_NAMES.get(code, "")
        return True, name

    return False, None


def _verify_airport_in_context(code, airport_name, text):
    """Verify that an airport code makes sense in the email context.

    Checks if the airport's city/name appears in the text, which validates
    that this code is actually relevant to the email content.

    Args:
        code: 3-letter airport code
        airport_name: Full airport name from our database
        text: Email text to search

    Returns:
        True if the airport appears to be valid for this email
    """
    text_lower = text.lower()

    # Get the friendly name which is usually the city
    friendly = FRIENDLY_NAMES.get(code, '')

    # Check if city name appears in text
    if friendly:
        # Handle multi-word names like "New York JFK" -> check for "new york"
        city_parts = friendly.lower().split()
        # Check the first word or first two words (the city part)
        if city_parts[0] in text_lower:
            return True
        if len(city_parts) >= 2 and f"{city_parts[0]} {city_parts[1]}" in text_lower:
            return True

    # Check if airport name from database appears
    if airport_name:
        # Extract likely city name from full airport name
        name_lower = airport_name.lower()
        # Common patterns: "City International", "City Municipal", etc.
        for word in name_lower.split():
            if len(word) >= 4 and word not in ('international', 'airport', 'regional', 'municipal', 'memorial', 'county', 'field'):
                if word in text_lower:
                    return True

    # Check our city-to-airport mapping in reverse
    for city, mapped_code in CITY_TO_AIRPORT.items():
        if mapped_code == code and len(city) >= 4:
            if city in text_lower:
                return True

    return False


def extract_airports_from_text(text):
    """Extract and validate airport codes from text.

    STRICT extraction - only returns codes with strong evidence:
    1. City (CODE) format - highest confidence, always accepted
    2. CODE → CODE route format - high confidence, always accepted
    3. Contextual patterns with city name verification
    4. City name recognition (Boston to Las Vegas)

    Does NOT accept random 3-letter codes without context.

    Returns:
        List of tuples: [(code, name, confidence), ...]
    """
    airports = []
    seen = set()

    # Strategy 1: "City Name (ABC)" format - highest confidence
    # This is explicit: "Los Angeles (LAX)" - always trust these
    city_code_pattern = re.compile(r'([A-Za-z][A-Za-z\s]{2,30})\s*\(([A-Z]{3})\)')
    for city, code in city_code_pattern.findall(text):
        code = code.upper()
        is_valid, name = validate_airport_code(code)
        if is_valid and code not in seen:
            seen.add(code)
            airports.append((code, name, 'high'))

    # Strategy 2: Route patterns "ABC → DEF" or "ABC to DEF"
    # Clear route format - high confidence
    route_patterns = [
        re.compile(r'\b([A-Z]{3})\s*(?:→|->|►|–|—)\s*([A-Z]{3})\b'),
        re.compile(r'\b([A-Z]{3})\s+to\s+([A-Z]{3})\b', re.IGNORECASE),
    ]
    for pattern in route_patterns:
        for origin, dest in pattern.findall(text):
            for code in [origin.upper(), dest.upper()]:
                is_valid, name = validate_airport_code(code)
                if is_valid and code not in seen:
                    seen.add(code)
                    airports.append((code, name, 'high'))

    # Strategy 3: Contextual patterns WITH verification
    # Only accept if the city name also appears in the email
    context_pattern = re.compile(
        r'(?:depart(?:ure|ing|s)?|arriv(?:al|ing|es)?|from|to|origin|destination|airport)[:\s]+([A-Z]{3})\b',
        re.IGNORECASE
    )
    for match in context_pattern.finditer(text):
        code = match.group(1).upper()
        if code in seen:
            continue
        is_valid, name = validate_airport_code(code)
        if is_valid:
            # VERIFY: the airport's city must appear somewhere in the email
            if _verify_airport_in_context(code, name, text):
                seen.add(code)
                airports.append((code, name, 'verified'))

    # Strategy 4: Look for 3-letter codes ONLY if they're near flight context
    # AND the city name appears in the email
    if len(airports) < 2:
        strong_flight_context = [
            'flight', 'airport', 'terminal', 'gate', 'boarding',
            'depart', 'arrive', 'itinerary', 'confirmation'
        ]
        text_lower = text.lower()

        # Find all 3-letter uppercase sequences
        all_codes = re.findall(r'\b([A-Z]{3})\b', text)

        for code in all_codes:
            code = code.upper()
            if code in seen:
                continue

            is_valid, name = validate_airport_code(code)
            if not is_valid:
                continue

            # Must be near flight context
            code_positions = [m.start() for m in re.finditer(r'\b' + code + r'\b', text)]
            near_context = False
            for pos in code_positions:
                context_start = max(0, pos - 80)
                context_end = min(len(text_lower), pos + 80)
                context = text_lower[context_start:context_end]
                if any(kw in context for kw in strong_flight_context):
                    near_context = True
                    break

            if not near_context:
                continue

            # CRITICAL: Verify the city name appears in the email
            if _verify_airport_in_context(code, name, text):
                seen.add(code)
                airports.append((code, name, 'context_verified'))
                if len(airports) >= 2:
                    break

    # Strategy 5: City name recognition (e.g., "Boston to Las Vegas")
    # This is reliable because we're matching actual city names
    if len(airports) < 2:
        airports = _extract_airports_from_city_names(text, airports, seen)

    return airports


def _extract_airports_from_city_names(text, existing_airports, seen_codes):
    """Extract airports by recognizing city names in the text.

    Handles patterns like:
    - "Boston to Las Vegas"
    - "flying from Chicago to Miami"
    - "New York - Los Angeles"

    Args:
        text: Text to search
        existing_airports: Already found airports
        seen_codes: Set of already seen codes

    Returns:
        Updated airports list
    """
    airports = list(existing_airports)
    seen = set(seen_codes)

    # Build regex pattern from city names (sorted by length descending to match longer names first)
    city_names = sorted(CITY_TO_AIRPORT.keys(), key=len, reverse=True)

    # Only use city names that are 4+ chars to avoid false positives
    city_names = [c for c in city_names if len(c) >= 4]

    # Create pattern groups for origin and destination
    # Pattern: "from <city> to <city>" or "<city> to <city>" or "<city> - <city>"
    city_pattern = '|'.join(re.escape(city) for city in city_names)

    route_patterns = [
        # "from Boston to Las Vegas" or "flying from Chicago to Miami"
        re.compile(rf'(?:from|departing|leaving)\s+({city_pattern})\s+(?:to|for)\s+({city_pattern})', re.IGNORECASE),
        # "Boston to Las Vegas" (simple)
        re.compile(rf'\b({city_pattern})\s+to\s+({city_pattern})\b', re.IGNORECASE),
        # "Boston - Las Vegas" or "Boston → Las Vegas"
        re.compile(rf'\b({city_pattern})\s*[-–→>]\s*({city_pattern})\b', re.IGNORECASE),
        # "trip to Las Vegas from Boston"
        re.compile(rf'(?:trip|flight|traveling)\s+to\s+({city_pattern})\s+from\s+({city_pattern})', re.IGNORECASE),
    ]

    for pattern in route_patterns:
        matches = pattern.findall(text)
        for match in matches:
            # match could be (origin, dest) or (dest, origin) depending on pattern
            for city in match:
                city_lower = city.lower().strip()
                code = CITY_TO_AIRPORT.get(city_lower)
                if code and code not in seen:
                    is_valid, name = validate_airport_code(code)
                    if is_valid:
                        seen.add(code)
                        airports.append((code, name, 'city_name'))

        if len(airports) >= 2:
            break

    # If still not enough, try single city mentions near flight keywords
    if len(airports) < 2:
        flight_keywords = ['flight', 'flying', 'trip', 'travel', 'arriving', 'departing', 'destination']
        text_lower = text.lower()

        for city_name in city_names[:100]:  # Check top 100 cities
            if city_name in text_lower:
                code = CITY_TO_AIRPORT.get(city_name)
                if code and code not in seen:
                    # Check if near a flight keyword
                    city_pos = text_lower.find(city_name)
                    context_start = max(0, city_pos - 50)
                    context_end = min(len(text_lower), city_pos + len(city_name) + 50)
                    context = text_lower[context_start:context_end]

                    if any(kw in context for kw in flight_keywords):
                        is_valid, name = validate_airport_code(code)
                        if is_valid:
                            seen.add(code)
                            airports.append((code, name, 'city_name'))

                        if len(airports) >= 2:
                            break

    return airports


# ============================================================================
# DATE EXTRACTION (using dateutil)
# ============================================================================

def _is_valid_flight_year(year, email_year):
    """Check if a year is reasonable for a flight date.

    Must be within ±2 years of when the email was sent.
    """
    return (email_year - 2) <= year <= (email_year + 2)


def extract_dates_from_text(text, email_date=None):
    """Extract flight dates from text using dateutil.

    Args:
        text: Text to search
        email_date: datetime when email was sent (for validation and default year)

    Returns:
        List of formatted date strings ("Month DD, YYYY")
    """
    dateutil_parser = get_dateutil_parser()
    if not dateutil_parser:
        return []

    email_year = email_date.year if email_date else datetime.now().year
    dates = []
    seen = set()

    # Pattern 1: "Month DD, YYYY" or "Month DD YYYY"
    explicit_pattern = re.compile(
        r'\b((?:January|February|March|April|May|June|July|August|September|October|November|December|'
        r'Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[.,]?\s+\d{1,2}(?:st|nd|rd|th)?[,\s]+\d{4})\b',
        re.IGNORECASE
    )
    for match in explicit_pattern.finditer(text):
        try:
            dt = dateutil_parser.parse(match.group(1), fuzzy=False)
            if _is_valid_flight_year(dt.year, email_year):
                formatted = dt.strftime("%B %d, %Y")
                if formatted not in seen:
                    seen.add(formatted)
                    dates.append(formatted)
        except (ValueError, TypeError):
            continue

    # Pattern 2: "DD Month YYYY" (European format)
    euro_pattern = re.compile(
        r'\b(\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December|'
        r'Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[,\s]+\d{4})\b',
        re.IGNORECASE
    )
    for match in euro_pattern.finditer(text):
        try:
            dt = dateutil_parser.parse(match.group(1), dayfirst=True, fuzzy=False)
            if _is_valid_flight_year(dt.year, email_year):
                formatted = dt.strftime("%B %d, %Y")
                if formatted not in seen:
                    seen.add(formatted)
                    dates.append(formatted)
        except (ValueError, TypeError):
            continue

    # Pattern 3: ISO format "YYYY-MM-DD"
    iso_pattern = re.compile(r'\b(\d{4}-\d{2}-\d{2})\b')
    for match in iso_pattern.finditer(text):
        try:
            dt = dateutil_parser.parse(match.group(1))
            if _is_valid_flight_year(dt.year, email_year):
                formatted = dt.strftime("%B %d, %Y")
                if formatted not in seen:
                    seen.add(formatted)
                    dates.append(formatted)
        except (ValueError, TypeError):
            continue

    # Pattern 4: US numeric "MM/DD/YYYY"
    us_pattern = re.compile(r'\b(\d{1,2}/\d{1,2}/\d{4})\b')
    for match in us_pattern.finditer(text):
        try:
            dt = dateutil_parser.parse(match.group(1))
            if _is_valid_flight_year(dt.year, email_year):
                formatted = dt.strftime("%B %d, %Y")
                if formatted not in seen:
                    seen.add(formatted)
                    dates.append(formatted)
        except (ValueError, TypeError):
            continue

    # Pattern 5: Date without year - use email year
    no_year_pattern = re.compile(
        r'\b((?:January|February|March|April|May|June|July|August|September|October|November|December|'
        r'Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[.,]?\s+\d{1,2}(?:st|nd|rd|th)?)\b'
        r'(?![,\s]*\d{4})',
        re.IGNORECASE
    )
    for match in no_year_pattern.finditer(text):
        try:
            dt = dateutil_parser.parse(match.group(1), default=datetime(email_year, 1, 1))
            formatted = dt.strftime("%B %d, %Y")
            if formatted not in seen:
                seen.add(formatted)
                dates.append(formatted)
        except (ValueError, TypeError):
            continue

    # If still no dates found, use email date as fallback
    if not dates and email_date:
        dates.append(email_date.strftime("%B %d, %Y"))

    return dates[:3]  # Max 3 dates


# ============================================================================
# MAIN EXTRACTION FUNCTION
# ============================================================================

def extract_flight_info(body, email_date=None, html_body=None):
    """Extract all flight information from an email.

    Uses a tiered approach:
    1. Schema.org structured data (most reliable)
    2. Native Python parsing with dateutil
    3. Regex patterns as fallback

    Args:
        body: Email body text
        email_date: datetime when email was sent
        html_body: Raw HTML body (for schema.org extraction)

    Returns:
        Dict with: airports, flight_numbers, dates, times, route
    """
    info = {
        "airports": [],
        "flight_numbers": [],
        "dates": [],
        "times": [],
        "route": None
    }

    if not body:
        return info

    # Try schema.org first (most reliable)
    schema_info = extract_schema_org_flights(html_body or body)
    if schema_info:
        if schema_info.get("airports"):
            info["airports"] = schema_info["airports"]
        if schema_info.get("route"):
            info["route"] = schema_info["route"]
        if schema_info.get("flight_numbers"):
            info["flight_numbers"] = schema_info["flight_numbers"]
        if schema_info.get("dates"):
            info["dates"] = schema_info["dates"]
        if schema_info.get("times"):
            info["times"] = schema_info["times"]

        # If we got good data, return early
        if info["airports"] and info["dates"]:
            return info

    # Strip HTML for text parsing
    text = strip_html_tags(body)

    # Extract airports
    if not info["airports"]:
        airport_results = extract_airports_from_text(text)
        info["airports"] = [code for code, name, conf in airport_results][:4]

        # Set route if we have exactly 2 airports
        if len(info["airports"]) >= 2 and not info["route"]:
            info["route"] = (info["airports"][0], info["airports"][1])

    # Extract flight numbers
    if not info["flight_numbers"]:
        flight_patterns = [
            re.compile(r'[Ff]light\s*#?\s*:?\s*([A-Z]{0,2}\s*\d{1,4})\b'),
            re.compile(r'\b(B6|DL|UA|AA|WN|AS|NK|F9|HA|AC|BA|LH|EK)\s*(\d{1,4})\b'),
        ]
        for pattern in flight_patterns:
            matches = pattern.findall(text)
            for match in matches:
                if isinstance(match, tuple):
                    num = ''.join(match).strip()
                else:
                    num = match.strip()
                if num and num not in info["flight_numbers"]:
                    info["flight_numbers"].append(num)
        info["flight_numbers"] = info["flight_numbers"][:4]

    # Extract dates
    if not info["dates"]:
        info["dates"] = extract_dates_from_text(text, email_date)

    # Extract times
    if not info["times"]:
        time_pattern = re.compile(r'\b(\d{1,2}:\d{2}\s*[AaPp][Mm])\b')
        times = time_pattern.findall(text)
        info["times"] = list(dict.fromkeys(times))[:4]

    return info


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

_WHITESPACE_PATTERN = re.compile(r'\s+')


def generate_content_hash(subject, body):
    """Generate a hash of email content for deduplication."""
    normalized = _WHITESPACE_PATTERN.sub(' ', (subject + body).lower().strip())
    return hashlib.md5(normalized.encode()).hexdigest()[:16]


def create_flight_fingerprint(flight_info):
    """Create a fingerprint to identify unique flights.

    Used for deduplication when confirmation code is not available.
    """
    parts = []

    # Route is most important
    if flight_info.get("route"):
        parts.append(f"{flight_info['route'][0]}-{flight_info['route'][1]}")
    elif flight_info.get("airports"):
        parts.append("-".join(flight_info["airports"][:2]))

    # Date
    if flight_info.get("dates"):
        parts.append(flight_info["dates"][0].lower())

    # Flight number
    if flight_info.get("flight_numbers"):
        parts.append(flight_info["flight_numbers"][0])

    return "|".join(parts) if parts else None
