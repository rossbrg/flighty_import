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
    create_flight_fingerprint,
    is_marketing_email,
    get_email_type,
    verify_and_correct_flight_info
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
    "suncountry.com", "allegiantair.com", "breezewayways.com",
    # International Airlines - Europe
    "aircanada.com", "britishairways.com", "lufthansa.com",
    "airfrance.com", "klm.com", "virginatlantic.com",
    "icelandair.com", "norwegian.com", "ryanair.com", "easyjet.com",
    "vueling.com", "iberia.com", "aeroflot.com", "lot.com",
    "finnair.com", "sas.com", "brusselsairlines.com", "swiss.com",
    "austrian.com", "tap.pt", "aegeanair.com",
    # International Airlines - Middle East/Africa
    "emirates.com", "etihad.com", "qatarairways.com",
    "turkishairlines.com", "saudia.com", "royalairmaroc.com",
    "ethiopianairlines.com", "kenya-airways.com", "egyptair.com",
    # International Airlines - Asia/Pacific
    "qantas.com", "singaporeair.com", "cathaypacific.com",
    "jal.com", "ana.co.jp", "koreanair.com", "asiana.com",
    "thaiairways.com", "vietnamairlines.com", "airchina.com",
    "chinaeastern.com", "chinasouthern.com", "hainanairlines.com",
    "airindia.com", "malaysiaairlines.com", "garuda-indonesia.com",
    "airasia.com", "scoot.com", "jetstar.com", "tigerair.com",
    "philippineairlines.com", "eloiqatar.com",
    # International Airlines - Americas
    "westjet.com", "avianca.com", "latam.com", "aeromexico.com",
    "copaair.com", "azul.com.br", "gol.com.br", "volaris.com",
    "viva.com", "interjet.com",
    # Booking Sites
    "expedia.com", "kayak.com", "priceline.com", "orbitz.com",
    "travelocity.com", "cheapoair.com", "hopper.com", "google.com",
    "booking.com", "trip.com", "skyscanner.com", "momondo.com",
    "kiwi.com", "flightaware.com", "studentuniverse.com",
    "cheapflights.com", "farecompare.com", "airfarewatchdog.com",
    # Corporate Travel & Expense
    "concur.com", "egencia.com", "tripactions.com", "navan.com",
    "brex.com", "ramp.com", "divvy.com", "airbase.com",
    "travelbank.com", "deem.com", "travelperk.com", "lola.com",
    "upside.com", "spotnana.com", "flightfox.com",
    # Credit Card Travel Portals
    "chase.com", "americanexpress.com", "capitalone.com", "citi.com",
    "barclaycardus.com", "wellsfargo.com", "usbank.com",
    # Travel Agencies & Consolidators
    "flightcentre.com", "carlsonwagonlit.com", "bcd.com",
    "worldtravelinc.com", "travelleaders.com", "frosch.com",
]

# Partial matches for subdomains (email.jetblue.com, etc.)
AIRLINE_KEYWORDS = [
    "jetblue", "delta", "united", "american", "southwest",
    "alaska", "spirit", "frontier", "hawaiian", "emirates",
    "british airways", "lufthansa", "air france", "klm",
]

# Subject line searches
SUBJECT_KEYWORDS = [
    "flight confirmation", "itinerary", "e-ticket", "eticket",
    "booking confirmation", "trip confirmation", "travel confirmation",
    "your flight", "your trip", "reservation confirmed",
    "flight details", "travel itinerary", "flight itinerary",
    "boarding pass", "check-in", "flight reminder",
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


def _search_individual(mail, since_date, terms, field, all_ids):
    """Fall back to individual searches when OR queries fail.

    Args:
        mail: IMAP connection
        since_date: Date string for SINCE filter
        terms: List of search terms
        field: IMAP field (FROM, SUBJECT)
        all_ids: Set to update with found IDs

    Returns:
        Number of new emails found
    """
    found = 0
    for term in terms:
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

    return found


def _search_by_confirmation_codes(mail, confirmation_codes, already_found_ids, verbose=True):
    """Search for additional emails containing known confirmation codes.

    This helps find related emails (e.g., check-in reminders, itinerary updates)
    that might have more complete flight information.

    Args:
        mail: IMAP connection
        confirmation_codes: Set of confirmation codes to search for
        already_found_ids: Set of email IDs already found (to avoid duplicates)
        verbose: Print progress

    Returns:
        Dict mapping confirmation code -> set of new email IDs
    """
    results = {}

    if not confirmation_codes:
        return results

    codes_list = list(confirmation_codes)
    total = len(codes_list)

    for i, code in enumerate(codes_list):
        if verbose:
            print(f"\r      Searching... {i+1}/{total} ({code})" + " " * 20, end="", flush=True)

        code_ids = set()

        try:
            # Search SUBJECT for confirmation code
            criteria = f'(SUBJECT "{code}")'
            ids = _imap_search_with_retry(mail, criteria)
            if ids:
                code_ids.update(ids - already_found_ids)

            time.sleep(IMAP_SEARCH_DELAY)

            # Search BODY for confirmation code (slower but catches more)
            criteria = f'(BODY "{code}")'
            ids = _imap_search_with_retry(mail, criteria)
            if ids:
                code_ids.update(ids - already_found_ids)

            time.sleep(IMAP_SEARCH_DELAY)

        except Exception:
            pass

        if code_ids:
            results[code] = code_ids

    if verbose and codes_list:
        print(f"\r      Searching... {total}/{total} done" + " " * 30)

    return results


def _process_email(mail, email_id, folder):
    """Fetch and parse a single email.

    Args:
        mail: IMAP connection
        email_id: Email ID to fetch
        folder: Folder name for metadata

    Returns:
        Dict with email data, or None on failure
    """
    raw_email = None
    for attempt in range(IMAP_MAX_RETRIES):
        try:
            result, msg_data = mail.fetch(email_id, '(RFC822)')
            time.sleep(IMAP_SEARCH_DELAY)

            if result == 'OK' and msg_data and msg_data[0]:
                raw_email = msg_data[0][1]
                if raw_email:
                    break

        except Exception:
            if attempt < IMAP_MAX_RETRIES - 1:
                time.sleep(IMAP_RETRY_DELAY)

    if not raw_email:
        return None

    try:
        msg = email.message_from_bytes(raw_email)
        from_addr = decode_header_value(msg.get('From', ''))
        subject = decode_header_value(msg.get('Subject', ''))
        date_str = msg.get('Date', '')

        body, html_body = get_email_body(msg)
        full_body = body or html_body or ""

        email_date = parse_email_date(date_str)
        confirmation = extract_confirmation_code(subject, full_body)
        content_hash = generate_content_hash(subject, full_body)

        # Extract flight info
        flight_info = extract_flight_info(full_body, email_date=email_date, html_body=html_body, from_addr=from_addr, subject=subject)

        # Verify with FlightAware if we have flight numbers
        if flight_info and flight_info.get('flight_numbers'):
            flight_info = verify_and_correct_flight_info(flight_info, verify_online=True)

        # Detect airline from sender
        _, airline = is_flight_email(from_addr, subject)

        return {
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

    except Exception:
        return None


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
    # This reduces roundtrips from 120+ to about 15 (with smaller batches)

    # Use smaller batches (8-12 terms) for better compatibility
    # Domains are organized by category in AIRLINE_DOMAINS (120 total)
    search_groups = [
        ("US Airlines", AIRLINE_DOMAINS[0:12], "FROM"),            # 0-11: US carriers
        ("European Airlines 1", AIRLINE_DOMAINS[12:22], "FROM"),   # 12-21: Major Europe
        ("European Airlines 2", AIRLINE_DOMAINS[22:33], "FROM"),   # 22-32: More Europe
        ("Middle East/Africa", AIRLINE_DOMAINS[33:42], "FROM"),    # 33-41: ME/Africa
        ("Asia/Pacific 1", AIRLINE_DOMAINS[42:54], "FROM"),        # 42-53: Asia/Pacific
        ("Asia/Pacific 2", AIRLINE_DOMAINS[54:64], "FROM"),        # 54-63: More Asia
        ("Americas Airlines", AIRLINE_DOMAINS[64:74], "FROM"),     # 64-73: Americas
        ("Booking Sites 1", AIRLINE_DOMAINS[74:84], "FROM"),       # 74-83: Booking sites
        ("Booking Sites 2", AIRLINE_DOMAINS[84:92], "FROM"),       # 84-91: More booking
        ("Corporate Travel", AIRLINE_DOMAINS[92:107], "FROM"),     # 92-106: Corporate/expense
        ("Credit/Travel Agencies", AIRLINE_DOMAINS[107:], "FROM"), # 107+: Credit cards, agencies
        ("Airline Keywords", AIRLINE_KEYWORDS, "FROM"),
        ("Subject Keywords", SUBJECT_KEYWORDS, "SUBJECT"),
    ]

    total_groups = len(search_groups)

    for idx, (group_name, terms, field) in enumerate(search_groups):
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
                if not using_fallback:
                    using_fallback = True
                    # Notice is printed only on first fallback, after all searching done

                found_in_group = _search_individual(mail, since_date, terms, field, all_ids)

        if found_in_group > 0:
            sources[group_name] = found_in_group

        # Update progress after each group
        if verbose:
            print(f"\r      Searching... ({idx+1}/{total_groups})" + " " * 20, end="", flush=True)

        time.sleep(IMAP_BATCH_DELAY)

    if verbose:
        print()  # Newline after progress

    return all_ids, sources, using_fallback


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
                print(f"\r      Checking... {processed}/{total}" + " " * 10, end="", flush=True)

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
                print(f"\r      Checking... {processed}/{total}" + " " * 10, end="", flush=True)

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

    try:
        result, _ = mail.select(folder)
        if result != 'OK':
            print(f"    Could not open folder: {folder}")
            return flights_found, skipped_confirmations
    except Exception as e:
        print(f"    Could not open folder: {folder}")
        return flights_found, skipped_confirmations

    since_date = (datetime.now() - timedelta(days=config['days_back'])).strftime("%d-%b-%Y")

    # Phase 1: Search for flight emails
    print()
    print(f"  [1/3] Searching for flight emails (past {config['days_back']} days)...")

    search_start = time.time()

    # Use optimized search with combined OR queries
    all_email_ids, sources, used_fallback = _optimized_search(mail, since_date, verbose=True)

    email_ids = list(all_email_ids)
    total = len(email_ids)
    search_time = time.time() - search_start

    if used_fallback:
        print(f"      Found {total} potential emails ({search_time:.1f}s) [slow search mode]")
    else:
        print(f"      Found {total} potential emails ({search_time:.1f}s)")

    if total == 0:
        print("      No emails found from airlines or booking sites.")
        print("      Try: python3 run.py --days 365")
        return flights_found, skipped_confirmations

    # Phase 2: Check headers to filter flight confirmations
    print(f"  [2/3] Filtering flight confirmations...")

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
    print(f"      {len(flight_candidates)} confirmations identified ({header_time:.1f}s)")

    if not flight_candidates:
        print("      No flight confirmations found in this folder.")
        return flights_found, skipped_confirmations

    # Phase 3: Download and analyze full emails
    print(f"  [3/3] Downloading and analyzing {len(flight_candidates)} emails...")

    download_start = time.time()
    flight_count = 0
    skipped_count = 0
    download_count = 0
    failed_downloads = 0
    marketing_filtered = 0  # Track marketing emails filtered out
    cancelled_codes = set()  # Track confirmation codes from cancellation emails

    for candidate in flight_candidates:
        download_count += 1
        email_id = candidate['email_id']

        # Progress update
        if download_count % 5 == 0 or download_count == len(flight_candidates):
            print(f"\r      Processing... {download_count}/{len(flight_candidates)}" + " " * 10, end="", flush=True)

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

            # Check if this is a marketing/promotional or cancellation email
            # Marketing emails mention destinations to entice booking but aren't actual confirmations
            # Cancellation emails indicate a flight was cancelled - don't forward these
            email_type = get_email_type(subject, full_body, has_confirmation_code=confirmation is not None)
            if email_type == 'marketing':
                marketing_filtered += 1
                continue  # Skip marketing emails
            if email_type == 'cancellation':
                # Track cancelled confirmation codes to exclude later
                if confirmation:
                    cancelled_codes.add(confirmation)
                continue  # Skip cancellation emails

            # Extract flight info from email content
            # Uses schema.org structured data if available, falls back to text parsing
            flight_info = extract_flight_info(full_body, email_date=email_date, html_body=html_body, from_addr=from_addr, subject=subject)

            # If we have a flight number, verify the route online via FlightAware
            if flight_info and flight_info.get('flight_numbers'):
                flight_info = verify_and_correct_flight_info(flight_info, verify_online=True)

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

            # Use confirmation code as key, or fall back to flight fingerprint for deduplication
            # This groups emails for the same flight even if confirmation code wasn't extracted
            if confirmation:
                key = confirmation
            else:
                # Create key from route + date to group related emails
                fingerprint = create_flight_fingerprint(flight_info)
                if fingerprint:
                    key = f"route_{fingerprint}"
                else:
                    key = f"unknown_{content_hash}"

            if key not in flights_found:
                flights_found[key] = []
            flights_found[key].append(flight_data)

        except Exception:
            failed_downloads += 1
            continue

    download_time = time.time() - download_start
    print()

    # Phase 4: Search for related emails by confirmation code
    # Find confirmation codes that have incomplete flight info (no route)
    # and search for additional emails that might have more details
    incomplete_codes = set()
    all_processed_ids = set(c['email_id'] for c in flight_candidates)

    for conf_code, emails in flights_found.items():
        # Only search for confirmation codes (not route_* or unknown_* keys)
        if conf_code.startswith('route_') or conf_code.startswith('unknown_'):
            continue

        # Check if any email for this code has incomplete route info
        has_complete_route = False
        for email_data in emails:
            flight_info = email_data.get('flight_info', {})
            if flight_info.get('route') or len(flight_info.get('airports', [])) >= 2:
                has_complete_route = True
                break

        if not has_complete_route:
            incomplete_codes.add(conf_code)

    # Search for related emails if we have incomplete codes
    related_emails_found = 0
    if incomplete_codes:
        print(f"  [4/4] Searching for more details on {len(incomplete_codes)} confirmations...")

        code_to_ids = _search_by_confirmation_codes(mail, incomplete_codes, all_processed_ids, verbose=True)

        # Count total emails to process
        total_to_process = sum(len(ids) for ids in code_to_ids.values())

        if total_to_process > 0:
            print(f"      Processing {total_to_process} related emails...")

        # Process the found emails
        processed_count = 0
        for conf_code, email_ids in code_to_ids.items():
            for eid in email_ids:
                processed_count += 1
                if total_to_process > 5:
                    print(f"\r      Downloading... {processed_count}/{total_to_process}" + " " * 10, end="", flush=True)

                email_data = _process_email(mail, eid, folder)
                if email_data:
                    related_emails_found += 1
                    # Add to the confirmation's email list
                    if conf_code in flights_found:
                        flights_found[conf_code].append(email_data)
                    else:
                        flights_found[conf_code] = [email_data]

        if total_to_process > 5:
            print()  # Newline after progress

        if related_emails_found > 0:
            print(f"      Found {related_emails_found} related emails with flight details")
        elif total_to_process > 0:
            print("      No additional flight details found")
        else:
            print("      No related emails found")

    # Remove any cancelled flights from the results
    cancelled_count = 0
    for code in cancelled_codes:
        if code in flights_found:
            cancelled_count += 1
            del flights_found[code]

    total_time = time.time() - folder_start

    # Summary line
    summary_parts = [f"{flight_count} new flights"]
    if related_emails_found > 0:
        summary_parts.append(f"{related_emails_found} related")
    if skipped_count > 0:
        summary_parts.append(f"{skipped_count} already imported")
    if marketing_filtered > 0:
        summary_parts.append(f"{marketing_filtered} marketing skipped")
    if cancelled_count > 0:
        summary_parts.append(f"{cancelled_count} cancelled")
    if failed_downloads > 0:
        summary_parts.append(f"{failed_downloads} failed")
    print(f"  ✓ {folder}: {', '.join(summary_parts)} ({total_time:.1f}s)")

    return flights_found, skipped_confirmations


def _merge_flight_info(info_list):
    """Merge flight info from multiple emails into the most complete version.

    Takes the best available data from each email to build a complete picture.

    Args:
        info_list: List of flight_info dicts from different emails

    Returns:
        Merged flight_info dict
    """
    if not info_list:
        return {}

    merged = {
        "airports": [],
        "flight_numbers": [],
        "dates": [],
        "route": None
    }

    for info in info_list:
        if not info:
            continue

        # Take the route if we don't have one
        if not merged["route"] and info.get("route"):
            merged["route"] = info["route"]

        # Merge airports (take unique ones)
        for airport in info.get("airports", []):
            if airport not in merged["airports"]:
                merged["airports"].append(airport)

        # Merge flight numbers (take unique ones)
        for flight_num in info.get("flight_numbers", []):
            if flight_num not in merged["flight_numbers"]:
                merged["flight_numbers"].append(flight_num)

        # Merge dates (take unique ones)
        for date in info.get("dates", []):
            if date not in merged["dates"]:
                merged["dates"].append(date)

    # If we have 2+ airports but no route, create route from first two
    if not merged["route"] and len(merged["airports"]) >= 2:
        merged["route"] = (merged["airports"][0], merged["airports"][1])

    return merged


def _normalize_route(flight_info):
    """Get a normalized route string for comparison."""
    route = flight_info.get("route")
    if route:
        return f"{route[0]}-{route[1]}"
    airports = flight_info.get("airports", [])
    if len(airports) >= 2:
        return f"{airports[0]}-{airports[1]}"
    return None


def _normalize_date(flight_info):
    """Get a normalized date string for comparison."""
    dates = flight_info.get("dates", [])
    if dates:
        # Extract just month and day for comparison (ignore year variations)
        date_str = dates[0].lower()
        # Remove year to match "january 15" regardless of year
        import re
        date_str = re.sub(r',?\s*\d{4}', '', date_str).strip()
        return date_str
    return None


def _merge_similar_flights(all_flights):
    """Merge flight groups that are actually the same flight.

    Flights should be merged if they have:
    - Same confirmation code (regardless of how they were keyed)
    - Same route AND same date (even with different/missing confirmation codes)
    - Same airline AND same route AND similar dates

    Returns:
        Merged dict of flights
    """
    merged = {}

    # First pass: collect all confirmation codes and their associated data
    conf_to_keys = {}  # confirmation -> list of keys that have this confirmation
    route_date_to_keys = {}  # "route|date" -> list of keys

    for key, emails in all_flights.items():
        # Get the best info from all emails in this group
        for email in emails:
            conf = email.get("confirmation")
            flight_info = email.get("flight_info", {})
            route = _normalize_route(flight_info)
            date = _normalize_date(flight_info)

            # Track by confirmation code
            if conf:
                if conf not in conf_to_keys:
                    conf_to_keys[conf] = set()
                conf_to_keys[conf].add(key)

            # Track by route + date
            if route and date:
                route_date = f"{route}|{date}"
                if route_date not in route_date_to_keys:
                    route_date_to_keys[route_date] = set()
                route_date_to_keys[route_date].add(key)

    # Build merge groups - keys that should be combined
    key_to_group = {}  # key -> group_id
    group_to_keys = {}  # group_id -> set of keys
    next_group = 0

    # Merge by confirmation code
    for conf, keys in conf_to_keys.items():
        if len(keys) > 1:
            # Multiple keys have the same confirmation - merge them
            keys_list = list(keys)
            # Check if any of these keys already have a group
            existing_groups = set()
            for k in keys_list:
                if k in key_to_group:
                    existing_groups.add(key_to_group[k])

            if existing_groups:
                # Merge into the first existing group
                target_group = min(existing_groups)
                for k in keys_list:
                    key_to_group[k] = target_group
                    if target_group not in group_to_keys:
                        group_to_keys[target_group] = set()
                    group_to_keys[target_group].add(k)
                # Merge other groups into target
                for g in existing_groups:
                    if g != target_group and g in group_to_keys:
                        for k in group_to_keys[g]:
                            key_to_group[k] = target_group
                            group_to_keys[target_group].add(k)
                        del group_to_keys[g]
            else:
                # Create new group
                for k in keys_list:
                    key_to_group[k] = next_group
                group_to_keys[next_group] = set(keys_list)
                next_group += 1

    # Merge by route + date
    for route_date, keys in route_date_to_keys.items():
        if len(keys) > 1:
            keys_list = list(keys)
            existing_groups = set()
            for k in keys_list:
                if k in key_to_group:
                    existing_groups.add(key_to_group[k])

            if existing_groups:
                target_group = min(existing_groups)
                for k in keys_list:
                    key_to_group[k] = target_group
                    if target_group not in group_to_keys:
                        group_to_keys[target_group] = set()
                    group_to_keys[target_group].add(k)
                for g in existing_groups:
                    if g != target_group and g in group_to_keys:
                        for k in group_to_keys[g]:
                            key_to_group[k] = target_group
                            group_to_keys[target_group].add(k)
                        del group_to_keys[g]
            else:
                for k in keys_list:
                    key_to_group[k] = next_group
                group_to_keys[next_group] = set(keys_list)
                next_group += 1

    # Now build the merged result
    processed_keys = set()

    for key, emails in all_flights.items():
        if key in processed_keys:
            continue

        if key in key_to_group:
            # This key is part of a merge group
            group_id = key_to_group[key]
            all_keys_in_group = group_to_keys.get(group_id, {key})

            # Combine all emails from all keys in this group
            combined_emails = []
            best_conf = None
            for k in all_keys_in_group:
                if k in all_flights:
                    combined_emails.extend(all_flights[k])
                    # Find the best confirmation code from any email
                    for email in all_flights[k]:
                        if email.get("confirmation") and not best_conf:
                            best_conf = email.get("confirmation")
                processed_keys.add(k)

            # Use the confirmation code as the key if we have one
            merge_key = best_conf if best_conf else key
            merged[merge_key] = combined_emails
        else:
            # Not part of a merge group - keep as is
            merged[key] = emails
            processed_keys.add(key)

    return merged


def select_latest_flights(all_flights, processed):
    """For each confirmation, select the latest email and verify flight details.

    FLOW:
    1. For each confirmation code, collect all related emails
    2. Extract the best flight number and date from trusted emails
    3. Use FlightAware to verify/get the correct route for that flight+date
    4. If no flight number, use route from most trusted email source
    5. Filter out cancelled flights

    Args:
        all_flights: Dict of confirmation -> list of flight data
        processed: Dict of already processed flights

    Returns:
        Tuple: (to_forward list, skipped list, duplicates_merged int)
    """
    from .parser import verify_flight_exists, validate_airport_code
    import re

    # First, merge flights that should be together
    all_flights = _merge_similar_flights(all_flights)

    to_forward = []
    skipped = []
    duplicates_merged = 0

    for conf_code, emails in all_flights.items():
        # Sort by email date, newest first
        emails.sort(key=lambda x: x["email_date"], reverse=True)
        latest = emails[0]

        # STEP 1: Identify trusted emails (confirmation code in subject)
        trusted_emails = []
        for e in emails:
            e_conf = e.get("confirmation")
            e_subject = e.get("subject", "").upper()
            # Trust if: confirmation in subject, OR it's a booking type email
            if e_conf and e_conf in e_subject:
                trusted_emails.append(e)
            elif e.get("flight_info", {}).get("route_verified"):
                trusted_emails.append(e)

        # If no trusted emails, use latest as fallback
        if not trusted_emails:
            trusted_emails = [latest]

        # STEP 2: Extract best flight number and date from trusted emails
        best_flight_number = None
        best_date = None
        best_route = None

        for e in trusted_emails:
            e_info = e.get("flight_info", {})

            # Get flight numbers
            flight_nums = e_info.get("flight_numbers", [])
            if flight_nums and not best_flight_number:
                best_flight_number = flight_nums[0]

            # Get dates
            dates = e_info.get("dates", [])
            if dates and not best_date:
                best_date = dates[0]

            # Get route (prefer verified)
            if e_info.get("route_verified") and e_info.get("route"):
                best_route = e_info.get("route")
            elif not best_route and e_info.get("route"):
                best_route = e_info.get("route")

        # STEP 3: If we have flight number + date, verify with FlightAware
        verified_route = None
        if best_flight_number and best_date:
            # Parse flight number
            match = re.match(r'^([A-Z][A-Z0-9])\s*(\d+)$', best_flight_number)
            if match:
                airline_code = match.group(1)
                flight_num = match.group(2)
                verified = verify_flight_exists(airline_code, flight_num, date_str=best_date)
                if verified and verified.get('verified_route'):
                    origin = verified.get('origin')
                    dest = verified.get('dest')
                    if origin and dest:
                        # Validate airports aren't excluded
                        origin_valid, _ = validate_airport_code(origin)
                        dest_valid, _ = validate_airport_code(dest)
                        if origin_valid and dest_valid:
                            verified_route = (origin, dest)

        # STEP 4: Build final flight info
        final_flight_info = latest.get("flight_info", {}).copy() if latest.get("flight_info") else {}

        # Use verified route if we have one, otherwise use best route from emails
        if verified_route:
            final_flight_info["route"] = verified_route
            final_flight_info["airports"] = list(verified_route)
            final_flight_info["route_verified"] = True
        elif best_route:
            final_flight_info["route"] = best_route
            final_flight_info["airports"] = list(best_route)

        # Set flight number and date
        if best_flight_number:
            final_flight_info["flight_numbers"] = [best_flight_number]
        if best_date:
            final_flight_info["dates"] = [best_date]

        latest["flight_info"] = final_flight_info

        # Filter out flights with no useful data
        # For Flighty to properly import, we need at least a route (2 airports)
        # Confirmation code alone isn't enough - we can't inject useful header info
        flight_info = latest.get("flight_info", {})
        has_confirmation = latest.get("confirmation") is not None
        has_route = flight_info.get("route") is not None
        has_airports = len(flight_info.get("airports", [])) >= 2
        has_flight_number = len(flight_info.get("flight_numbers", [])) > 0
        route_verified = flight_info.get("route_verified", False)

        # Require at least a valid route (2 airports) to forward
        # A confirmation code alone without route won't help Flighty
        if not has_route and not has_airports:
            skipped.append({
                "confirmation": conf_code,
                "reason": "no valid route extracted",
                "subject": latest["subject"][:50],
                "flight_info": flight_info,
                "email_date": latest.get("email_date"),
                "airline": latest.get("airline", "Unknown")
            })
            continue

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
