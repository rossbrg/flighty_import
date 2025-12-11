"""
Airline and booking site patterns for detecting flight confirmation emails.

Also includes airline hub/focus city data for validating airport codes.
"""

import re

# Airline IATA codes (2-letter) for flight number extraction
AIRLINE_CODES = {
    # US Airlines
    'AA': 'American Airlines',
    'DL': 'Delta',
    'UA': 'United',
    'WN': 'Southwest',
    'B6': 'JetBlue',
    'AS': 'Alaska Airlines',
    'NK': 'Spirit',
    'F9': 'Frontier',
    'HA': 'Hawaiian Airlines',
    'G4': 'Allegiant',
    'SY': 'Sun Country',
    'MX': 'Breeze Airways',
    # Canada
    'AC': 'Air Canada',
    'WS': 'WestJet',
    # Europe
    'BA': 'British Airways',
    'LH': 'Lufthansa',
    'AF': 'Air France',
    'KL': 'KLM',
    'VS': 'Virgin Atlantic',
    'IB': 'Iberia',
    'AZ': 'ITA Airways',
    'SK': 'SAS',
    'AY': 'Finnair',
    'LX': 'Swiss',
    'OS': 'Austrian',
    'TP': 'TAP Portugal',
    'EI': 'Aer Lingus',
    'FR': 'Ryanair',
    'U2': 'easyJet',
    'FI': 'Icelandair',
    'DY': 'Norwegian',
    # Middle East
    'EK': 'Emirates',
    'EY': 'Etihad',
    'QR': 'Qatar Airways',
    'TK': 'Turkish Airlines',
    'SV': 'Saudia',
    'GF': 'Gulf Air',
    'WY': 'Oman Air',
    # Asia
    'CX': 'Cathay Pacific',
    'SQ': 'Singapore Airlines',
    'JL': 'Japan Airlines',
    'NH': 'ANA',
    'KE': 'Korean Air',
    'OZ': 'Asiana',
    'TG': 'Thai Airways',
    'MH': 'Malaysia Airlines',
    'CI': 'China Airlines',
    'BR': 'EVA Air',
    'CA': 'Air China',
    'MU': 'China Eastern',
    'CZ': 'China Southern',
    'VN': 'Vietnam Airlines',
    'GA': 'Garuda',
    'PR': 'Philippine Airlines',
    'AK': 'AirAsia',
    # Australia/Pacific
    'QF': 'Qantas',
    'VA': 'Virgin Australia',
    'NZ': 'Air New Zealand',
    'FJ': 'Fiji Airways',
    # Latin America
    'AM': 'Aeromexico',
    'AV': 'Avianca',
    'LA': 'LATAM',
    'CM': 'Copa',
    'AD': 'Azul',
    'G3': 'GOL',
    'Y4': 'Volaris',
}

# Airline hubs and focus cities - airports where each airline has significant operations
# This helps validate that an airport code makes sense for a given airline
AIRLINE_HUBS = {
    # US Airlines
    'American Airlines': {'DFW', 'CLT', 'MIA', 'ORD', 'PHX', 'PHL', 'LAX', 'JFK', 'DCA', 'LGA'},
    'Delta': {'ATL', 'MSP', 'DTW', 'SLC', 'SEA', 'LAX', 'JFK', 'LGA', 'BOS', 'AUS'},
    'United': {'ORD', 'DEN', 'IAH', 'EWR', 'SFO', 'LAX', 'IAD', 'GUM'},
    'Southwest': {'DAL', 'HOU', 'LAS', 'PHX', 'DEN', 'MDW', 'BWI', 'OAK', 'LAX', 'SAN'},
    'JetBlue': {'JFK', 'BOS', 'FLL', 'MCO', 'LAX', 'LGB', 'SJU', 'EWR', 'TPA'},
    'Alaska Airlines': {'SEA', 'PDX', 'SFO', 'LAX', 'ANC', 'SAN'},
    'Spirit': {'FLL', 'LAS', 'MCO', 'ORD', 'DFW', 'ATL', 'LAX'},
    'Frontier': {'DEN', 'LAS', 'ORD', 'MCO', 'PHX', 'ATL'},
    'Hawaiian Airlines': {'HNL', 'OGG', 'LIH', 'KOA', 'LAX', 'SFO', 'SEA'},
    # Canada
    'Air Canada': {'YYZ', 'YVR', 'YUL', 'YYC', 'YEG'},
    'WestJet': {'YYC', 'YYZ', 'YVR', 'YWG', 'YEG'},
    # Europe
    'British Airways': {'LHR', 'LGW', 'JFK', 'BOS', 'MIA'},
    'Lufthansa': {'FRA', 'MUC', 'JFK', 'ORD', 'LAX'},
    'Air France': {'CDG', 'ORY', 'JFK', 'LAX', 'MIA'},
    'KLM': {'AMS', 'JFK', 'ATL', 'LAX', 'SFO'},
    'Virgin Atlantic': {'LHR', 'MAN', 'JFK', 'LAX', 'SFO', 'BOS', 'MIA', 'ATL'},
    # Middle East
    'Emirates': {'DXB', 'JFK', 'LAX', 'SFO', 'ORD', 'BOS', 'IAD', 'IAH', 'DFW', 'SEA', 'MIA'},
    'Etihad': {'AUH', 'JFK', 'ORD', 'LAX', 'IAD'},
    'Qatar Airways': {'DOH', 'JFK', 'ORD', 'LAX', 'IAH', 'MIA', 'ATL', 'BOS', 'DFW', 'IAD', 'PHL', 'SEA'},
    'Turkish Airlines': {'IST', 'JFK', 'ORD', 'LAX', 'SFO', 'MIA', 'IAH', 'IAD', 'ATL', 'BOS'},
    # Asia
    'Cathay Pacific': {'HKG', 'JFK', 'LAX', 'SFO', 'ORD', 'BOS'},
    'Singapore Airlines': {'SIN', 'JFK', 'LAX', 'SFO', 'IAH', 'EWR', 'SEA'},
    'Japan Airlines': {'NRT', 'HND', 'JFK', 'LAX', 'SFO', 'ORD', 'DFW', 'BOS', 'SEA'},
    'ANA': {'NRT', 'HND', 'JFK', 'LAX', 'SFO', 'ORD', 'IAH', 'IAD', 'SEA'},
    'Korean Air': {'ICN', 'JFK', 'LAX', 'SFO', 'ATL', 'IAD', 'ORD', 'SEA', 'DFW', 'LAS'},
    # Australia
    'Qantas': {'SYD', 'MEL', 'BNE', 'LAX', 'SFO', 'DFW', 'JFK'},
}

# Mapping from airline name variations to standard name
AIRLINE_NAME_VARIATIONS = {
    'jetblue': 'JetBlue',
    'jet blue': 'JetBlue',
    'delta': 'Delta',
    'delta air lines': 'Delta',
    'united': 'United',
    'united airlines': 'United',
    'american': 'American Airlines',
    'american airlines': 'American Airlines',
    'southwest': 'Southwest',
    'southwest airlines': 'Southwest',
    'alaska': 'Alaska Airlines',
    'alaska airlines': 'Alaska Airlines',
    'spirit': 'Spirit',
    'spirit airlines': 'Spirit',
    'frontier': 'Frontier',
    'frontier airlines': 'Frontier',
    'hawaiian': 'Hawaiian Airlines',
    'hawaiian airlines': 'Hawaiian Airlines',
    'air canada': 'Air Canada',
    'british airways': 'British Airways',
    'lufthansa': 'Lufthansa',
    'emirates': 'Emirates',
    'qatar': 'Qatar Airways',
    'qatar airways': 'Qatar Airways',
    'singapore': 'Singapore Airlines',
    'singapore airlines': 'Singapore Airlines',
    'cathay': 'Cathay Pacific',
    'cathay pacific': 'Cathay Pacific',
    'qantas': 'Qantas',
    'virgin atlantic': 'Virgin Atlantic',
    'air france': 'Air France',
    'klm': 'KLM',
}

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


def extract_airline_from_text(text, from_addr=None):
    """Extract airline name from email text and sender.

    Args:
        text: Email body text
        from_addr: Optional sender address

    Returns:
        Standardized airline name or None
    """
    text_lower = (text or '').lower()
    from_lower = (from_addr or '').lower()

    # Check sender first (most reliable)
    for variation, standard_name in AIRLINE_NAME_VARIATIONS.items():
        if variation in from_lower:
            return standard_name

    # Check text for airline names
    for variation, standard_name in AIRLINE_NAME_VARIATIONS.items():
        if variation in text_lower:
            return standard_name

    return None


def extract_flight_numbers(text):
    """Extract flight numbers from email text.

    Returns list of tuples: [(airline_code, flight_num, airline_name), ...]
    """
    flight_numbers = []
    seen = set()

    # Pattern 1: Standard format "AA 123" or "AA123"
    pattern1 = re.compile(r'\b([A-Z]{2})\s*(\d{1,4})\b')
    for match in pattern1.finditer(text):
        code = match.group(1).upper()
        num = match.group(2)
        key = f"{code}{num}"
        if code in AIRLINE_CODES and key not in seen:
            seen.add(key)
            flight_numbers.append((code, num, AIRLINE_CODES[code]))

    # Pattern 2: "Flight 123" with airline context nearby
    pattern2 = re.compile(r'[Ff]light\s*#?\s*(\d{1,4})\b')
    for match in pattern2.finditer(text):
        num = match.group(1)
        # Look for airline name near this flight number
        start = max(0, match.start() - 100)
        end = min(len(text), match.end() + 50)
        context = text[start:end].lower()

        for variation, airline_name in AIRLINE_NAME_VARIATIONS.items():
            if variation in context:
                # Try to find the airline code
                for code, name in AIRLINE_CODES.items():
                    if name == airline_name:
                        key = f"{code}{num}"
                        if key not in seen:
                            seen.add(key)
                            flight_numbers.append((code, num, airline_name))
                        break
                break

    return flight_numbers


def validate_airport_for_airline(airport_code, airline_name):
    """Check if an airport makes sense for a given airline.

    Returns:
        - 'hub': Airport is a hub/focus city for this airline (high confidence)
        - 'served': Airline likely serves this airport (medium confidence)
        - 'unknown': No data to confirm or deny
    """
    if not airline_name or not airport_code:
        return 'unknown'

    # Check if it's a hub
    hubs = AIRLINE_HUBS.get(airline_name, set())
    if airport_code in hubs:
        return 'hub'

    # Major airports are served by most airlines
    major_airports = {
        'JFK', 'LAX', 'ORD', 'DFW', 'DEN', 'SFO', 'SEA', 'ATL', 'MIA', 'BOS',
        'EWR', 'IAD', 'IAH', 'PHX', 'LAS', 'MCO', 'CLT', 'MSP', 'DTW', 'PHL',
        'LHR', 'CDG', 'FRA', 'AMS', 'DXB', 'SIN', 'HKG', 'NRT', 'ICN', 'SYD'
    }
    if airport_code in major_airports:
        return 'served'

    return 'unknown'


def get_airline_for_code(airline_code):
    """Get airline name from 2-letter IATA code."""
    return AIRLINE_CODES.get(airline_code.upper())
