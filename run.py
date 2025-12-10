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
from datetime import datetime, timedelta
from pathlib import Path

CONFIG_FILE = Path(__file__).parent / "config.json"
PROCESSED_FILE = Path(__file__).parent / "processed_flights.json"

# Confirmation code patterns for different airlines
CONFIRMATION_PATTERNS = [
    # Most airlines use 6-character alphanumeric codes
    r'\b([A-Z0-9]{6})\b',
    # Some use longer codes
    r'\b([A-Z0-9]{8})\b',
    # Ticket numbers (13 digits)
    r'\b(\d{13})\b',
]

# Airline patterns to detect flight confirmation emails
AIRLINE_PATTERNS = [
    {
        "name": "JetBlue",
        "from_patterns": [r"jetblue", r"@.*jetblue\.com"],
        "subject_patterns": [r"booking confirmation", r"itinerary", r"flight confirmation"],
        "confirmation_pattern": r"confirmation[:\s]+code[:\s]+([A-Z0-9]{6})|code\s+is\s+([A-Z0-9]{6})|[–-]\s*([A-Z0-9]{6})\s*$"
    },
    {
        "name": "Delta",
        "from_patterns": [r"delta", r"@.*delta\.com"],
        "subject_patterns": [r"ereceipt", r"trip confirmation", r"itinerary", r"booking confirmation"],
        "confirmation_pattern": r"confirmation[:\s#]+([A-Z0-9]{6})"
    },
    {
        "name": "United",
        "from_patterns": [r"united", r"@.*united\.com"],
        "subject_patterns": [r"confirmation", r"itinerary", r"trip details"],
        "confirmation_pattern": r"confirmation[:\s#]+([A-Z0-9]{6})"
    },
    {
        "name": "American Airlines",
        "from_patterns": [r"american", r"@.*aa\.com", r"americanairlines"],
        "subject_patterns": [r"reservation", r"confirmation", r"itinerary"],
        "confirmation_pattern": r"record locator[:\s]+([A-Z0-9]{6})|confirmation[:\s#]+([A-Z0-9]{6})"
    },
    {
        "name": "Southwest",
        "from_patterns": [r"southwest", r"@.*southwest\.com"],
        "subject_patterns": [r"confirmation", r"itinerary", r"trip"],
        "confirmation_pattern": r"confirmation[:\s#]+([A-Z0-9]{6})"
    },
    {
        "name": "Alaska Airlines",
        "from_patterns": [r"alaska", r"@.*alaskaair\.com"],
        "subject_patterns": [r"confirmation", r"itinerary"],
        "confirmation_pattern": r"confirmation[:\s#]+([A-Z0-9]{6})"
    },
    {
        "name": "Spirit",
        "from_patterns": [r"spirit", r"@.*spirit\.com"],
        "subject_patterns": [r"confirmation", r"itinerary"],
        "confirmation_pattern": r"confirmation[:\s#]+([A-Z0-9]{6})"
    },
    {
        "name": "Frontier",
        "from_patterns": [r"frontier", r"@.*flyfrontier\.com"],
        "subject_patterns": [r"confirmation", r"itinerary"],
        "confirmation_pattern": r"confirmation[:\s#]+([A-Z0-9]{6})"
    },
    {
        "name": "Hawaiian Airlines",
        "from_patterns": [r"hawaiian", r"@.*hawaiianairlines\.com"],
        "subject_patterns": [r"confirmation", r"itinerary"],
        "confirmation_pattern": r"confirmation[:\s#]+([A-Z0-9]{6})"
    },
    {
        "name": "Air Canada",
        "from_patterns": [r"aircanada", r"@.*aircanada\.com"],
        "subject_patterns": [r"confirmation", r"itinerary"],
        "confirmation_pattern": r"confirmation[:\s#]+([A-Z0-9]{6})"
    },
    {
        "name": "British Airways",
        "from_patterns": [r"british", r"@.*britishairways\.com", r"@.*ba\.com"],
        "subject_patterns": [r"confirmation", r"booking", r"itinerary"],
        "confirmation_pattern": r"booking reference[:\s]+([A-Z0-9]{6})|confirmation[:\s#]+([A-Z0-9]{6})"
    },
    {
        "name": "Lufthansa",
        "from_patterns": [r"lufthansa", r"@.*lufthansa\.com"],
        "subject_patterns": [r"confirmation", r"booking"],
        "confirmation_pattern": r"booking code[:\s]+([A-Z0-9]{6})|confirmation[:\s#]+([A-Z0-9]{6})"
    },
    {
        "name": "Emirates",
        "from_patterns": [r"emirates", r"@.*emirates\.com"],
        "subject_patterns": [r"confirmation", r"booking", r"itinerary"],
        "confirmation_pattern": r"booking reference[:\s]+([A-Z0-9]{6})|confirmation[:\s#]+([A-Z0-9]{6})"
    },
    {
        "name": "KLM",
        "from_patterns": [r"klm", r"@.*klm\.com"],
        "subject_patterns": [r"confirmation", r"booking", r"itinerary"],
        "confirmation_pattern": r"booking code[:\s]+([A-Z0-9]{6})|confirmation[:\s#]+([A-Z0-9]{6})"
    },
    {
        "name": "Air France",
        "from_patterns": [r"airfrance", r"@.*airfrance\.com"],
        "subject_patterns": [r"confirmation", r"booking", r"itinerary"],
        "confirmation_pattern": r"booking reference[:\s]+([A-Z0-9]{6})|confirmation[:\s#]+([A-Z0-9]{6})"
    },
    {
        "name": "Qantas",
        "from_patterns": [r"qantas", r"@.*qantas\.com"],
        "subject_patterns": [r"confirmation", r"booking", r"itinerary"],
        "confirmation_pattern": r"booking reference[:\s]+([A-Z0-9]{6})|confirmation[:\s#]+([A-Z0-9]{6})"
    },
    {
        "name": "Singapore Airlines",
        "from_patterns": [r"singapore", r"@.*singaporeair\.com"],
        "subject_patterns": [r"confirmation", r"booking", r"itinerary"],
        "confirmation_pattern": r"booking reference[:\s]+([A-Z0-9]{6})|confirmation[:\s#]+([A-Z0-9]{6})"
    },
    # Generic patterns - match any sender with flight-related subject
    {
        "name": "Generic Flight",
        "from_patterns": [r".*"],
        "subject_patterns": [
            r"flight.*confirmation",
            r"booking.*confirmation.*flight",
            r"e-?ticket",
            r"itinerary.*flight",
            r"your.*trip.*confirmation",
            r"airline.*confirmation"
        ],
        "confirmation_pattern": r"confirmation[:\s#]+([A-Z0-9]{6})|booking[:\s#]+([A-Z0-9]{6})"
    }
]


def load_config():
    """Load configuration from file."""
    if not CONFIG_FILE.exists():
        return None

    with open(CONFIG_FILE, 'r') as f:
        return json.load(f)


def load_processed_flights():
    """Load dictionary of processed flights with their details."""
    if PROCESSED_FILE.exists():
        with open(PROCESSED_FILE, 'r') as f:
            return json.load(f)
    return {"confirmations": {}, "email_ids": [], "content_hashes": []}


def save_processed_flights(processed):
    """Save processed flights data."""
    # Convert sets to lists for JSON serialization
    save_data = {
        "email_ids": list(processed.get("email_ids", set())),
        "content_hashes": list(processed.get("content_hashes", set())),
        "confirmations": processed.get("confirmations", {})
    }
    with open(PROCESSED_FILE, 'w') as f:
        json.dump(save_data, f, indent=2)


def extract_confirmation_code(subject, body, airline_pattern=None):
    """Extract confirmation code from email subject or body."""
    # First try subject line - often has format "... - ABCDEF" or "... ABCDEF"
    subject_match = re.search(r'[–-]\s*([A-Z0-9]{6})\s*$', subject)
    if subject_match:
        return subject_match.group(1)

    # Try airline-specific pattern if available
    if airline_pattern:
        for text in [subject, body]:
            match = re.search(airline_pattern, text, re.IGNORECASE)
            if match:
                # Return first non-None group
                for group in match.groups():
                    if group:
                        return group.upper()

    # Try generic patterns in subject first
    for pattern in CONFIRMATION_PATTERNS:
        match = re.search(pattern, subject)
        if match:
            code = match.group(1)
            # Filter out common false positives
            if code not in ['FLIGHT', 'TRAVEL', 'TICKET', 'CONFIRMATION']:
                return code

    # Then try body with context
    confirmation_contexts = [
        r'confirmation[:\s]+(?:code[:\s]+)?([A-Z0-9]{6})',
        r'booking[:\s]+(?:reference[:\s]+)?([A-Z0-9]{6})',
        r'record[:\s]+locator[:\s]+([A-Z0-9]{6})',
        r'PNR[:\s]+([A-Z0-9]{6})',
        r'reservation[:\s]+(?:number[:\s]+)?([A-Z0-9]{6})',
    ]

    for pattern in confirmation_contexts:
        match = re.search(pattern, body, re.IGNORECASE)
        if match:
            return match.group(1).upper()

    return None


def extract_flight_details(body):
    """Extract flight numbers, dates, times, and airports from email body."""

    # Extract airport codes (3 capital letters that look like airports)
    airport_pattern = r'\b([A-Z]{3})\b'
    # Common airport codes context
    airport_context = r'(?:from|to|depart|arrive|origin|destination)[\s:]+([A-Z]{3})|([A-Z]{3})\s*(?:→|->|to|–)\s*([A-Z]{3})'

    airports = []
    matches = re.findall(airport_context, body)
    for match in matches:
        airports.extend([m for m in match if m])

    # Also look for standalone 3-letter codes near flight context
    if not airports:
        # Look for patterns like "MCO → BOS" or "MCO to BOS"
        route_match = re.search(r'([A-Z]{3})\s*(?:→|->|to|–|-)\s*([A-Z]{3})', body)
        if route_match:
            airports = [route_match.group(1), route_match.group(2)]

    # Extract dates - be more specific
    date_patterns = [
        r'(\w{3},?\s+\w{3}\s+\d{1,2}(?:,?\s+\d{4})?)',  # Sun, Dec 07 or Sun Dec 07, 2025
        r'(\w+\s+\d{1,2},?\s+\d{4})',  # December 7, 2025
        r'(\d{1,2}/\d{1,2}/\d{2,4})',   # 12/07/2025
    ]

    dates = []
    for pattern in date_patterns:
        matches = re.findall(pattern, body)
        for m in matches[:3]:
            if m not in dates:
                dates.append(m)

    # Extract times
    time_pattern = r'(\d{1,2}:\d{2}\s*(?:am|pm)?)'
    times = re.findall(time_pattern, body, re.IGNORECASE)
    times = list(dict.fromkeys(times))[:4]  # Unique times, limit to 4

    # Extract flight numbers - be more careful
    flight_patterns = [
        r'flight\s*#?\s*(\d{1,4})\b',  # Flight 123 or Flight #123
        r'\bflight\s+([A-Z]{2}\s*\d{1,4})\b',  # Flight AA 123
    ]

    flights = []
    for pattern in flight_patterns:
        matches = re.findall(pattern, body, re.IGNORECASE)
        for m in matches:
            if m not in flights:
                flights.append(m.upper() if isinstance(m, str) else m)

    return {
        "flights": flights[:3],
        "dates": dates[:3],
        "times": times[:4],
        "airports": airports[:4]
    }


def generate_content_hash(subject, body):
    """Generate a hash of the email content for deduplication."""
    # Normalize content - remove whitespace variations, lowercase
    normalized = re.sub(r'\s+', ' ', (subject + body).lower().strip())
    # Remove common variable parts like dates/times that might differ in forwards
    normalized = re.sub(r'\d{1,2}:\d{2}(?::\d{2})?\s*(?:am|pm)?', '', normalized, flags=re.IGNORECASE)
    return hashlib.md5(normalized.encode()).hexdigest()[:16]


def is_duplicate_flight(processed, confirmation_code, content_hash, flight_details):
    """Check if this flight has already been processed."""
    # Check by content hash first (catches exact/near duplicates)
    if content_hash in processed.get("content_hashes", set()):
        return True, "duplicate content"

    # Check by confirmation code
    if confirmation_code:
        existing = processed.get("confirmations", {}).get(confirmation_code)
        if existing:
            # Same confirmation exists - check if flight details changed
            # (date, time, or airports different = allow through as a change)
            old_dates = set(existing.get("dates", []))
            old_times = set(existing.get("times", []))
            old_airports = set(existing.get("airports", []))

            new_dates = set(flight_details.get("dates", []))
            new_times = set(flight_details.get("times", []))
            new_airports = set(flight_details.get("airports", []))

            # If dates or airports changed, this is likely a rebooking - allow it
            if old_airports and new_airports and old_airports != new_airports:
                return False, None  # Different route, allow through

            if old_dates and new_dates and old_dates != new_dates:
                return False, None  # Different dates, allow through

            # Same confirmation, same basic details = duplicate
            return True, f"confirmation {confirmation_code}"

    return False, None


def is_flight_email(from_addr, subject):
    """Check if an email appears to be a flight confirmation."""
    from_addr = from_addr.lower() if from_addr else ""
    subject = subject.lower() if subject else ""

    for airline in AIRLINE_PATTERNS:
        from_match = any(re.search(pattern, from_addr, re.IGNORECASE)
                        for pattern in airline["from_patterns"])

        subject_match = any(re.search(pattern, subject, re.IGNORECASE)
                           for pattern in airline["subject_patterns"])

        if airline["name"] == "Generic Flight":
            if subject_match:
                return True, airline
        elif from_match and subject_match:
            return True, airline

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
                if content_type == "text/plain":
                    try:
                        body = part.get_payload(decode=True).decode('utf-8', errors='replace')
                    except:
                        pass
                elif content_type == "text/html":
                    try:
                        html_body = part.get_payload(decode=True).decode('utf-8', errors='replace')
                    except:
                        pass
    else:
        content_type = msg.get_content_type()
        try:
            payload = msg.get_payload(decode=True).decode('utf-8', errors='replace')
            if content_type == "text/plain":
                body = payload
            elif content_type == "text/html":
                html_body = payload
        except:
            pass

    return body, html_body


def forward_email(config, original_msg, from_addr, subject):
    """Forward an email to Flighty."""
    forward_msg = MIMEMultipart('mixed')
    forward_msg['From'] = config['email']
    forward_msg['To'] = config['flighty_email']
    forward_msg['Subject'] = f"Fwd: {subject}"

    body, html_body = get_email_body(original_msg)

    forward_text = f"""
---------- Forwarded message ---------
From: {from_addr}
Date: {original_msg.get('Date', 'Unknown')}
Subject: {subject}
To: {original_msg.get('To', 'Unknown')}

"""

    if body:
        forward_text += body
        text_part = MIMEText(forward_text, 'plain')
        forward_msg.attach(text_part)

    if html_body:
        html_forward = f"""
<div style="border-left: 2px solid #ccc; padding-left: 10px; margin: 10px 0;">
<p><strong>---------- Forwarded message ---------</strong></p>
<p>From: {from_addr}<br>
Date: {original_msg.get('Date', 'Unknown')}<br>
Subject: {subject}<br>
To: {original_msg.get('To', 'Unknown')}</p>
</div>
{html_body}
"""
        html_part = MIMEText(html_forward, 'html')
        forward_msg.attach(html_part)

    if original_msg.is_multipart():
        for part in original_msg.walk():
            content_disposition = str(part.get("Content-Disposition", ""))
            if "attachment" in content_disposition:
                forward_msg.attach(part)

    try:
        with smtplib.SMTP(config['smtp_server'], config['smtp_port']) as server:
            server.starttls()
            server.login(config['email'], config['password'])
            server.send_message(forward_msg)
        return True
    except Exception as e:
        print(f"    Error sending: {e}")
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
        print("Run 'python3 setup.py' to reconfigure.")
        return None


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


def search_folder(mail, config, folder, processed, dry_run):
    """Search a single folder for flight emails."""
    try:
        result, _ = mail.select(folder)
        if result != 'OK':
            print(f"  Could not open folder: {folder}")
            return 0, 0, 0, processed
    except:
        print(f"  Could not open folder: {folder}")
        return 0, 0, 0, processed

    since_date = (datetime.now() - timedelta(days=config['days_back'])).strftime("%d-%b-%Y")
    result, data = mail.search(None, f'(SINCE {since_date})')

    if result != 'OK':
        return 0, 0, 0, processed

    email_ids = data[0].split()
    total_emails = len(email_ids)
    print(f"  ({total_emails} emails to scan)")

    # Convert to sets for O(1) lookup
    if "email_ids" not in processed:
        processed["email_ids"] = set()
    elif isinstance(processed["email_ids"], list):
        processed["email_ids"] = set(processed["email_ids"])

    if "content_hashes" not in processed:
        processed["content_hashes"] = set()
    elif isinstance(processed["content_hashes"], list):
        processed["content_hashes"] = set(processed["content_hashes"])

    if "confirmations" not in processed:
        processed["confirmations"] = {}

    found = 0
    forwarded = 0
    skipped = 0

    for idx, email_id in enumerate(email_ids):
        email_id_str = f"{folder}:{email_id.decode()}"

        # Quick check - already processed this exact email ID
        if email_id_str in processed["email_ids"]:
            continue

        # OPTIMIZATION: Fetch only headers first (much faster than full email)
        result, header_data = mail.fetch(email_id, '(BODY[HEADER.FIELDS (FROM SUBJECT DATE)])')
        if result != 'OK':
            continue

        header_raw = header_data[0][1]
        header_msg = email.message_from_bytes(header_raw)

        from_addr = decode_header_value(header_msg.get('From', ''))
        subject = decode_header_value(header_msg.get('Subject', ''))

        # Quick filter - check if this looks like a flight email
        is_flight, airline = is_flight_email(from_addr, subject)

        if not is_flight:
            continue

        # Only now fetch the full email body
        result, msg_data = mail.fetch(email_id, '(RFC822)')
        if result != 'OK':
            continue

        found += 1
        raw_email = msg_data[0][1]
        msg = email.message_from_bytes(raw_email)

        # Get email body for analysis
        body, html_body = get_email_body(msg)
        full_body = body or html_body or ""

        # Extract confirmation code
        conf_pattern = airline.get("confirmation_pattern") if isinstance(airline, dict) else None
        confirmation_code = extract_confirmation_code(subject, full_body, conf_pattern)

        # Extract flight details
        flight_details = extract_flight_details(full_body)

        # Generate content hash
        content_hash = generate_content_hash(subject, full_body)

        # Check for duplicates
        is_dup, dup_reason = is_duplicate_flight(
            processed, confirmation_code, content_hash, flight_details
        )

        airline_name = airline["name"] if isinstance(airline, dict) else airline

        if is_dup:
            skipped += 1
            print(f"\n  [SKIP] {airline_name} - already processed ({dup_reason})")
            print(f"    Subject: {subject[:50]}...")
            if confirmation_code:
                print(f"    Confirmation: {confirmation_code}")
            # Mark email ID as seen even if skipped
            processed["email_ids"].add(email_id_str)
            continue

        # Show flight details
        print(f"\n  {'[DRY RUN] ' if dry_run else ''}Found: {airline_name}")
        print(f"    Subject: {subject[:60]}...")
        if confirmation_code:
            print(f"    Confirmation: {confirmation_code}")
        if flight_details.get("airports"):
            print(f"    Route: {' -> '.join(flight_details['airports'][:2])}")
        if flight_details.get("dates"):
            print(f"    Date: {flight_details['dates'][0]}")

        if not dry_run:
            if forward_email(config, msg, from_addr, subject):
                print(f"    -> Forwarded to Flighty")
                forwarded += 1

                # Record this flight immediately
                processed["email_ids"].add(email_id_str)
                processed["content_hashes"].add(content_hash)

                if confirmation_code:
                    processed["confirmations"][confirmation_code] = {
                        "flights": flight_details.get("flights", []),
                        "dates": flight_details.get("dates", []),
                        "times": flight_details.get("times", []),
                        "airports": flight_details.get("airports", []),
                        "forwarded_at": datetime.now().isoformat(),
                        "subject": subject[:100]
                    }
        else:
            forwarded += 1

    return found, forwarded, skipped, processed


def run(dry_run=False):
    """Main run function."""
    config = load_config()

    if not config:
        print("No configuration found!")
        print("Run 'python3 setup.py' to set up your email.")
        return

    if not config.get('email') or not config.get('password'):
        print("Email or password not configured!")
        print("Run 'python3 setup.py' to set up your email.")
        return

    print()
    print("=" * 50)
    print("  Flighty Email Forwarder")
    print("=" * 50)
    print()
    print(f"  Account:     {config['email']}")
    print(f"  Forward to:  {config['flighty_email']}")
    print(f"  Days back:   {config['days_back']}")
    if dry_run:
        print(f"  Mode:        DRY RUN (no emails will be sent)")
    print()

    mail = connect_imap(config)
    if not mail:
        return

    try:
        processed = load_processed_flights()
        folders = config.get('check_folders', ['INBOX'])

        total_found = 0
        total_forwarded = 0
        total_skipped = 0

        for folder in folders:
            print(f"Searching: {folder}")
            found, forwarded, skipped, processed = search_folder(
                mail, config, folder, processed, dry_run
            )
            total_found += found
            total_forwarded += forwarded
            total_skipped += skipped

        if not dry_run:
            save_processed_flights(processed)

        print()
        print("-" * 50)
        print(f"  Flight emails found:    {total_found}")
        print(f"  Already processed:      {total_skipped}")
        if dry_run:
            print(f"  Would be forwarded:     {total_forwarded}")
        else:
            print(f"  Successfully forwarded: {total_forwarded}")
        print("-" * 50)
        print()

    finally:
        mail.logout()


def main():
    args = sys.argv[1:]

    if "--setup" in args or "-s" in args:
        os.system(f"python3 {Path(__file__).parent / 'setup.py'}")
        return

    if "--reset" in args:
        if PROCESSED_FILE.exists():
            PROCESSED_FILE.unlink()
            print("Reset processed flights tracking.")
        return

    dry_run = "--dry-run" in args or "-d" in args

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

    run(dry_run=dry_run)


if __name__ == "__main__":
    main()
