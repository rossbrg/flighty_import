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
    # False positive "airport codes" that appear in emails but aren't real routes
    'NON', 'APY', 'CIA',  # CIA is Rome Ciampino but rarely used in US travel emails
    'AAA', 'BBB', 'CCC', 'DDD', 'EEE', 'FFF', 'GGG', 'HHH', 'III', 'JJJ',
    'KKK', 'LLL', 'MMM', 'NNN', 'OOO', 'PPP', 'QQQ', 'RRR', 'SSS', 'TTT',
    'UUU', 'VVV', 'WWW', 'XXX', 'YYY', 'ZZZ',  # Repeated letters not real codes
    # Obscure airports that cause false positives in email parsing
    'PEC',  # Pelican SPB - tiny seaplane base in Alaska, also "Personal Effects Coverage"
    'HIT',  # Haivaro - tiny airport in Papua New Guinea
    'GAP',  # Gusap - tiny PNG airport
    'TAB',  # Tabora - Tanzania
    'WEB',  # Web - sounds like internet
    'LOG',  # Longana - Vanuatu
    'DOT',  # Tawi-Tawi - Philippines
    'LET',  # Leticia - Colombia
    'CAT',  # Cat Island - Bahamas but "CAT" appears in text
    'POP',  # Puerto Plata - but "POP" appears in emails
    'TOP',  # Topeka - but rarely used
    'TAP',  # appears in emails
    'TON',  # Tonu - PNG
    'BAN',  # appears in text
    'RUN',  # appears in text
    # False positives from Enterprise/rental car ads
    'RAP',  # Rapid City SD, but matches "Roadside Assistance Protection"
    'SLP',  # San Luis Potosi Mexico, but matches "Supplemental Liability Protection"
    # Very obscure airports that cause false positives in domestic US emails
    'CUS',  # Columbus NM - tiny municipal airport, false positive from "customer"
    'MER',  # Castle AFB - closed military base, false positive from "mer" in words
    'LOS',  # Lagos Nigeria - appears in partial word matches
    'ADD',  # Addis Ababa - appears in English text as "add"
    'USE',  # Useless Loop Australia - appears in English text
    'WAY',  # Waycross GA - appears in English text
    'OWN',  # Norwood MA - appears in English text
    'CAP',  # Cap Haitien - appears in text
    'PAL',  # Palenque Mexico - appears in text
    'PAT',  # Patna India - appears in text
    'PAD',  # Paderborn Germany - appears in text
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

# City name to airport code mapping
# Maps common city names/variations to their primary airport codes
CITY_TO_AIRPORT = {
    # US Cities - with variations and nicknames
    'atlanta': 'ATL', 'atl': 'ATL',
    'dallas': 'DFW', 'dallas fort worth': 'DFW', 'dallas-fort worth': 'DFW', 'dfw': 'DFW',
    'denver': 'DEN', 'den': 'DEN',
    'chicago': 'ORD', "chicago o'hare": 'ORD', 'ohare': 'ORD', "o'hare": 'ORD', 'ord': 'ORD',
    'los angeles': 'LAX', 'la': 'LAX', 'lax': 'LAX', 'l.a.': 'LAX',
    'new york': 'JFK', 'nyc': 'JFK', 'jfk': 'JFK', 'new york city': 'JFK',
    'las vegas': 'LAS', 'vegas': 'LAS', 'las': 'LAS',
    'orlando': 'MCO', 'mco': 'MCO',
    'miami': 'MIA', 'mia': 'MIA',
    'charlotte': 'CLT', 'clt': 'CLT',
    'seattle': 'SEA', 'sea': 'SEA',
    'phoenix': 'PHX', 'phx': 'PHX',
    'newark': 'EWR', 'ewr': 'EWR',
    'san francisco': 'SFO', 'sf': 'SFO', 'sfo': 'SFO', 'san fran': 'SFO',
    'houston': 'IAH', 'iah': 'IAH',
    'boston': 'BOS', 'bos': 'BOS',
    'fort lauderdale': 'FLL', 'ft lauderdale': 'FLL', 'fll': 'FLL',
    'minneapolis': 'MSP', 'msp': 'MSP', 'minneapolis-st paul': 'MSP',
    'laguardia': 'LGA', 'la guardia': 'LGA', 'lga': 'LGA',
    'detroit': 'DTW', 'dtw': 'DTW',
    'philadelphia': 'PHL', 'philly': 'PHL', 'phl': 'PHL',
    'salt lake city': 'SLC', 'salt lake': 'SLC', 'slc': 'SLC',
    'washington': 'DCA', 'washington dc': 'DCA', 'dc': 'DCA', 'reagan': 'DCA',
    'san diego': 'SAN',
    'baltimore': 'BWI', 'bwi': 'BWI',
    'tampa': 'TPA', 'tpa': 'TPA',
    'austin': 'AUS', 'aus': 'AUS',
    'dulles': 'IAD', 'iad': 'IAD',
    'nashville': 'BNA', 'bna': 'BNA',
    'midway': 'MDW', 'chicago midway': 'MDW', 'mdw': 'MDW',
    'honolulu': 'HNL', 'hnl': 'HNL', 'hawaii': 'HNL',
    'portland': 'PDX', 'pdx': 'PDX',
    'st louis': 'STL', 'st. louis': 'STL', 'saint louis': 'STL', 'stl': 'STL',
    'raleigh': 'RDU', 'raleigh durham': 'RDU', 'raleigh-durham': 'RDU', 'rdu': 'RDU', 'durham': 'RDU',
    'new orleans': 'MSY', 'msy': 'MSY', 'nola': 'MSY',
    'san jose': 'SJC', 'sjc': 'SJC',
    'sacramento': 'SMF', 'smf': 'SMF',
    'kansas city': 'MCI', 'mci': 'MCI',
    'san antonio': 'SAT',
    'cleveland': 'CLE', 'cle': 'CLE',
    'indianapolis': 'IND', 'indy': 'IND', 'ind': 'IND',
    'pittsburgh': 'PIT', 'pit': 'PIT',
    'columbus': 'CMH', 'cmh': 'CMH',
    'cincinnati': 'CVG', 'cvg': 'CVG',
    'hartford': 'BDL', 'bdl': 'BDL',
    'jacksonville': 'JAX', 'jax': 'JAX',
    'maui': 'OGG', 'ogg': 'OGG',
    'anchorage': 'ANC', 'anc': 'ANC', 'alaska': 'ANC',
    'buffalo': 'BUF', 'buf': 'BUF',
    'albuquerque': 'ABQ', 'abq': 'ABQ',
    'omaha': 'OMA', 'oma': 'OMA',
    'burbank': 'BUR', 'bur': 'BUR',
    'west palm beach': 'PBI', 'palm beach': 'PBI', 'pbi': 'PBI',
    'richmond': 'RIC', 'ric': 'RIC',
    'fort myers': 'RSW', 'ft myers': 'RSW', 'rsw': 'RSW',
    'louisville': 'SDF', 'sdf': 'SDF',
    'milwaukee': 'MKE', 'mke': 'MKE',
    'tucson': 'TUS', 'tus': 'TUS',
    'oklahoma city': 'OKC', 'okc': 'OKC',
    'reno': 'RNO', 'rno': 'RNO',
    'el paso': 'ELP', 'elp': 'ELP',
    'boise': 'BOI', 'boi': 'BOI',
    'charleston': 'CHS', 'chs': 'CHS',
    'savannah': 'SAV', 'sav': 'SAV',
    'providence': 'PVD', 'pvd': 'PVD',
    'norfolk': 'ORF', 'orf': 'ORF',
    'memphis': 'MEM', 'mem': 'MEM',
    'birmingham': 'BHM', 'bhm': 'BHM',
    'rochester': 'ROC', 'roc': 'ROC',
    'syracuse': 'SYR', 'syr': 'SYR',
    'albany': 'ALB', 'alb': 'ALB',
    'hartford': 'BDL',
    "martha's vineyard": 'MVY', 'marthas vineyard': 'MVY', 'the vineyard': 'MVY', 'mvy': 'MVY',
    'nantucket': 'ACK', 'ack': 'ACK',
    'key west': 'EYW', 'eyw': 'EYW',

    # Canada
    'toronto': 'YYZ', 'yyz': 'YYZ',
    'vancouver': 'YVR', 'yvr': 'YVR',
    'montreal': 'YUL', 'yul': 'YUL',
    'calgary': 'YYC', 'yyc': 'YYC',
    'edmonton': 'YEG', 'yeg': 'YEG',
    'ottawa': 'YOW', 'yow': 'YOW',

    # Mexico & Caribbean
    'mexico city': 'MEX', 'mex': 'MEX',
    'cancun': 'CUN', 'cun': 'CUN',
    'cabo': 'SJD', 'cabo san lucas': 'SJD', 'los cabos': 'SJD', 'sjd': 'SJD',
    'puerto vallarta': 'PVR', 'pvr': 'PVR',
    'san juan': 'SJU', 'puerto rico': 'SJU', 'sju': 'SJU',
    'nassau': 'NAS', 'bahamas': 'NAS', 'nas': 'NAS',
    'montego bay': 'MBJ', 'jamaica': 'MBJ', 'mbj': 'MBJ',
    'punta cana': 'PUJ', 'puj': 'PUJ',
    'aruba': 'AUA', 'aua': 'AUA',
    'st maarten': 'SXM', 'st. maarten': 'SXM', 'sint maarten': 'SXM', 'sxm': 'SXM',

    # Europe
    'london': 'LHR', 'london heathrow': 'LHR', 'heathrow': 'LHR', 'lhr': 'LHR',
    'gatwick': 'LGW', 'london gatwick': 'LGW', 'lgw': 'LGW',
    'paris': 'CDG', 'cdg': 'CDG',
    'amsterdam': 'AMS', 'ams': 'AMS',
    'frankfurt': 'FRA', 'fra': 'FRA',
    'madrid': 'MAD', 'mad': 'MAD',
    'barcelona': 'BCN', 'bcn': 'BCN',
    'rome': 'FCO', 'fco': 'FCO',
    'munich': 'MUC', 'muc': 'MUC',
    'zurich': 'ZRH', 'zrh': 'ZRH',
    'dublin': 'DUB', 'dub': 'DUB',
    'lisbon': 'LIS', 'lis': 'LIS',
    'athens': 'ATH', 'ath': 'ATH',
    'istanbul': 'IST', 'ist': 'IST',
    'prague': 'PRG', 'prg': 'PRG',

    # Asia & Middle East
    'tokyo': 'NRT', 'nrt': 'NRT',
    'seoul': 'ICN', 'icn': 'ICN',
    'beijing': 'PEK', 'pek': 'PEK',
    'shanghai': 'PVG', 'pvg': 'PVG',
    'hong kong': 'HKG', 'hkg': 'HKG',
    'singapore': 'SIN', 'sin': 'SIN',
    'bangkok': 'BKK', 'bkk': 'BKK',
    'dubai': 'DXB', 'dxb': 'DXB',
    'doha': 'DOH', 'doh': 'DOH', 'qatar': 'DOH',
    'tel aviv': 'TLV', 'tlv': 'TLV', 'israel': 'TLV',

    # Australia/Pacific
    'sydney': 'SYD', 'syd': 'SYD',
    'melbourne': 'MEL', 'mel': 'MEL',
    'auckland': 'AKL', 'akl': 'AKL', 'new zealand': 'AKL',
}


def city_to_airport_code(city_name):
    """Convert a city name to its airport code.

    Args:
        city_name: City name string (case insensitive)

    Returns:
        Airport code string or None if not found
    """
    if not city_name:
        return None
    normalized = city_name.lower().strip()
    return CITY_TO_AIRPORT.get(normalized)


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
