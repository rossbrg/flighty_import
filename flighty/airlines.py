"""
Airline and booking site patterns for detecting flight confirmation emails.
"""

import re

# Airline and booking site patterns to detect flight confirmation emails
AIRLINE_PATTERNS = [
    # Major US Airlines
    {
        "name": "JetBlue",
        "from_patterns": [r"jetblue", r"@.*jetblue\.com"],
        "subject_patterns": [r"booking confirmation", r"itinerary", r"flight confirmation", r"confirmation"],
    },
    {
        "name": "Delta",
        "from_patterns": [r"delta", r"@.*delta\.com"],
        "subject_patterns": [r"ereceipt", r"trip confirmation", r"itinerary", r"booking confirmation", r"confirmation"],
    },
    {
        "name": "United",
        "from_patterns": [r"united", r"@.*united\.com"],
        "subject_patterns": [r"confirmation", r"itinerary", r"trip details"],
    },
    {
        "name": "American Airlines",
        "from_patterns": [r"american", r"@.*aa\.com", r"americanairlines"],
        "subject_patterns": [r"reservation", r"confirmation", r"itinerary"],
    },
    {
        "name": "Southwest",
        "from_patterns": [r"southwest", r"@.*southwest\.com"],
        "subject_patterns": [r"confirmation", r"itinerary", r"trip", r"flight"],
    },
    {
        "name": "Alaska Airlines",
        "from_patterns": [r"alaska", r"@.*alaskaair\.com"],
        "subject_patterns": [r"confirmation", r"itinerary"],
    },
    {
        "name": "Spirit",
        "from_patterns": [r"spirit", r"@.*spirit\.com"],
        "subject_patterns": [r"confirmation", r"itinerary"],
    },
    {
        "name": "Frontier",
        "from_patterns": [r"frontier", r"@.*flyfrontier\.com"],
        "subject_patterns": [r"confirmation", r"itinerary"],
    },
    {
        "name": "Hawaiian Airlines",
        "from_patterns": [r"hawaiian", r"@.*hawaiianairlines\.com"],
        "subject_patterns": [r"confirmation", r"itinerary"],
    },
    # International Airlines
    {
        "name": "Air Canada",
        "from_patterns": [r"aircanada", r"@.*aircanada\.com"],
        "subject_patterns": [r"confirmation", r"itinerary"],
    },
    {
        "name": "British Airways",
        "from_patterns": [r"british", r"@.*britishairways\.com", r"@.*ba\.com"],
        "subject_patterns": [r"confirmation", r"booking", r"itinerary"],
    },
    {
        "name": "Lufthansa",
        "from_patterns": [r"lufthansa", r"@.*lufthansa\.com"],
        "subject_patterns": [r"confirmation", r"booking"],
    },
    {
        "name": "Emirates",
        "from_patterns": [r"emirates", r"@.*emirates\.com"],
        "subject_patterns": [r"confirmation", r"booking", r"itinerary"],
    },
    {
        "name": "International Airline",
        "from_patterns": [r"airfrance|klm|qantas|singapore|cathay|jal|ana|korean|turkish|qatar|etihad|virgin|icelandair|norwegian|ryanair|easyjet|westjet|avianca|latam|aeromexico|copa"],
        "subject_patterns": [r"confirmation", r"booking", r"itinerary", r"e-?ticket"],
    },
    # Booking Sites
    {
        "name": "Expedia",
        "from_patterns": [r"expedia"],
        "subject_patterns": [r"confirmation", r"itinerary", r"trip", r"booking"],
    },
    {
        "name": "Booking Site",
        "from_patterns": [r"kayak|priceline|orbitz|travelocity|cheapoair|hopper|skyscanner|trip\.com|booking\.com"],
        "subject_patterns": [r"confirmation", r"itinerary", r"trip", r"booking", r"flight"],
    },
    {
        "name": "Google Travel",
        "from_patterns": [r"google"],
        "subject_patterns": [r"flight.*confirmation", r"trip.*confirmation", r"itinerary"],
    },
    # Corporate Travel
    {
        "name": "Corporate Travel",
        "from_patterns": [r"concur|egencia|tripactions|navan"],
        "subject_patterns": [r"confirmation", r"itinerary", r"trip", r"travel"],
    },
    # Credit Card Travel
    {
        "name": "Credit Card Travel",
        "from_patterns": [r"chase|americanexpress|capitalone|citi"],
        "subject_patterns": [r"flight.*confirmation", r"trip.*confirmation", r"travel.*confirmation", r"itinerary"],
    },
    # Generic catch-all for any flight-related email
    {
        "name": "Generic Flight",
        "from_patterns": [r".*"],
        "subject_patterns": [
            r"flight.*confirmation",
            r"booking.*confirmation.*flight",
            r"e-?ticket",
            r"itinerary.*flight",
            r"your.*flight",
            r"trip.*confirmation",
        ],
    }
]


def is_flight_email(from_addr, subject):
    """Check if email matches flight confirmation patterns.

    Args:
        from_addr: Email sender address
        subject: Email subject line

    Returns:
        Tuple of (is_match, airline_name) or (False, None)
    """
    from_addr = (from_addr or "").lower()
    subject = (subject or "").lower()

    for pattern in AIRLINE_PATTERNS:
        from_match = any(re.search(p, from_addr, re.IGNORECASE) for p in pattern["from_patterns"])
        subject_match = any(re.search(p, subject, re.IGNORECASE) for p in pattern["subject_patterns"])

        if from_match and subject_match:
            return True, pattern["name"]

    return False, None


def get_airline_name(from_addr, subject):
    """Get the airline name for a flight email.

    Args:
        from_addr: Email sender address
        subject: Email subject line

    Returns:
        Airline name or "Unknown" if not matched
    """
    is_match, name = is_flight_email(from_addr, subject)
    return name if is_match else "Unknown"
