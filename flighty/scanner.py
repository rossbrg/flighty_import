"""
Simplified Email Scanner

Scans email folders for flight confirmation emails using IMAP.
Groups by unique segment key, keeps latest email per segment.
"""

import email
import hashlib
import pickle
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

from .airports import VALID_AIRPORT_CODES
from .airlines import is_flight_email
from .parser import (
    extract_flight_info,
    is_marketing_email,
    get_email_type,
    format_date_display
)
from .email_handler import decode_header_value, get_email_body, parse_email_date
from .scoring import passes_score_threshold

# Rate limiting settings
IMAP_BATCH_DELAY = 0.2
IMAP_SEARCH_DELAY = 0.1
IMAP_RETRY_DELAY = 5
IMAP_MAX_RETRIES = 3

# Cache settings
CACHE_DIR = Path(__file__).parent.parent / ".email_cache"
CACHE_FILE = CACHE_DIR / "emails.pkl"

# Airline domains for IMAP search
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
    # Defunct Airlines (for historical email scanning)
    "continental.com", "nwa.com", "usairways.com", "americawest.com",
    "virginamerica.com", "airtran.com", "thomascookairlines.com",
    "monarch.co.uk", "wowair.com", "airberlin.com", "flybe.com",
    "jetairways.com", "kingfisherairlines.com", "ikifly.com",
    "ikifly.aero", "alohaairlines.com", "midwestairlines.com",
    "germanwings.com", "primeraair.com", "mexicana.com",
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

AIRLINE_KEYWORDS = [
    "jetblue", "delta", "united", "american", "southwest",
    "alaska", "spirit", "frontier", "hawaiian", "emirates",
    "british airways", "lufthansa", "air france", "klm",
]

SUBJECT_KEYWORDS = [
    "flight confirmation", "itinerary", "e-ticket", "eticket",
    "booking confirmation", "trip confirmation", "travel confirmation",
    "your flight", "your trip", "reservation confirmed",
    "flight details", "travel itinerary", "flight itinerary",
    "boarding pass", "check-in", "flight reminder",
]


def _imap_search_with_retry(mail, criteria, max_retries=IMAP_MAX_RETRIES):
    """Execute IMAP search with retry logic using UIDs."""
    for attempt in range(max_retries):
        try:
            result, data = mail.uid('search', None, criteria)
            if result == 'OK' and data[0]:
                return set(data[0].split())
            return set()
        except Exception:
            if attempt < max_retries - 1:
                time.sleep(IMAP_RETRY_DELAY)
    return set()


def _build_or_query(terms, field="FROM"):
    """Build an IMAP OR query from multiple terms."""
    if not terms:
        return None
    if len(terms) == 1:
        return f'({field} "{terms[0]}")'
    query = f'({field} "{terms[-1]}")'
    for term in reversed(terms[:-1]):
        query = f'(OR ({field} "{term}") {query})'
    return query


def _search_individual(mail, since_date, terms, field, all_ids):
    """Fall back to individual searches when OR queries fail."""
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


def _optimized_search(mail, since_date, verbose=True):
    """Execute optimized searches using combined OR queries with fallback."""
    all_ids = set()
    sources = {}
    using_fallback = False

    search_groups = [
        ("US Airlines", AIRLINE_DOMAINS[0:12], "FROM"),
        ("European Airlines 1", AIRLINE_DOMAINS[12:22], "FROM"),
        ("European Airlines 2", AIRLINE_DOMAINS[22:33], "FROM"),
        ("Middle East/Africa", AIRLINE_DOMAINS[33:42], "FROM"),
        ("Asia/Pacific 1", AIRLINE_DOMAINS[42:54], "FROM"),
        ("Asia/Pacific 2", AIRLINE_DOMAINS[54:64], "FROM"),
        ("Americas Airlines", AIRLINE_DOMAINS[64:74], "FROM"),
        ("Booking Sites 1", AIRLINE_DOMAINS[74:84], "FROM"),
        ("Booking Sites 2", AIRLINE_DOMAINS[84:92], "FROM"),
        ("Corporate Travel", AIRLINE_DOMAINS[92:107], "FROM"),
        ("Credit/Travel Agencies", AIRLINE_DOMAINS[107:], "FROM"),
        ("Airline Keywords", AIRLINE_KEYWORDS, "FROM"),
        ("Subject Keywords", SUBJECT_KEYWORDS, "SUBJECT"),
    ]

    total_groups = len(search_groups)

    for idx, (group_name, terms, field) in enumerate(search_groups):
        if not terms:
            continue

        found_in_group = 0
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

            if not ids and len(terms) > 1:
                if not using_fallback:
                    using_fallback = True
                found_in_group = _search_individual(mail, since_date, terms, field, all_ids)

        if found_in_group > 0:
            sources[group_name] = found_in_group

        if verbose:
            print(f"\r      Searching... ({idx+1}/{total_groups})" + " " * 20, end="", flush=True)

        time.sleep(IMAP_BATCH_DELAY)

    if verbose:
        print()

    return all_ids, sources, using_fallback


def _fetch_headers_batch(mail, email_ids, batch_size=50, verbose=True):
    """Fetch email headers in batches for speed."""
    results = []
    total = len(email_ids)
    processed = 0

    for i in range(0, len(email_ids), batch_size):
        batch = email_ids[i:i + batch_size]
        id_str = b','.join(batch)

        try:
            result, data = mail.uid('fetch', id_str, '(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])')
            if result != 'OK':
                processed += len(batch)
                continue

            for item in data:
                if isinstance(item, tuple) and len(item) >= 2:
                    info = item[0]
                    if isinstance(info, bytes):
                        info = info.decode('ascii', errors='ignore')

                    uid_match = re.search(r'UID\s+(\d+)', info)
                    if not uid_match:
                        continue

                    uid = uid_match.group(1).encode('ascii')
                    header_data = item[1]

                    if header_data:
                        try:
                            header_msg = email.message_from_bytes(header_data)
                            results.append((uid, {
                                'from': decode_header_value(header_msg.get('From', '')),
                                'subject': decode_header_value(header_msg.get('Subject', '')),
                                'date': header_msg.get('Date', '')
                            }))
                        except Exception:
                            pass

            processed += len(batch)
            if verbose:
                print(f"\r      Checking... {processed}/{total}" + " " * 10, end="", flush=True)

        except Exception:
            for eid in batch:
                try:
                    result, msg_data = mail.uid('fetch', eid, '(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])')
                    if result == 'OK' and msg_data and msg_data[0]:
                        header_data = msg_data[0][1]
                        if header_data:
                            header_msg = email.message_from_bytes(header_data)
                            results.append((eid, {
                                'from': decode_header_value(header_msg.get('From', '')),
                                'subject': decode_header_value(header_msg.get('Subject', '')),
                                'date': header_msg.get('Date', '')
                            }))
                    time.sleep(IMAP_SEARCH_DELAY)
                except Exception:
                    pass

            processed += len(batch)
            if verbose:
                print(f"\r      Checking... {processed}/{total}" + " " * 10, end="", flush=True)

        time.sleep(IMAP_BATCH_DELAY)

    return results


def save_email_cache(flight_candidates, raw_emails, related_emails):
    """Save downloaded emails to cache."""
    CACHE_DIR.mkdir(exist_ok=True)
    cache_data = {
        'version': 2,
        'flight_candidates': flight_candidates,
        'related_emails': related_emails,
        'timestamp': datetime.now().isoformat()
    }
    with open(CACHE_FILE, 'wb') as f:
        pickle.dump(cache_data, f)
    embedded_count = sum(1 for c in flight_candidates if c.get('raw_bytes'))
    print(f"      Cache saved: {embedded_count} emails")


def load_email_cache():
    """Load cached emails from disk."""
    if not CACHE_FILE.exists():
        return None, None, None
    try:
        with open(CACHE_FILE, 'rb') as f:
            cache_data = pickle.load(f)

        version = cache_data.get('version', 1)
        print(f"      Loaded cache v{version} from {cache_data.get('timestamp', 'unknown')}")

        if version >= 2:
            return (
                cache_data['flight_candidates'],
                None,
                cache_data.get('related_emails', {})
            )
        else:
            print("      WARNING: Old cache format - run with --save-cache to regenerate")
            return (
                cache_data['flight_candidates'],
                cache_data.get('raw_emails', {}),
                cache_data.get('related_emails', {})
            )
    except Exception as e:
        print(f"      Cache load failed: {e}")
        return None, None, None


def generate_content_hash(subject: str, body: str) -> str:
    """Generate a hash of email content for deduplication."""
    content = f"{subject}|{body[:1000]}"
    return hashlib.md5(content.encode()).hexdigest()[:12]


def create_segment_key(confirmation: str, origin: str, dest: str, date: str, flight_num: str = None) -> str:
    """Create a unique key for a flight segment.

    Primary: (confirmation, origin, dest, date)
    Fallback: (flight_number, origin, dest, date) if no confirmation
    """
    if confirmation:
        return f"{confirmation}|{origin}|{dest}|{date}"
    elif flight_num:
        return f"FN:{flight_num}|{origin}|{dest}|{date}"
    else:
        return f"ROUTE|{origin}|{dest}|{date}"


def scan_for_flights(mail, config, folder, processed, use_cache=False, save_cache=False,
                     use_scoring=False, score_threshold=50, verbose=True):
    """Scan folder and collect all flight emails.

    Args:
        mail: IMAP connection
        config: Configuration dict
        folder: Folder name to scan
        processed: Already processed flights dict
        use_cache: Use cached emails instead of IMAP
        save_cache: Save emails to cache
        use_scoring: Enable score-based pre-filtering
        score_threshold: Minimum score to pass (default 50)
        verbose: Print progress output

    Returns:
        Tuple: (flights_found dict, skipped_confirmations list)
    """
    flights_found = {}
    skipped_confirmations = []
    already_processed = processed.get("confirmations", {})
    processed_hashes = processed.get("content_hashes", set())

    folder_start = time.time()

    # Cache mode
    cached_flight_candidates = None
    cached_raw_emails = None

    if use_cache:
        print()
        print("  [CACHE MODE] Loading emails from cache...")
        cached_flight_candidates, cached_raw_emails, _ = load_email_cache()
        if cached_flight_candidates is None:
            print("      No cache found. Run with --save-cache first.")
            return flights_found, skipped_confirmations
        print(f"      Loaded {len(cached_flight_candidates)} candidates from cache")
        flight_candidates = cached_flight_candidates
    else:
        # Normal IMAP mode
        try:
            result, _ = mail.select(folder)
            if result != 'OK':
                print(f"    Could not open folder: {folder}")
                return flights_found, skipped_confirmations
        except Exception:
            print(f"    Could not open folder: {folder}")
            return flights_found, skipped_confirmations

        since_date = (datetime.now() - timedelta(days=config['days_back'])).strftime("%d-%b-%Y")

        # Phase 1: Search for flight emails
        print()
        print(f"  [1/3] Searching for flight emails (past {config['days_back']} days)...")
        search_start = time.time()
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
            return flights_found, skipped_confirmations

        # Phase 2: Check headers to filter flight confirmations
        print(f"  [2/3] Filtering flight confirmations...")
        scan_start = time.time()
        flight_candidates = []
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
    if use_cache:
        print(f"  [CACHE MODE] Processing {len(flight_candidates)} cached emails...")
    else:
        print(f"  [3/3] Downloading and analyzing {len(flight_candidates)} emails...")

    download_start = time.time()
    flight_count = 0
    skipped_count = 0
    download_count = 0
    failed_downloads = 0
    marketing_filtered = 0
    score_filtered = 0
    cancelled_codes = set()

    for candidate in flight_candidates:
        download_count += 1
        email_id = candidate['email_id']

        if download_count % 5 == 0 or download_count == len(flight_candidates):
            print(f"\r      Processing... {download_count}/{len(flight_candidates)}" + " " * 10, end="", flush=True)

        # Get raw email
        raw_email = None
        if use_cache and candidate.get('raw_bytes'):
            raw_email = candidate['raw_bytes']
        elif use_cache and cached_raw_emails:
            raw_email = cached_raw_emails.get(email_id)
        elif use_cache:
            continue
        else:
            for attempt in range(IMAP_MAX_RETRIES):
                try:
                    result, msg_data = mail.uid('fetch', email_id, '(RFC822)')
                    time.sleep(IMAP_SEARCH_DELAY)
                    if result == 'OK' and msg_data and msg_data[0]:
                        raw_email = msg_data[0][1]
                        if raw_email:
                            if save_cache:
                                candidate['raw_bytes'] = raw_email
                            break
                except Exception:
                    if attempt < IMAP_MAX_RETRIES - 1:
                        time.sleep(IMAP_RETRY_DELAY)
                    else:
                        failed_downloads += 1

        if not raw_email:
            continue

        try:
            msg = email.message_from_bytes(raw_email)
            from_addr = decode_header_value(msg.get('From', ''))
            subject = decode_header_value(msg.get('Subject', ''))
            date_str = msg.get('Date', '')

            # Re-detect airline
            is_flight, airline = is_flight_email(from_addr, subject)
            if not is_flight:
                continue

            body, html_body = get_email_body(msg)
            full_body = body or html_body or ""
            email_date = parse_email_date(date_str)
            content_hash = generate_content_hash(subject, full_body)

            # Optional scoring pre-filter
            email_score = None
            score_reasons = None
            if use_scoring:
                passes, email_score, score_reasons = passes_score_threshold(
                    subject, full_body, from_addr, score_threshold
                )
                if not passes:
                    if verbose:
                        print(f"\r      [SCORE:{email_score}] Below threshold - skipping" + " " * 20)
                    score_filtered += 1
                    continue

            # Extract flight info using new simplified parser
            flight_info = extract_flight_info(
                html_content=html_body or full_body,
                text_content=body,
                subject=subject,
                from_addr=from_addr,
                email_date=email_date
            )

            # Check email type
            email_type = flight_info.get("email_type", "unknown")
            if email_type == 'marketing':
                marketing_filtered += 1
                continue
            if email_type == 'cancellation':
                conf = flight_info.get("confirmation")
                if conf:
                    cancelled_codes.add(conf)
                continue

            confirmation = flight_info.get("confirmation")

            # Skip if already processed
            if confirmation and confirmation in already_processed:
                if content_hash in processed_hashes:
                    skipped_count += 1
                    if confirmation not in skipped_confirmations:
                        skipped_confirmations.append(confirmation)
                    continue

            flight_count += 1

            # Store flight data with segments
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
                "folder": folder,
                "score": email_score,
                "score_reasons": score_reasons,
            }

            # Group by confirmation code or route fingerprint
            if confirmation:
                key = confirmation
            else:
                # Fallback: use route + date
                route = flight_info.get("route")
                dates = flight_info.get("dates", [])
                if route and dates:
                    key = f"route_{route[0]}_{route[1]}_{dates[0]}"
                else:
                    key = f"unknown_{content_hash}"

            if key not in flights_found:
                flights_found[key] = []
            flights_found[key].append(flight_data)

        except Exception:
            failed_downloads += 1
            continue

    print()

    # Save cache if requested
    if save_cache:
        has_embedded = any(c.get('raw_bytes') for c in flight_candidates)
        if has_embedded:
            save_email_cache(flight_candidates, None, {})

    # Remove cancelled flights
    cancelled_count = 0
    for code in cancelled_codes:
        if code in flights_found:
            cancelled_count += 1
            del flights_found[code]

    total_time = time.time() - folder_start

    # Summary
    summary_parts = [f"{flight_count} new flights"]
    if skipped_count > 0:
        summary_parts.append(f"{skipped_count} already imported")
    if marketing_filtered > 0:
        summary_parts.append(f"{marketing_filtered} marketing skipped")
    if score_filtered > 0:
        summary_parts.append(f"{score_filtered} below score threshold")
    if cancelled_count > 0:
        summary_parts.append(f"{cancelled_count} cancelled")
    if failed_downloads > 0:
        summary_parts.append(f"{failed_downloads} failed")
    if use_cache:
        summary_parts.append("from cache")
    print(f"  ✓ {folder}: {', '.join(summary_parts)} ({total_time:.1f}s)")

    return flights_found, skipped_confirmations


def _safe_datetime(dt):
    """Convert datetime to naive for safe comparison."""
    if dt is None:
        return datetime.min
    if hasattr(dt, 'tzinfo') and dt.tzinfo is not None:
        return dt.replace(tzinfo=None)
    return dt


def select_latest_flights(all_flights, processed):
    """For each confirmation code, use ONLY the latest email's segments.

    This handles rebookings correctly: older emails have outdated info.

    Strategy:
    1. Group all emails by confirmation code
    2. For each confirmation, sort by email_date and pick NEWEST
    3. Use ONLY segments from that newest email
    4. For emails without confirmation, fall back to flight_number grouping

    Returns:
        Tuple: (to_forward list, skipped list, duplicates_merged int)
    """
    to_forward = []
    skipped = []
    duplicates_merged = 0

    processed_confs = processed.get("confirmations", {})
    processed_hashes = processed.get("content_hashes", set())

    # First pass: group by confirmation code
    conf_groups = {}  # confirmation -> list of emails
    no_conf_emails = []  # emails without confirmation

    for conf_code, emails in all_flights.items():
        for email_data in emails:
            flight_info = email_data.get("flight_info", {})
            confirmation = email_data.get("confirmation") or flight_info.get("confirmation")

            if confirmation:
                if confirmation not in conf_groups:
                    conf_groups[confirmation] = []
                conf_groups[confirmation].append(email_data)
            else:
                no_conf_emails.append(email_data)

    # Process emails WITH confirmation codes
    for confirmation, emails in conf_groups.items():
        if len(emails) > 1:
            duplicates_merged += len(emails) - 1

        # Sort by email date, newest first
        emails.sort(key=lambda x: _safe_datetime(x.get("email_date")), reverse=True)
        best_email = emails[0]

        # Check if already processed (any segment of this confirmation)
        if confirmation in processed_confs:
            skipped.append({
                "confirmation": confirmation,
                "reason": "already imported",
                "subject": best_email.get("subject", "")[:50],
                "email_date": best_email.get("email_date"),
                "airline": best_email.get("airline", "Unknown")
            })
            continue

        if best_email.get("content_hash") in processed_hashes:
            skipped.append({
                "confirmation": confirmation,
                "reason": "duplicate content",
                "subject": best_email.get("subject", "")[:50],
                "email_date": best_email.get("email_date"),
                "airline": best_email.get("airline", "Unknown")
            })
            continue

        # Merge ALL unique segments from ALL emails for this confirmation
        # This handles round trips where check-in emails only show one leg
        all_segments = {}  # key: (origin, dest, date) -> segment with latest flight_number
        for email_data in emails:
            flight_info = email_data.get("flight_info", {})
            email_segments = flight_info.get("segments", [])

            for seg in email_segments:
                origin = seg.get("origin")
                dest = seg.get("destination")
                date = seg.get("date")
                if origin and dest and date:
                    key = (origin, dest, date)
                    # Keep segment with flight number, or latest one
                    if key not in all_segments or (seg.get("flight_number") and not all_segments[key].get("flight_number")):
                        all_segments[key] = seg

        segments = list(all_segments.values())

        # Sort segments by date
        segments.sort(key=lambda x: x.get("date", "9999"))

        if not segments:
            skipped.append({
                "confirmation": confirmation,
                "reason": "no segments found",
                "subject": best_email.get("subject", "")[:50],
                "email_date": best_email.get("email_date"),
                "airline": best_email.get("airline", "Unknown")
            })
            continue

        # Create a flight entry for EACH unique segment
        for segment in segments:
            origin = segment.get("origin")
            dest = segment.get("destination")
            date = segment.get("date")
            flight_num = segment.get("flight_number")

            if not origin or not dest:
                continue

            result = best_email.copy()
            result["confirmation"] = confirmation
            result["flight_info"] = flight_info.copy()
            result["flight_info"]["route"] = (origin, dest)
            result["flight_info"]["airports"] = [origin, dest]
            result["flight_info"]["iso_date"] = date  # Keep ISO date for sorting
            if date:
                result["flight_info"]["dates"] = [format_date_display(date)]
            if flight_num:
                result["flight_info"]["flight_numbers"] = [flight_num]

            result["email_count"] = len(emails)
            to_forward.append(result)

    # Process emails WITHOUT confirmation codes (group by flight_number + route + date)
    segment_groups = {}  # key -> list of (email_data, segment)

    for email_data in no_conf_emails:
        flight_info = email_data.get("flight_info", {})
        segments = flight_info.get("segments", [])

        if not segments:
            route = flight_info.get("route")
            dates = flight_info.get("dates", [])
            flight_nums = flight_info.get("flight_numbers", [])
            if route and dates:
                segments = [{
                    "origin": route[0],
                    "destination": route[1],
                    "date": dates[0],
                    "flight_number": flight_nums[0] if flight_nums else None
                }]

        for segment in segments:
            origin = segment.get("origin")
            dest = segment.get("destination")
            date = segment.get("date")
            flight_num = segment.get("flight_number")

            if not origin or not dest or not date:
                continue

            key = create_segment_key(None, origin, dest, date, flight_num)
            if key not in segment_groups:
                segment_groups[key] = []
            segment_groups[key].append((email_data, segment))

    for key, entries in segment_groups.items():
        if len(entries) > 1:
            duplicates_merged += len(entries) - 1

        # Sort by email date, newest first
        entries.sort(key=lambda x: _safe_datetime(x[0].get("email_date")), reverse=True)
        best_email, segment = entries[0]

        if best_email.get("content_hash") in processed_hashes:
            continue

        origin = segment.get("origin")
        dest = segment.get("destination")
        date = segment.get("date")
        flight_num = segment.get("flight_number")

        if not flight_num:
            skipped.append({
                "confirmation": key,
                "reason": "no confirmation or flight number",
                "subject": best_email.get("subject", "")[:50],
                "email_date": best_email.get("email_date"),
                "airline": best_email.get("airline", "Unknown")
            })
            continue

        flight_info = best_email.get("flight_info", {})
        result = best_email.copy()
        result["confirmation"] = None
        result["flight_info"] = flight_info.copy()
        result["flight_info"]["route"] = (origin, dest)
        result["flight_info"]["airports"] = [origin, dest]
        result["flight_info"]["iso_date"] = date
        if date:
            result["flight_info"]["dates"] = [format_date_display(date)]
        if flight_num:
            result["flight_info"]["flight_numbers"] = [flight_num]

        result["email_count"] = len(entries)
        to_forward.append(result)

    # Sort by flight date (use ISO date for proper chronological order)
    def get_sort_date(f):
        iso_date = f.get("flight_info", {}).get("iso_date")
        if iso_date:
            return iso_date
        dates = f.get("flight_info", {}).get("dates", [])
        return dates[0] if dates else "9999"

    to_forward.sort(key=get_sort_date)

    return to_forward, skipped, duplicates_merged


def export_flights_to_json(flights: list, output_path: str, include_raw: bool = False) -> str:
    """Export flights to JSON file.

    Args:
        flights: List of flight dicts from scan
        output_path: Path to output JSON file
        include_raw: If True, include raw email body (large!)

    Returns:
        Path to created file
    """
    import json
    from datetime import datetime

    export_data = []

    for flight in flights:
        flight_info = flight.get("flight_info", {})

        entry = {
            # Metadata
            "confirmation": flight.get("confirmation"),
            "airline": flight.get("airline"),
            "content_hash": flight.get("content_hash"),
            "folder": flight.get("folder"),

            # Scoring (if available)
            "score": flight.get("score"),
            "score_reasons": flight.get("score_reasons"),

            # Headers
            "subject": flight.get("subject"),
            "from": flight.get("from_addr"),
            "date": flight.get("email_date").isoformat() if flight.get("email_date") else None,

            # Flight details
            "route": flight_info.get("route"),
            "airports": flight_info.get("airports"),
            "flight_numbers": flight_info.get("flight_numbers"),
            "dates": flight_info.get("dates"),
            "segments": flight_info.get("segments"),
            "email_type": flight_info.get("email_type"),
        }

        if include_raw:
            # Include plain body (not full MIME - too large)
            msg = flight.get("msg")
            if msg:
                from .email_handler import get_email_body
                body, _ = get_email_body(msg)
                entry["plain_body"] = body[:10000] if body else None  # Limit size

        export_data.append(entry)

    # Write JSON
    output = {
        "exported_at": datetime.now().isoformat(),
        "flight_count": len(export_data),
        "flights": export_data
    }

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, default=str)

    return output_path
