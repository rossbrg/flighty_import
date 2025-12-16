#!/usr/bin/env python3
"""
POP3 Full Mailbox Scanner for AOL accounts.

AOL limits IMAP to 10,000 messages, but POP3 gives access to all emails.
This script scans the full mailbox history via POP3 and generates a PDF.

Usage:
  python3 pop3_full_scan.py              # Scan all messages
  python3 pop3_full_scan.py --resume     # Resume from last position
  python3 pop3_full_scan.py --batch N    # Process N messages then stop
  python3 pop3_full_scan.py --pdf        # Generate PDF from saved results
  python3 pop3_full_scan.py --status     # Show current progress
  python3 pop3_full_scan.py --clear      # Clear saved progress
"""

import poplib
import email
import json
import re
import sys
import time
import pickle
from datetime import datetime
from pathlib import Path
from email.utils import parsedate_to_datetime

SCRIPT_DIR = Path(__file__).parent
PROGRESS_FILE = SCRIPT_DIR / ".pop3_scan_progress.pkl"
RESULTS_FILE = SCRIPT_DIR / ".pop3_scan_results.pkl"

# Import flight detection modules
from flighty.airlines import is_flight_email
from flighty.parser import extract_flight_info
from flighty.email_handler import decode_header_value, get_email_body
from flighty.pdf_report import generate_pdf_report

# Comprehensive sender patterns for flight emails
SENDER_PATTERNS = [
    # US Airlines
    r'jetblue', r'delta', r'united', r'aa\.com', r'americanair', r'southwest',
    r'alaskaair', r'spirit', r'frontier', r'flyfrontier', r'hawaiian', r'suncountry', r'allegiant',
    r'breeze', r'breezeairways',
    # Canada
    r'aircanada', r'westjet',
    # Europe
    r'british.*air', r'ba\.com', r'lufthansa', r'airfrance', r'klm', r'iberia',
    r'virgin.*atlantic', r'aer.*lingus', r'icelandair', r'norwegian', r'ryanair',
    r'easyjet', r'vueling', r'swiss', r'austrian', r'finnair', r'tap.*portugal', r'sas\.se',
    # Middle East
    r'emirates', r'etihad', r'qatar', r'turkish', r'saudia', r'gulfair', r'omanair',
    # Asia
    r'singapore.*air', r'cathay', r'jal\.com', r'ana\.co', r'korean.*air', r'asiana',
    r'thaiairways', r'malaysia.*air', r'garuda', r'airindia', r'vietnam.*air',
    r'chinaairlines', r'evaair', r'airchina', r'chinaeastern', r'chinasouthern',
    r'philippine', r'airasia',
    # Australia/Pacific
    r'qantas', r'virgin.*australia', r'airnewzealand', r'fijiairways',
    # Latin America
    r'aeromexico', r'avianca', r'latam', r'copa', r'azul', r'gol\.com', r'volaris',
    # Booking sites
    r'expedia', r'kayak', r'priceline', r'orbitz', r'travelocity', r'hopper',
    r'booking\.com', r'trip\.com', r'skyscanner', r'cheapoair', r'google.*travel',
    r'momondo', r'kiwi\.com',
    # Corporate travel
    r'concur', r'egencia', r'tripactions', r'navan\.com', r'brex\.com', r'ramp\.com', r'travelperk',
]

SENDER_RE = re.compile('|'.join(SENDER_PATTERNS), re.IGNORECASE)


def load_progress():
    """Load saved scan progress."""
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE, 'rb') as f:
            return pickle.load(f)
    return {'last_msg': 0, 'scanned': 0, 'found': 0, 'errors': 0}


def save_progress(progress):
    """Save scan progress."""
    with open(PROGRESS_FILE, 'wb') as f:
        pickle.dump(progress, f)


def load_results():
    """Load saved flight results."""
    if RESULTS_FILE.exists():
        with open(RESULTS_FILE, 'rb') as f:
            return pickle.load(f)
    return []


def save_results(results):
    """Save flight results."""
    with open(RESULTS_FILE, 'wb') as f:
        pickle.dump(results, f)


def quick_header_check(headers_str):
    """Quick check if headers might be a flight email."""
    return SENDER_RE.search(headers_str) is not None


def connect_pop3(config):
    """Connect to POP3 server and return connection."""
    pop = poplib.POP3_SSL('pop.aol.com', 995)
    pop.user(config['email'])
    pop.pass_(config['password'])
    return pop


def scan_mailbox(config, start_msg=1, batch_size=None, resume=False):
    """Scan mailbox via POP3 and extract flight information."""

    # Load previous state if resuming
    if resume:
        progress = load_progress()
        results = load_results()
        if progress['last_msg'] > 0:
            start_msg = progress['last_msg'] + 1
            print(f"  Resuming from message {start_msg}")
            print(f"  Previous: {progress['scanned']} scanned, {progress['found']} flights found")
    else:
        progress = {'last_msg': 0, 'scanned': 0, 'found': 0, 'errors': 0}
        results = []

    # Connect to POP3
    print("\n  Connecting to POP3 server...")
    pop = connect_pop3(config)

    num_messages, total_size = pop.stat()
    print(f"  Total messages in mailbox: {num_messages:,}")
    print(f"  Mailbox size: {total_size/1024/1024:.1f} MB")

    end_msg = num_messages
    if batch_size:
        end_msg = min(start_msg + batch_size - 1, num_messages)
        print(f"\n  Batch mode: messages {start_msg:,} to {end_msg:,}")

    to_scan = end_msg - start_msg + 1
    print(f"\n  Scanning {to_scan:,} messages...")
    print("  " + "=" * 56)

    scan_start = time.time()
    batch_found = 0
    consecutive_errors = 0
    last_reconnect = start_msg

    for msg_num in range(start_msg, end_msg + 1):
        progress['scanned'] += 1

        # Progress update
        if progress['scanned'] % 100 == 0:
            elapsed = time.time() - scan_start
            scanned_this_batch = msg_num - start_msg + 1
            rate = scanned_this_batch / elapsed if elapsed > 0 else 0
            remaining = end_msg - msg_num
            eta_min = (remaining / rate / 60) if rate > 0 else 0
            pct = (scanned_this_batch / to_scan) * 100
            print(f"\r  [{pct:5.1f}%] Msg {msg_num:,}/{end_msg:,} | "
                  f"Found: {batch_found} | ETA: {eta_min:.0f} min   ", end="", flush=True)

        # Save progress periodically
        if msg_num % 500 == 0:
            progress['last_msg'] = msg_num
            save_progress(progress)
            save_results(results)

        # Reconnect every 5000 messages to avoid timeout
        if msg_num - last_reconnect >= 5000:
            try:
                pop.quit()
            except:
                pass
            print(f"\n  Reconnecting at message {msg_num:,}...")
            pop = connect_pop3(config)
            last_reconnect = msg_num
            consecutive_errors = 0

        try:
            # Step 1: Quick header check (fast)
            resp, header_lines, _ = pop.top(msg_num, 0)
            headers_raw = b'\n'.join(header_lines)
            headers_str = headers_raw.decode('utf-8', errors='ignore')
            consecutive_errors = 0  # Reset on success

            # Skip if not from a flight-related sender
            if not quick_header_check(headers_str):
                continue

            # Step 2: Parse headers
            from_match = re.search(r'^From:\s*(.+)$', headers_str, re.M | re.I)
            subj_match = re.search(r'^Subject:\s*(.+)$', headers_str, re.M | re.I)
            date_match = re.search(r'^Date:\s*(.+)$', headers_str, re.M | re.I)

            from_addr = from_match.group(1).strip() if from_match else ''
            subject = subj_match.group(1).strip() if subj_match else ''
            date_str = date_match.group(1).strip() if date_match else ''

            # Step 3: Check if it's actually a flight email
            is_flight, airline = is_flight_email(from_addr, subject)
            if not is_flight:
                continue

            # Step 4: Download full message
            resp, msg_lines, _ = pop.retr(msg_num)
            raw_email = b'\n'.join(msg_lines)
            msg = email.message_from_bytes(raw_email)

            # Parse email date
            try:
                email_date = parsedate_to_datetime(date_str)
            except:
                email_date = datetime.min

            # Extract body
            body, html_body = get_email_body(msg)

            # Step 5: Extract flight info
            flight_info = extract_flight_info(
                html_content=html_body or body or "",
                text_content=body,
                subject=subject,
                from_addr=from_addr,
                email_date=email_date
            )

            # Skip marketing emails
            email_type = flight_info.get("email_type", "")
            if email_type == "marketing":
                continue

            # Check if valid flight data was extracted
            confirmation = flight_info.get("confirmation")
            route = flight_info.get("route")
            dates = flight_info.get("dates", [])
            flight_numbers = flight_info.get("flight_numbers", [])

            # Need either confirmation code or route+date
            if not confirmation and not (route and dates):
                continue

            # Store flight data
            flight_data = {
                "msg_num": msg_num,
                "from_addr": from_addr,
                "subject": subject,
                "email_date": email_date,
                "confirmation": confirmation,
                "flight_info": flight_info,
                "airline": airline
            }

            results.append(flight_data)
            progress['found'] += 1
            batch_found += 1

            # Print found flight
            route_str = f"{route[0]}->{route[1]}" if route else "???"
            date_disp = dates[0] if dates else "???"
            conf_disp = confirmation or "N/A"
            flight_disp = flight_numbers[0] if flight_numbers else ""
            print(f"\n  ✓ {conf_disp:8} {flight_disp:8} {route_str:12} {date_disp}")

        except Exception as e:
            progress['errors'] += 1
            consecutive_errors += 1

            # If too many consecutive errors, reconnect
            if consecutive_errors >= 5:
                try:
                    pop.quit()
                except:
                    pass
                print(f"\n  Connection lost at {msg_num:,}, reconnecting...")
                try:
                    pop = connect_pop3(config)
                    last_reconnect = msg_num
                    consecutive_errors = 0
                except Exception as conn_err:
                    print(f"\n  Reconnection failed: {conn_err}")
                    # Save and exit
                    progress['last_msg'] = msg_num - 1
                    save_progress(progress)
                    save_results(results)
                    return results
            continue

    # Final save
    progress['last_msg'] = end_msg
    save_progress(progress)
    save_results(results)

    pop.quit()

    # Summary
    elapsed = time.time() - scan_start
    print(f"\n\n  {'=' * 56}")
    print(f"  Batch complete!")
    print(f"    Messages scanned:  {to_scan:,}")
    print(f"    Flights found:     {batch_found}")
    print(f"    Total flights:     {progress['found']}")
    print(f"    Errors:            {progress['errors']}")
    print(f"    Time:              {elapsed/60:.1f} minutes")

    if end_msg < num_messages:
        remaining = num_messages - end_msg
        print(f"\n  Remaining: {remaining:,} messages")
        print(f"  To continue: python3 pop3_full_scan.py --resume")

    return results


def normalize_datetime(dt):
    """Convert datetime to naive for comparison."""
    if dt is None:
        return datetime.min
    if dt.tzinfo is not None:
        return dt.replace(tzinfo=None)
    return dt


def normalize_flight_number(fn):
    """Normalize flight numbers to handle variations.

    JetBlue check-in emails use B60xxx format, bookings use B6xxx.
    """
    import re
    if not fn:
        return fn

    # JetBlue: B60xxx -> B6xxx (remove extra 0 after B6)
    match = re.match(r'^B60(\d{3,4})$', fn)
    if match:
        return f"B6{match.group(1)}"

    return fn


# City name to airport code mapping for common destinations
CITY_TO_AIRPORT = {
    'boston': 'BOS',
    'orlando': 'MCO',
    'los angeles': 'LAX',
    'chicago': 'ORD',
    'new york': 'JFK',
    'san francisco': 'SFO',
    'las vegas': 'LAS',
    'atlanta': 'ATL',
    'miami': 'MIA',
    'fort lauderdale': 'FLL',
    'denver': 'DEN',
    'seattle': 'SEA',
    'phoenix': 'PHX',
    'san diego': 'SAN',
    'tampa': 'TPA',
    'new orleans': 'MSY',
    'washington': 'DCA',
    'charlotte': 'CLT',
    'nashville': 'BNA',
    'austin': 'AUS',
    'dallas': 'DFW',
    'houston': 'IAH',
    'portland': 'PDX',
    'minneapolis': 'MSP',
    'detroit': 'DTW',
    'philadelphia': 'PHL',
    'baltimore': 'BWI',
    'san juan': 'SJU',
    'fort myers': 'RSW',
    'west palm': 'PBI',
    'palm beach': 'PBI',
    'jacksonville': 'JAX',
    'savannah': 'SAV',
    'buffalo': 'BUF',
    'cleveland': 'CLE',
    'raleigh': 'RDU',
    'providence': 'PVD',
    'hartford': 'BDL',
    'manchester': 'MHT',
    'worcester': 'ORH',
    'charleston': 'CHS',
    'wilmington': 'ILM',
    'pittsburgh': 'PIT',
    'rochester': 'ROC',
    'syracuse': 'SYR',
    'albany': 'ALB',
    'burlington': 'BTV',
    'portland, me': 'PWM',
    'bangor': 'BGR',
    'presque isle': 'PQI',
    'nantucket': 'ACK',
    'martha': 'MVY',  # Martha's Vineyard
}


def extract_destination_from_subject(subject):
    """Extract destination airport code from check-in email subject."""
    import re
    match = re.search(r'flight to ([A-Za-z\s\',]+?)[\.\!]?$', subject, re.IGNORECASE)
    if match:
        city = match.group(1).strip().lower()
        # Remove trailing punctuation
        city = re.sub(r'[\.\!]+$', '', city)
        # Check mapping
        for city_name, code in CITY_TO_AIRPORT.items():
            if city_name in city or city in city_name:
                return code
    return None


def deduplicate_flights(flights):
    """Deduplicate flights by confirmation code, extracting all unique segments.

    Handles:
    - Round-trip bookings: BOS->MCO and MCO->BOS as 2 separate flights
    - Same-day switches: keeps most recent flight number for route+date
    - Cancellations: excludes cancelled bookings entirely
    - Flight number normalization: B60xxx -> B6xxx
    - Booking vs check-in: prefers check-in route (leg-specific) over booking route
    """
    from collections import defaultdict

    # Group all emails by confirmation code
    by_conf = defaultdict(list)
    no_conf = []

    for flight in flights:
        conf = flight.get("confirmation")
        if conf:
            by_conf[conf].append(flight)
        else:
            no_conf.append(flight)

    result = []

    for conf, conf_flights in by_conf.items():
        # Check if this booking was cancelled
        is_cancelled = any(
            f.get("flight_info", {}).get("email_type") == "cancellation"
            for f in conf_flights
        )
        if is_cancelled:
            # Skip cancelled bookings entirely
            continue

        # Two-pass approach:
        # 1. Collect check-in emails (single date, accurate route per leg)
        # 2. Collect booking emails (may have multiple dates with only outbound route)
        # Prefer check-in data when available for a given date

        # Key: date -> {route, flight_number, email_date, is_checkin}
        flights_by_date = defaultdict(list)
        best_email = None
        best_date = datetime.min

        for flight in conf_flights:
            email_date = normalize_datetime(flight.get("email_date"))
            if email_date > best_date:
                best_date = email_date
                best_email = flight

            fi = flight.get("flight_info", {})
            route = fi.get("route")
            dates = fi.get("dates", [])
            flight_numbers = fi.get("flight_numbers", [])
            subject = flight.get("subject", "")
            subject_lower = subject.lower()

            # Check-in emails have single date and accurate route for that leg
            is_checkin = "check in" in subject_lower or "check-in" in subject_lower

            if route and dates:
                if is_checkin and len(dates) == 1:
                    # Check-in email: single date with correct route for this leg
                    fn = normalize_flight_number(flight_numbers[0] if flight_numbers else "")
                    flights_by_date[dates[0]].append({
                        "route": route,
                        "flight_number": fn,
                        "email_date": email_date,
                        "is_checkin": True
                    })
                else:
                    # Booking email: may have multiple dates
                    # Only use if we don't have check-in data for this date
                    for i, date in enumerate(dates):
                        fn = flight_numbers[i] if i < len(flight_numbers) else (flight_numbers[0] if flight_numbers else "")
                        fn = normalize_flight_number(fn)
                        flights_by_date[date].append({
                            "route": route,
                            "flight_number": fn,
                            "email_date": email_date,
                            "is_checkin": False
                        })
            elif route:
                fn = normalize_flight_number(flight_numbers[0] if flight_numbers else "")
                date_key = str(email_date.date()) if email_date != datetime.min else ""
                flights_by_date[date_key].append({
                    "route": route,
                    "flight_number": fn,
                    "email_date": email_date,
                    "is_checkin": is_checkin
                })
            elif is_checkin:
                # No route data but it's a check-in email - extract destination from subject
                dest_code = extract_destination_from_subject(subject)
                if dest_code:
                    # Use email date as the flight date
                    date_key = str(email_date.date()) if email_date != datetime.min else ""
                    fn = normalize_flight_number(flight_numbers[0] if flight_numbers else "")
                    # We only know destination, not origin - use (None, dest) as route marker
                    flights_by_date[date_key].append({
                        "route": (None, dest_code),
                        "dest_only": dest_code,
                        "flight_number": fn,
                        "email_date": email_date,
                        "is_checkin": True
                    })

        # Now deduplicate: for each date, prefer check-in data
        seen_segments = set()  # (route_or_dest, date) to avoid duplicates

        for date, entries in flights_by_date.items():
            # Separate check-in vs booking entries
            checkin_entries = [e for e in entries if e["is_checkin"]]
            booking_entries = [e for e in entries if not e["is_checkin"]]

            if checkin_entries:
                # Use check-in data (most accurate for this date)
                # Sort by email_date to get most recent in case of switches
                checkin_entries.sort(key=lambda x: x["email_date"], reverse=True)
                entry = checkin_entries[0]

                # Handle dest-only entries (older emails without full route)
                if entry.get("dest_only"):
                    seg_key = (entry["dest_only"], date)
                    route_for_output = None
                    airports = [entry["dest_only"]]
                else:
                    seg_key = (entry["route"], date)
                    route_for_output = entry["route"]
                    airports = list(entry["route"]) if entry["route"] else []

                if seg_key not in seen_segments:
                    seen_segments.add(seg_key)
                    result.append({
                        "confirmation": conf,
                        "email_date": best_email.get("email_date") if best_email else None,
                        "from_addr": best_email.get("from_addr", "") if best_email else "",
                        "subject": best_email.get("subject", "") if best_email else "",
                        "airline": best_email.get("airline", "") if best_email else "",
                        "flight_info": {
                            "confirmation": conf,
                            "route": route_for_output,
                            "dates": [date] if date else [],
                            "flight_numbers": [entry["flight_number"]] if entry["flight_number"] else [],
                            "airports": airports,
                            "dest_only": entry.get("dest_only"),
                            "email_type": "booking"
                        }
                    })
            elif booking_entries:
                # No check-in data, use booking data
                # Sort by email_date for most recent
                booking_entries.sort(key=lambda x: x["email_date"], reverse=True)
                entry = booking_entries[0]
                seg_key = (entry["route"], date)
                if seg_key not in seen_segments:
                    seen_segments.add(seg_key)
                    result.append({
                        "confirmation": conf,
                        "email_date": best_email.get("email_date") if best_email else None,
                        "from_addr": best_email.get("from_addr", "") if best_email else "",
                        "subject": best_email.get("subject", "") if best_email else "",
                        "airline": best_email.get("airline", "") if best_email else "",
                        "flight_info": {
                            "confirmation": conf,
                            "route": entry["route"],
                            "dates": [date] if date else [],
                            "flight_numbers": [entry["flight_number"]] if entry["flight_number"] else [],
                            "airports": list(entry["route"]) if entry["route"] else [],
                            "email_type": "booking"
                        }
                    })

        # If no flights found from dates, use best email
        if not flights_by_date and best_email:
            result.append(best_email)

    return result + no_conf


def generate_pdf_from_results():
    """Generate PDF from saved scan results."""
    results = load_results()

    if not results:
        print("  No results found. Run the scan first.")
        return

    print(f"\n  Loaded {len(results)} flight emails")

    # Deduplicate
    unique_flights = deduplicate_flights(results)
    print(f"  After deduplication: {len(unique_flights)} unique flights")

    # Sort by email date
    unique_flights.sort(key=lambda x: normalize_datetime(x.get("email_date")))

    # Generate PDF
    raw_dir = SCRIPT_DIR / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    pdf_path = raw_dir / f"flight_history_{timestamp}.pdf"

    print(f"\n  Generating PDF...")
    result = generate_pdf_report(unique_flights, pdf_path, "Complete Flight History")

    if result:
        print(f"  ✓ PDF saved to: {result}")
    else:
        print("  ✗ PDF generation failed")


def show_status():
    """Show current scan status."""
    progress = load_progress()
    results = load_results()

    print("\n  POP3 Scan Status")
    print("  " + "=" * 40)
    print(f"  Last message:    {progress.get('last_msg', 0):,}")
    print(f"  Messages scanned: {progress.get('scanned', 0):,}")
    print(f"  Flights found:    {progress.get('found', 0)}")
    print(f"  Errors:           {progress.get('errors', 0)}")
    print(f"  Results saved:    {len(results)}")

    if results:
        # Show date range
        dates = [r.get("email_date") for r in results if r.get("email_date")]
        if dates:
            try:
                # Handle mixed timezone-aware and naive datetimes
                min_date = min(dates, key=lambda d: d.replace(tzinfo=None) if d.tzinfo else d)
                max_date = max(dates, key=lambda d: d.replace(tzinfo=None) if d.tzinfo else d)
                print(f"\n  Date range: {min_date.strftime('%Y-%m-%d')} to {max_date.strftime('%Y-%m-%d')}")
            except:
                pass


def clear_progress():
    """Clear saved progress and results."""
    if PROGRESS_FILE.exists():
        PROGRESS_FILE.unlink()
    if RESULTS_FILE.exists():
        RESULTS_FILE.unlink()
    print("  Progress and results cleared.")


def main():
    # Load config
    config_file = SCRIPT_DIR / "config.json"
    if not config_file.exists():
        print("Error: config.json not found. Run 'python3 run.py --setup' first.")
        sys.exit(1)

    with open(config_file) as f:
        config = json.load(f)

    # Parse arguments
    resume = '--resume' in sys.argv
    batch_size = None
    start_msg = 1

    for i, arg in enumerate(sys.argv):
        if arg == '--batch' and i + 1 < len(sys.argv):
            batch_size = int(sys.argv[i + 1])
        elif arg == '--start' and i + 1 < len(sys.argv):
            start_msg = int(sys.argv[i + 1])
        elif arg == '--pdf':
            generate_pdf_from_results()
            return
        elif arg == '--status':
            show_status()
            return
        elif arg == '--clear':
            clear_progress()
            return

    # Header
    print()
    print("=" * 60)
    print("  POP3 FULL MAILBOX SCANNER")
    print("=" * 60)
    print()
    print("  This scans ALL emails in your AOL inbox via POP3.")
    print("  Progress is saved every 500 messages - you can stop and resume.")
    print()
    print("  Tip: Use --batch 10000 to process in chunks.")
    print()

    # Run scan
    results = scan_mailbox(config, start_msg=start_msg, batch_size=batch_size, resume=resume)

    # Check if scan is complete
    progress = load_progress()
    all_results = load_results()

    print(f"\n  Total flights found so far: {len(all_results)}")
    print()
    print("  To generate PDF: python3 pop3_full_scan.py --pdf")
    print("  To check status: python3 pop3_full_scan.py --status")


if __name__ == "__main__":
    main()
