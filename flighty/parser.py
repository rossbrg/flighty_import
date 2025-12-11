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
import logging
from datetime import datetime
from html.parser import HTMLParser
from html import unescape

from .airports import VALID_AIRPORT_CODES, EXCLUDED_CODES, AIRPORT_NAMES, ALL_AIRPORT_CODES, city_to_airport_code, CITY_TO_AIRPORT, FRIENDLY_NAMES
from .airlines import extract_airline_from_text, extract_flight_numbers, validate_airport_for_airline, AIRLINE_HUBS
import urllib.request
import urllib.error

# Set up logging
logger = logging.getLogger(__name__)

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
    """Classify an email as booking, marketing, cancellation, or unknown.

    Args:
        subject: Email subject line
        body: Email body text
        has_confirmation_code: Whether a confirmation code was found

    Returns:
        String: 'booking', 'marketing', 'cancellation', or 'unknown'
    """
    subject_lower = subject.lower() if subject else ""
    body_lower = body.lower() if body else ""

    # Check for cancellation indicators first
    cancellation_subject_patterns = [
        r'\bcancel+ed\b',           # "cancelled" or "canceled"
        r'\bcancel+ation\b',        # "cancellation"
        r'\bflight\s+cancel',       # "flight cancelled"
        r'\btrip\s+cancel',         # "trip cancelled"
        r'\bbooking\s+cancel',      # "booking cancelled"
        r'\breservation\s+cancel',  # "reservation cancelled"
        r'\bitinerary\s+.*cancel',  # "itinerary has been cancelled"
        r'\bhas\s+been\s+cancel',   # "has been cancelled"
        r'\bwas\s+cancel',          # "was cancelled"
        r'\brefund\s+confirm',      # "refund confirmation"
        r'\brefund\s+processed',    # "refund processed"
    ]

    for pattern in cancellation_subject_patterns:
        if re.search(pattern, subject_lower):
            return 'cancellation'

    # Check body for strong cancellation indicators
    cancellation_body_patterns = [
        r'your\s+(?:flight|trip|booking|reservation|itinerary)\s+(?:has\s+been\s+)?cancel+ed',
        r'we\s+(?:have\s+)?cancel+ed\s+your',
        r'cancel+ation\s+(?:confirm|notice|notification)',
        r'this\s+(?:flight|booking|reservation|itinerary)\s+(?:has\s+been\s+)?cancel+ed',
        r'your\s+refund\s+(?:has\s+been\s+|is\s+)?(?:processed|confirmed)',
        r'plans\s+change',          # JetBlue's "Plans change" cancellation message
    ]

    for pattern in cancellation_body_patterns:
        if re.search(pattern, body_lower):
            return 'cancellation'

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
# This is a comprehensive list to prevent false positives
_EXCLUDED_CONFIRMATION_CODES = {
    # Words found in user's output that were incorrectly identified as codes
    'SEARCH', 'CLICKS', 'JETBLUE', 'CENTER', 'DETAILS', 'CHANNELS', 'BOOKED',
    'CLOSER', 'EXCESS', 'RENTAL', 'REDEEM', 'HILTON', 'MARTHA', 'REWARD',
    'TERMS', 'HEIGHT', 'BORDER', 'EXPECT', 'HOTELS', 'BUNDLE', 'LIGHTS',
    'PREFER', 'NUMBER', 'DOESN',
    # URL/protocol terms
    'HTTPS', 'EMAIL', 'CLICK', 'TRACK', 'VIEWS', 'LINKS',
    # More false positives
    'HOLDS', 'ITEMS', 'CARDS', 'SAVES', 'WORKS', 'PLANS',
    # More words found as false positives in email scanning
    'VALID', 'DATES', 'STILL', 'THROUGH', 'INVOICES', 'INVOICE',
    'PURCHASE', 'ORIGINAL', 'ORIGINAL', 'CHARGED', 'AMOUNT', 'CUSTOMER',
    'ENTERPRISE', 'NATIONAL', 'PROTECT', 'COVERAGE', 'INSURANCE',
    'WAIVER', 'DAMAGE', 'LIABILITY', 'RENTAL', 'VEHICLE', 'DRIVER',
    # Airline and travel related words
    'JETBLUE', 'DELTA', 'UNITED', 'SPIRIT', 'VIRGIN', 'ALASKA', 'FRONTIER',
    'SOUTHWEST', 'AMERICAN', 'AIRLINES', 'AIRWAYS', 'FLIGHT', 'FLIGHTS',
    'AIRPORT', 'BOOKING', 'BOOKED', 'TRAVEL', 'TRAVELS', 'TICKET', 'TICKETS',
    'BOARDING', 'TERMINAL', 'DEPARTURE', 'ARRIVAL', 'DEPART', 'ARRIVE',
    'RETURN', 'ROUNDTRIP', 'ONEWAY',
    # Hotel/rental words
    'HILTON', 'MARRIOTT', 'HYATT', 'HOTELS', 'HOTEL', 'RENTAL', 'RENTALS',
    'HERTZ', 'AVIS', 'BUDGET', 'ENTERPRISE',
    # Common action words
    'SEARCH', 'CLICK', 'CLICKS', 'SELECT', 'CHOOSE', 'SUBMIT', 'CANCEL',
    'MODIFY', 'CHANGE', 'UPDATE', 'MANAGE', 'REVIEW', 'CONFIRM', 'REDEEM',
    'PREFER', 'PREFER', 'EXPECT', 'BUNDLE', 'CLOSER', 'EXCESS',
    # Common nouns
    'CENTER', 'CENTRE', 'DETAILS', 'DETAIL', 'CHANNELS', 'CHANNEL',
    'REWARD', 'REWARDS', 'POINTS', 'MILES', 'CREDIT', 'CREDITS',
    'MEMBER', 'MEMBERS', 'STATUS', 'ACCOUNT', 'PROFILE',
    'BORDER', 'BORDERS', 'HEIGHT', 'WEIGHT', 'LENGTH', 'WIDTH',
    'LIGHTS', 'TERMS', 'POLICY', 'POLICIES', 'NOTICE', 'RECEIPT',
    'SERVICE', 'SERVICES', 'CONTACT', 'CUSTOMER', 'SUPPORT',
    'AMOUNT', 'DOLLAR', 'DOLLARS', 'CHARGE', 'CHARGES', 'REFUND',
    'WINDOW', 'BUTTON', 'FOOTER', 'HEADER', 'IMAGES', 'DOMAIN', 'SERVER',
    # Common descriptive words
    'BEFORE', 'AFTER', 'DURING', 'WITHIN', 'ACROSS', 'AROUND', 'BEHIND',
    'BEYOND', 'TOWARD', 'DOUBLE', 'SINGLE', 'PLEASE', 'THANKS', 'REALLY',
    'ALWAYS', 'ALMOST', 'SHOULD', 'THOUGH', 'EITHER', 'OTHERS',
    # People/places
    'BOSTON', 'DALLAS', 'DENVER', 'MARTHA', 'FAMILY', 'FRIEND', 'PEOPLE',
    # Time words
    'SUMMER', 'WINTER', 'SPRING', 'MONDAY', 'TUESDAY', 'WEDNESDAY',
    'THURSDAY', 'FRIDAY', 'SATURDAY', 'SUNDAY',
    # Tech words
    'CHROME', 'SAFARI', 'MOBILE', 'ONLINE', 'ACCESS', 'SECURE',
    # Common 5-letter words that appear frequently
    'WHICH', 'THEIR', 'ABOUT', 'WOULD', 'THERE', 'COULD', 'OTHER', 'THESE',
    'FIRST', 'BEING', 'WHERE', 'SINCE', 'UNDER', 'PRICE', 'BELOW', 'ABOVE',
    'TODAY', 'LATER', 'EARLY', 'OFFER', 'DEALS', 'EXTRA', 'BONUS', 'MILES',
    'SEATS', 'CLASS', 'CABIN',
    # More action/state words
    'COMING', 'MAKING', 'TAKING', 'HAVING', 'HAPPEN', 'THINGS', 'STREET',
    # Email/marketing words that appear as false positives
    'UNSUBSCRIBE', 'PRIVACY', 'SETTINGS', 'OPTIONS', 'FORWARD',
}


def _looks_like_english_word(code):
    """Check if a code looks like it could be an English word.

    Uses simple heuristics to detect word-like patterns.
    Real confirmation codes are random; English words have patterns.
    """
    code = code.upper()

    # Common English word endings
    word_endings = ('ING', 'TED', 'LES', 'ERS', 'LLY', 'ARD', 'GHT', 'NCE', 'ION',
                    'EST', 'ANT', 'ENT', 'ALS', 'OWN', 'AIN', 'OWS', 'ELS', 'EED',
                    'EEN', 'OSE', 'ASE', 'USE', 'ICE', 'AGE', 'ATE', 'ILE', 'ARS',
                    'ALS', 'UND', 'ELT', 'OST', 'AST', 'ETS', 'ITS', 'OTS', 'ATS')

    # Common English word beginnings
    word_beginnings = ('THE', 'AND', 'FOR', 'ARE', 'BUT', 'NOT', 'YOU', 'ALL',
                       'CAN', 'HER', 'WAS', 'ONE', 'OUR', 'OUT', 'PRE', 'PRO',
                       'CON', 'DIS', 'MIS', 'UNS', 'RES', 'EXP', 'IMP', 'COM',
                       'SUB', 'SEA', 'CHA', 'BOR', 'CEN', 'DET', 'HEI', 'HOT',
                       'LIG', 'REW', 'TER', 'CLO', 'EXC', 'BUN', 'PRE', 'REF')

    # Check endings
    for ending in word_endings:
        if code.endswith(ending):
            return True

    # Check beginnings
    for beginning in word_beginnings:
        if code.startswith(beginning):
            return True

    # Check for common consonant-vowel patterns typical of English
    # Words like "HOTELS" = H-O-T-E-L-S (C-V-C-V-C-C pattern)
    # Random codes like "XKJFQZ" don't follow this pattern
    pattern = ''
    for c in code:
        if c in 'AEIOU':
            pattern += 'V'
        else:
            pattern += 'C'

    # English words often have alternating or near-alternating C-V patterns
    # Check for CVCVCV, CVCCVC, CCVCVC, etc.
    english_patterns = {'CVCCVC', 'CVCVCV', 'CVCCCC', 'CVCVCC', 'CCVCVC',
                        'CVCCVV', 'CVCVVC', 'CVVCVC', 'CCVCCV', 'CVVCCV'}
    if pattern in english_patterns:
        return True

    return False


def _is_valid_confirmation_code(code):
    """Check if a code looks like a valid confirmation code.

    Real confirmation codes are typically:
    - 6 characters (most common)
    - Mix of letters and sometimes numbers
    - Not common English words
    - Usually uppercase letters with maybe 1-2 digits
    """
    if not code or len(code) < 5 or len(code) > 8:
        return False

    code = code.upper()

    # Check against excluded words (comprehensive list)
    if code in _EXCLUDED_CONFIRMATION_CODES:
        return False

    # All digits is rarely a confirmation code (more likely a date, amount, etc.)
    if code.isdigit():
        return False

    # All letters that form a pronounceable English word pattern is suspicious
    # Real codes like "ABCDEF" are random, not words like "SEARCH" or "HOTELS"
    if code.isalpha():
        # Check if it looks like a word (has vowels in reasonable positions)
        vowels = sum(1 for c in code if c in 'AEIOU')
        # English words typically have 1-2 vowels per 6 letters
        # Random codes have more random distribution
        if len(code) == 6 and 1 <= vowels <= 3:
            # Looks like it could be a word - additional checks
            # Check for common word patterns
            if _looks_like_english_word(code):
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

    # Strategy 4: STRICT - Only accept codes that appear IMMEDIATELY after a label
    # This prevents picking up random 6-letter words in the email body
    # Pattern: "Confirmation: ABCDEF" or "Code: ABCDEF" (within 20 chars, not 100)
    strict_label_patterns = [
        re.compile(r'confirmation[:\s#]+([A-Z0-9]{6})\b', re.IGNORECASE),
        re.compile(r'booking\s*(?:code|ref|#)?[:\s]+([A-Z0-9]{6})\b', re.IGNORECASE),
        re.compile(r'reference[:\s#]+([A-Z0-9]{6})\b', re.IGNORECASE),
        re.compile(r'code[:\s]+([A-Z0-9]{6})\b', re.IGNORECASE),
        re.compile(r'locator[:\s]+([A-Z0-9]{6})\b', re.IGNORECASE),
        re.compile(r'pnr[:\s]+([A-Z0-9]{6})\b', re.IGNORECASE),
    ]

    for pattern in strict_label_patterns:
        match = pattern.search(body)
        if match:
            code = match.group(1).upper()
            if _is_valid_confirmation_code(code):
                return code

    # Strategy 5: Subject line with confirmation context
    # Only accept if subject contains confirmation-related keywords
    if subject:
        subject_lower = subject.lower()
        has_conf_context = any(kw in subject_lower for kw in
            ['confirmation', 'confirmed', 'booking', 'itinerary', 'e-ticket', 'eticket', 'receipt'])

        if has_conf_context:
            # Look for 6-char codes in subject
            codes = re.findall(r'\b([A-Z0-9]{6})\b', subject.upper())
            for code in codes:
                # Extra strict for subject line codes - must have a digit
                # Real codes often have 1-2 digits mixed with letters
                has_digit = any(c.isdigit() for c in code)
                has_letter = any(c.isalpha() for c in code)

                if has_digit and has_letter and _is_valid_confirmation_code(code):
                    return code

                # If all letters, must pass very strict validation
                if code.isalpha() and _is_valid_confirmation_code(code):
                    # Additional check: code should not be pronounceable
                    if not _looks_like_english_word(code):
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
        result = text.strip()

        # If HTMLParser returns empty but we have HTML, use regex fallback
        # This happens with some complex/malformed HTML emails
        if not result and len(html_text) > 100:
            text = re.sub(r'<[^>]+>', ' ', html_text)
            text = unescape(text)
            text = re.sub(r'\s+', ' ', text)
            result = text.strip()

        return result
    except Exception:
        # Fallback: simple regex strip
        text = re.sub(r'<[^>]+>', ' ', html_text)
        text = unescape(text)
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


# Cache for flight verification results (to avoid repeated lookups)
_FLIGHT_VERIFICATION_CACHE = {}

# IATA to ICAO airline code mapping for FlightAware lookups
# FlightAware uses ICAO codes (3-letter) in their URLs
IATA_TO_ICAO = {
    # Major US Airlines
    'AA': 'AAL',  # American Airlines
    'DL': 'DAL',  # Delta
    'UA': 'UAL',  # United
    'WN': 'SWA',  # Southwest
    'B6': 'JBU',  # JetBlue
    'AS': 'ASA',  # Alaska Airlines
    'NK': 'NKS',  # Spirit
    'F9': 'FFT',  # Frontier
    'G4': 'AAY',  # Allegiant
    'HA': 'HAL',  # Hawaiian Airlines
    'SY': 'SCX',  # Sun Country
    # International Carriers
    'BA': 'BAW',  # British Airways
    'LH': 'DLH',  # Lufthansa
    'AF': 'AFR',  # Air France
    'KL': 'KLM',  # KLM
    'AC': 'ACA',  # Air Canada
    'QF': 'QFA',  # Qantas
    'EK': 'UAE',  # Emirates
    'QR': 'QTR',  # Qatar Airways
    'SQ': 'SIA',  # Singapore Airlines
    'CX': 'CPA',  # Cathay Pacific
    'JL': 'JAL',  # Japan Airlines
    'NH': 'ANA',  # All Nippon Airways
    'TK': 'THY',  # Turkish Airlines
    'LX': 'SWR',  # Swiss
    'AZ': 'ITY',  # ITA Airways (was Alitalia)
    'IB': 'IBE',  # Iberia
    'VS': 'VIR',  # Virgin Atlantic
    'EI': 'EIN',  # Aer Lingus
    'SK': 'SAS',  # SAS Scandinavian
    'AY': 'FIN',  # Finnair
    'TP': 'TAP',  # TAP Portugal
    'OS': 'AUA',  # Austrian
    'SN': 'BEL',  # Brussels Airlines
    'AM': 'AMX',  # Aeromexico
    'CM': 'CMP',  # Copa Airlines
    'AV': 'AVA',  # Avianca
    'LA': 'LAN',  # LATAM
    # Low Cost Carriers
    'FR': 'RYR',  # Ryanair
    'U2': 'EZY',  # easyJet
    'VY': 'VLG',  # Vueling
    'W6': 'WZZ',  # Wizz Air
    'XP': 'CXP',  # Avelo Airlines
    'MX': 'MXY',  # Breeze Airways
}


def verify_flight_exists(airline_code, flight_num, date_str=None):
    """Verify a flight exists using FlightAware.

    This checks if the flight number is real and gets the route.
    Results are cached to avoid repeated lookups.

    If a date is provided, we check the flight history to verify the route
    on that specific day (since the same flight number can operate different
    routes on different days).

    Args:
        airline_code: 2-letter IATA code (e.g., "B6")
        flight_num: Flight number (e.g., "123")
        date_str: Optional date string (e.g., "December 07, 2025") to verify flight on specific day

    Returns:
        Dict with 'exists', 'origin', 'dest', 'verified_route', 'date_matched' if found, or None if couldn't verify
    """
    # Include date in cache key if provided
    cache_key = f"{airline_code}{flight_num}"
    if date_str:
        cache_key += f"_{date_str}"

    if cache_key in _FLIGHT_VERIFICATION_CACHE:
        return _FLIGHT_VERIFICATION_CACHE[cache_key]

    try:
        # Convert IATA to ICAO code for FlightAware URL
        # FlightAware uses ICAO codes (e.g., JBU652 not B6652)
        icao_code = IATA_TO_ICAO.get(airline_code.upper(), airline_code)
        flight_id = f"{icao_code}{flight_num}"

        result = {'exists': True, 'origin': None, 'dest': None, 'verified_route': False, 'date_matched': False}

        # If we have a date, use the history page to find the route for that specific day
        if date_str:
            # Parse the date to get YYYYMMDD format
            target_date = None
            date_formats = [
                '%B %d, %Y',     # December 07, 2025
                '%b %d, %Y',     # Dec 07, 2025
                '%Y-%m-%d',      # 2025-12-07
                '%m/%d/%Y',      # 12/07/2025
            ]
            for fmt in date_formats:
                try:
                    parsed = datetime.strptime(date_str, fmt)
                    target_date = parsed.strftime('%Y%m%d')
                    break
                except ValueError:
                    continue

            if target_date:
                # Fetch the history page
                history_url = f"https://www.flightaware.com/live/flight/{flight_id}/history"
                logger.debug(f"FlightAware history lookup: {history_url} for date {target_date}")

                req = urllib.request.Request(
                    history_url,
                    headers={'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'}
                )

                with urllib.request.urlopen(req, timeout=10) as response:
                    html = response.read().decode('utf-8', errors='ignore')

                # Look for history entries: flight/JBU652/history/20251207/2310Z/KMCO/KBOS
                history_pattern = rf'flight/{re.escape(flight_id)}/history/(\d{{8}})/\d+Z/([A-Z]{{4}})/([A-Z]{{4}})'
                history_entries = re.findall(history_pattern, html)

                # Find an entry matching our target date
                for entry_date, icao_orig, icao_dest in history_entries:
                    if entry_date == target_date:
                        # Convert ICAO (KMCO) to IATA (MCO) - US airports start with K
                        origin_iata = icao_orig[1:] if icao_orig.startswith('K') and len(icao_orig) == 4 else icao_orig
                        dest_iata = icao_dest[1:] if icao_dest.startswith('K') and len(icao_dest) == 4 else icao_dest
                        result['origin'] = origin_iata
                        result['dest'] = dest_iata
                        result['verified_route'] = True
                        result['date_matched'] = True
                        logger.debug(f"FlightAware history MATCH: {origin_iata} → {dest_iata} on {target_date}")
                        _FLIGHT_VERIFICATION_CACHE[cache_key] = result
                        return result

                # No exact date match, try to get the most common route from history
                if history_entries:
                    # Count routes to find the most common one
                    route_counts = {}
                    for _, icao_orig, icao_dest in history_entries:
                        origin_iata = icao_orig[1:] if icao_orig.startswith('K') and len(icao_orig) == 4 else icao_orig
                        dest_iata = icao_dest[1:] if icao_dest.startswith('K') and len(icao_dest) == 4 else icao_dest
                        route_key = (origin_iata, dest_iata)
                        route_counts[route_key] = route_counts.get(route_key, 0) + 1

                    # Use the most common route
                    most_common = max(route_counts.items(), key=lambda x: x[1])
                    result['origin'] = most_common[0][0]
                    result['dest'] = most_common[0][1]
                    result['verified_route'] = True
                    result['date_matched'] = False  # Didn't match exact date
                    logger.debug(f"FlightAware history (most common): {result['origin']} → {result['dest']} (date {target_date} not in history)")
                    _FLIGHT_VERIFICATION_CACHE[cache_key] = result
                    return result

        # Fall back to the main flight page (no date or history failed)
        url = f"https://www.flightaware.com/live/flight/{flight_id}"
        logger.debug(f"FlightAware lookup: {url}")

        req = urllib.request.Request(
            url,
            headers={'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'}
        )

        with urllib.request.urlopen(req, timeout=10) as response:
            html = response.read().decode('utf-8', errors='ignore')

            # FlightAware embeds route data in JavaScript targeting calls
            # Look for: setTargeting('origin_IATA', 'MCO') and setTargeting('destination_IATA', 'BOS')
            origin_match = re.search(r"['\"]origin_IATA['\"],\s*['\"]([A-Z]{3})['\"]", html)
            dest_match = re.search(r"['\"]destination_IATA['\"],\s*['\"]([A-Z]{3})['\"]", html)

            if origin_match and dest_match:
                result['origin'] = origin_match.group(1).upper()
                result['dest'] = dest_match.group(1).upper()
                result['verified_route'] = True
                logger.debug(f"FlightAware verified: {result['origin']} → {result['dest']}")
            else:
                # Fallback: Try other patterns in page content
                route_patterns = [
                    r'<title>[^<]*\b([A-Z]{3})\s*[-→–]\s*([A-Z]{3})\b',  # In title
                    r'\b([A-Z]{3})\s*(?:→|->|–)\s*([A-Z]{3})\b',  # Arrow route pattern
                ]

                for pattern in route_patterns:
                    match = re.search(pattern, html)
                    if match:
                        result['origin'] = match.group(1).upper()
                        result['dest'] = match.group(2).upper()
                        result['verified_route'] = True
                        logger.debug(f"FlightAware fallback verified: {result['origin']} → {result['dest']}")
                        break

                if not result['verified_route']:
                    logger.debug(f"FlightAware: flight page found but couldn't extract route")

            _FLIGHT_VERIFICATION_CACHE[cache_key] = result
            return result

    except urllib.error.HTTPError as e:
        if e.code == 404:
            logger.debug(f"FlightAware: flight {airline_code}{flight_num} not found (404)")
        else:
            logger.debug(f"FlightAware HTTP error: {e.code}")
        _FLIGHT_VERIFICATION_CACHE[cache_key] = None
        return None
    except Exception as e:
        logger.debug(f"FlightAware lookup failed: {e}")
        _FLIGHT_VERIFICATION_CACHE[cache_key] = None
        return None


def verify_route_for_flight(airline_code, flight_num, proposed_origin, proposed_dest):
    """Verify that a proposed route matches the actual flight route.

    Args:
        airline_code: 2-letter IATA code
        flight_num: Flight number
        proposed_origin: Airport code we think is the origin
        proposed_dest: Airport code we think is the destination

    Returns:
        Tuple of (is_correct, actual_origin, actual_dest)
        - is_correct: True if proposed route matches actual route
        - actual_origin/dest: The verified route (or None if couldn't verify)
    """
    verified = verify_flight_exists(airline_code, flight_num)

    if not verified or not verified.get('verified_route'):
        # Couldn't verify - return unknown
        return None, None, None

    actual_origin = verified.get('origin')
    actual_dest = verified.get('dest')

    # Check if proposed route matches actual route
    if actual_origin and actual_dest:
        if proposed_origin == actual_origin and proposed_dest == actual_dest:
            return True, actual_origin, actual_dest
        # Maybe they got the direction wrong?
        if proposed_origin == actual_dest and proposed_dest == actual_origin:
            return True, actual_origin, actual_dest
        # Doesn't match
        return False, actual_origin, actual_dest

    return None, actual_origin, actual_dest


def verify_and_correct_flight_info(flight_info, verify_online=True):
    """Verify and potentially correct flight info using online lookup.

    If we have a flight number, look up the actual route and use that
    instead of what we extracted (which might be wrong).

    Args:
        flight_info: Dict with airports, route, flight_numbers, etc.
        verify_online: Whether to do online verification

    Returns:
        Updated flight_info dict with verified/corrected route
    """
    if not verify_online or not flight_info:
        return flight_info

    flight_numbers = flight_info.get('flight_numbers', [])
    if not flight_numbers:
        return flight_info

    # Get the first date from flight info for date-specific verification
    dates = flight_info.get('dates', [])
    first_date = dates[0] if dates else None

    logger.debug(f"  -> Verifying flight via FlightAware: {flight_numbers[0]} (date: {first_date})")

    # Try to verify with the first flight number
    for fn in flight_numbers[:1]:
        # Parse flight number - could be "B6 123" or "B6123"
        # Airline codes can be 2 letters (AA, DL) or letter+digit (B6, F9, G4)
        match = re.match(r'^([A-Z][A-Z0-9])\s*(\d+)$', fn)
        if not match:
            logger.debug(f"  -> Flight number format not recognized: {fn}")
            continue

        airline_code = match.group(1)
        flight_num = match.group(2)

        # Pass date for date-specific verification
        verified = verify_flight_exists(airline_code, flight_num, date_str=first_date)
        if verified and verified.get('verified_route'):
            actual_origin = verified.get('origin')
            actual_dest = verified.get('dest')
            date_matched = verified.get('date_matched', False)

            if actual_origin and actual_dest:
                # Validate that verified airports aren't excluded codes
                origin_valid, _ = validate_airport_code(actual_origin)
                dest_valid, _ = validate_airport_code(actual_dest)
                if origin_valid and dest_valid:
                    old_route = flight_info.get('route')
                    # Update flight info with verified route
                    flight_info['route'] = (actual_origin, actual_dest)
                    flight_info['airports'] = [actual_origin, actual_dest]
                    flight_info['route_verified'] = True
                    flight_info['date_verified'] = date_matched

                    if date_matched:
                        logger.debug(f"  -> FlightAware VERIFIED (date matched): {actual_origin} → {actual_dest} (was: {old_route})")
                    else:
                        logger.debug(f"  -> FlightAware VERIFIED (most common route): {actual_origin} → {actual_dest} (was: {old_route})")
                    break
        else:
            logger.debug(f"  -> FlightAware: could not verify route for {fn}")

    return flight_info


def _code_appears_as_regular_word(code, text):
    """Check if a 3-letter code appears as a regular English word in context.

    This helps distinguish between:
    - "BUY" as airport code vs "buy points now"
    - "PAY" as airport code vs "pay your bill"
    - "NON" as airport code vs "non-refundable"

    The key insight: Real airport codes in flight emails appear in UPPERCASE
    in contexts like "MCO BOS" or "Departing: JFK". If the code appears
    predominantly in lowercase or mixed-case as part of regular sentences,
    it's likely just a regular word, not an airport code.

    Args:
        code: 3-letter airport code (uppercase)
        text: Email text to search

    Returns:
        True if the code appears to be used as a regular word (NOT an airport)
    """
    code_lower = code.lower()

    # Find all occurrences of this code (case-insensitive)
    pattern = r'\b' + re.escape(code_lower) + r'\b'
    matches = list(re.finditer(pattern, text, re.IGNORECASE))

    if not matches:
        return False

    uppercase_count = 0
    lowercase_count = 0

    for match in matches:
        matched_text = match.group()
        if matched_text.isupper():
            uppercase_count += 1
        else:
            lowercase_count += 1

    # If the code appears more often in lowercase/mixed case than uppercase,
    # it's probably being used as a regular word
    if lowercase_count > uppercase_count:
        return True

    # Also check the immediate context - if it's in a phrase like "buy now" or "pay here"
    # those are strong indicators it's a regular word
    # Note: Be careful not to match flight-related patterns like "MCO to BOS"
    regular_word_contexts = [
        # Common verb phrases (exclude "to" which is used in routes like "MCO to BOS")
        rf'\b{code_lower}\s+(?:now|here|today|online|more|it|this|that|them|the|a|an|for|your|our|my)\b',
        rf'\b(?:can|will|should|must|please|you|we|i|dont|don\'t)\s+{code_lower}\b',
        # Common adjective/noun usage
        rf'\b{code_lower}[-](?:stop|refundable|smoking|alcoholic|transferable|applicable)\b',
        rf'\b(?:non|pre|re)[-]{code_lower}\b',
        # Financial terms (APR = Annual Percentage Rate)
        rf'\b(?:lower|low|high|fixed|variable|annual|current|your)[-\s]{code_lower}\b',
        rf'\b{code_lower}[-\s](?:rate|loan|offer|credit|card|interest|financing)\b',
        rf'\b\d+(?:\.\d+)?%?\s*{code_lower}\b',  # "0% APR", "5.99% APR"
        # Technology/email terms
        rf'@{code_lower}\.com',  # @aol.com -> AOL is not an airport
        rf'{code_lower}\.com',   # aol.com
        rf'\.{code_lower}\b',    # .pdf, .com file extensions
        rf'\b{code_lower}\s+(?:file|document|attachment|format)\b',  # PDF file
        rf'\b(?:open|view|download|attach)\s+{code_lower}\b',  # open PDF
        rf'\b{code_lower}\s+(?:portugal|air)\b',  # TAP Portugal airline name
    ]

    for ctx_pattern in regular_word_contexts:
        if re.search(ctx_pattern, text, re.IGNORECASE):
            return True

    return False


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

    # Helper to check for word with boundaries (avoid "san" matching "san francisco")
    def word_in_text(word, text):
        if len(word) < 4:
            return False  # Too short, too many false positives
        # Use word boundary check
        pattern = r'\b' + re.escape(word) + r'\b'
        return bool(re.search(pattern, text, re.IGNORECASE))

    # First check if this code appears as a lowercase word in context
    # If it appears as lowercase (e.g. "buy" in "buy points"), it's NOT an airport
    if _code_appears_as_regular_word(code, text):
        return False

    # Get the friendly name which is usually the city
    friendly = FRIENDLY_NAMES.get(code, '')

    # Check if city name appears in text
    if friendly:
        friendly_lower = friendly.lower()
        # For multi-word city names, check the full name first
        if len(friendly_lower) >= 4 and word_in_text(friendly_lower, text_lower):
            return True
        # Also check just the city part (first word or two) but only if 5+ chars
        city_parts = friendly_lower.split()
        if len(city_parts) >= 2:
            two_word = f"{city_parts[0]} {city_parts[1]}"
            if len(two_word) >= 5 and word_in_text(two_word, text_lower):
                return True
        # Single word city names must be 5+ chars to avoid "san", "los", etc.
        if len(city_parts) == 1 and len(city_parts[0]) >= 5:
            if word_in_text(city_parts[0], text_lower):
                return True

    # Check if airport name from database appears
    if airport_name:
        # Extract likely city name from full airport name
        name_lower = airport_name.lower()
        # Common patterns: "City International", "City Municipal", etc.
        for word in name_lower.split():
            if len(word) >= 5 and word not in ('international', 'airport', 'regional', 'municipal', 'memorial', 'county', 'field'):
                if word_in_text(word, text_lower):
                    return True

    # Check our city-to-airport mapping in reverse
    for city, mapped_code in CITY_TO_AIRPORT.items():
        if mapped_code == code and len(city) >= 5:  # Require 5+ chars
            if word_in_text(city, text_lower):
                return True

    return False


def extract_airports_from_text(text, from_addr=None, verify_online=False, is_confirmed_flight_email=False):
    """Extract and validate airport codes from text.

    SMART extraction with airline cross-validation:
    1. Flight verification via FlightAware (if enabled and flight number found)
    2. City (CODE) format - highest confidence, always accepted
    3. CODE → CODE route format - high confidence, always accepted
    4. Airline hub airports when airline is identified
    5. Contextual patterns - accepted if strong flight evidence OR city verified
    6. City name recognition (Boston to Las Vegas)

    Strong flight evidence = airline detected + (flight number OR confirmation pattern)
    OR is_confirmed_flight_email=True (email already passed flight detection)
    With strong evidence, we trust airport codes even without city name verification.

    Args:
        text: Email text to search
        from_addr: Optional sender address for airline detection
        verify_online: If True, verify flights via FlightAware (slower but more accurate)
        is_confirmed_flight_email: If True, we already know this is a flight confirmation email
                                   (passed is_flight_email check), so be more aggressive

    Returns:
        List of tuples: [(code, name, confidence), ...]
    """
    airports = []
    seen = set()

    logger.debug(f"    extract_airports_from_text: from_addr='{from_addr}', is_confirmed={is_confirmed_flight_email}")
    logger.debug(f"    Text preview (first 200 chars): {text[:200] if text else 'EMPTY'}...")

    # First, identify the airline - this helps validate airport codes
    airline = extract_airline_from_text(text, from_addr)
    flight_numbers = extract_flight_numbers(text)
    logger.debug(f"    Detected airline: {airline}, flight_numbers: {flight_numbers}")

    # If we found flight numbers, use the airline from those
    if flight_numbers and not airline:
        airline = flight_numbers[0][2]  # airline name from first flight number
        logger.debug(f"    Using airline from flight number: {airline}")

    # Determine if we have strong flight evidence
    # Strong evidence means we're confident this is a real flight email
    text_lower = text.lower()
    has_confirmation_pattern = bool(re.search(
        r'(?:confirmation|booking|reservation|itinerary|pnr|record.?locator)[:\s#]+[A-Z0-9]{5,8}',
        text, re.IGNORECASE
    ))
    has_flight_number = len(flight_numbers) > 0
    has_airline = airline is not None
    logger.debug(f"    Evidence: airline={has_airline}, flight_num={has_flight_number}, confirmation={has_confirmation_pattern}")

    # Strong evidence: airline + (flight number OR confirmation)
    # OR we already know this is a confirmed flight email (passed is_flight_email check)
    strong_flight_evidence = is_confirmed_flight_email or (has_airline and (has_flight_number or has_confirmation_pattern))
    logger.debug(f"    Strong flight evidence: {strong_flight_evidence}")

    # Strategy 0: If we have a flight number and online verification is enabled,
    # try to get the actual route from FlightAware
    if verify_online and flight_numbers:
        for airline_code, flight_num, airline_name in flight_numbers[:1]:  # Just check first flight
            verified = verify_flight_exists(airline_code, flight_num)
            if verified and verified.get('origin') and verified.get('dest'):
                origin_code = verified['origin']
                dest_code = verified['dest']

                # Add verified airports
                for code in [origin_code, dest_code]:
                    if code not in seen:
                        is_valid, name = validate_airport_code(code)
                        if is_valid:
                            seen.add(code)
                            airports.append((code, name, 'flight_verified'))

                # If we got both airports from flight verification, we're done
                if len(airports) >= 2:
                    return airports

    # Strategy 1: "City Name (ABC)" format - highest confidence
    # This is explicit: "Los Angeles (LAX)" - always trust these
    # But we must verify the city name actually corresponds to the airport code
    # to avoid matching garbage like "Personal Effects Coverage (PEC)"
    city_code_pattern = re.compile(r'([A-Za-z][A-Za-z\s]{2,30})\s*\(([A-Z]{3})\)')
    city_matches = city_code_pattern.findall(text)
    logger.debug(f"    Strategy 1 (City Name (ABC)): found {len(city_matches)} matches: {city_matches[:5]}")
    for city, code in city_matches:
        code = code.upper()
        city_clean = city.strip().lower()

        # Verify this city name actually maps to this airport code
        # This prevents "Personal Effects Coverage (PEC)" from being accepted
        expected_code = city_to_airport_code(city_clean)
        if expected_code != code:
            # City name doesn't map to this code - skip it
            # But check if the airport name contains this city
            is_valid, name = validate_airport_code(code)
            if is_valid and name:
                # Check if the text before the code is actually a city/airport name
                name_words = name.lower().split()
                city_words = city_clean.split()
                # Must have at least one significant word match (4+ chars)
                has_match = False
                for cw in city_words:
                    if len(cw) >= 4:
                        for nw in name_words:
                            if len(nw) >= 4 and (cw in nw or nw in cw):
                                has_match = True
                                break
                if has_match and code not in seen:
                    seen.add(code)
                    airports.append((code, name, 'high'))
                    logger.debug(f"      -> Added {code} from 'City (CODE)' pattern (name match)")
            continue

        # City name directly maps to code - definitely valid
        is_valid, name = validate_airport_code(code)
        if is_valid and code not in seen:
            seen.add(code)
            airports.append((code, name, 'high'))
            logger.debug(f"      -> Added {code} from 'City (CODE)' pattern (direct city match)")

    # Strategy 2: Route patterns "ABC → DEF" or "BOS to LAX"
    # Clear route format - high confidence
    # The "to" pattern must use uppercase only to avoid matching "due to the"
    route_patterns = [
        re.compile(r'\b([A-Z]{3})\s*(?:→|->|►|–|—)\s*([A-Z]{3})\b'),
        re.compile(r'\b([A-Z]{3})\s+to\s+([A-Z]{3})\b'),  # No IGNORECASE - both must be uppercase
    ]
    for i, pattern in enumerate(route_patterns):
        matches = pattern.findall(text)
        if matches:
            logger.debug(f"    Strategy 2 (Route pattern {i+1}): found {matches}")
        for origin, dest in matches:
            origin_up = origin.upper()
            dest_up = dest.upper()
            # Both must be valid airport codes
            origin_valid, origin_name = validate_airport_code(origin_up)
            dest_valid, dest_name = validate_airport_code(dest_up)
            if origin_valid and dest_valid:
                if origin_up not in seen:
                    seen.add(origin_up)
                    airports.append((origin_up, origin_name, 'high'))
                    logger.debug(f"      -> Added {origin_up} from route pattern")
                if dest_up not in seen:
                    seen.add(dest_up)
                    airports.append((dest_up, dest_name, 'high'))
                    logger.debug(f"      -> Added {dest_up} from route pattern")

    # Strategy 3: If we know the airline, look for their hub airports
    # This is strong evidence - if Delta email mentions ATL, it's almost certainly correct
    if airline and len(airports) < 2:
        airline_hubs = AIRLINE_HUBS.get(airline, set())
        logger.debug(f"    Strategy 3 (Airline hubs): airline={airline}, hubs={airline_hubs}")
        if airline_hubs:
            # Find 3-letter codes that match airline hubs
            all_codes = re.findall(r'\b([A-Z]{3})\b', text)
            logger.debug(f"      All 3-letter codes in text: {list(set(all_codes))[:20]}")
            for code in all_codes:
                code = code.upper()
                if code in seen:
                    continue
                if code in airline_hubs:
                    is_valid, name = validate_airport_code(code)
                    if is_valid:
                        seen.add(code)
                        airports.append((code, name, 'airline_hub'))
                        logger.debug(f"      -> Added {code} as airline hub")

    # Strategy 4: Contextual patterns
    # Accept if: strong flight evidence OR city name verified OR airline hub
    # BUT: Always reject if the code is used as a regular English word in the text
    context_pattern = re.compile(
        r'(?:depart(?:ure|ing|s)?|arriv(?:al|ing|es)?|from|to|origin|destination|airport)[:\s]+([A-Z]{3})\b',
        re.IGNORECASE
    )
    context_matches = list(context_pattern.finditer(text))
    logger.debug(f"    Strategy 4 (Context patterns): found {len(context_matches)} matches")
    for match in context_matches:
        code = match.group(1).upper()
        if code in seen:
            continue
        is_valid, name = validate_airport_code(code)
        if is_valid:
            # First check: reject if this code appears as a regular English word
            is_regular_word = _code_appears_as_regular_word(code, text)
            if is_regular_word:
                logger.debug(f"      Code {code}: rejected - appears as regular word in text")
                continue

            # Accept if: strong flight evidence OR city name in text OR airline hub
            city_verified = _verify_airport_in_context(code, name, text)
            airline_verified = validate_airport_for_airline(code, airline) in ('hub', 'served')
            logger.debug(f"      Code {code}: valid={is_valid}, city_verified={city_verified}, airline_verified={airline_verified}, strong_evidence={strong_flight_evidence}")

            if strong_flight_evidence or city_verified or airline_verified:
                seen.add(code)
                if strong_flight_evidence:
                    confidence = 'flight_context'
                elif airline_verified:
                    confidence = 'airline_verified'
                else:
                    confidence = 'verified'
                airports.append((code, name, confidence))
                logger.debug(f"      -> Added {code} with confidence '{confidence}'")
            else:
                logger.debug(f"      -> Rejected {code} - no verification passed")

    # Strategy 5: Look for 3-letter codes near flight keywords
    # Accept if: strong flight evidence OR city verified OR airline hub
    # BUT: Always reject if the code is used as a regular English word in the text
    logger.debug(f"    Strategy 5 (Near keywords): airports so far = {len(airports)}")
    if len(airports) < 2:
        flight_keywords = [
            'flight', 'airport', 'terminal', 'gate', 'boarding',
            'depart', 'arrive', 'itinerary', 'confirmation'
        ]

        # Find all 3-letter uppercase sequences
        all_codes = re.findall(r'\b([A-Z]{3})\b', text)
        valid_codes = [c for c in set(all_codes) if validate_airport_code(c)[0]]
        logger.debug(f"      Valid 3-letter codes in text: {valid_codes[:15]}")

        for code in all_codes:
            code = code.upper()
            if code in seen:
                continue

            is_valid, name = validate_airport_code(code)
            if not is_valid:
                continue

            # First check: reject if this code appears as a regular English word
            if _code_appears_as_regular_word(code, text):
                continue

            # Must be near flight keywords
            code_positions = [m.start() for m in re.finditer(r'\b' + code + r'\b', text)]
            near_keywords = False
            for pos in code_positions:
                context_start = max(0, pos - 80)
                context_end = min(len(text_lower), pos + 80)
                context = text_lower[context_start:context_end]
                if any(kw in context for kw in flight_keywords):
                    near_keywords = True
                    break

            if not near_keywords:
                continue

            # Accept if: strong flight evidence OR city verified OR airline hub
            city_verified = _verify_airport_in_context(code, name, text)
            airline_verified = validate_airport_for_airline(code, airline) in ('hub', 'served')

            if strong_flight_evidence or city_verified or airline_verified:
                seen.add(code)
                airports.append((code, name, 'keyword_context'))
                logger.debug(f"      -> Added {code} near keywords")
                if len(airports) >= 2:
                    break

    # Strategy 6: City name recognition (e.g., "Boston to Las Vegas")
    # This is reliable because we're matching actual city names
    logger.debug(f"    Strategy 6 (City names): airports so far = {len(airports)}")
    if len(airports) < 2:
        airports = _extract_airports_from_city_names(text, airports, seen)

    logger.debug(f"    Final airports: {airports}")
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
        flight_keywords = ['flight', 'flying', 'trip', 'travel', 'arriving', 'departing', 'destination', 'check', 'expect']
        text_lower = text.lower()

        # Check all city names (sorted by length to prefer specific matches)
        for city_name in city_names:
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

def extract_flight_info(body, email_date=None, html_body=None, from_addr=None, subject=None):
    """Extract all flight information from an email.

    Uses a tiered approach:
    1. Schema.org structured data (most reliable)
    2. Native Python parsing with dateutil
    3. Regex patterns as fallback

    Args:
        body: Email body text
        email_date: datetime when email was sent
        html_body: Raw HTML body (for schema.org extraction)
        from_addr: Sender email address (helps with airline detection)
        subject: Email subject line

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

    logger.debug(f"extract_flight_info called: subject='{subject[:50] if subject else None}...', from='{from_addr}'")

    if not body:
        logger.debug("  -> No body, returning empty info")
        return info

    # Try schema.org first (most reliable)
    schema_info = extract_schema_org_flights(html_body or body)
    if schema_info:
        logger.debug(f"  -> Schema.org found: {schema_info}")
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
            logger.debug(f"  -> Using schema.org data, returning: {info}")
            return info
    else:
        logger.debug("  -> No schema.org data found")

    # Strip HTML for text parsing
    # Use HTML body if plain text body is empty/short
    text = strip_html_tags(body)
    text_len = len(text)
    logger.debug(f"  -> Stripped text length: {text_len} chars")

    # If plain text body is too short, try extracting text from HTML body
    if text_len < 100 and html_body:
        html_text = strip_html_tags(html_body)
        if len(html_text) > text_len:
            text = html_text
            text_len = len(text)
            logger.debug(f"  -> Using HTML body instead, length: {text_len} chars")

    # Include subject in text for pattern matching
    if subject:
        text = subject + " " + text
        logger.debug(f"  -> Added subject to text")

    # Extract airports - pass from_addr to help with airline detection
    # Set is_confirmed_flight_email=True since we know this is a flight email
    if not info["airports"]:
        logger.debug(f"  -> Calling extract_airports_from_text...")
        airport_results = extract_airports_from_text(text, from_addr=from_addr, is_confirmed_flight_email=True)
        logger.debug(f"  -> Airport results: {airport_results}")
        info["airports"] = [code for code, name, conf in airport_results][:4]

        # Set route if we have exactly 2 airports
        if len(info["airports"]) >= 2 and not info["route"]:
            info["route"] = (info["airports"][0], info["airports"][1])

    # Extract flight numbers using the comprehensive function from airlines.py
    if not info["flight_numbers"]:
        flight_nums = extract_flight_numbers(text)
        logger.debug(f"  -> Flight numbers found: {flight_nums}")
        # Format as "AA123" strings
        for airline_code, flight_num, airline_name in flight_nums:
            formatted = f"{airline_code}{flight_num}"
            if formatted not in info["flight_numbers"]:
                info["flight_numbers"].append(formatted)
        info["flight_numbers"] = info["flight_numbers"][:4]

    # Extract dates
    if not info["dates"]:
        info["dates"] = extract_dates_from_text(text, email_date)
        logger.debug(f"  -> Dates found: {info['dates']}")

    # Extract times
    if not info["times"]:
        time_pattern = re.compile(r'\b(\d{1,2}:\d{2}\s*[AaPp][Mm])\b')
        times = time_pattern.findall(text)
        info["times"] = list(dict.fromkeys(times))[:4]

    logger.debug(f"  -> Final info: airports={info['airports']}, route={info['route']}, flights={info['flight_numbers']}, dates={info['dates']}")
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
