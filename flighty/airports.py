"""
Airport codes data and utilities.

Handles loading, validating, and displaying airport codes.
"""

from pathlib import Path

# Data file path (relative to package)
_DATA_DIR = Path(__file__).parent.parent
AIRPORT_CODES_FILE = _DATA_DIR / "airport_codes.txt"

# Common English words that happen to be 3 letters - exclude these
# These cause false positives when parsing email text
EXCLUDED_CODES = {
    # Common words
    'THE', 'AND', 'FOR', 'ARE', 'BUT', 'NOT', 'YOU', 'ALL', 'CAN', 'HAD',
    'HER', 'WAS', 'ONE', 'OUR', 'OUT', 'DAY', 'GET', 'HAS', 'HIM', 'HIS',
    'HOW', 'ITS', 'MAY', 'NEW', 'NOW', 'OLD', 'SEE', 'WAY', 'WHO', 'BOY',
    'DID', 'SAY', 'SHE', 'TOO', 'USE', 'AIR', 'FLY', 'RUN', 'TRY', 'CAR',
    'END', 'PRE', 'PRO', 'VIA', 'PER', 'NET', 'WEB', 'APP', 'API', 'URL',
    'USA', 'CRO', 'CSS', 'PHP', 'SQL', 'XML', 'PDF', 'JPG', 'PNG', 'GIF',
    # More common words that appear in emails
    'ADD', 'AGO', 'ANY', 'ASK', 'BAD', 'BAG', 'BIG', 'BIT', 'BOX', 'BUS',
    'BUY', 'CUT', 'DOC', 'DUE', 'EAT', 'FAR', 'FAX', 'FEW', 'FIT', 'FUN',
    'GOT', 'GUN', 'GUY', 'HOT', 'JOB', 'KEY', 'KID', 'LAW', 'LAY', 'LED',
    'LET', 'LIE', 'LOG', 'LOT', 'LOW', 'MAN', 'MAP', 'MEN', 'MET', 'MIX',
    'MOM', 'NOR', 'ODD', 'OFF', 'OIL', 'PAY', 'PEN', 'PET', 'PIN', 'POP',
    'POT', 'PUT', 'RAW', 'RED', 'REF', 'RID', 'ROW', 'SAT', 'SET', 'SIT',
    'SIX', 'SKY', 'SON', 'SUM', 'SUN', 'TAX', 'TEN', 'TIP', 'TOP', 'TOY',
    'TWO', 'VAN', 'WAR', 'WAS', 'WET', 'WIN', 'WON', 'YES', 'YET', 'ZIP',
    # Email/travel specific words that aren't airports
    'COM', 'ORG', 'EDU', 'GOV', 'MIL', 'BIZ', 'INFO',
    'MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT', 'SUN',  # Days
    'JAN', 'FEB', 'MAR', 'APR', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC',  # Months
}

# Friendly names for major airports (override file names for cleaner display)
FRIENDLY_NAMES = {
    # US Major
    'ATL': 'Atlanta', 'DFW': 'Dallas-Fort Worth', 'DEN': 'Denver', 'ORD': "Chicago O'Hare",
    'LAX': 'Los Angeles', 'JFK': 'New York JFK', 'LAS': 'Las Vegas', 'MCO': 'Orlando',
    'MIA': 'Miami', 'CLT': 'Charlotte', 'SEA': 'Seattle', 'PHX': 'Phoenix',
    'EWR': 'Newark', 'SFO': 'San Francisco', 'IAH': 'Houston', 'BOS': 'Boston',
    'FLL': 'Fort Lauderdale', 'MSP': 'Minneapolis', 'LGA': 'New York LaGuardia', 'DTW': 'Detroit',
    'PHL': 'Philadelphia', 'SLC': 'Salt Lake City', 'DCA': 'Washington Reagan', 'SAN': 'San Diego',
    'BWI': 'Baltimore', 'TPA': 'Tampa', 'AUS': 'Austin', 'IAD': 'Washington Dulles',
    'BNA': 'Nashville', 'MDW': 'Chicago Midway', 'HNL': 'Honolulu', 'DAL': 'Dallas Love Field',
    'PDX': 'Portland', 'STL': 'St. Louis', 'RDU': 'Raleigh-Durham', 'HOU': 'Houston Hobby',
    'OAK': 'Oakland', 'MSY': 'New Orleans', 'SJC': 'San Jose', 'SMF': 'Sacramento',
    'SNA': 'Orange County', 'MCI': 'Kansas City', 'SAT': 'San Antonio', 'CLE': 'Cleveland',
    'IND': 'Indianapolis', 'PIT': 'Pittsburgh', 'CMH': 'Columbus', 'CVG': 'Cincinnati',
    'BDL': 'Hartford', 'JAX': 'Jacksonville', 'OGG': 'Maui', 'ANC': 'Anchorage',
    'BUF': 'Buffalo', 'ABQ': 'Albuquerque', 'ONT': 'Ontario CA', 'OMA': 'Omaha',
    'BUR': 'Burbank', 'PBI': 'West Palm Beach', 'RIC': 'Richmond', 'RSW': 'Fort Myers',
    'SDF': 'Louisville', 'MKE': 'Milwaukee', 'TUS': 'Tucson', 'OKC': 'Oklahoma City',
    'RNO': 'Reno', 'ELP': 'El Paso', 'BOI': 'Boise', 'LIT': 'Little Rock',
    'TUL': 'Tulsa', 'GEG': 'Spokane', 'MVY': "Martha's Vineyard", 'ACK': 'Nantucket',
    # Canada
    'YYZ': 'Toronto', 'YVR': 'Vancouver', 'YUL': 'Montreal', 'YYC': 'Calgary',
    'YEG': 'Edmonton', 'YOW': 'Ottawa', 'YWG': 'Winnipeg', 'YHZ': 'Halifax',
    # Mexico & Caribbean
    'MEX': 'Mexico City', 'CUN': 'Cancun', 'GDL': 'Guadalajara', 'SJD': 'Cabo',
    'PVR': 'Puerto Vallarta', 'SJU': 'San Juan', 'NAS': 'Nassau', 'MBJ': 'Montego Bay',
    'PUJ': 'Punta Cana', 'STT': 'St. Thomas', 'AUA': 'Aruba', 'SXM': 'St. Maarten',
    # Europe
    'LHR': 'London Heathrow', 'LGW': 'London Gatwick', 'CDG': 'Paris', 'AMS': 'Amsterdam',
    'FRA': 'Frankfurt', 'MAD': 'Madrid', 'BCN': 'Barcelona', 'FCO': 'Rome',
    'MUC': 'Munich', 'ZRH': 'Zurich', 'DUB': 'Dublin', 'LIS': 'Lisbon',
    'ATH': 'Athens', 'IST': 'Istanbul', 'PRG': 'Prague',
    # Asia & Middle East
    'HND': 'Tokyo Haneda', 'NRT': 'Tokyo Narita', 'ICN': 'Seoul', 'PEK': 'Beijing',
    'PVG': 'Shanghai', 'HKG': 'Hong Kong', 'SIN': 'Singapore', 'BKK': 'Bangkok',
    'DXB': 'Dubai', 'DOH': 'Doha', 'TLV': 'Tel Aviv',
    # Australia/Pacific
    'SYD': 'Sydney', 'MEL': 'Melbourne', 'AKL': 'Auckland',
    # South America
    'GRU': 'Sao Paulo', 'EZE': 'Buenos Aires', 'BOG': 'Bogota', 'LIM': 'Lima',
}

# Fallback codes if file doesn't exist
_FALLBACK_CODES = {
    'ATL', 'DFW', 'DEN', 'ORD', 'LAX', 'JFK', 'LAS', 'MCO', 'MIA', 'CLT',
    'SEA', 'PHX', 'EWR', 'SFO', 'IAH', 'BOS', 'FLL', 'MSP', 'LGA', 'DTW',
}


def load_airport_codes(codes_file=None):
    """Load valid airport codes and names from file.

    Args:
        codes_file: Path to airport codes file. Defaults to airport_codes.txt.

    Returns:
        Tuple of (codes set, names dict)
    """
    if codes_file is None:
        codes_file = AIRPORT_CODES_FILE

    codes = set()
    names = {}

    try:
        if Path(codes_file).exists():
            with open(codes_file, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    try:
                        line = line.strip()
                        if ',' in line:
                            parts = line.split(',', 1)
                            code = parts[0].strip().upper()
                            name = parts[1].strip() if len(parts) > 1 else ""
                            if len(code) == 3 and code.isalpha():
                                codes.add(code)
                                if name:
                                    names[code] = name
                    except Exception:
                        continue
    except Exception:
        pass

    # Fallback to common codes if file doesn't exist
    if not codes:
        codes = _FALLBACK_CODES.copy()

    return codes, names


def _initialize():
    """Initialize module-level data."""
    all_codes, names_from_file = load_airport_codes()
    valid_codes = all_codes - EXCLUDED_CODES
    # Merge names: use friendly names first, then file names
    all_names = {**names_from_file, **FRIENDLY_NAMES}
    return all_codes, valid_codes, all_names


# Module-level initialized data
ALL_AIRPORT_CODES, VALID_AIRPORT_CODES, AIRPORT_NAMES = _initialize()


def get_airport_display(code):
    """Get display string for airport code.

    Args:
        code: 3-letter IATA airport code

    Returns:
        Formatted string like "JFK (New York JFK)" or just "XYZ" if unknown
    """
    name = AIRPORT_NAMES.get(code, "")
    if name:
        # Shorten long airport names
        short_name = name.replace(" International Airport", "").replace(" Airport", "")
        short_name = short_name.replace(" International", "").replace(" Regional", "")
        # Truncate if still too long
        if len(short_name) > 25:
            short_name = short_name[:22] + "..."
        return f"{code} ({short_name})"
    return code


def is_valid_airport(code):
    """Check if a code is a valid airport code (not an excluded word)."""
    return code in VALID_AIRPORT_CODES
