"""
Email Scoring System for Flight Detection

Borrowed from Google Apps Script flight classifier.
Assigns weighted scores to emails based on flight indicators.
"""

import re
from typing import Tuple, List

from .airports import VALID_AIRPORT_CODES
from .airlines import AIRLINE_CODES

# Score weights (from Apps Script)
SCORE_WEIGHTS = {
    'airports_multiple': 30,   # 2+ airport codes
    'airports_single': 10,     # 1 airport code
    'flight_number': 25,       # Flight number detected
    'pnr': 15,                 # Valid PNR/confirmation code
    'flight_phrase': 10,       # Each flight-related phrase
    'airline_domain': 20,      # From airline domain
    'hotel_domain': -30,       # From hotel/accommodation domain
    'accommodation_text': -30, # Accommodation keywords in body
    'spam_phrase': -15,        # Each spam phrase
    'survey': -20,             # Survey keywords
}

# High-signal phrases (weighted +10 each)
FLIGHT_PHRASES = [
    'boarding pass', 'e-ticket', 'eticket', 'itinerary', 'flight confirmation',
    'booking confirmation', 'reservation confirmation', 'check-in', 'check in online',
    'confirmation code', 'confirmation number', 'record locator', 'departure', 'arrival',
    'terminal', 'gate', 'seat assignment', 'baggage', 'carry-on', 'checked bag',
    'reservation number', 'booking reference',
]

# Hotel/Accommodation senders to penalize
HOTEL_DOMAINS = [
    'airbnb.com', 'vrbo.com', 'booking.com', 'hotels.com', 'marriott.com',
    'hilton.com', 'hyatt.com', 'ihg.com', 'wyndham.com', 'choicehotels.com',
    'expedia.com/hotels', 'trivago.com', 'hostelworld.com',
]

# Spam phrases to penalize
SPAM_PHRASES = [
    'earn miles', 'bonus miles', 'limited time', 'act now', 'special offer',
    'unsubscribe', 'email preferences', 'promotional', 'sale ends', 'book now and save',
    'exclusive experiences', 'reach status', 'make an impact',
    'get more with', 'discover exclusive', 'your benefits',
]

# Airline domains (positive signal)
AIRLINE_SENDER_DOMAINS = [
    'aa.com', 'united.com', 'delta.com', 'southwest.com', 'jetblue.com',
    'alaskaair.com', 'frontier.com', 'spirit.com', 'hawaiianairlines.com',
    'easyjet.com', 'ryanair.com', 'britishairways.com', 'lufthansa.com',
    'airfrance.com', 'klm.com', 'emirates.com', 'qatarairways.com',
    # Booking sites (count as positive)
    'chasetravel.com', 'expedia.com', 'kayak.com', 'priceline.com',
]


def _find_airports(text: str) -> List[str]:
    """Find IATA airport codes in text."""
    found = []
    text_upper = text.upper()

    # Use word boundaries to find 3-letter codes
    for match in re.finditer(r'\b([A-Z]{3})\b', text_upper):
        code = match.group(1)
        if code in VALID_AIRPORT_CODES:
            if code not in found:
                found.append(code)

    return found


def _find_flight_numbers(text: str) -> List[str]:
    """Find flight numbers in text (e.g., AA123, DL456)."""
    found = []
    text_upper = text.upper()

    # Pattern: 2-letter airline code + 1-4 digits
    pattern = re.compile(r'\b([A-Z][A-Z0-9])\s?(\d{1,4})\b')

    for match in pattern.finditer(text_upper):
        code = match.group(1)
        num = match.group(2)

        # Validate airline code
        if code in AIRLINE_CODES:
            flight_num = f"{code}{num}"
            if flight_num not in found:
                found.append(flight_num)

    return found


def _find_pnr(text: str) -> str:
    """Find potential PNR/confirmation code."""
    # Import here to avoid circular dependency
    from .parser import is_valid_pnr

    text_upper = text.upper()

    # Look for 6-character alphanumeric codes
    for match in re.finditer(r'\b([A-Z0-9]{6})\b', text_upper):
        code = match.group(1)
        if is_valid_pnr(code):
            return code

    return None


def score_email(subject: str, body: str, from_addr: str) -> Tuple[int, List[str]]:
    """Score an email for flight content likelihood.

    Args:
        subject: Email subject line
        body: Email body text (plain text, first 5000 chars recommended)
        from_addr: Sender email address

    Returns:
        Tuple of (score, reasons) where score is numeric and reasons is list of strings
    """
    score = 0
    reasons = []

    text = f"{subject} {body}".upper()
    text_lower = f"{subject} {body}".lower()
    from_lower = (from_addr or "").lower()

    # Check for airport codes
    airports = _find_airports(text)
    if len(airports) >= 2:
        score += SCORE_WEIGHTS['airports_multiple']
        reasons.append(f"Airports: {', '.join(airports[:4])}")
    elif len(airports) == 1:
        score += SCORE_WEIGHTS['airports_single']
        reasons.append(f"Airport: {airports[0]}")

    # Check for flight numbers
    flight_numbers = _find_flight_numbers(text)
    if flight_numbers:
        score += SCORE_WEIGHTS['flight_number']
        reasons.append(f"Flight#: {', '.join(flight_numbers[:3])}")

    # Check for PNR
    pnr = _find_pnr(text)
    if pnr:
        score += SCORE_WEIGHTS['pnr']
        reasons.append(f"PNR: {pnr}")

    # Check for flight phrases (limit to avoid over-counting)
    phrase_count = 0
    for phrase in FLIGHT_PHRASES:
        if phrase in text_lower:
            score += SCORE_WEIGHTS['flight_phrase']
            if phrase_count < 3:
                reasons.append(f'"{phrase}"')
            phrase_count += 1
            if phrase_count >= 5:  # Cap at 5 phrases
                break

    # POSITIVE: Airline sender domain
    for domain in AIRLINE_SENDER_DOMAINS:
        if domain in from_lower:
            score += SCORE_WEIGHTS['airline_domain']
            reasons.append(f"From: {domain}")
            break

    # NEGATIVE: Hotel/accommodation sender
    for domain in HOTEL_DOMAINS:
        if domain in from_lower:
            score += SCORE_WEIGHTS['hotel_domain']
            reasons.append(f"HOTEL: {domain}")
            break

    # NEGATIVE: Accommodation keywords in body
    accommodation_keywords = ['airbnb', "where you're staying", 'your stay', 'your host', 'check-in time']
    for keyword in accommodation_keywords:
        if keyword in text_lower:
            score += SCORE_WEIGHTS['accommodation_text']
            reasons.append('ACCOMMODATION')
            break

    # NEGATIVE: Spam phrases
    spam_count = 0
    for phrase in SPAM_PHRASES:
        if phrase in text_lower:
            score += SCORE_WEIGHTS['spam_phrase']
            if spam_count < 2:
                reasons.append(f'SPAM: "{phrase}"')
            spam_count += 1
            if spam_count >= 3:  # Cap spam penalty
                break

    # NEGATIVE: Survey emails
    survey_keywords = ['survey', 'how was your flight', 'rate your experience', 'feedback']
    for keyword in survey_keywords:
        if keyword in text_lower or keyword in from_lower:
            score += SCORE_WEIGHTS['survey']
            reasons.append('SURVEY')
            break

    return score, reasons


def passes_score_threshold(subject: str, body: str, from_addr: str, threshold: int = 50) -> Tuple[bool, int, List[str]]:
    """Check if email passes the score threshold.

    Args:
        subject: Email subject line
        body: Email body text
        from_addr: Sender email address
        threshold: Minimum score to pass (default 50)

    Returns:
        Tuple of (passes, score, reasons)
    """
    score, reasons = score_email(subject, body, from_addr)
    return score >= threshold, score, reasons
