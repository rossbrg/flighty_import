#!/usr/bin/env python3
"""
Flighty Email Forwarder - Main Runner

Usage:
    python3 run.py              # Run normally
    python3 run.py --dry-run    # Test without forwarding
    python3 run.py --setup      # Run setup wizard
"""

import imaplib
import smtplib
import email
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import re
import json
import sys
import os
import hashlib
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from email.utils import parsedate_to_datetime

SCRIPT_DIR = Path(__file__).parent
CONFIG_FILE = SCRIPT_DIR / "config.json"
PROCESSED_FILE = SCRIPT_DIR / "processed_flights.json"


VERSION = "1.9.4"
GITHUB_REPO = "drewtwitchell/flighty_import"
UPDATE_FILES = ["run.py", "setup.py", "airport_codes.txt"]


def auto_update():
    """Check for and apply updates from GitHub (no git required). Returns True if updated."""
    import urllib.request
    import urllib.error

    print()
    print("=" * 60)
    print("  STEP 1 OF 4: CHECKING FOR UPDATES")
    print("=" * 60)
    print()
    print("  Connecting to GitHub to check if a newer version exists...")

    try:
        # Get latest version from GitHub
        version_url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/VERSION"
        try:
            with urllib.request.urlopen(version_url, timeout=5) as response:
                latest_version = response.read().decode('utf-8').strip()
        except urllib.error.HTTPError:
            # VERSION file doesn't exist yet, check run.py for version
            run_url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/run.py"
            with urllib.request.urlopen(run_url, timeout=5) as response:
                content = response.read().decode('utf-8')
                # Extract version from file
                for line in content.split('\n'):
                    if line.startswith('VERSION = '):
                        latest_version = line.split('"')[1]
                        break
                else:
                    latest_version = VERSION

        if latest_version == VERSION:
            print(f"  You have the latest version (v{VERSION})")
            print()
            return False

        print(f"  Update available: v{VERSION} -> v{latest_version}")
        print()
        print("  Downloading new version from GitHub...", end="", flush=True)

        # Download updated files
        updated = False
        for filename in UPDATE_FILES:
            file_url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/{filename}"
            try:
                with urllib.request.urlopen(file_url, timeout=10) as response:
                    content = response.read().decode('utf-8')
                    file_path = SCRIPT_DIR / filename
                    with open(file_path, 'w', encoding='utf-8') as f:
                        f.write(content)
                    print(f" {filename}", end="", flush=True)
                    updated = True
            except Exception as e:
                print(f" (failed: {filename})", end="", flush=True)

        if updated:
            print()
            print(f"  Updated to v{latest_version}!")
            print("  Restarting with new version...")
            print()
            return True
        else:
            print()
            print("  Update failed - continuing with current version")
            print()
            return False

    except urllib.error.URLError:
        print("  No internet connection - skipping update check")
        print()
        return False
    except Exception as e:
        print("  Could not check for updates - continuing with current version")
        print()
        return False

# Load ALL valid IATA airport codes from file (9800+ codes)
AIRPORT_CODES_FILE = SCRIPT_DIR / "airport_codes.txt"

def load_airport_codes():
    """Load valid airport codes and names from file."""
    codes = set()
    names = {}
    try:
        if AIRPORT_CODES_FILE.exists():
            with open(AIRPORT_CODES_FILE, 'r', encoding='utf-8', errors='ignore') as f:
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
        codes = {
            'ATL', 'DFW', 'DEN', 'ORD', 'LAX', 'JFK', 'LAS', 'MCO', 'MIA', 'CLT',
            'SEA', 'PHX', 'EWR', 'SFO', 'IAH', 'BOS', 'FLL', 'MSP', 'LGA', 'DTW',
        }
    return codes, names

# Load all airport codes from file
ALL_AIRPORT_CODES, AIRPORT_NAMES_FROM_FILE = load_airport_codes()

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

# Use all codes except excluded words
VALID_AIRPORT_CODES = ALL_AIRPORT_CODES - EXCLUDED_CODES

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

# Merge names: use friendly names first, then file names
AIRPORT_NAMES = {**AIRPORT_NAMES_FROM_FILE, **FRIENDLY_NAMES}


def get_airport_display(code):
    """Get display string for airport code."""
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


def load_config():
    """Load configuration from file with error handling."""
    if not CONFIG_FILE.exists():
        return None
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            config = json.load(f)
            # Validate required fields
            required = ['email', 'password', 'imap_server', 'smtp_server']
            for field in required:
                if not config.get(field):
                    print(f"Warning: Missing required config field: {field}")
                    return None
            # Set defaults for optional fields
            config.setdefault('days_back', 30)
            config.setdefault('check_folders', ['INBOX'])
            config.setdefault('flighty_email', 'track@my.flightyapp.com')
            config.setdefault('imap_port', 993)
            config.setdefault('smtp_port', 587)
            return config
    except json.JSONDecodeError as e:
        print(f"Error: config.json is corrupted: {e}")
        print("Please run 'python3 setup.py' to reconfigure.")
        return None
    except Exception as e:
        print(f"Error loading config: {e}")
        return None


def load_processed_flights():
    """Load dictionary of processed flights with error handling and validation."""
    default_data = {"confirmations": {}, "content_hashes": set()}

    if not PROCESSED_FILE.exists():
        return default_data

    try:
        with open(PROCESSED_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)

            # Validate structure
            if not isinstance(data, dict):
                print("Warning: processed_flights.json has invalid format, starting fresh")
                return default_data

            # Ensure required keys exist with proper types
            if "confirmations" not in data or not isinstance(data.get("confirmations"), dict):
                data["confirmations"] = {}

            # Convert lists to sets for faster lookup
            content_hashes = data.get("content_hashes", [])
            if isinstance(content_hashes, list):
                data["content_hashes"] = set(content_hashes)
            elif isinstance(content_hashes, set):
                pass  # Already a set
            else:
                data["content_hashes"] = set()

            return data

    except json.JSONDecodeError as e:
        print(f"Warning: processed_flights.json is corrupted ({e})")
        print("Starting with fresh tracking. Previously imported flights may be re-imported.")
        # Backup corrupt file
        try:
            backup_path = PROCESSED_FILE.with_suffix('.json.bak')
            PROCESSED_FILE.rename(backup_path)
            print(f"Corrupt file backed up to: {backup_path}")
        except Exception:
            pass
        return default_data
    except Exception as e:
        print(f"Warning: Could not load processed flights ({e})")
        print("Starting with fresh tracking.")
        return default_data


def save_processed_flights(processed):
    """Save processed flights data with atomic write for crash protection."""
    save_data = {
        "content_hashes": list(processed.get("content_hashes", set())),
        "confirmations": processed.get("confirmations", {})
    }

    # Write to temp file first, then rename (atomic operation)
    temp_file = PROCESSED_FILE.with_suffix('.json.tmp')
    try:
        with open(temp_file, 'w', encoding='utf-8') as f:
            json.dump(save_data, f, indent=2)

        # Atomic rename
        temp_file.replace(PROCESSED_FILE)
    except Exception as e:
        print(f"\n    Warning: Could not save progress ({e})")
        # Try to clean up temp file
        try:
            if temp_file.exists():
                temp_file.unlink()
        except Exception:
            pass


def decode_header_value(value):
    """Decode an email header value."""
    if not value:
        return ""
    try:
        decoded_parts = email.header.decode_header(value)
        return ''.join(
            part.decode(charset or 'utf-8', errors='replace') if isinstance(part, bytes) else part
            for part, charset in decoded_parts
        )
    except:
        return str(value)


def extract_confirmation_code(subject, body):
    """Extract confirmation code from email subject or body."""
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


def extract_flight_info(body):
    """Extract flight information from email body with error handling."""
    info = {
        "airports": [],
        "flight_numbers": [],
        "dates": [],
        "times": []
    }

    if not body:
        return info

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

        # Extract dates - prioritize dates WITH year, then add year to those without
        try:
            current_year = datetime.now().year
            dates_with_year = []
            dates_without_year = []

            # Patterns that include year (preferred)
            year_patterns = [
                # "December 7, 2025" or "Dec 7, 2025"
                r'([A-Z][a-z]{2,8}\s+\d{1,2},?\s+\d{4})',
                # "12/07/2025" or "12-07-2025"
                r'(\d{1,2}[/-]\d{1,2}[/-]\d{4})',
                # "2025-12-07"
                r'(\d{4}-\d{2}-\d{2})',
                # "Sun, Dec 07, 2025" or "Sunday, December 7, 2025"
                r'([A-Z][a-z]{2,8},?\s+[A-Z][a-z]{2,8}\s+\d{1,2},?\s+\d{4})',
                # "07 Dec 2025" or "7 December 2025"
                r'(\d{1,2}\s+[A-Z][a-z]{2,8},?\s+\d{4})',
            ]
            for pattern in year_patterns:
                matches = re.findall(pattern, body)
                for m in matches:
                    m = m.strip()
                    if m and m not in dates_with_year and len(m) > 5:
                        dates_with_year.append(m)

            # Patterns without year (will add current year)
            no_year_patterns = [
                # "Sun, Dec 07" or "Sunday, December 7"
                r'([A-Z][a-z]{2,8},?\s+[A-Z][a-z]{2,8}\s+\d{1,2})(?!\d|,?\s*\d{4})',
                # "Dec 07" or "December 7" (but not if followed by year)
                r'\b([A-Z][a-z]{2,8}\s+\d{1,2})(?!\d|,?\s*\d{4})',
            ]
            for pattern in no_year_patterns:
                matches = re.findall(pattern, body)
                for m in matches:
                    m = m.strip()
                    # Add current year if not already have enough dates with year
                    if m and len(dates_with_year) < 3:
                        date_with_year = f"{m}, {current_year}"
                        if date_with_year not in dates_with_year:
                            dates_without_year.append(date_with_year)

            # Combine: prioritize dates with year, then add augmented dates
            info["dates"] = dates_with_year[:3]
            if len(info["dates"]) < 3:
                for d in dates_without_year:
                    if d not in info["dates"]:
                        info["dates"].append(d)
                        if len(info["dates"]) >= 3:
                            break
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
    """Generate a hash of the email content."""
    normalized = re.sub(r'\s+', ' ', (subject + body).lower().strip())
    return hashlib.md5(normalized.encode()).hexdigest()[:16]


def create_flight_fingerprint(flight_info):
    """Create a fingerprint to identify unique flight itineraries."""
    parts = []

    if flight_info.get("airports"):
        parts.append("-".join(flight_info["airports"]))

    if flight_info.get("dates"):
        parts.append(flight_info["dates"][0].lower())

    if flight_info.get("flight_numbers"):
        parts.append("-".join(sorted(flight_info["flight_numbers"])))

    return "|".join(parts) if parts else None


def is_flight_email(from_addr, subject):
    """Check if an email appears to be a flight confirmation."""
    from_addr_lower = from_addr.lower() if from_addr else ""
    subject_lower = subject.lower() if subject else ""

    for airline in AIRLINE_PATTERNS:
        from_match = any(re.search(p, from_addr_lower) for p in airline["from_patterns"])
        subject_match = any(re.search(p, subject_lower) for p in airline["subject_patterns"])

        if airline["name"] == "Generic Flight":
            if subject_match:
                return True, airline["name"]
        elif from_match and subject_match:
            return True, airline["name"]

    return False, None


def get_email_body(msg):
    """Extract the email body."""
    body = ""
    html_body = ""

    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition", ""))

            if "attachment" not in content_disposition:
                try:
                    payload = part.get_payload(decode=True)
                    if payload:
                        text = payload.decode('utf-8', errors='replace')
                        if content_type == "text/plain":
                            body = text
                        elif content_type == "text/html":
                            html_body = text
                except:
                    pass
    else:
        try:
            payload = msg.get_payload(decode=True)
            if payload:
                text = payload.decode('utf-8', errors='replace')
                if msg.get_content_type() == "text/plain":
                    body = text
                else:
                    html_body = text
        except:
            pass

    return body, html_body


def parse_email_date(date_str):
    """Parse email date header into datetime."""
    try:
        return parsedate_to_datetime(date_str)
    except:
        return datetime.min


def forward_email(config, msg, from_addr, subject):
    """Forward an email to Flighty. Waits and retries until it succeeds or hard fails."""
    import time

    forward_msg = MIMEMultipart('mixed')
    forward_msg['From'] = config['email']
    forward_msg['To'] = config['flighty_email']
    forward_msg['Subject'] = f"Fwd: {subject}"

    body, html_body = get_email_body(msg)

    forward_text = f"""
---------- Forwarded message ---------
From: {from_addr}
Date: {msg.get('Date', 'Unknown')}
Subject: {subject}

"""
    if body:
        forward_text += body
        forward_msg.attach(MIMEText(forward_text, 'plain'))

    if html_body:
        forward_msg.attach(MIMEText(html_body, 'html'))

    # Retry with increasing delays until it works
    # AOL/Yahoo rate limit aggressively - we need to wait it out
    retry_delays = [10, 30, 60, 120, 180, 300]  # Up to 5 minutes wait
    max_attempts = len(retry_delays) + 1

    for attempt in range(max_attempts):
        try:
            with smtplib.SMTP(config['smtp_server'], config['smtp_port'], timeout=60) as server:
                server.starttls()
                server.login(config['email'], config['password'])
                server.send_message(forward_msg)
            return True  # Success
        except Exception as e:
            error_msg = str(e).lower()

            # Check if this is a rate limit / connection error (recoverable)
            is_rate_limit = any(x in error_msg for x in [
                'rate', 'limit', 'too many', 'try again', 'temporarily',
                '421', '450', '451', '452', '454', '554',
                'connection', 'closed', 'reset', 'refused', 'timeout'
            ])

            if attempt < max_attempts - 1:
                wait_time = retry_delays[attempt]
                wait_mins = wait_time // 60
                wait_secs = wait_time % 60

                print()  # New line for clarity
                if is_rate_limit:
                    print(f"        BLOCKED by email provider (they limit sending speed)")
                    print(f"        Error: {str(e)[:100]}")
                    if wait_mins > 0:
                        print(f"        Waiting {wait_mins} min {wait_secs} sec then retrying (attempt {attempt + 2} of {max_attempts})...", end="", flush=True)
                    else:
                        print(f"        Waiting {wait_secs} sec then retrying (attempt {attempt + 2} of {max_attempts})...", end="", flush=True)
                else:
                    print(f"        Connection error: {str(e)[:100]}")
                    if wait_mins > 0:
                        print(f"        Waiting {wait_mins} min {wait_secs} sec then retrying (attempt {attempt + 2} of {max_attempts})...", end="", flush=True)
                    else:
                        print(f"        Waiting {wait_secs} sec then retrying (attempt {attempt + 2} of {max_attempts})...", end="", flush=True)

                time.sleep(wait_time)
                print(" retrying now...", end="", flush=True)
            else:
                # All retries exhausted
                print()
                print(f"        FAILED after {max_attempts} attempts")
                print(f"        Final error: {str(e)}")
                print(f"        This email will be skipped - run again later to retry")
                return False

    return False


def connect_imap(config):
    """Connect to the IMAP server."""
    try:
        mail = imaplib.IMAP4_SSL(config['imap_server'], config['imap_port'])
        mail.login(config['email'], config['password'])
        return mail
    except imaplib.IMAP4.error as e:
        print(f"\nLogin failed: {e}")
        print("\nMake sure you're using an App Password, not your regular password.")
        return None


def scan_for_flights(mail, config, folder, processed):
    """
    Phase 1: Scan folder and collect all flight emails.
    Uses server-side IMAP search for speed.
    Skips already-processed confirmations for performance.
    Returns tuple: (flights_found dict, skipped_confirmations list)
    """
    import time
    flights_found = {}  # confirmation_code -> list of {email_id, date, subject, ...}
    skipped_confirmations = []  # list of confirmation codes that were already processed
    already_processed = processed.get("confirmations", {})
    processed_hashes = processed.get("content_hashes", set())

    try:
        result, _ = mail.select(folder)
        if result != 'OK':
            print(f"    Could not open folder: {folder}")
            return flights_found, skipped_confirmations
    except:
        print(f"    Could not open folder: {folder}")
        return flights_found, skipped_confirmations

    since_date = (datetime.now() - timedelta(days=config['days_back'])).strftime("%d-%b-%Y")

    # Search for airline emails server-side
    # Include airlines, booking sites, and travel agencies
    airline_searches = [
        # Major US Airlines
        ('JetBlue', 'FROM "jetblue.com"'),
        ('Delta', 'FROM "delta.com"'),
        ('United', 'FROM "united.com"'),
        ('American', 'FROM "aa.com"'),
        ('Southwest', 'FROM "southwest.com"'),
        ('Alaska', 'FROM "alaskaair.com"'),
        ('Spirit', 'FROM "spirit.com"'),
        ('Frontier', 'FROM "flyfrontier.com"'),
        ('Hawaiian', 'FROM "hawaiianairlines.com"'),
        # International Airlines
        ('Air Canada', 'FROM "aircanada.com"'),
        ('British Airways', 'FROM "britishairways.com"'),
        ('Lufthansa', 'FROM "lufthansa.com"'),
        ('Emirates', 'FROM "emirates.com"'),
        ('Air France', 'FROM "airfrance.com"'),
        ('KLM', 'FROM "klm.com"'),
        ('Qantas', 'FROM "qantas.com"'),
        ('Singapore', 'FROM "singaporeair.com"'),
        ('Cathay', 'FROM "cathaypacific.com"'),
        ('JAL', 'FROM "jal.com"'),
        ('ANA', 'FROM "ana.co.jp"'),
        ('Korean Air', 'FROM "koreanair.com"'),
        ('Turkish', 'FROM "turkishairlines.com"'),
        ('Qatar', 'FROM "qatarairways.com"'),
        ('Etihad', 'FROM "etihad.com"'),
        ('Virgin', 'FROM "virginatlantic.com"'),
        ('Icelandair', 'FROM "icelandair.com"'),
        ('Norwegian', 'FROM "norwegian.com"'),
        ('Ryanair', 'FROM "ryanair.com"'),
        ('EasyJet', 'FROM "easyjet.com"'),
        ('WestJet', 'FROM "westjet.com"'),
        ('Avianca', 'FROM "avianca.com"'),
        ('LATAM', 'FROM "latam.com"'),
        ('Aeromexico', 'FROM "aeromexico.com"'),
        ('Copa', 'FROM "copaair.com"'),
        # Booking Sites
        ('Expedia', 'FROM "expedia.com"'),
        ('Kayak', 'FROM "kayak.com"'),
        ('Priceline', 'FROM "priceline.com"'),
        ('Orbitz', 'FROM "orbitz.com"'),
        ('Travelocity', 'FROM "travelocity.com"'),
        ('CheapOair', 'FROM "cheapoair.com"'),
        ('Hopper', 'FROM "hopper.com"'),
        ('Google', 'FROM "google.com"'),
        ('Booking', 'FROM "booking.com"'),
        ('Trip.com', 'FROM "trip.com"'),
        ('Skyscanner', 'FROM "skyscanner.com"'),
        # Corporate Travel
        ('Concur', 'FROM "concur.com"'),
        ('Egencia', 'FROM "egencia.com"'),
        ('TripActions', 'FROM "tripactions.com"'),
        ('Navan', 'FROM "navan.com"'),
        # Credit Card Travel
        ('Chase Travel', 'FROM "chase.com"'),
        ('Amex Travel', 'FROM "americanexpress.com"'),
        ('Capital One', 'FROM "capitalone.com"'),
        ('Citi', 'FROM "citi.com"'),
    ]

    # Also search by subject for any we might miss
    subject_searches = [
        ('Flight Conf', 'SUBJECT "flight confirmation"'),
        ('Itinerary', 'SUBJECT "itinerary"'),
        ('E-Ticket', 'SUBJECT "e-ticket"'),
        ('Booking Conf', 'SUBJECT "booking confirmation"'),
        ('Trip Conf', 'SUBJECT "trip confirmation"'),
        ('Travel Conf', 'SUBJECT "travel confirmation"'),
    ]

    all_email_ids = set()

    print(f"    Searching: ", end="", flush=True)

    # Search by sender
    found_sources = []
    for source_name, search_term in airline_searches:
        try:
            result, data = mail.search(None, f'(SINCE {since_date} {search_term})')
            if result == 'OK' and data[0]:
                ids = data[0].split()
                if ids:
                    all_email_ids.update(ids)
                    found_sources.append(f"{source_name}({len(ids)})")
        except:
            pass

    # Search by subject
    for subject_name, search_term in subject_searches:
        try:
            result, data = mail.search(None, f'(SINCE {since_date} {search_term})')
            if result == 'OK' and data[0]:
                ids = data[0].split()
                if ids:
                    new_ids = set(ids) - all_email_ids
                    if new_ids:
                        all_email_ids.update(new_ids)
                        found_sources.append(f"{subject_name}({len(new_ids)})")
        except:
            pass

    # Also search for partial matches in FROM field (catches subdomains like email.jetblue.com)
    partial_sender_searches = [
        ('JetBlue+', 'FROM "jetblue"'),
        ('Delta+', 'FROM "delta"'),
        ('United+', 'FROM "united"'),
        ('American+', 'FROM "american"'),
        ('Southwest+', 'FROM "southwest"'),
    ]
    for source_name, search_term in partial_sender_searches:
        try:
            result, data = mail.search(None, f'(SINCE {since_date} {search_term})')
            if result == 'OK' and data[0]:
                ids = data[0].split()
                if ids:
                    new_ids = set(ids) - all_email_ids
                    if new_ids:
                        all_email_ids.update(new_ids)
                        found_sources.append(f"{source_name}({len(new_ids)})")
        except:
            pass

    # Print what we found (limit to avoid super long output)
    if found_sources:
        if len(found_sources) <= 8:
            print(" ".join(found_sources), flush=True)
        else:
            print(" ".join(found_sources[:6]) + f" (+{len(found_sources)-6} more)", flush=True)
    else:
        print("searching...", flush=True)

    email_ids = list(all_email_ids)
    total = len(email_ids)

    if total == 0:
        print("    No matching emails found in this folder.")
        return flights_found, skipped_confirmations

    print()
    print(f"    Found {total} emails that might contain flight info.")
    print()

    # Estimate time based on number of emails
    est_seconds = total * 0.5  # Roughly 0.5 sec per email
    est_mins = int(est_seconds // 60)
    est_secs = int(est_seconds % 60)
    if est_mins > 0:
        print(f"    ESTIMATED TIME: {est_mins}-{est_mins + 2} minutes for {total} emails")
    else:
        print(f"    ESTIMATED TIME: About {est_secs} seconds for {total} emails")
    print()
    print(f"    Now examining each email to find flight confirmations...")
    print(f"    Please wait - this downloads and checks each email individually.")
    print()

    flight_count = 0
    skipped_count = 0
    scan_start_time = time.time()

    error_count = 0
    for idx, email_id in enumerate(email_ids):
        try:
            # Calculate time remaining
            elapsed = time.time() - scan_start_time
            if idx > 0:
                avg_per_email = elapsed / idx
                remaining = avg_per_email * (total - idx)
                remaining_mins = int(remaining // 60)
                remaining_secs = int(remaining % 60)
                if remaining_mins > 0:
                    time_str = f"~{remaining_mins}m {remaining_secs}s left"
                else:
                    time_str = f"~{remaining_secs}s left"
            else:
                time_str = "calculating..."

            # Show progress with percentage and time remaining
            pct = int((idx + 1) / total * 100)
            print(f"\r    Progress: {idx + 1}/{total} ({pct}%) | {time_str} | Found: {flight_count} new, {skipped_count} already imported   ", end="", flush=True)

            # Fetch full email
            try:
                result, msg_data = mail.fetch(email_id, '(RFC822)')
                if result != 'OK' or not msg_data or not msg_data[0]:
                    error_count += 1
                    continue
            except Exception:
                error_count += 1
                continue

            raw_email = msg_data[0][1]
            if not raw_email:
                error_count += 1
                continue

            msg = email.message_from_bytes(raw_email)

            from_addr = decode_header_value(msg.get('From', ''))
            subject = decode_header_value(msg.get('Subject', ''))
            date_str = msg.get('Date', '')

            # Verify it's actually a flight email
            is_flight, airline = is_flight_email(from_addr, subject)
            if not is_flight:
                continue

            body, html_body = get_email_body(msg)
            full_body = body or html_body or ""

            # Extract confirmation code early to check if already processed
            confirmation = extract_confirmation_code(subject, full_body)
            content_hash = generate_content_hash(subject, full_body)

            # Skip if already processed (same confirmation AND same content hash)
            if confirmation and confirmation in already_processed:
                if content_hash in processed_hashes:
                    skipped_count += 1
                    if confirmation not in skipped_confirmations:
                        skipped_confirmations.append(confirmation)
                    continue

            flight_count += 1

            # Extract remaining details
            flight_info = extract_flight_info(full_body)
            email_date = parse_email_date(date_str)

            # Store this flight email
            flight_data = {
                "email_id": email_id,
                "msg": msg,
                "from_addr": from_addr,
                "subject": subject,
                "email_date": email_date,
                "confirmation": confirmation,
                "flight_info": flight_info,
                "content_hash": content_hash,
                "airline": airline,
                "folder": folder
            }

            # Group by confirmation code (or use content hash if no confirmation)
            key = confirmation if confirmation else f"unknown_{content_hash}"

            if key not in flights_found:
                flights_found[key] = []
            flights_found[key].append(flight_data)

        except Exception as e:
            # Log error but continue processing other emails
            error_count += 1
            continue

    # Calculate actual time taken
    scan_elapsed = time.time() - scan_start_time
    scan_mins = int(scan_elapsed // 60)
    scan_secs = int(scan_elapsed % 60)

    print(f"\r    Scan complete!" + " " * 60)
    print()
    if scan_mins > 0:
        print(f"    Time taken: {scan_mins} min {scan_secs} sec")
    else:
        print(f"    Time taken: {scan_secs} seconds")
    print()
    print(f"    Results for this folder:")
    print(f"      - New flight confirmations found: {flight_count}")
    print(f"      - Already imported (skipped):     {skipped_count}")

    return flights_found, skipped_confirmations


def select_latest_flights(all_flights, processed):
    """
    Phase 2: For each confirmation, select the latest email.
    Skip already-processed flights unless they've changed.
    """
    to_forward = []
    skipped = []

    for conf_code, emails in all_flights.items():
        # Sort by email date, newest first
        emails.sort(key=lambda x: x["email_date"], reverse=True)
        latest = emails[0]

        # Check if already processed
        fingerprint = create_flight_fingerprint(latest["flight_info"])
        existing = processed.get("confirmations", {}).get(conf_code)

        if existing:
            old_fingerprint = existing.get("fingerprint", "")
            if fingerprint == old_fingerprint:
                skipped.append({
                    "confirmation": conf_code,
                    "reason": "already imported",
                    "subject": latest["subject"][:50]
                })
                continue
            else:
                # Flight changed - mark for re-import
                latest["is_change"] = True

        # Check content hash
        if latest["content_hash"] in processed.get("content_hashes", set()):
            skipped.append({
                "confirmation": conf_code,
                "reason": "duplicate content",
                "subject": latest["subject"][:50]
            })
            continue

        latest["fingerprint"] = fingerprint
        latest["email_count"] = len(emails)
        to_forward.append(latest)

    return to_forward, skipped


def display_previously_imported(processed):
    """Display flights that were previously imported."""
    confirmations = processed.get("confirmations", {})
    if not confirmations:
        return

    count = len(confirmations)

    print()
    print("  -" * 30)
    print(f"  Previously imported flights ({count} total):")
    print()

    for conf_code, data in sorted(confirmations.items()):
        airports = data.get("airports", [])
        dates = data.get("dates", [])
        flight_nums = data.get("flight_numbers", [])

        # Filter out bad airport codes that were saved before exclusions were added
        valid_airports = [code for code in airports if code in VALID_AIRPORT_CODES]

        # Build route - only if we have valid airports
        route = " → ".join(valid_airports[:2]) if valid_airports else ""

        # Only show flight number if it looks valid (not too long, is a number)
        flight_str = ""
        if flight_nums and len(flight_nums[0]) <= 5:
            try:
                int(flight_nums[0])  # Verify it's a number
                flight_str = f"Flight {flight_nums[0]}"
            except ValueError:
                pass

        # Only show date if it doesn't look like garbage (has a month name or valid format)
        date_str = ""
        if dates and dates[0]:
            d = dates[0]
            # Check if it contains a real month name or looks like a date
            months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
            if any(m in d for m in months) or '/' in d or '-' in d:
                date_str = d

        # Build the display line - only include valid parts
        line = f"    {conf_code}"
        if route:
            line += f"  {route}"
        if flight_str:
            line += f"  {flight_str}"
        if date_str:
            line += f"  {date_str}"

        print(line)

    print("  -" * 30)
    print()


def display_new_flights(to_forward):
    """Display new flights that will be imported."""
    if not to_forward:
        return

    print()
    print("  -" * 30)
    print(f"  NEW FLIGHTS FOUND: {len(to_forward)}")
    print("  These will now be sent to Flighty:")
    print()

    for flight in to_forward:
        conf = flight.get("confirmation", "Unknown")
        info = flight.get("flight_info", {})

        airports = info.get("airports", [])
        dates = info.get("dates", [])
        flight_nums = info.get("flight_numbers", [])

        # Filter to only valid airport codes
        valid_airports = [code for code in airports if code in VALID_AIRPORT_CODES]
        route = " → ".join(valid_airports[:2]) if valid_airports else ""

        # Only show flight number if it looks valid
        flight_str = ""
        if flight_nums and len(flight_nums[0]) <= 5:
            try:
                int(flight_nums[0])
                flight_str = f"Flight {flight_nums[0]}"
            except ValueError:
                pass

        # Only show date if it looks valid
        date_str = ""
        if dates and dates[0]:
            d = dates[0]
            months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
            if any(m in d for m in months) or '/' in d or '-' in d:
                date_str = d

        line = f"    {conf}"
        if route:
            line += f"  {route}"
        if flight_str:
            line += f"  {flight_str}"
        if date_str:
            line += f"  {date_str}"

        print(line)

    print("  -" * 30)
    print()


def display_flight_summary(to_forward, skipped, all_flights):
    """Phase 3: Display what will be imported - simplified version."""
    # Just return whether there are flights to forward
    return len(to_forward) > 0


def forward_flights(config, to_forward, processed, dry_run):
    """Forward the selected flights to Flighty."""
    import time

    total = len(to_forward)

    print()
    print("=" * 60)
    print("  SENDING EMAILS TO FLIGHTY")
    print("=" * 60)
    print()
    print(f"  Total to send: {total} flight confirmation emails")
    print()
    print("  HOW THIS WORKS:")
    print("  - Each flight email is forwarded to Flighty one at a time")
    print("  - There's an 8-second delay between each send to avoid spam filters")
    print()
    print("  IMPORTANT - PLEASE BE PATIENT:")
    print("  - Email providers (AOL, Yahoo, Gmail, etc.) limit sending speed")
    print("  - If we send too fast, they temporarily block us")
    print("  - When blocked, we wait and automatically retry (up to 5 minutes)")
    print("  - Large batches may take 10-30+ minutes - this is normal!")
    print()
    print("  Do not close this window - your progress is saved after each send.")
    print()
    print("-" * 60)

    forwarded = 0
    failed = 0
    start_time = time.time()

    for idx, flight in enumerate(to_forward):
        try:
            # Delay between sends to avoid rate limiting (except first one)
            # AOL is very strict - need longer delays
            if idx > 0 and not dry_run:
                time.sleep(8)

            conf = flight.get('confirmation') or '------'
            info = flight.get('flight_info', {})

            # Build compact status line - filter to valid airports only
            airports = info.get("airports", [])
            valid_airports = [code for code in airports if code in VALID_AIRPORT_CODES]
            route = " → ".join(valid_airports[:2]) if valid_airports else ""

            # Build display line
            display = f"  [{idx + 1}/{total}] Sending {conf}"
            if route:
                display += f" ({route})"
            display += "... "

            print(display, end="", flush=True)

            if dry_run:
                print("SKIPPED (dry run)")
                forwarded += 1
                continue

            # Attempt to forward
            try:
                msg = flight.get("msg")
                from_addr = flight.get("from_addr", "")
                subject = flight.get("subject", "Flight Confirmation")

                if not msg:
                    print("FAILED - no message content found")
                    failed += 1
                    continue

                if forward_email(config, msg, from_addr, subject):
                    print("Sent!")
                    forwarded += 1

                    # Record as processed
                    try:
                        content_hash = flight.get("content_hash")
                        if content_hash:
                            processed["content_hashes"].add(content_hash)

                        if flight.get("confirmation"):
                            processed["confirmations"][flight["confirmation"]] = {
                                "fingerprint": flight.get("fingerprint"),
                                "airports": info.get("airports", []),
                                "dates": info.get("dates", []),
                                "flight_numbers": info.get("flight_numbers", []),
                                "forwarded_at": datetime.now().isoformat(),
                                "subject": subject[:100] if subject else ""
                            }

                        save_processed_flights(processed)
                        print(f"        (Progress saved)")
                    except Exception as save_err:
                        print(f"        Warning: Could not save progress: {save_err}")
                else:
                    # forward_email already printed detailed error info
                    failed += 1

            except Exception as send_err:
                print(f"FAILED - {str(send_err)[:80]}")
                failed += 1

        except Exception as e:
            print(f"  [Error processing flight: {str(e)[:60]}]")
            failed += 1
            continue

    # Summary
    print()
    print("-" * 60)
    elapsed = time.time() - start_time
    elapsed_mins = int(elapsed // 60)
    elapsed_secs = int(elapsed % 60)

    print()
    print("  FORWARDING COMPLETE")
    print()
    print(f"  Time elapsed:       {elapsed_mins} min {elapsed_secs} sec")
    print(f"  Successfully sent:  {forwarded} of {total}")
    if failed > 0:
        print(f"  Failed to send:     {failed}")
        print()
        print(f"  NOTE: {failed} email(s) could not be sent after multiple retries.")
        print(f"  Run this script again later to retry the failed ones.")
        print(f"  (Successfully sent flights are saved and won't be re-sent)")

    return forwarded


def run(dry_run=False, days_override=None):
    """Main run function."""
    config = load_config()

    if not config:
        print("No configuration found! Run 'python3 setup.py' first.")
        return

    if not config.get('email') or not config.get('password'):
        print("Email or password not configured! Run 'python3 setup.py'.")
        return

    # Apply days override if specified
    if days_override:
        config['days_back'] = days_override

    # STEP 2: CONFIGURATION & CONNECT
    print()
    print("=" * 60)
    print("  STEP 2 OF 4: CONNECTING TO YOUR EMAIL")
    print("=" * 60)
    print()
    print(f"  Email account:  {config['email']}")
    print(f"  Forward to:     {config['flighty_email']}")
    print(f"  Search period:  Last {config['days_back']} days of emails")
    if dry_run:
        print()
        print("  *** DRY RUN MODE - No emails will actually be sent ***")
    print()
    print(f"  Connecting to {config['imap_server']}...")
    mail = connect_imap(config)
    if not mail:
        print()
        print("  *** CONNECTION FAILED ***")
        print("  Please check your email and password in config.json")
        print("  Or run 'python3 setup.py' to reconfigure.")
        return
    print("  Connected successfully!")

    # STEP 3: LOAD HISTORY
    print()
    print("=" * 60)
    print("  STEP 3 OF 4: CHECKING IMPORT HISTORY")
    print("=" * 60)
    print()
    print("  Loading your import history to avoid duplicates...")
    processed = load_processed_flights()
    prev_count = len(processed.get("confirmations", {}))
    if prev_count > 0:
        print(f"  Found {prev_count} flights that were previously sent to Flighty.")
        print("  These will be skipped to avoid duplicates.")
        display_previously_imported(processed)
    else:
        print("  No previous imports found - this appears to be your first run!")
        print()

    try:
        folders = config.get('check_folders', ['INBOX'])

        # STEP 4: SCANNING section
        print()
        print("=" * 60)
        print("  STEP 4 OF 4: SCANNING & FORWARDING")
        print("=" * 60)
        print()
        print("  Now searching your email for flight confirmations...")
        print("  This checks emails from airlines, booking sites, and travel agencies.")
        print()

        all_flights = {}
        for folder in folders:
            print(f"  Scanning folder: {folder}")
            folder_flights, _ = scan_for_flights(mail, config, folder, processed)
            for conf, emails in folder_flights.items():
                if conf in all_flights:
                    all_flights[conf].extend(emails)
                else:
                    all_flights[conf] = emails

        # Select latest version of each flight
        to_forward, skipped = select_latest_flights(all_flights, processed)

        # Show new flights to import
        if to_forward:
            display_new_flights(to_forward)

            # Forward to Flighty
            forwarded = forward_flights(config, to_forward, processed, dry_run)

            # COMPLETE section
            print()
            print("=" * 60)
            print("  ALL DONE!")
            print("=" * 60)
            print()
            print("  Summary:")
            print(f"    - Sent to Flighty:      {forwarded} new flights")
            print(f"    - Already in Flighty:   {len(processed.get('confirmations', {})) - forwarded} previously imported")
            print()
            print("  Your flights should now appear in Flighty!")
            print("  Run this script again anytime to check for new flight emails.")
            print()
        else:
            # No new flights
            print()
            print("=" * 60)
            print("  ALL DONE!")
            print("=" * 60)
            print()
            print("  No new flight confirmations found.")
            print()
            if prev_count > 0:
                print(f"  You already have {prev_count} flights imported to Flighty.")
            print("  Run this script again anytime to check for new flight emails.")
            print()

    except Exception as e:
        print(f"\n\n*** ERROR: {e} ***")
        print("\nThe script encountered an error. Your progress has been saved.")
        print("Run the script again to continue from where it left off.")
        import traceback
        traceback.print_exc()
    finally:
        try:
            mail.logout()
        except:
            pass


def main():
    args = sys.argv[1:]

    if "--setup" in args or "-s" in args:
        os.system(f"python3 {SCRIPT_DIR / 'setup.py'}")
        return

    if "--reset" in args:
        if PROCESSED_FILE.exists():
            PROCESSED_FILE.unlink()
            print("Reset processed flights tracking. All flights will be re-scanned.")
        else:
            print("No tracking file found - already clean.")
        return

    if "--clean" in args:
        # Clean up potentially corrupt data files
        cleaned = False
        files_to_clean = [
            PROCESSED_FILE,
            PROCESSED_FILE.with_suffix('.json.tmp'),
            PROCESSED_FILE.with_suffix('.json.bak'),
        ]
        for f in files_to_clean:
            if f.exists():
                try:
                    f.unlink()
                    print(f"Removed: {f.name}")
                    cleaned = True
                except Exception as e:
                    print(f"Could not remove {f.name}: {e}")

        if cleaned:
            print("\nCleanup complete! Run 'python3 run.py' to start fresh.")
        else:
            print("No files to clean up.")
        return

    if "--help" in args or "-h" in args:
        print(f"""
Flighty Email Forwarder v{VERSION}

Usage:
    python3 run.py              Run and forward flight emails
    python3 run.py --dry-run    Test without forwarding
    python3 run.py --days N     Search N days back (e.g., --days 180)
    python3 run.py --setup      Run setup wizard
    python3 run.py --reset      Clear processed flights history
    python3 run.py --clean      Clean up corrupt/temp files and start fresh
    python3 run.py --help       Show this help

Examples:
    python3 run.py --days 365           Search 1 year of emails
    python3 run.py --days 180 --dry-run Test 6 months without sending

First time? Run: python3 setup.py

Had issues or crashes? Run: python3 run.py --clean
""")
        return

    # Auto-update before running - restart if updated
    if auto_update():
        # Re-run the script with the new version
        import subprocess
        subprocess.run([sys.executable, str(SCRIPT_DIR / "run.py")] + args)
        return

    # Parse --days option
    days_override = None
    for i, arg in enumerate(args):
        if arg == "--days" and i + 1 < len(args):
            try:
                days_override = int(args[i + 1])
                if days_override < 1:
                    print("Error: --days must be a positive number")
                    return
            except ValueError:
                print(f"Error: --days requires a number, got '{args[i + 1]}'")
                return

    dry_run = "--dry-run" in args or "-d" in args
    run(dry_run=dry_run, days_override=days_override)


def wait_for_keypress():
    """Wait for user to press Enter before closing (for Windows users who double-click)."""
    import platform
    # Only prompt on Windows where double-clicking closes the window immediately
    if platform.system() == "Windows":
        print("\n" + "-" * 40)
        input("Press Enter to close this window...")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nCancelled by user.")
    except Exception as e:
        print(f"\n\nUnexpected error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        wait_for_keypress()
