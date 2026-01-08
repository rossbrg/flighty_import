#!/usr/bin/env python3
"""
Flighty Email Forwarder - Main Runner

Scans your email for flight confirmations and forwards them to Flighty.

Usage:
    python3 run.py              # Run normally
    python3 run.py --dry-run    # Test without forwarding
    python3 run.py --setup      # Run setup wizard
    python3 run.py --days N     # Search N days back
    python3 run.py --debug      # Enable debug logging
    python3 run.py --help       # Show help
"""

import sys
import logging
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime

# Import from the flighty package
from flighty import __version__
from flighty.config import (
    CONFIG_FILE,
    load_config,
    load_processed_flights,
    save_processed_flights,
    reset_processed_flights,
    clean_data_files
)
from flighty.airports import VALID_AIRPORT_CODES, get_airport_display
from flighty.email_handler import connect_imap, forward_email
from flighty.scanner import scan_for_flights, select_latest_flights
from flighty.setup import run_setup
from flighty.pdf_report import generate_pdf_report
# Note: deps.py auto-installs python-dateutil when parser.py first needs it

# Constants
SCRIPT_DIR = Path(__file__).parent
VERSION = __version__
GITHUB_REPO = "drewtwitchell/flighty_import"
# Files to download during auto-update (order matters - run.py last so it restarts with new code)
UPDATE_FILES = [
    # Package files
    "flighty/__init__.py",
    "flighty/airports.py",
    "flighty/airlines.py",
    "flighty/config.py",
    "flighty/parser.py",
    "flighty/email_handler.py",
    "flighty/scanner.py",
    "flighty/setup.py",
    "flighty/deps.py",
    # Data files
    "airport_codes.txt",
    "VERSION",
    "pyproject.toml",
    # Main script (last, triggers restart)
    "run.py",
]


def auto_update():
    """Check for and apply updates from GitHub. Returns True if updated."""
    print()
    print("=" * 60)
    print("  STEP 1 OF 4: CHECKING FOR UPDATES")
    print("=" * 60)
    print()
    print("  Connecting to GitHub to check if a newer version exists...")

    try:
        # Get latest version from GitHub
        version_url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/flighty/__init__.py"
        try:
            with urllib.request.urlopen(version_url, timeout=5) as response:
                content = response.read().decode('utf-8')
                # Extract version from file
                for line in content.split('\n'):
                    if '__version__' in line and '=' in line:
                        latest_version = line.split('"')[1]
                        break
                else:
                    latest_version = VERSION
        except urllib.error.HTTPError:
            # Fallback: check VERSION file
            version_url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/VERSION"
            with urllib.request.urlopen(version_url, timeout=5) as response:
                latest_version = response.read().decode('utf-8').strip()

        # Compare versions semantically (major.minor.patch)
        def parse_version(v):
            try:
                parts = v.split('.')
                return tuple(int(p) for p in parts)
            except (ValueError, AttributeError):
                return (0, 0, 0)

        current_ver = parse_version(VERSION)
        latest_ver = parse_version(latest_version)

        if latest_ver <= current_ver:
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
                    # Create directory if needed
                    file_path.parent.mkdir(parents=True, exist_ok=True)
                    with open(file_path, 'w', encoding='utf-8') as f:
                        f.write(content)
                    print(f" {filename.split('/')[-1]}", end="", flush=True)
                    updated = True
            except Exception as e:
                print(f" [FAILED: {e}]", end="", flush=True)

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
    except Exception:
        print("  Could not check for updates - continuing with current version")
        print()
        return False


def format_flight_line(conf, flight_info, airline=None, email_date=None, is_update=False, email_count=None):
    """Format a single flight for display."""
    airports = flight_info.get("airports", []) if flight_info else []
    dates = flight_info.get("dates", []) if flight_info else []
    flights = flight_info.get("flight_numbers", []) if flight_info else []
    route_tuple = flight_info.get("route") if flight_info else None

    # Use route tuple if available, otherwise use airports list
    if route_tuple:
        valid_airports = list(route_tuple)
    else:
        valid_airports = [code for code in airports if code in VALID_AIRPORT_CODES]

    # Format route with airport names
    if len(valid_airports) >= 2:
        origin = get_airport_display(valid_airports[0])
        dest = get_airport_display(valid_airports[1])
        route = f"{origin} → {dest}"
    elif valid_airports:
        route = get_airport_display(valid_airports[0])
    else:
        route = ""

    date = dates[0] if dates else ""
    flight_num = flights[0] if flights else ""

    # Build display line - ensure conf is never None
    conf_display = conf if conf else "------"
    parts = [f"  {conf_display:<8}"]
    if flight_num:
        parts.append(f"{flight_num:<10}")
    if route:
        parts.append(route)
    if date:
        parts.append(f"  {date}")

    line = " ".join(parts)

    # Add status indicators
    if is_update:
        line += "  [UPDATE]"
    if email_count and email_count > 1:
        line += f"  ({email_count} emails)"

    return line


def display_scan_results(to_forward, skipped, duplicates_merged, processed):
    """Display comprehensive scan results before forwarding."""

    already_in_flighty = processed.get("confirmations", {})

    print()
    print("=" * 70)
    print("  SCAN RESULTS")
    print("=" * 70)

    # ============================================
    # Section 1: Flights to be forwarded
    # ============================================
    print()
    if to_forward:
        updates = [f for f in to_forward if f.get("is_update")]
        new_flights = [f for f in to_forward if not f.get("is_update")]

        print(f"  ┌─ NEW FLIGHTS TO FORWARD: {len(to_forward)} ─────────────────────────────")
        print("  │")

        if new_flights:
            print("  │  NEW:")
            for flight in new_flights[:15]:
                conf = flight.get("confirmation") or "------"
                flight_info = flight.get("flight_info") or {}
                line = format_flight_line(
                    conf, flight_info,
                    airline=flight.get("airline"),
                    email_count=flight.get("email_count")
                )
                print(f"  │  {line[2:]}")  # Remove leading spaces since we have │

            if len(new_flights) > 15:
                print(f"  │    ... and {len(new_flights) - 15} more new flights")

        if updates:
            if new_flights:
                print("  │")
            print("  │  UPDATES (flight details changed):")
            for flight in updates[:10]:
                conf = flight.get("confirmation") or "------"
                flight_info = flight.get("flight_info") or {}
                line = format_flight_line(
                    conf, flight_info,
                    is_update=True,
                    email_count=flight.get("email_count")
                )
                print(f"  │  {line[2:]}")

            if len(updates) > 10:
                print(f"  │    ... and {len(updates) - 10} more updates")

        print("  │")
        print("  └" + "─" * 55)
    else:
        print("  ┌─ NEW FLIGHTS TO FORWARD: 0 ──────────────────────────────")
        print("  │")
        print("  │  No new flights found to forward.")
        print("  │")
        print("  └" + "─" * 55)

    # ============================================
    # Section 2: Already in Flighty
    # ============================================
    print()
    if already_in_flighty:
        print(f"  ┌─ ALREADY IN FLIGHTY: {len(already_in_flighty)} ────────────────────────────────")
        print("  │")

        # Sort by flight date, most recent first
        sorted_flights = sorted(
            already_in_flighty.items(),
            key=lambda x: x[1].get("date", ""),
            reverse=True
        )

        for conf, data in sorted_flights[:10]:
            route = data.get("route", "")
            date = data.get("date", "")
            display = f"  │    {conf:<8}"
            if route:
                display += f"  {route:<15}"
            if date:
                display += f"  {date}"
            print(display)

        if len(sorted_flights) > 10:
            print(f"  │    ... and {len(sorted_flights) - 10} more already imported")

        print("  │")
        print("  └" + "─" * 55)

    # ============================================
    # Section 3: Skipped in this scan
    # ============================================
    if skipped:
        print()
        print(f"  ┌─ SKIPPED (already processed): {len(skipped)} ─────────────────────")
        print("  │")

        for item in skipped[:5]:
            conf = item.get("confirmation") or "------"
            reason = item.get("reason") or ""
            flight_info = item.get("flight_info") or {}
            airports = flight_info.get("airports", [])
            valid_airports = [code for code in airports if code in VALID_AIRPORT_CODES]
            route = " → ".join(valid_airports[:2]) if valid_airports else ""

            display = f"  │    {conf:<8}"
            if route:
                display += f"  {route:<15}"
            display += f"  ({reason})"
            print(display)

        if len(skipped) > 5:
            print(f"  │    ... and {len(skipped) - 5} more skipped")

        print("  │")
        print("  └" + "─" * 55)

    # ============================================
    # Section 4: Summary stats
    # ============================================
    print()
    print("  ─" * 35)
    print()
    print("  SUMMARY:")
    print(f"    • New flights to forward:    {len(to_forward)}")
    print(f"    • Already in Flighty:        {len(already_in_flighty)}")
    print(f"    • Skipped (duplicates):      {len(skipped)}")
    if duplicates_merged > 0:
        print(f"    • Duplicate emails merged:   {duplicates_merged}")
        print("      (Multiple emails for same confirmation - using latest)")
    print()

    # ============================================
    # Section 5: Preview what will be sent
    # ============================================
    if to_forward:
        print()
        print("  ┌─ WHAT WILL BE SENT TO FLIGHTY ─────────────────────────────")
        print("  │")
        print("  │  The original airline emails will be forwarded to Flighty.")
        print("  │")
        print("  └" + "─" * 55)
        print()


def forward_flights(config, to_forward, processed, dry_run):
    """Forward flights to Flighty."""
    if not to_forward:
        print()
        print("  No new flights to forward - all caught up!")
        print()
        return

    if dry_run:
        print()
        print("  ╔════════════════════════════════════════════════════════════╗")
        print("  ║  DRY RUN MODE - No emails will actually be sent            ║")
        print("  ╚════════════════════════════════════════════════════════════╝")
        print()
        print(f"  The following {len(to_forward)} flights WOULD be forwarded to Flighty:")
        print()

        # Group flights by month for easier reading
        import re
        from collections import defaultdict

        def parse_month_year(date_str):
            """Extract month and year from date string like 'April 28, 2025'."""
            if not date_str:
                return ("Unknown", 9999)
            # Try to match "Month DD, YYYY" or "Month YYYY" formats
            match = re.match(r'(\w+)\s+\d{1,2}?,?\s*(\d{4})', date_str)
            if match:
                return (match.group(1), int(match.group(2)))
            match = re.match(r'(\w+)\s+(\d{4})', date_str)
            if match:
                return (match.group(1), int(match.group(2)))
            return ("Unknown", 9999)

        # Month order for sorting
        month_order = {
            'January': 1, 'February': 2, 'March': 3, 'April': 4,
            'May': 5, 'June': 6, 'July': 7, 'August': 8,
            'September': 9, 'October': 10, 'November': 11, 'December': 12,
            'Unknown': 13
        }

        # Group by month-year
        flights_by_month = defaultdict(list)
        for flight in to_forward:
            flight_info = flight.get("flight_info") or {}
            dates = flight_info.get("dates") or []
            date = dates[0] if dates else ""
            month, year = parse_month_year(date)
            key = (year, month_order.get(month, 13), month)
            flights_by_month[key].append(flight)

        # Sort by year, then month
        sorted_months = sorted(flights_by_month.keys())

        flight_num_counter = 0
        for (year, month_num, month_name) in sorted_months:
            flights = flights_by_month[(year, month_num, month_name)]

            # Print month header
            print()
            print(f"  ══════════════════════════════════════════════════════════════")
            print(f"   {month_name.upper()} {year}  ({len(flights)} flights)")
            print(f"  ══════════════════════════════════════════════════════════════")
            print()

            for flight in flights:
                flight_num_counter += 1
                conf = flight.get("confirmation") or "------"
                flight_info = flight.get("flight_info") or {}
                airports = flight_info.get("airports") or []
                dates = flight_info.get("dates") or []
                flights_list = flight_info.get("flight_numbers") or []
                route_tuple = flight_info.get("route")
                email_count = flight.get("email_count") or 1
                email_date = flight.get("email_date")

                # Use route tuple if available
                if route_tuple:
                    valid_airports = list(route_tuple)
                else:
                    valid_airports = [code for code in airports if code in VALID_AIRPORT_CODES]

                # Format route with airport names
                if len(valid_airports) >= 2:
                    origin = get_airport_display(valid_airports[0])
                    dest = get_airport_display(valid_airports[1])
                    route = f"{origin} → {dest}"
                elif valid_airports:
                    route = get_airport_display(valid_airports[0])
                else:
                    route = ""

                date = dates[0] if dates else ""
                flight_num = flights_list[0] if flights_list else ""

                print(f"  ┌─ Email {flight_num_counter} of {len(to_forward)} ─────────────────────────────────────")
                print(f"  │  From:         {flight.get('from_addr', '')[:50]}")
                print(f"  │  Subject:      {flight.get('subject', '')[:50]}")
                if conf != "------":
                    print(f"  │  Confirmation: {conf}")
                if route:
                    print(f"  │  Route:        {route}")
                if flight_num:
                    print(f"  │  Flight:       {flight_num}")
                if date:
                    print(f"  │  Flight Date:  {date}")
                if email_date:
                    print(f"  │  Email Date:   {email_date.strftime('%Y-%m-%d %H:%M') if hasattr(email_date, 'strftime') else email_date}")
                if email_count > 1:
                    print(f"  │  ({email_count} emails found - using most recent)")
                if flight.get("is_update"):
                    print(f"  │  UPDATE: Flight details changed since last import")
                print(f"  └────────────────────────────────────────────────────────────")
                print()

        print("  ═" * 32)
        print()
        print("  ✓ Dry run complete!")
        print()
        print("  What happens next if you run without --dry-run:")
        print("    1. The original airline emails will be forwarded to Flighty")
        print("    2. Progress is saved after each successful send")
        print("    3. If rate-limited, we'll wait and retry automatically")
        print()
        print("  Ready to import? Run: python3 run.py")
        print()
        return

    print()
    print("  IMPORTANT - PLEASE BE PATIENT:")
    print("  - Email providers (AOL, Yahoo, Gmail, etc.) limit sending speed")
    print("  - If we send too fast, they temporarily block us")
    print("  - When blocked, we wait and automatically retry (up to 5 minutes)")
    print("  - Large batches may take 10-30+ minutes - this is normal!")
    print()
    print("  Do not close this window - your progress is saved after each send.")
    print()
    print("  ─" * 35)

    sent = 0
    failed = 0

    for i, flight in enumerate(to_forward):
        conf = flight.get("confirmation") or "------"
        flight_info = flight.get("flight_info") or {}
        airports = flight_info.get("airports") or []
        dates = flight_info.get("dates") or []
        flights_list = flight_info.get("flight_numbers") or []
        route_tuple = flight_info.get("route")

        # Use route tuple if available
        if route_tuple:
            valid_airports = list(route_tuple)
        else:
            valid_airports = [code for code in airports if code in VALID_AIRPORT_CODES]

        # Format route with airport codes (keep short for header)
        route = " → ".join(valid_airports[:2]) if valid_airports else ""
        date = dates[0] if dates else ""
        flight_num = flights_list[0] if flights_list else ""

        # Show what email we're sending
        print()
        print(f"  [{i+1}/{len(to_forward)}] Sending original email to Flighty:")
        print(f"        From:    {flight['from_addr'][:60]}")
        print(f"        Subject: {flight['subject'][:60]}")
        if conf != "------":
            print(f"        Conf:    {conf}")
        if route:
            print(f"        Route:   {route}")
        if flight_num:
            print(f"        Flight:  {flight_num}")
        if date:
            print(f"        Date:    {date}")

        success = forward_email(
            config,
            flight["msg"],
            flight["from_addr"],
            flight["subject"],
            flight_info=flight_info
        )

        if success:
            print(f"        ✓ Sent successfully")
            sent += 1

            # Save progress immediately
            conf_key = conf if conf else f"unknown_{flight['content_hash']}"
            processed["confirmations"][conf_key] = {
                "imported_at": datetime.now().isoformat(),
                "fingerprint": flight.get("fingerprint", ""),
                "route": route,
                "date": date,
                "flight_number": flight_num
            }
            processed["content_hashes"].add(flight["content_hash"])
            save_processed_flights(processed)
        else:
            failed += 1

            # If the FIRST email fails after all retries, exit gracefully
            # This indicates a systemic issue (rate limiting, auth problem, etc.)
            if i == 0:
                print()
                print("  ╔════════════════════════════════════════════════════════════╗")
                print("  ║  UNABLE TO SEND EMAILS                                     ║")
                print("  ╚════════════════════════════════════════════════════════════╝")
                print()
                print("  The first email failed after all retry attempts.")
                print("  This usually means:")
                print()
                print("    • Your email provider is rate limiting you")
                print("    • There's a temporary server issue")
                print("    • Your SMTP settings or credentials need updating")
                print()
                print("  What to do:")
                print("    1. Wait 15-30 minutes and try again")
                print("    2. If it keeps failing, run: python3 run.py --setup")
                print()
                print("  Your flight data has been saved to the PDF in the raw/ folder.")
                print()
                return

    print()
    print("  ─" * 35)
    print()
    print("  FORWARDING COMPLETE")
    print()
    print(f"    ✓ Successfully sent: {sent}")
    if failed > 0:
        print(f"    ✗ Failed: {failed} (run again to retry)")
    print()


def check_imap_limitation(config, flight_count, oldest_flight_date):
    """Check if IMAP might be limited and POP3 would give more results.

    AOL limits IMAP to ~10,000 messages. If we detect this, suggest POP3.
    """
    # Only check for AOL accounts
    if 'aol.com' not in config.get('email', '').lower():
        return False

    try:
        import poplib

        # Quick POP3 check
        pop = poplib.POP3_SSL('pop.aol.com', 995)
        pop.user(config['email'])
        pop.pass_(config['password'])
        total_messages, _ = pop.stat()
        pop.quit()

        # If POP3 has significantly more messages than what IMAP could scan,
        # and our oldest flight is relatively recent, suggest POP3
        if total_messages > 15000:  # AOL typically limits IMAP to ~10,000
            return True

    except Exception:
        pass

    return False


def run(dry_run=False, days_override=None, full_scan=False, use_scoring=False, score_threshold=50, export_json_path=None):
    """Main run function."""
    config = load_config()
    if not config:
        print()
        print("No configuration found!")
        print()
        print("Please run setup first:")
        print("  python3 run.py --setup")
        print()
        return

    if days_override:
        config['days_back'] = days_override

    # If full_scan requested and AOL account, use POP3 scanner
    if full_scan and 'aol.com' in config.get('email', '').lower():
        print()
        print("=" * 60)
        print("  FULL HISTORICAL SCAN (POP3)")
        print("=" * 60)
        print()
        print("  AOL limits IMAP to ~10,000 messages.")
        print("  Using POP3 to scan your entire mailbox history.")
        print()
        print("  This will take several hours for large mailboxes.")
        print("  Progress is saved - you can stop and resume anytime.")
        print()
        import subprocess
        subprocess.run([sys.executable, str(SCRIPT_DIR / "pop3_full_scan.py"), "--resume"])
        return

    processed = load_processed_flights()

    # Show what we're doing
    print()
    print("=" * 60)
    print("  STEP 2 OF 4: CONNECTING TO EMAIL")
    print("=" * 60)
    print()
    print(f"  Email:    {config['email']}")
    print(f"  Server:   {config['imap_server']}")
    print(f"  Folders:  {', '.join(config['check_folders'])}")
    print(f"  Looking:  Last {config['days_back']} days")
    print()
    print("  Connecting...", end="", flush=True)

    mail = connect_imap(config)
    if not mail:
        return

    print(" Connected!")
    print()

    # Scan for flights
    print()
    print("=" * 60)
    print("  STEP 3 OF 4: SCANNING FOR FLIGHTS")
    print("=" * 60)

    all_flights = {}
    all_skipped = []

    for folder in config['check_folders']:
        print()
        print(f"  Scanning folder: {folder}")
        flights, skipped = scan_for_flights(
            mail, config, folder, processed,
            use_scoring=use_scoring, score_threshold=score_threshold
        )
        all_flights.update(flights)
        all_skipped.extend(skipped)

    try:
        mail.logout()
    except Exception:
        pass

    print()
    print("  Email scan complete. Analyzing results...")

    # Select latest emails per confirmation (handles same-day updates)
    to_forward, skipped, duplicates_merged = select_latest_flights(all_flights, processed)

    # Export to JSON if requested
    if export_json_path:
        from flighty.scanner import export_flights_to_json
        all_for_export = list(to_forward)
        # Also include skipped flights for debugging
        for item in skipped:
            all_for_export.append(item)

        result_path = export_flights_to_json(all_for_export, export_json_path)
        print(f"  ✓ Exported {len(all_for_export)} flights to: {result_path}")

    # Check if IMAP might be limited (AOL accounts)
    total_flights_found = len(to_forward) + len(processed.get("confirmations", {}))
    if check_imap_limitation(config, total_flights_found, None):
        print()
        print("  ╔════════════════════════════════════════════════════════════╗")
        print("  ║  NOTE: Your mailbox has more emails than IMAP can access   ║")
        print("  ╚════════════════════════════════════════════════════════════╝")
        print()
        print("  AOL limits IMAP to ~10,000 recent messages. You may have older")
        print("  flight emails that weren't scanned.")
        print()
        print("  To scan your FULL email history (going back 10+ years), run:")
        print("    python3 run.py --full-scan")
        print()
        print("  Or run the POP3 scanner directly:")
        print("    python3 pop3_full_scan.py --resume")
        print()

    # Generate PDF summary of ALL flights immediately (new + already imported)
    all_scanned_flights = list(to_forward)  # Start with new flights

    # Add already-imported flights from processed data
    already_in_flighty = processed.get("confirmations", {})
    for conf, data in already_in_flighty.items():
        route_str = data.get("route", "")
        if " → " in route_str:
            route_parts = route_str.split(" → ")
            route_tuple = tuple(route_parts[:2])
            airports = route_parts[:2]
        else:
            route_tuple = None
            airports = []

        all_scanned_flights.append({
            "confirmation": conf,
            "flight_info": {
                "route": route_tuple,
                "airports": airports,
                "dates": [data.get("date")] if data.get("date") else [],
                "flight_numbers": [data.get("flight_number")] if data.get("flight_number") else []
            }
        })

    # Generate PDF for all flights
    if all_scanned_flights:
        raw_dir = SCRIPT_DIR / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        pdf_path = raw_dir / f"all_flights_{timestamp}.pdf"

        print()
        print("  Generating PDF summary of ALL flights...")
        result_path = generate_pdf_report(all_scanned_flights, pdf_path, "All Flights Summary")
        if result_path:
            print(f"  ✓ PDF saved to: {result_path}")
            print(f"    Contains {len(all_scanned_flights)} flights ({len(to_forward)} new, {len(already_in_flighty)} already imported)")
        else:
            print("  ✗ Failed to generate PDF")
        print()
    else:
        print()
        print("  No flights found to generate PDF.")
        print()

    # Display comprehensive scan results
    display_scan_results(to_forward, skipped, duplicates_merged, processed)

    # Forward to Flighty
    print()
    print("=" * 70)
    print("  STEP 4 OF 4: FORWARDING TO FLIGHTY")
    print("=" * 70)

    forward_flights(config, to_forward, processed, dry_run)

    print()
    print("=" * 70)
    print("  ALL DONE!")
    print("=" * 70)
    print()
    print("  Your flights should now appear in Flighty!")
    print("  Run this script again anytime to check for new flight emails.")
    print()


def show_help():
    """Show help message."""
    print(f"""
Flighty Email Forwarder v{VERSION}

Usage:
    python3 run.py              Run and forward flight emails
    python3 run.py --dry-run    Test without forwarding
    python3 run.py --days N     Search N days back (e.g., --days 180)
    python3 run.py --full-scan  Scan entire mailbox via POP3 (for AOL accounts)
    python3 run.py --use-scoring        Enable score-based pre-filtering
    python3 run.py --score-threshold N  Set minimum score (default 50, requires --use-scoring)
    python3 run.py --export-json FILE   Export scanned flights to JSON file
    python3 run.py --debug      Enable debug logging (shows extraction details)
    python3 run.py --setup      Run setup wizard
    python3 run.py --reset      Clear processed flights history
    python3 run.py --clean      Clean up corrupt/temp files and start fresh
    python3 run.py --help       Show this help

Examples:
    python3 run.py --days 365           Search 1 year of emails
    python3 run.py --days 180 --dry-run Test 6 months without sending
    python3 run.py --debug --dry-run    Debug extraction without sending
    python3 run.py --full-scan          Full historical scan (AOL POP3)
    python3 run.py --use-scoring --score-threshold 40  Filter low-confidence emails
    python3 run.py --export-json flights.json  Export flights to JSON for analysis

First time? Run: python3 run.py --setup

AOL users: If IMAP only shows recent emails, use --full-scan for complete history.

Had issues or crashes? Run: python3 run.py --clean
""")


def main():
    """Entry point."""
    args = sys.argv[1:]

    # Set up debug logging if requested
    if "--debug" in args:
        logging.basicConfig(
            level=logging.DEBUG,
            format='%(name)s: %(message)s'
        )
        print("Debug logging enabled\n")

    if "--setup" in args or "-s" in args:
        run_setup()
        return

    if "--reset" in args:
        if reset_processed_flights():
            print("Reset processed flights tracking. All flights will be re-scanned.")
        else:
            print("No tracking file found - already clean.")
        return

    if "--clean" in args:
        cleaned = clean_data_files()
        if cleaned:
            print(f"Removed: {', '.join(cleaned)}")
            print("\nCleanup complete! Run 'python3 run.py' to start fresh.")
        else:
            print("No files to clean up.")
        return

    if "--help" in args or "-h" in args:
        show_help()
        return

    # Auto-update before running - restart if updated
    if auto_update():
        import os
        # Use os.execv to replace this process entirely with the new version
        # This ensures no old code remains in memory
        os.execv(sys.executable, [sys.executable, str(SCRIPT_DIR / "run.py")] + args)

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
    full_scan = "--full-scan" in args or "--pop3" in args

    # Parse scoring options
    use_scoring = "--use-scoring" in args
    score_threshold = 50  # default
    for i, arg in enumerate(args):
        if arg == "--score-threshold" and i + 1 < len(args):
            try:
                score_threshold = int(args[i + 1])
            except ValueError:
                print(f"Error: --score-threshold requires a number")
                return

    # Parse export-json option
    export_json_path = None
    for i, arg in enumerate(args):
        if arg == "--export-json" and i + 1 < len(args):
            export_json_path = args[i + 1]

    run(dry_run=dry_run, days_override=days_override, full_scan=full_scan,
        use_scoring=use_scoring, score_threshold=score_threshold,
        export_json_path=export_json_path)


def wait_for_keypress():
    """Wait for user to press Enter before closing (for Windows users who double-click)."""
    import platform
    if platform.system() == "Windows":
        print()
        print("  ═" * 30)
        print()
        print("  Windows detected - window will stay open.")
        print()
        input("  Press Enter to close this window...")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
        print()
        print("  Cancelled by user (Ctrl+C).")
        print("  Your progress has been saved - run again to continue.")
        print()
    except Exception as e:
        print()
        print()
        print("  ╔════════════════════════════════════════════════════════════╗")
        print("  ║  UNEXPECTED ERROR                                          ║")
        print("  ╚════════════════════════════════════════════════════════════╝")
        print()
        print(f"  Error: {e}")
        print()
        print("  This is likely a bug. Please report it at:")
        print("  https://github.com/drewtwitchell/flighty_import/issues")
        print()
        print("  Technical details:")
        import traceback
        traceback.print_exc()
        print()
        print("  Your progress has been saved - run again to retry.")
        print()
    finally:
        wait_for_keypress()
