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


VERSION = "1.1.0"
GITHUB_REPO = "drewtwitchell/flighty_import"
UPDATE_FILES = ["run.py", "setup.py"]


def auto_update():
    """Check for and apply updates from GitHub (no git required)."""
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
            return

        print(f"Update available: v{VERSION} -> v{latest_version}")
        print("Downloading updates...", end="", flush=True)

        # Download updated files
        for filename in UPDATE_FILES:
            file_url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/{filename}"
            try:
                with urllib.request.urlopen(file_url, timeout=10) as response:
                    content = response.read().decode('utf-8')
                    file_path = SCRIPT_DIR / filename
                    with open(file_path, 'w', encoding='utf-8') as f:
                        f.write(content)
                    print(f" {filename}", end="", flush=True)
            except Exception as e:
                print(f" (failed: {filename})", end="", flush=True)

        print("\nUpdated successfully! Restart to use new version.")
        print()

    except urllib.error.URLError:
        print("No internet connection - skipping update check")
        print()
    except Exception as e:
        print(f"Could not check for updates - continuing")
        print()

# Valid 3-letter airport codes (common ones to filter out false positives)
# We'll be more restrictive - only match codes that appear in flight context
AIRPORT_CONTEXT_WORDS = {'from', 'to', 'depart', 'arrive', 'origin', 'destination', 'terminal'}

# Words that look like airport codes but aren't
FALSE_AIRPORT_CODES = {
    'THE', 'AND', 'FOR', 'YOU', 'ARE', 'NOT', 'ALL', 'CAN', 'HAD', 'HER',
    'WAS', 'ONE', 'OUR', 'OUT', 'DAY', 'GET', 'HAS', 'HIM', 'HIS', 'HOW',
    'ITS', 'MAY', 'NEW', 'NOW', 'OLD', 'SEE', 'WAY', 'WHO', 'BOY', 'DID',
    'PRE', 'DET', 'END', 'USE', 'SAY', 'SHE', 'TWO', 'WAR', 'SET', 'GOT',
    'LET', 'PUT', 'SAT', 'TOP', 'ANY', 'YET', 'TRY', 'ASK', 'BIG', 'OWN',
    'SUN', 'MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT', 'JAN', 'FEB', 'MAR',
    'APR', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC', 'EST', 'PST',
    'CST', 'MST', 'PDT', 'CDT', 'MDT', 'EDT', 'GMT', 'UTC', 'USA', 'USD',
    'FEE', 'BAG', 'PER', 'REF', 'NON', 'TAX', 'VIA', 'WWW', 'COM', 'NET',
    'ORG', 'GOV', 'MIL', 'EDU', 'INT', 'BIZ', 'APP', 'SMS', 'FAX', 'TEL',
}

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
    """Load configuration from file."""
    if not CONFIG_FILE.exists():
        return None
    with open(CONFIG_FILE, 'r') as f:
        return json.load(f)


def load_processed_flights():
    """Load dictionary of processed flights."""
    if PROCESSED_FILE.exists():
        with open(PROCESSED_FILE, 'r') as f:
            data = json.load(f)
            # Convert lists to sets for faster lookup
            if isinstance(data.get("content_hashes"), list):
                data["content_hashes"] = set(data["content_hashes"])
            return data
    return {"confirmations": {}, "content_hashes": set()}


def save_processed_flights(processed):
    """Save processed flights data."""
    save_data = {
        "content_hashes": list(processed.get("content_hashes", set())),
        "confirmations": processed.get("confirmations", {})
    }
    with open(PROCESSED_FILE, 'w') as f:
        json.dump(save_data, f, indent=2)


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
    """Extract flight information from email body."""
    info = {
        "airports": [],
        "flight_numbers": [],
        "dates": [],
        "times": []
    }

    # Extract airport codes - look for route patterns like "MCO → BOS" or "Orlando (MCO)"
    # Pattern 1: City (CODE) format
    city_code_pattern = r'([A-Za-z\s]+)\s*\(([A-Z]{3})\)'
    city_matches = re.findall(city_code_pattern, body)
    for city, code in city_matches:
        if code not in FALSE_AIRPORT_CODES:
            info["airports"].append(code)

    # Pattern 2: CODE → CODE or CODE to CODE (arrow/to between codes)
    route_pattern = r'\b([A-Z]{3})\s*(?:→|->|►|to|–|-)\s*([A-Z]{3})\b'
    route_matches = re.findall(route_pattern, body)
    for origin, dest in route_matches:
        if origin not in FALSE_AIRPORT_CODES and dest not in FALSE_AIRPORT_CODES:
            if origin not in info["airports"]:
                info["airports"].append(origin)
            if dest not in info["airports"]:
                info["airports"].append(dest)

    # Remove duplicates while preserving order
    seen = set()
    unique_airports = []
    for apt in info["airports"]:
        if apt not in seen:
            seen.add(apt)
            unique_airports.append(apt)
    info["airports"] = unique_airports[:4]  # Limit to 4 airports

    # Extract flight numbers - "Flight 123" or "Flight # 652"
    flight_pattern = r'[Ff]light\s*#?\s*(\d{1,4})\b'
    flight_matches = re.findall(flight_pattern, body)
    info["flight_numbers"] = list(dict.fromkeys(flight_matches))[:4]

    # Extract dates - look for specific patterns
    # Pattern: "Sun, Dec 07" or "Dec 07, 2025" or "December 7, 2025"
    date_patterns = [
        r'([A-Z][a-z]{2},?\s+[A-Z][a-z]{2}\s+\d{1,2}(?:,?\s+\d{4})?)',  # Sun, Dec 07
        r'([A-Z][a-z]+\s+\d{1,2},?\s+\d{4})',  # December 7, 2025
    ]
    for pattern in date_patterns:
        matches = re.findall(pattern, body)
        for m in matches:
            if m not in info["dates"]:
                info["dates"].append(m)
    info["dates"] = info["dates"][:3]

    # Extract times - "6:00pm" or "18:00"
    time_pattern = r'\b(\d{1,2}:\d{2}\s*(?:am|pm|AM|PM)?)\b'
    time_matches = re.findall(time_pattern, body)
    info["times"] = list(dict.fromkeys(time_matches))[:4]

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
    """Forward an email to Flighty."""
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

    try:
        with smtplib.SMTP(config['smtp_server'], config['smtp_port']) as server:
            server.starttls()
            server.login(config['email'], config['password'])
            server.send_message(forward_msg)
        return True
    except Exception as e:
        print(f"      Error sending: {e}")
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


def scan_for_flights(mail, config, folder):
    """
    Phase 1: Scan folder and collect all flight emails.
    Uses server-side IMAP search for speed.
    Returns dict of confirmation_code -> list of email data
    """
    flights_found = {}  # confirmation_code -> list of {email_id, date, subject, ...}

    try:
        result, _ = mail.select(folder)
        if result != 'OK':
            print(f"    Could not open folder: {folder}")
            return flights_found
    except:
        print(f"    Could not open folder: {folder}")
        return flights_found

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
        return flights_found

    print(f"\n    Found {total} airline emails, analyzing...", flush=True)

    flight_count = 0

    for idx, email_id in enumerate(email_ids):
        # Show progress with percentage
        pct = int((idx + 1) / total * 100)
        print(f"\r    Analyzing: {idx + 1}/{total} ({pct}%)", end="", flush=True)

        # Fetch full email
        result, msg_data = mail.fetch(email_id, '(RFC822)')
        if result != 'OK':
            continue

        raw_email = msg_data[0][1]
        msg = email.message_from_bytes(raw_email)

        from_addr = decode_header_value(msg.get('From', ''))
        subject = decode_header_value(msg.get('Subject', ''))
        date_str = msg.get('Date', '')

        # Verify it's actually a flight email
        is_flight, airline = is_flight_email(from_addr, subject)
        if not is_flight:
            continue

        flight_count += 1

        body, html_body = get_email_body(msg)
        full_body = body or html_body or ""

        # Extract details
        confirmation = extract_confirmation_code(subject, full_body)
        flight_info = extract_flight_info(full_body)
        content_hash = generate_content_hash(subject, full_body)
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

    print(f"\r    Analyzing: {total}/{total} (100%) - {flight_count} flight confirmations found")
    return flights_found


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

            # Build route string
            route = ""
            if info.get("airports"):
                route = " -> ".join(info["airports"][:2])

            date_str = info["dates"][0] if info.get("dates") else "Unknown date"

            print(f"\n  {conf_code}{status}")
            if route:
                print(f"    Route: {route}")
            print(f"    Date: {date_str}")
            if info.get("flight_numbers"):
                print(f"    Flight: {', '.join(info['flight_numbers'][:2])}")

            if len(emails) > 1:
                print(f"    Emails: {len(emails)} found (using latest from {latest['email_date'].strftime('%m/%d %I:%M%p')})")
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
    """Phase 4: Forward the selected flights to Flighty."""
    forwarded = 0

    for flight in to_forward:
        conf = flight['confirmation'] or 'Unknown'
        info = flight['flight_info']

        # Build flight details string
        details = []
        if info.get("airports"):
            details.append(" -> ".join(info["airports"][:2]))
        if info.get("flight_numbers"):
            details.append(f"Flight {', '.join(info['flight_numbers'][:2])}")
        if info.get("dates"):
            details.append(info["dates"][0])

        details_str = " | ".join(details) if details else "No details"

        print(f"\n  Forwarding: {conf}")
        print(f"    {details_str}")
        print(f"    Status: ", end="")

        if dry_run:
            print("[DRY RUN - not sent]")
            forwarded += 1
            continue

        if forward_email(config, flight["msg"], flight["from_addr"], flight["subject"]):
            print("Sent!")
            forwarded += 1

            # Record as processed
            processed["content_hashes"].add(flight["content_hash"])
            if flight["confirmation"]:
                processed["confirmations"][flight["confirmation"]] = {
                    "fingerprint": flight.get("fingerprint"),
                    "airports": flight["flight_info"].get("airports", []),
                    "dates": flight["flight_info"].get("dates", []),
                    "flight_numbers": flight["flight_info"].get("flight_numbers", []),
                    "forwarded_at": datetime.now().isoformat(),
                    "subject": flight["subject"][:100]
                }
        else:
            print("FAILED")

    return forwarded


def run(dry_run=False):
    """Main run function."""
    config = load_config()

    if not config:
        print("No configuration found! Run 'python3 setup.py' first.")
        return

    if not config.get('email') or not config.get('password'):
        print("Email or password not configured! Run 'python3 setup.py'.")
        return

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
        for folder in folders:
            print(f"\n  Folder: {folder}")
            folder_flights = scan_for_flights(mail, config, folder)
            # Merge results
            for conf, emails in folder_flights.items():
                if conf in all_flights:
                    all_flights[conf].extend(emails)
                else:
                    all_flights[conf] = emails

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

            if not dry_run:
                save_processed_flights(processed)

            print(f"\n  Successfully forwarded: {forwarded}/{len(to_forward)}")
        else:
            print("\n  Nothing to forward.")

        print()

    finally:
        mail.logout()


def main():
    args = sys.argv[1:]

    if "--setup" in args or "-s" in args:
        os.system(f"python3 {SCRIPT_DIR / 'setup.py'}")
        return

    if "--reset" in args:
        if PROCESSED_FILE.exists():
            PROCESSED_FILE.unlink()
            print("Reset processed flights tracking.")
        return

    if "--help" in args or "-h" in args:
        print("""
Flighty Email Forwarder

Usage:
    python3 run.py              Run and forward flight emails
    python3 run.py --dry-run    Test without forwarding
    python3 run.py --setup      Run setup wizard
    python3 run.py --reset      Clear processed flights history
    python3 run.py --help       Show this help

First time? Run: python3 setup.py
""")
        return

    # Auto-update before running
    auto_update()

    dry_run = "--dry-run" in args or "-d" in args
    run(dry_run=dry_run)


if __name__ == "__main__":
    main()
