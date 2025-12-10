"""
Email scanning functionality.

Scans email folders for flight confirmation emails using IMAP.
"""

import email
import time
from datetime import datetime, timedelta

from .airports import VALID_AIRPORT_CODES
from .airlines import is_flight_email
from .parser import (
    extract_confirmation_code,
    extract_flight_info,
    generate_content_hash,
    create_flight_fingerprint
)
from .email_handler import decode_header_value, get_email_body, parse_email_date


# IMAP search queries for airlines and booking sites
AIRLINE_SEARCHES = [
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

SUBJECT_SEARCHES = [
    ('Flight Conf', 'SUBJECT "flight confirmation"'),
    ('Itinerary', 'SUBJECT "itinerary"'),
    ('E-Ticket', 'SUBJECT "e-ticket"'),
    ('Booking Conf', 'SUBJECT "booking confirmation"'),
    ('Trip Conf', 'SUBJECT "trip confirmation"'),
    ('Travel Conf', 'SUBJECT "travel confirmation"'),
]

PARTIAL_SENDER_SEARCHES = [
    ('JetBlue+', 'FROM "jetblue"'),
    ('Delta+', 'FROM "delta"'),
    ('United+', 'FROM "united"'),
    ('American+', 'FROM "american"'),
    ('Southwest+', 'FROM "southwest"'),
]


def scan_for_flights(mail, config, folder, processed):
    """Scan folder and collect all flight emails.

    Uses server-side IMAP search for speed.
    Skips already-processed confirmations for performance.

    Args:
        mail: IMAP connection
        config: Config dict
        folder: Folder name to scan
        processed: Dict of already processed flights

    Returns:
        Tuple: (flights_found dict, skipped_confirmations list)
    """
    flights_found = {}  # confirmation_code -> list of {email_id, date, subject, ...}
    skipped_confirmations = []
    already_processed = processed.get("confirmations", {})
    processed_hashes = processed.get("content_hashes", set())

    try:
        result, _ = mail.select(folder)
        if result != 'OK':
            print(f"    Could not open folder: {folder}")
            return flights_found, skipped_confirmations
    except Exception:
        print(f"    Could not open folder: {folder}")
        return flights_found, skipped_confirmations

    since_date = (datetime.now() - timedelta(days=config['days_back'])).strftime("%d-%b-%Y")

    all_email_ids = set()

    # ============================================
    # STEP A: Ask email server for matching emails
    # ============================================
    print()
    print("    ┌─────────────────────────────────────────────────────────┐")
    print("    │  STEP A: Asking your email server for potential matches │")
    print("    └─────────────────────────────────────────────────────────┘")
    print()
    print("    What's happening: Your email server is searching for emails")
    print("    from airlines and booking sites. This is fast because the")
    print("    server does the work (we're not downloading anything yet).")
    print()

    # Search by sender
    found_sources = []
    search_count = 0
    total_searches = len(AIRLINE_SEARCHES) + len(SUBJECT_SEARCHES) + len(PARTIAL_SENDER_SEARCHES)

    for source_name, search_term in AIRLINE_SEARCHES:
        search_count += 1
        print(f"\r    Searching ({search_count}/{total_searches}): {source_name}...          ", end="", flush=True)
        try:
            result, data = mail.search(None, f'(SINCE {since_date} {search_term})')
            if result == 'OK' and data[0]:
                ids = data[0].split()
                if ids:
                    all_email_ids.update(ids)
                    found_sources.append(f"{source_name}({len(ids)})")
        except Exception:
            pass

    # Search by subject
    for subject_name, search_term in SUBJECT_SEARCHES:
        search_count += 1
        print(f"\r    Searching ({search_count}/{total_searches}): {subject_name}...          ", end="", flush=True)
        try:
            result, data = mail.search(None, f'(SINCE {since_date} {search_term})')
            if result == 'OK' and data[0]:
                ids = data[0].split()
                if ids:
                    new_ids = set(ids) - all_email_ids
                    if new_ids:
                        all_email_ids.update(new_ids)
                        found_sources.append(f"{subject_name}({len(new_ids)})")
        except Exception:
            pass

    # Also search for partial matches (catches subdomains)
    for source_name, search_term in PARTIAL_SENDER_SEARCHES:
        search_count += 1
        print(f"\r    Searching ({search_count}/{total_searches}): {source_name}...          ", end="", flush=True)
        try:
            result, data = mail.search(None, f'(SINCE {since_date} {search_term})')
            if result == 'OK' and data[0]:
                ids = data[0].split()
                if ids:
                    new_ids = set(ids) - all_email_ids
                    if new_ids:
                        all_email_ids.update(new_ids)
                        found_sources.append(f"{source_name}({len(new_ids)})")
        except Exception:
            pass

    print(f"\r    Server search complete!                                    ")
    print()

    email_ids = list(all_email_ids)
    total = len(email_ids)

    if total == 0:
        print("    Result: No emails found from airlines or booking sites.")
        return flights_found, skipped_confirmations

    # Show what was found
    print(f"    Result: Found {total} emails that MIGHT be flight-related")
    if found_sources:
        top_sources = found_sources[:5]
        print(f"    Sources: {', '.join(top_sources)}" + (f" +{len(found_sources)-5} more" if len(found_sources) > 5 else ""))
    print()

    # ============================================
    # STEP B: Quick header check
    # ============================================
    print("    ┌─────────────────────────────────────────────────────────┐")
    print("    │  STEP B: Quick check of email headers (fast)            │")
    print("    └─────────────────────────────────────────────────────────┘")
    print()
    print("    What's happening: Downloading just the subject line of each")
    print("    email (NOT the full email). This lets us quickly filter out")
    print("    non-flight emails like newsletters and promotions.")
    print()

    flight_candidates = []
    scan_start_time = time.time()

    for idx, email_id in enumerate(email_ids):
        try:
            # Show progress with time estimate
            elapsed = time.time() - scan_start_time
            if idx > 5:
                avg_per = elapsed / idx
                remaining = avg_per * (total - idx)
                if remaining > 60:
                    time_str = f"~{int(remaining//60)}m {int(remaining%60)}s left"
                else:
                    time_str = f"~{int(remaining)}s left"
            else:
                time_str = "calculating..."

            pct = int((idx + 1) / total * 100)
            print(f"\r    Checking: {idx + 1}/{total} ({pct}%) | {time_str} | Flight emails found: {len(flight_candidates)}   ", end="", flush=True)

            # Fetch ONLY headers (much faster than full email)
            try:
                result, msg_data = mail.fetch(email_id, '(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])')
                if result != 'OK' or not msg_data or not msg_data[0]:
                    continue
            except Exception:
                continue

            # Parse headers
            header_data = msg_data[0][1]
            if not header_data:
                continue

            header_msg = email.message_from_bytes(header_data)
            from_addr = decode_header_value(header_msg.get('From', ''))
            subject = decode_header_value(header_msg.get('Subject', ''))
            date_str = header_msg.get('Date', '')

            # Check if this looks like a flight email based on headers
            is_flight, airline = is_flight_email(from_addr, subject)
            if is_flight:
                flight_candidates.append({
                    'email_id': email_id,
                    'from_addr': from_addr,
                    'subject': subject,
                    'date_str': date_str,
                    'airline': airline
                })

        except Exception:
            continue

    phase1_time = time.time() - scan_start_time
    print(f"\r    Header check complete!                                              ")
    print()
    print(f"    Time taken: {int(phase1_time)} seconds")
    print(f"    Result: {len(flight_candidates)} emails are actual flight confirmations")
    print(f"    (Filtered out {total - len(flight_candidates)} newsletters/promotions/other)")
    print()

    if not flight_candidates:
        print("    No flight confirmations found in this folder.")
        return flights_found, skipped_confirmations

    # ============================================
    # STEP C: Download flight email details
    # ============================================
    print("    ┌─────────────────────────────────────────────────────────┐")
    print("    │  STEP C: Downloading flight confirmation details        │")
    print("    └─────────────────────────────────────────────────────────┘")
    print()
    print(f"    What's happening: Now downloading the full content of the")
    print(f"    {len(flight_candidates)} flight emails to extract confirmation codes,")
    print(f"    airports, dates, and flight numbers.")
    print()

    flight_count = 0
    skipped_count = 0
    phase2_start = time.time()

    for idx, candidate in enumerate(flight_candidates):
        try:
            # Show progress with time estimate
            elapsed = time.time() - phase2_start
            if idx > 0:
                avg_per = elapsed / idx
                remaining = avg_per * (len(flight_candidates) - idx)
                remaining_secs = int(remaining)
                if remaining_secs > 60:
                    time_str = f"~{remaining_secs // 60}m {remaining_secs % 60}s left"
                else:
                    time_str = f"~{remaining_secs}s left"
            else:
                time_str = "starting..."

            pct = int((idx + 1) / len(flight_candidates) * 100)
            print(f"\r    Processing: {idx + 1}/{len(flight_candidates)} ({pct}%) | {time_str}                    ", end="", flush=True)

            # Now fetch full email content
            email_id = candidate['email_id']
            try:
                result, msg_data = mail.fetch(email_id, '(RFC822)')
                if result != 'OK' or not msg_data or not msg_data[0]:
                    continue
            except Exception:
                continue

            raw_email = msg_data[0][1]
            if not raw_email:
                continue

            msg = email.message_from_bytes(raw_email)
            from_addr = candidate['from_addr']
            subject = candidate['subject']
            date_str = candidate['date_str']
            airline = candidate['airline']

            body, html_body = get_email_body(msg)
            full_body = body or html_body or ""

            # Parse email date first - needed for correct year on flight dates
            email_date = parse_email_date(date_str)

            # Extract confirmation code
            confirmation = extract_confirmation_code(subject, full_body)
            content_hash = generate_content_hash(subject, full_body)

            # Extract flight details (pass email_date so we use correct year)
            flight_info = extract_flight_info(full_body, email_date=email_date)

            # Build display string for this flight
            conf_display = confirmation if confirmation else "------"
            airports = flight_info.get("airports", [])
            valid_airports = [code for code in airports if code in VALID_AIRPORT_CODES]
            route = " → ".join(valid_airports[:2]) if valid_airports else ""
            dates = flight_info.get("dates", [])
            date_display = ""
            if dates:
                d = dates[0]
                months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
                if any(m in d for m in months) or '/' in d or '-' in d:
                    date_display = d

            flight_display = f"{conf_display}"
            if route:
                flight_display += f"  {route}"
            if date_display:
                flight_display += f"  {date_display}"

            # Skip if already processed
            if confirmation and confirmation in already_processed:
                if content_hash in processed_hashes:
                    skipped_count += 1
                    if confirmation not in skipped_confirmations:
                        skipped_confirmations.append(confirmation)
                    # Show skipped flight
                    print(f"\r    [SKIP] {flight_display} (already in Flighty)                    ")
                    continue

            flight_count += 1
            # Show new flight found
            print(f"\r    [NEW]  {flight_display}                                        ")

            # Store this flight
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

            key = confirmation if confirmation else f"unknown_{content_hash}"
            if key not in flights_found:
                flights_found[key] = []
            flights_found[key].append(flight_data)

        except Exception:
            continue

    # Final summary
    phase2_time = time.time() - phase2_start
    total_time = time.time() - scan_start_time

    print(f"\r    Download complete!                                              ")
    print()
    print(f"    Time taken: {int(phase2_time)} seconds")
    print()

    # ============================================
    # FOLDER SUMMARY
    # ============================================
    print("    ┌─────────────────────────────────────────────────────────┐")
    print("    │  FOLDER SCAN COMPLETE                                   │")
    print("    └─────────────────────────────────────────────────────────┘")
    print()
    total_mins = int(total_time // 60)
    total_secs = int(total_time % 60)
    if total_mins > 0:
        print(f"    Total scan time: {total_mins} min {total_secs} sec")
    else:
        print(f"    Total scan time: {total_secs} seconds")
    print()
    print(f"    Results:")
    print(f"      - New flights to import:    {flight_count}")
    print(f"      - Already in Flighty:       {skipped_count}")
    print(f"      - Emails checked:           {total}")
    print(f"      - Flight emails found:      {len(flight_candidates)}")

    return flights_found, skipped_confirmations


def select_latest_flights(all_flights, processed):
    """For each confirmation, select the latest email.

    Skip already-processed flights unless they've changed.

    Args:
        all_flights: Dict of confirmation -> list of flight data
        processed: Dict of already processed flights

    Returns:
        Tuple: (to_forward list, skipped list)
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
        to_forward.append(latest)

    return to_forward, skipped
