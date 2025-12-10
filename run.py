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


VERSION = "1.8.0"
GITHUB_REPO = "drewtwitchell/flighty_import"
UPDATE_FILES = ["run.py", "setup.py", "airport_codes.txt"]


def auto_update():
    """Check for and apply updates from GitHub (no git required). Returns True if updated."""
    import urllib.request
    import urllib.error

    print("\n=== Checking for updates ===")

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
            print(f"Already up to date! (v{VERSION})")
            print()
            return False

        print(f"Update available: v{VERSION} -> v{latest_version}")
        print("Downloading updates...", end="", flush=True)

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
            print(f"\nUpdated to v{latest_version}! Restarting...")
            print()
            return True
        else:
            print("\nUpdate failed - continuing with current version")
            print()
            return False

    except urllib.error.URLError:
        print("No internet connection - skipping update check")
        print()
        return False
    except Exception as e:
        print(f"Could not check for updates - continuing")
        print()
        return False

# Major airports that people actually fly through
# This curated list prevents false positives from obscure codes like CRO, THE, AIR
MAJOR_AIRPORT_CODES = {
    # Top 50 US airports
    'ATL', 'DFW', 'DEN', 'ORD', 'LAX', 'JFK', 'LAS', 'MCO', 'MIA', 'CLT',
    'SEA', 'PHX', 'EWR', 'SFO', 'IAH', 'BOS', 'FLL', 'MSP', 'LGA', 'DTW',
    'PHL', 'SLC', 'DCA', 'SAN', 'BWI', 'TPA', 'AUS', 'IAD', 'BNA', 'MDW',
    'HNL', 'DAL', 'PDX', 'STL', 'RDU', 'HOU', 'OAK', 'MSY', 'SJC', 'SMF',
    'SNA', 'MCI', 'SAT', 'CLE', 'IND', 'PIT', 'CMH', 'CVG', 'BDL', 'JAX',
    'OGG', 'ANC', 'BUF', 'ABQ', 'ONT', 'OMA', 'BUR', 'PBI', 'RIC', 'RSW',
    'SDF', 'MKE', 'TUS', 'OKC', 'RNO', 'ELP', 'BOI', 'LIT', 'TUL', 'GEG',

    # Major Canadian airports
    'YYZ', 'YVR', 'YUL', 'YYC', 'YEG', 'YOW', 'YWG', 'YHZ', 'YQB',

    # Major Mexican airports
    'MEX', 'CUN', 'GDL', 'SJD', 'PVR', 'MTY',

    # Major Caribbean airports
    'SJU', 'NAS', 'MBJ', 'PUJ', 'STT', 'STX', 'AUA', 'CUR', 'SXM', 'GCM',

    # Major European airports
    'LHR', 'CDG', 'AMS', 'FRA', 'MAD', 'BCN', 'FCO', 'MUC', 'ZRH', 'VIE',
    'DUB', 'LIS', 'CPH', 'OSL', 'ARN', 'HEL', 'BRU', 'MAN', 'EDI', 'GLA',
    'ATH', 'IST', 'PRG', 'WAW', 'BUD',

    # Major Asian airports
    'HND', 'NRT', 'ICN', 'PEK', 'PVG', 'HKG', 'SIN', 'BKK', 'KUL', 'TPE',
    'DEL', 'BOM', 'DXB', 'AUH', 'DOH', 'TLV',

    # Major Australian/Pacific airports
    'SYD', 'MEL', 'BNE', 'AKL', 'PPT', 'NAN',

    # Major South American airports
    'GRU', 'GIG', 'EZE', 'SCL', 'BOG', 'LIM', 'PTY',

    # Major African airports
    'JNB', 'CPT', 'CAI', 'CMN', 'ADD',
}

# Airport names for display
AIRPORT_NAMES = {
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
    'YEG': 'Edmonton', 'YOW': 'Ottawa', 'YWG': 'Winnipeg', 'YHZ': 'Halifax', 'YQB': 'Quebec City',

    # Mexico & Caribbean
    'MEX': 'Mexico City', 'CUN': 'Cancun', 'GDL': 'Guadalajara', 'SJD': 'San Jose del Cabo',
    'PVR': 'Puerto Vallarta', 'MTY': 'Monterrey', 'SJU': 'San Juan', 'NAS': 'Nassau',
    'MBJ': 'Montego Bay', 'PUJ': 'Punta Cana', 'STT': 'St. Thomas', 'STX': 'St. Croix',
    'AUA': 'Aruba', 'CUR': 'Curacao', 'SXM': 'St. Maarten', 'GCM': 'Grand Cayman',

    # Europe
    'LHR': 'London Heathrow', 'CDG': 'Paris', 'AMS': 'Amsterdam', 'FRA': 'Frankfurt',
    'MAD': 'Madrid', 'BCN': 'Barcelona', 'FCO': 'Rome', 'MUC': 'Munich', 'ZRH': 'Zurich',
    'VIE': 'Vienna', 'DUB': 'Dublin', 'LIS': 'Lisbon', 'CPH': 'Copenhagen', 'OSL': 'Oslo',
    'ARN': 'Stockholm', 'HEL': 'Helsinki', 'BRU': 'Brussels', 'MAN': 'Manchester',
    'EDI': 'Edinburgh', 'GLA': 'Glasgow', 'ATH': 'Athens', 'IST': 'Istanbul',
    'PRG': 'Prague', 'WAW': 'Warsaw', 'BUD': 'Budapest',

    # Asia & Middle East
    'HND': 'Tokyo Haneda', 'NRT': 'Tokyo Narita', 'ICN': 'Seoul', 'PEK': 'Beijing',
    'PVG': 'Shanghai', 'HKG': 'Hong Kong', 'SIN': 'Singapore', 'BKK': 'Bangkok',
    'KUL': 'Kuala Lumpur', 'TPE': 'Taipei', 'DEL': 'Delhi', 'BOM': 'Mumbai',
    'DXB': 'Dubai', 'AUH': 'Abu Dhabi', 'DOH': 'Doha', 'TLV': 'Tel Aviv',

    # Australia/Pacific
    'SYD': 'Sydney', 'MEL': 'Melbourne', 'BNE': 'Brisbane', 'AKL': 'Auckland',
    'PPT': 'Tahiti', 'NAN': 'Fiji',

    # South America
    'GRU': 'Sao Paulo', 'GIG': 'Rio de Janeiro', 'EZE': 'Buenos Aires', 'SCL': 'Santiago',
    'BOG': 'Bogota', 'LIM': 'Lima', 'PTY': 'Panama City',

    # Africa
    'JNB': 'Johannesburg', 'CPT': 'Cape Town', 'CAI': 'Cairo', 'CMN': 'Casablanca', 'ADD': 'Addis Ababa',
}

VALID_AIRPORT_CODES = MAJOR_AIRPORT_CODES


def get_airport_display(code):
    """Get display string for airport code."""
    name = AIRPORT_NAMES.get(code, "")
    if name:
        return f"{code} ({name})"
    return code

# Airline patterns to detect flight confirmation emails
AIRLINE_PATTERNS = [
    {
        "name": "JetBlue",
        "from_patterns": [r"jetblue", r"@.*jetblue\.com"],
        "subject_patterns": [r"booking confirmation", r"itinerary", r"flight confirmation"],
    },
    {
        "name": "Delta",
        "from_patterns": [r"delta", r"@.*delta\.com"],
        "subject_patterns": [r"ereceipt", r"trip confirmation", r"itinerary", r"booking confirmation"],
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
        "subject_patterns": [r"confirmation", r"itinerary", r"trip"],
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
        "name": "Generic Flight",
        "from_patterns": [r".*"],
        "subject_patterns": [
            r"flight.*confirmation",
            r"booking.*confirmation.*flight",
            r"e-?ticket",
            r"itinerary.*flight",
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
        if code not in FALSE_AIRPORT_CODES and not code.isdigit():
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


def forward_email(config, msg, from_addr, subject, max_retries=3):
    """Forward an email to Flighty with retry logic."""
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

    retry_delays = [10, 20, 30, 45, 60]  # Longer delays for rate limiting
    max_retries = len(retry_delays) + 1  # 6 total attempts

    for attempt in range(max_retries):
        try:
            with smtplib.SMTP(config['smtp_server'], config['smtp_port'], timeout=60) as server:
                server.starttls()
                server.login(config['email'], config['password'])
                server.send_message(forward_msg)
            return True
        except Exception as e:
            error_msg = str(e).lower()
            # Check if it's a rate limit or temporary error
            is_rate_limit = any(x in error_msg for x in ['rate', 'limit', 'too many', '421', '450', '451', '452', '554'])

            if attempt < max_retries - 1:
                wait_time = retry_delays[attempt]
                if is_rate_limit:
                    wait_time = wait_time * 2  # Double wait time for rate limits
                print(f"waiting {wait_time}s...", end="", flush=True)
                time.sleep(wait_time)
                print(f" retry {attempt + 2}/{max_retries}...", end="", flush=True)
            else:
                print(f"\n      Failed after {max_retries} attempts: {e}")
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

    # Search for airline emails server-side - be specific to reduce false matches
    airline_searches = [
        ('JetBlue', 'FROM "jetblue.com"'),
        ('Delta', 'FROM "delta.com"'),
        ('United', 'FROM "united.com"'),
        ('American', 'FROM "aa.com"'),
        ('Southwest', 'FROM "southwest.com"'),
        ('Alaska', 'FROM "alaskaair.com"'),
        ('Spirit', 'FROM "spirit.com"'),
        ('Frontier', 'FROM "flyfrontier.com"'),
        ('Hawaiian', 'FROM "hawaiianairlines.com"'),
        ('Air Canada', 'FROM "aircanada.com"'),
        ('British Airways', 'FROM "britishairways.com"'),
        ('Lufthansa', 'FROM "lufthansa.com"'),
        ('Emirates', 'FROM "emirates.com"'),
    ]

    all_email_ids = set()

    print(f"    Searching airlines: ", end="", flush=True)

    for airline_name, search_term in airline_searches:
        try:
            result, data = mail.search(None, f'(SINCE {since_date} {search_term})')
            if result == 'OK' and data[0]:
                ids = data[0].split()
                if ids:
                    all_email_ids.update(ids)
                    print(f"{airline_name}({len(ids)}) ", end="", flush=True)
        except:
            pass

    email_ids = list(all_email_ids)
    total = len(email_ids)

    if total == 0:
        print("none found")
        return flights_found, skipped_confirmations

    print(f"\n    Found {total} airline emails, analyzing...", flush=True)

    flight_count = 0
    skipped_count = 0

    error_count = 0
    for idx, email_id in enumerate(email_ids):
        try:
            # Show progress with percentage
            pct = int((idx + 1) / total * 100)
            print(f"\r    Analyzing: {idx + 1}/{total} ({pct}%) - {flight_count} new, {skipped_count} already processed", end="", flush=True)

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
                    # Track which confirmations were skipped (only add once per confirmation)
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

    status_msg = f"\r    Done: {flight_count} new flights, {skipped_count} already processed"
    if error_count > 0:
        status_msg += f", {error_count} errors"
    print(status_msg + " " * 20)

    # Show which confirmations were skipped
    if skipped_confirmations:
        print(f"    Already imported: {', '.join(skipped_confirmations[:10])}", end="")
        if len(skipped_confirmations) > 10:
            print(f" (+{len(skipped_confirmations) - 10} more)")
        else:
            print()

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


def display_flight_summary(to_forward, skipped, all_flights):
    """Phase 3: Display what will be imported."""
    print("\n" + "=" * 60)
    print("  FLIGHT IMPORT SUMMARY")
    print("=" * 60)

    # Show grouped emails
    if all_flights:
        print(f"\n  Found {len(all_flights)} unique booking(s):")
        print("-" * 58)

        for conf_code, emails in sorted(all_flights.items()):
            # Sort by date for display
            emails_sorted = sorted(emails, key=lambda x: x["email_date"], reverse=True)
            latest = emails_sorted[0]
            info = latest["flight_info"]

            # Check if this one will be forwarded or skipped
            is_skipped = any(s["confirmation"] == conf_code for s in skipped)
            will_forward = any(f["confirmation"] == conf_code for f in to_forward)
            is_update = will_forward and any(f.get("is_change") and f["confirmation"] == conf_code for f in to_forward)

            status = ""
            if is_skipped:
                status = " [SKIP - already imported]"
            elif is_update:
                status = " [UPDATE]"
            elif will_forward:
                status = " [NEW]"

            # Build route string with airport names
            route = ""
            if info.get("airports"):
                route = " -> ".join(get_airport_display(code) for code in info["airports"][:2])

            date_str = info["dates"][0] if info.get("dates") else "Unknown date"
            time_str = info["times"][0] if info.get("times") else ""

            print(f"\n  {conf_code}{status}")
            if route:
                print(f"    Route: {route}")
            if date_str != "Unknown date":
                if time_str:
                    print(f"    Date: {date_str} at {time_str}")
                else:
                    print(f"    Date: {date_str}")
            if info.get("flight_numbers"):
                print(f"    Flight: {', '.join(info['flight_numbers'][:2])}")

            if len(emails) > 1:
                print(f"    Emails: {len(emails)} found (using latest from {latest['email_date'].strftime('%m/%d/%Y %I:%M%p')})")
            else:
                print(f"    Email: {latest['email_date'].strftime('%m/%d/%Y %I:%M%p')}")

        print("\n" + "-" * 58)

    # Summary counts
    print(f"\n  Summary:")
    print(f"    New flights to import: {len(to_forward)}")
    print(f"    Already imported:      {len(skipped)}")

    print("\n" + "=" * 60)
    return len(to_forward) > 0


def forward_flights(config, to_forward, processed, dry_run):
    """Phase 4: Forward the selected flights to Flighty with comprehensive error handling."""
    import time

    forwarded = 0
    failed = 0
    total = len(to_forward)

    for idx, flight in enumerate(to_forward):
        try:
            # Delay between sends to avoid rate limiting (except first one)
            # AOL/Yahoo especially strict - need longer delays
            if idx > 0 and not dry_run:
                time.sleep(5)  # 5 seconds between emails

            conf = flight.get('confirmation') or 'Unknown'
            info = flight.get('flight_info', {})

            # Build flight details string with airport names (safely)
            details = []
            try:
                if info.get("airports"):
                    details.append(" -> ".join(get_airport_display(code) for code in info["airports"][:2]))
                if info.get("flight_numbers"):
                    details.append(f"Flight {', '.join(info['flight_numbers'][:2])}")
                if info.get("dates"):
                    date_part = info["dates"][0]
                    if info.get("times"):
                        date_part += f" at {info['times'][0]}"
                    details.append(date_part)
            except Exception:
                pass

            details_str = " | ".join(details) if details else "No details extracted"

            print(f"\n  [{idx + 1}/{total}] Forwarding: {conf}")
            print(f"    {details_str}")
            print(f"    Status: ", end="", flush=True)

            if dry_run:
                print("[DRY RUN - not sent]")
                forwarded += 1
                continue

            # Attempt to forward with error handling
            try:
                msg = flight.get("msg")
                from_addr = flight.get("from_addr", "")
                subject = flight.get("subject", "Flight Confirmation")

                if not msg:
                    print("FAILED (no email data)")
                    failed += 1
                    continue

                if forward_email(config, msg, from_addr, subject):
                    print("Sent!")
                    forwarded += 1

                    # Record as processed (safely)
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

                        # Save immediately after each successful forward (crash protection)
                        save_processed_flights(processed)
                    except Exception as save_err:
                        print(f"\n    Warning: Email sent but could not save progress: {save_err}")
                else:
                    print("FAILED")
                    failed += 1

            except Exception as send_err:
                print(f"FAILED ({send_err})")
                failed += 1

        except Exception as e:
            print(f"\n  [{idx + 1}/{total}] Error processing flight: {e}")
            failed += 1
            continue

    if failed > 0:
        print(f"\n  Note: {failed} email(s) failed to send. Run again to retry.")

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

    print()
    print("=" * 60)
    print("  FLIGHTY EMAIL FORWARDER")
    print("=" * 60)
    print(f"\n  Account:     {config['email']}")
    print(f"  Forward to:  {config['flighty_email']}")
    print(f"  Looking back: {config['days_back']} days")
    if dry_run:
        print(f"  Mode:        DRY RUN (no emails will be sent)")

    mail = connect_imap(config)
    if not mail:
        return

    try:
        processed = load_processed_flights()
        folders = config.get('check_folders', ['INBOX'])

        # Phase 1: Scan all folders for flight emails
        print(f"\n[Phase 1] Scanning for flight emails...")
        all_flights = {}
        all_skipped = []
        for folder in folders:
            print(f"\n  Folder: {folder}")
            folder_flights, folder_skipped = scan_for_flights(mail, config, folder, processed)
            # Merge results
            for conf, emails in folder_flights.items():
                if conf in all_flights:
                    all_flights[conf].extend(emails)
                else:
                    all_flights[conf] = emails
            all_skipped.extend(folder_skipped)

        print(f"\n  Found {len(all_flights)} unique confirmation(s)")

        # Phase 2: Select latest version of each flight
        print(f"\n[Phase 2] Selecting latest version of each flight...")
        to_forward, skipped = select_latest_flights(all_flights, processed)

        # Phase 3: Display summary
        has_flights = display_flight_summary(to_forward, skipped, all_flights)

        # Phase 4: Forward to Flighty
        if has_flights:
            if not dry_run:
                print(f"\n[Phase 4] Forwarding to Flighty...")
            else:
                print(f"\n[Phase 4] DRY RUN - showing what would be sent...")

            forwarded = forward_flights(config, to_forward, processed, dry_run)

            print(f"\n  Successfully forwarded: {forwarded}/{len(to_forward)}")
        else:
            print("\n  Nothing new to forward.")

        # Show helpful next steps
        print()
        print("-" * 60)
        print("  WHAT'S NEXT?")
        print("-" * 60)
        print(f"\n  You searched the last {config['days_back']} days.")
        print()
        print("  To search further back:")
        print("    python3 run.py --days 180    (6 months)")
        print("    python3 run.py --days 365    (1 year)")
        print()
        print("  Already imported flights will be skipped automatically.")
        print("  Run anytime to check for new flight emails!")
        print()

    except Exception as e:
        print(f"\n\n*** ERROR: {e} ***")
        print("\nThe script encountered an error. Your progress has been saved.")
        print("Run the script again to continue from where it left off.")
        print(f"\nTechnical details: {type(e).__name__}: {e}")
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
