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

# Rate limiting settings - conservative to avoid server blocks
IMAP_BATCH_DELAY = 0.2  # Delay between batch operations (seconds)
IMAP_SEARCH_DELAY = 0.1  # Delay between individual searches (seconds)
IMAP_RETRY_DELAY = 5  # Delay before retrying failed operations (seconds)
IMAP_MAX_RETRIES = 3  # Maximum retries for failed operations


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


def _imap_search_with_retry(mail, criteria, max_retries=IMAP_MAX_RETRIES):
    """Execute IMAP search with retry logic for transient failures.

    Args:
        mail: IMAP connection
        criteria: Search criteria string
        max_retries: Maximum number of retries

    Returns:
        Set of email IDs found, or empty set on failure
    """
    for attempt in range(max_retries):
        try:
            result, data = mail.search(None, criteria)
            if result == 'OK' and data[0]:
                return set(data[0].split())
            return set()
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(IMAP_RETRY_DELAY)
            # Last attempt failed, return empty
    return set()


def _build_or_query(terms, field="FROM"):
    """Build an IMAP OR query from multiple terms.

    IMAP OR syntax: OR (FROM "a") (FROM "b") for 2 terms
    For 3+ terms: OR (FROM "a") (OR (FROM "b") (FROM "c"))

    Args:
        terms: List of search terms
        field: IMAP field to search (FROM, SUBJECT, etc.)

    Returns:
        IMAP search query string
    """
    if not terms:
        return None
    if len(terms) == 1:
        return f'({field} "{terms[0]}")'

    # Build nested OR query
    query = f'({field} "{terms[-1]}")'
    for term in reversed(terms[:-1]):
        query = f'(OR ({field} "{term}") {query})'
    return query


def _search_individual(mail, since_date, terms, field, all_ids, verbose=True, group_name=""):
    """Fall back to individual searches when OR queries fail.

    Args:
        mail: IMAP connection
        since_date: Date string for SINCE filter
        terms: List of search terms
        field: IMAP field (FROM, SUBJECT)
        all_ids: Set to update with found IDs
        verbose: Print progress
        group_name: Name of the search group for display

    Returns:
        Number of new emails found
    """
    found = 0
    for i, term in enumerate(terms):
        try:
            criteria = f'(SINCE {since_date} ({field} "{term}"))'
            ids = _imap_search_with_retry(mail, criteria)
            if ids:
                new_ids = ids - all_ids
                if new_ids:
                    all_ids.update(new_ids)
                    found += len(new_ids)
            time.sleep(IMAP_SEARCH_DELAY)
        except Exception:
            pass

        if verbose:
            # Show progress every 5 terms or at the end
            if (i + 1) % 5 == 0 or i == len(terms) - 1:
                print(f"\r    Searching {group_name}... {i+1}/{len(terms)} ({found} found)    ", end="", flush=True)

    return found


def _optimized_search(mail, since_date, verbose=True):
    """Execute optimized searches using combined OR queries with fallback.

    Strategy:
    1. Try combined OR queries first (fast - fewer roundtrips)
    2. If OR query fails or returns nothing, fall back to individual searches
    3. Some IMAP servers don't support complex OR queries, so fallback is essential

    Args:
        mail: IMAP connection
        since_date: Date string for SINCE filter
        verbose: Print progress updates

    Returns:
        Set of email IDs and dict of sources found
    """
    all_ids = set()
    sources = {}
    using_fallback = False

    # Strategy: Combine searches into groups using OR queries
    # This reduces roundtrips from 55+ to about 10-15 (with smaller batches)

    # Use smaller batches (5-8 terms) for better compatibility
    search_groups = [
        ("Major US Airlines", AIRLINE_DOMAINS[:8], "FROM"),
        ("More US Airlines", AIRLINE_DOMAINS[8:15], "FROM"),
        ("European Airlines", AIRLINE_DOMAINS[15:23], "FROM"),
        ("Asian/Other Airlines", AIRLINE_DOMAINS[23:30], "FROM"),
        ("Booking Sites", AIRLINE_DOMAINS[30:38], "FROM"),
        ("Travel/Corporate", AIRLINE_DOMAINS[38:], "FROM"),
        ("Airline Keywords", AIRLINE_KEYWORDS, "FROM"),
        ("Subject Keywords", SUBJECT_KEYWORDS, "SUBJECT"),
    ]

    total_groups = len(search_groups)

    for idx, (group_name, terms, field) in enumerate(search_groups):
        if verbose:
            print(f"\r    Searching {group_name}... ({idx+1}/{total_groups})", end="", flush=True)

        if not terms:
            continue

        found_in_group = 0

        # Try combined OR query first
        or_query = _build_or_query(terms, field)
        if or_query:
            criteria = f'(SINCE {since_date} {or_query})'

            try:
                ids = _imap_search_with_retry(mail, criteria)
                if ids:
                    new_ids = ids - all_ids
                    if new_ids:
                        all_ids.update(new_ids)
                        found_in_group = len(new_ids)
            except Exception:
                ids = set()

            # If OR query returned nothing, try fallback
            # (Some servers silently fail on complex queries)
            if not ids and len(terms) > 1:
                if verbose and not using_fallback:
                    print()
                    print("    Note: Your email server doesn't support batch queries.")
                    print("    Switching to individual searches (slower but thorough)...")
                    using_fallback = True

                found_in_group = _search_individual(mail, since_date, terms, field, all_ids, verbose=verbose, group_name=group_name)

        if found_in_group > 0:
            sources[group_name] = found_in_group

        time.sleep(IMAP_BATCH_DELAY)

    if verbose:
        print()  # Newline after progress
        if using_fallback:
            print("    Note: Your email server required individual searches (slower but still works)")

    return all_ids, sources


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

    folder_start = time.time()  # Track total time for this folder

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

    # ============================================
    # STEP A: Server-side search (optimized with OR queries)
    # ============================================
    print()
    print("  ┌─────────────────────────────────────────────────────────────")
    print("  │ PHASE 1: SEARCHING FOR FLIGHT EMAILS")
    print("  │")
    print(f"  │ Looking back {config['days_back']} days (since {since_date})")
    print("  │ Searching 50+ airlines, booking sites, and travel services...")
    print("  │")
    print("  │ What's happening: Asking your email server to find emails from")
    print("  │ airlines like Delta, United, JetBlue, and booking sites like")
    print("  │ Expedia, Kayak, etc. This filters thousands of emails down to")
    print("  │ just the ones that might be flight confirmations.")
    print("  └─────────────────────────────────────────────────────────────")
    print()

    search_start = time.time()

    # Use optimized search with combined OR queries
    all_email_ids, sources = _optimized_search(mail, since_date, verbose=True)

    email_ids = list(all_email_ids)
    total = len(email_ids)
    search_time = time.time() - search_start

    print()
    print(f"    ✓ Phase 1 complete: Found {total} potential flight emails ({search_time:.1f}s)")

    if total == 0:
        print()
        print("    No emails found from airlines or booking sites in this time range.")
        print("    Try: python3 run.py --days 365  (to search a full year)")
        return flights_found, skipped_confirmations

    if sources:
        source_list = [f"{name}: {count}" for name, count in sources.items()]
        print(f"    Sources: {', '.join(source_list)}")

    # ============================================
    # STEP B: Quick header check (batch fetch)
    # ============================================
    print()
    print("  ┌─────────────────────────────────────────────────────────────")
    print("  │ PHASE 2: CHECKING EMAIL HEADERS")
    print("  │")
    print(f"  │ Checking {total} emails to identify actual flight confirmations...")
    print("  │")
    print("  │ What's happening: Downloading just the subject lines and sender")
    print("  │ info to filter out non-flight emails (newsletters, promos, etc.)")
    print("  │ This is much faster than downloading full emails.")
    print("  └─────────────────────────────────────────────────────────────")
    print()

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
    print()
    print(f"    ✓ Phase 2 complete: {len(flight_candidates)} flight confirmations found ({header_time:.1f}s)")
    print(f"    (Filtered out {total - len(flight_candidates)} non-flight emails)")

    if not flight_candidates:
        print()
        print("    No flight confirmations found in this folder.")
        return flights_found, skipped_confirmations

    # ============================================
    # STEP C: Download full emails (with retry logic)
    # ============================================
    print()
    print("  ┌─────────────────────────────────────────────────────────────")
    print("  │ PHASE 3: DOWNLOADING FLIGHT EMAILS")
    print("  │")
    print(f"  │ Downloading {len(flight_candidates)} full flight confirmation emails...")
    print("  │")
    print("  │ What's happening: Getting the complete email content so we can")
    print("  │ extract confirmation codes, flight numbers, dates, and routes.")
    print("  │ This takes a bit longer as full emails are larger.")
    print("  └─────────────────────────────────────────────────────────────")
    print()

    download_start = time.time()
    flight_count = 0
    skipped_count = 0
    download_count = 0
    failed_downloads = 0

    for candidate in flight_candidates:
        download_count += 1
        email_id = candidate['email_id']

        # Progress update
        if download_count % 5 == 0 or download_count == len(flight_candidates):
            print(f"\r    Phase 3: Downloading... {download_count}/{len(flight_candidates)}", end="", flush=True)

        # Try to fetch with retry logic
        raw_email = None
        for attempt in range(IMAP_MAX_RETRIES):
            try:
                result, msg_data = mail.fetch(email_id, '(RFC822)')
                time.sleep(IMAP_SEARCH_DELAY)  # Rate limit

                if result == 'OK' and msg_data and msg_data[0]:
                    raw_email = msg_data[0][1]
                    if raw_email:
                        break

            except Exception as e:
                if attempt < IMAP_MAX_RETRIES - 1:
                    time.sleep(IMAP_RETRY_DELAY)
                else:
                    failed_downloads += 1

        if not raw_email:
            continue

        try:
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
            failed_downloads += 1
            continue

    download_time = time.time() - download_start
    total_time = time.time() - folder_start

    print()
    print(f"    ✓ Phase 3 complete: Downloaded and analyzed {download_count} emails ({download_time:.1f}s)")
    print()
    print("  ┌─────────────────────────────────────────────────────────────")
    print(f"  │ FOLDER '{folder}' SCAN COMPLETE")
    print("  │")
    print(f"  │   New flights found:        {flight_count}")
    print(f"  │   Already imported:         {skipped_count}")
    if failed_downloads > 0:
        print(f"  │   Failed downloads:         {failed_downloads} (will retry next run)")
    print(f"  │   Total time for folder:    {total_time:.1f}s")
    print("  └─────────────────────────────────────────────────────────────")

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
