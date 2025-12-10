"""
Email scanning functionality.

Scans email folders for flight confirmation emails using IMAP.
Optimized for speed with batch operations and rate limiting.
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

# Rate limiting settings
IMAP_BATCH_DELAY = 0.1  # Delay between batch operations (seconds)
IMAP_SEARCH_DELAY = 0.05  # Delay between individual searches (seconds)


# Combine all searches into groups for batch OR queries
# This reduces the number of IMAP roundtrips significantly
AIRLINE_DOMAINS = [
    # Major US Airlines
    "jetblue.com", "delta.com", "united.com", "aa.com", "southwest.com",
    "alaskaair.com", "spirit.com", "flyfrontier.com", "hawaiianairlines.com",
    # International Airlines
    "aircanada.com", "britishairways.com", "lufthansa.com", "emirates.com",
    "airfrance.com", "klm.com", "qantas.com", "singaporeair.com",
    "cathaypacific.com", "jal.com", "ana.co.jp", "koreanair.com",
    "turkishairlines.com", "qatarairways.com", "etihad.com",
    "virginatlantic.com", "icelandair.com", "norwegian.com",
    "ryanair.com", "easyjet.com", "westjet.com", "avianca.com",
    "latam.com", "aeromexico.com", "copaair.com",
    # Booking Sites
    "expedia.com", "kayak.com", "priceline.com", "orbitz.com",
    "travelocity.com", "cheapoair.com", "hopper.com", "google.com",
    "booking.com", "trip.com", "skyscanner.com",
    # Corporate Travel
    "concur.com", "egencia.com", "tripactions.com", "navan.com",
    # Credit Card Travel
    "chase.com", "americanexpress.com", "capitalone.com", "citi.com",
]

# Partial matches for subdomains (email.jetblue.com, etc.)
AIRLINE_KEYWORDS = ["jetblue", "delta", "united", "american", "southwest"]

# Subject line searches
SUBJECT_KEYWORDS = [
    "flight confirmation", "itinerary", "e-ticket",
    "booking confirmation", "trip confirmation", "travel confirmation"
]


def _batch_search(mail, since_date, search_terms, batch_size=10, verbose=True):
    """Execute searches in batches using OR queries for speed.

    Args:
        mail: IMAP connection
        since_date: Date string for SINCE filter
        search_terms: List of (name, query) tuples
        batch_size: Number of queries to OR together
        verbose: Print progress updates

    Returns:
        Set of email IDs and list of source names found
    """
    all_ids = set()
    found_sources = []
    total_searches = len(search_terms)
    completed = 0

    # Group search terms into batches
    for i in range(0, len(search_terms), batch_size):
        batch = search_terms[i:i + batch_size]

        for name, query in batch:
            try:
                result, data = mail.search(None, f'(SINCE {since_date} {query})')
                if result == 'OK' and data[0]:
                    ids = data[0].split()
                    if ids:
                        new_ids = set(ids) - all_ids
                        if new_ids:
                            all_ids.update(new_ids)
                            found_sources.append(f"{name}({len(new_ids)})")

                # Rate limiting between searches
                time.sleep(IMAP_SEARCH_DELAY)
            except Exception:
                pass

            completed += 1
            if verbose and completed % 10 == 0:
                print(f"\r    Searching... {completed}/{total_searches} queries ({len(all_ids)} emails found)", end="", flush=True)

        # Small delay between batches
        time.sleep(IMAP_BATCH_DELAY)

    return all_ids, found_sources


def _fetch_headers_batch(mail, email_ids, batch_size=50, verbose=True):
    """Fetch email headers in batches for speed.

    Args:
        mail: IMAP connection
        email_ids: List of email IDs
        batch_size: Number of emails to fetch per request
        verbose: Print progress updates

    Returns:
        List of (email_id, headers_dict) tuples
    """
    results = []
    total = len(email_ids)
    processed = 0

    for i in range(0, len(email_ids), batch_size):
        batch = email_ids[i:i + batch_size]
        # Create comma-separated ID list for batch fetch
        id_str = b','.join(batch)

        try:
            result, data = mail.fetch(id_str, '(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])')
            if result != 'OK':
                processed += len(batch)
                continue

            # Parse batch response
            idx = 0
            for item in data:
                if isinstance(item, tuple) and len(item) >= 2:
                    header_data = item[1]
                    if header_data and idx < len(batch):
                        try:
                            header_msg = email.message_from_bytes(header_data)
                            results.append((batch[idx], {
                                'from': decode_header_value(header_msg.get('From', '')),
                                'subject': decode_header_value(header_msg.get('Subject', '')),
                                'date': header_msg.get('Date', '')
                            }))
                        except Exception:
                            pass
                        idx += 1

            processed += len(batch)
            if verbose:
                print(f"\r    Checking headers... {processed}/{total}", end="", flush=True)

        except Exception:
            # Fall back to individual fetches if batch fails
            for eid in batch:
                try:
                    result, msg_data = mail.fetch(eid, '(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])')
                    if result == 'OK' and msg_data and msg_data[0]:
                        header_data = msg_data[0][1]
                        if header_data:
                            header_msg = email.message_from_bytes(header_data)
                            results.append((eid, {
                                'from': decode_header_value(header_msg.get('From', '')),
                                'subject': decode_header_value(header_msg.get('Subject', '')),
                                'date': header_msg.get('Date', '')
                            }))
                    time.sleep(IMAP_SEARCH_DELAY)  # Rate limit individual fetches
                except Exception:
                    pass

            processed += len(batch)
            if verbose:
                print(f"\r    Checking headers... {processed}/{total}", end="", flush=True)

        # Rate limit between batches
        time.sleep(IMAP_BATCH_DELAY)

    return results


def scan_for_flights(mail, config, folder, processed):
    """Scan folder and collect all flight emails.

    Optimized with batch IMAP operations and rate limiting.

    Args:
        mail: IMAP connection
        config: Config dict
        folder: Folder name to scan
        processed: Dict of already processed flights

    Returns:
        Tuple: (flights_found dict, skipped_confirmations list)
    """
    flights_found = {}
    skipped_confirmations = []
    already_processed = processed.get("confirmations", {})
    processed_hashes = processed.get("content_hashes", set())

    print()
    print(f"    Opening folder '{folder}'...", end="", flush=True)
    try:
        result, _ = mail.select(folder)
        if result != 'OK':
            print(f" failed!")
            print(f"    Could not open folder: {folder}")
            return flights_found, skipped_confirmations
        print(" done")
    except Exception as e:
        print(f" failed!")
        print(f"    Could not open folder: {folder}")
        return flights_found, skipped_confirmations

    since_date = (datetime.now() - timedelta(days=config['days_back'])).strftime("%d-%b-%Y")
    print(f"    Searching emails since {since_date}...")

    # ============================================
    # STEP A: Server-side search
    # ============================================
    print()
    print("    Phase 1: Searching for airline/booking emails...")

    all_email_ids = set()
    found_sources = []
    search_count = 0
    total_searches = len(AIRLINE_DOMAINS) + len(AIRLINE_KEYWORDS) + len(SUBJECT_KEYWORDS)

    # Build search queries
    search_queries = []
    for domain in AIRLINE_DOMAINS:
        search_queries.append((domain.split('.')[0].title(), f'FROM "{domain}"'))

    for keyword in AIRLINE_KEYWORDS:
        search_queries.append((f"{keyword.title()}+", f'FROM "{keyword}"'))

    for keyword in SUBJECT_KEYWORDS:
        search_queries.append((keyword.split()[0].title(), f'SUBJECT "{keyword}"'))

    # Execute searches with rate limiting
    for name, query in search_queries:
        try:
            result, data = mail.search(None, f'(SINCE {since_date} {query})')
            if result == 'OK' and data[0]:
                ids = data[0].split()
                if ids:
                    new_ids = set(ids) - all_email_ids
                    if new_ids:
                        all_email_ids.update(new_ids)
                        found_sources.append(f"{name}({len(new_ids)})")
            time.sleep(IMAP_SEARCH_DELAY)  # Rate limit
        except Exception:
            pass

        search_count += 1
        if search_count % 10 == 0:
            print(f"\r    Phase 1: Searching... {search_count}/{total_searches} ({len(all_email_ids)} found)", end="", flush=True)

    email_ids = list(all_email_ids)
    total = len(email_ids)

    print(f"\r    Phase 1: Complete - found {total} potential emails from {len(found_sources)} sources")

    if total == 0:
        print("    No emails found from airlines or booking sites.")
        return flights_found, skipped_confirmations

    if found_sources:
        top_sources = found_sources[:5]
        print(f"    Top sources: {', '.join(top_sources)}" + (f" +{len(found_sources)-5} more" if len(found_sources) > 5 else ""))

    # ============================================
    # STEP B: Quick header check (batch fetch)
    # ============================================
    print()
    print("    Phase 2: Checking email headers...")

    scan_start = time.time()
    flight_candidates = []

    # Batch fetch headers for speed (with rate limiting)
    headers = _fetch_headers_batch(mail, email_ids, verbose=True)

    for email_id, hdr in headers:
        is_flight, airline = is_flight_email(hdr['from'], hdr['subject'])
        if is_flight:
            flight_candidates.append({
                'email_id': email_id,
                'from_addr': hdr['from'],
                'subject': hdr['subject'],
                'date_str': hdr['date'],
                'airline': airline
            })

    header_time = time.time() - scan_start
    print(f"\r    Phase 2: Complete - {len(flight_candidates)} flight emails identified ({header_time:.1f}s)")

    if not flight_candidates:
        print("    No flight confirmations found in this folder.")
        return flights_found, skipped_confirmations

    # ============================================
    # STEP C: Download full emails
    # ============================================
    print()
    print(f"    Phase 3: Downloading {len(flight_candidates)} flight emails...")

    download_start = time.time()
    flight_count = 0
    skipped_count = 0
    download_count = 0

    for candidate in flight_candidates:
        download_count += 1
        try:
            email_id = candidate['email_id']

            # Progress update
            if download_count % 5 == 0 or download_count == len(flight_candidates):
                print(f"\r    Phase 3: Downloading... {download_count}/{len(flight_candidates)}", end="", flush=True)

            result, msg_data = mail.fetch(email_id, '(RFC822)')
            time.sleep(IMAP_SEARCH_DELAY)  # Rate limit

            if result != 'OK' or not msg_data or not msg_data[0]:
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

            email_date = parse_email_date(date_str)
            confirmation = extract_confirmation_code(subject, full_body)
            content_hash = generate_content_hash(subject, full_body)
            flight_info = extract_flight_info(full_body, email_date=email_date)

            # Skip if already processed
            if confirmation and confirmation in already_processed:
                if content_hash in processed_hashes:
                    skipped_count += 1
                    if confirmation not in skipped_confirmations:
                        skipped_confirmations.append(confirmation)
                    continue

            flight_count += 1

            # Store flight
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

    download_time = time.time() - download_start
    total_time = time.time() - scan_start

    print(f"\r    Phase 3: Complete - downloaded {download_count} emails ({download_time:.1f}s)")
    print()
    print(f"    Summary: {flight_count} new flights, {skipped_count} already imported")
    print(f"    Total scan time: {total_time:.1f}s")

    return flights_found, skipped_confirmations


def select_latest_flights(all_flights, processed):
    """For each confirmation, select the latest email.

    For same-day flight changes/updates, we always take the most recent email
    for each confirmation code to ensure we forward the latest itinerary.

    Args:
        all_flights: Dict of confirmation -> list of flight data
        processed: Dict of already processed flights

    Returns:
        Tuple: (to_forward list, skipped list, duplicates_merged int)
    """
    to_forward = []
    skipped = []
    duplicates_merged = 0

    for conf_code, emails in all_flights.items():
        # Sort by email date, newest first - this ensures we always pick
        # the most recent version for same-day changes/updates
        emails.sort(key=lambda x: x["email_date"], reverse=True)
        latest = emails[0]

        # Track how many duplicates we merged (for user feedback)
        if len(emails) > 1:
            duplicates_merged += len(emails) - 1

        # Check if already processed
        fingerprint = create_flight_fingerprint(latest["flight_info"])
        existing = processed.get("confirmations", {}).get(conf_code)

        if existing:
            old_fingerprint = existing.get("fingerprint", "")
            if fingerprint == old_fingerprint:
                skipped.append({
                    "confirmation": conf_code,
                    "reason": "already imported",
                    "subject": latest["subject"][:50],
                    "flight_info": latest.get("flight_info", {}),
                    "email_date": latest.get("email_date"),
                    "airline": latest.get("airline", "Unknown")
                })
                continue
            else:
                # Flight details changed - this is an update
                latest["is_update"] = True

        # Check content hash
        if latest["content_hash"] in processed.get("content_hashes", set()):
            skipped.append({
                "confirmation": conf_code,
                "reason": "duplicate content",
                "subject": latest["subject"][:50],
                "flight_info": latest.get("flight_info", {}),
                "email_date": latest.get("email_date"),
                "airline": latest.get("airline", "Unknown")
            })
            continue

        latest["fingerprint"] = fingerprint
        latest["email_count"] = len(emails)  # How many emails we found for this confirmation
        to_forward.append(latest)

    # Sort to_forward by flight date (soonest first) for better display
    def get_flight_date(flight):
        dates = flight.get("flight_info", {}).get("dates", [])
        return dates[0] if dates else "9999-99-99"

    to_forward.sort(key=get_flight_date)

    return to_forward, skipped, duplicates_merged
