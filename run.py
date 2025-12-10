#!/usr/bin/env python3
"""
Flighty Email Forwarder - Main Runner

Scans your email for flight confirmations and forwards them to Flighty.

Usage:
    python3 run.py              # Run normally
    python3 run.py --dry-run    # Test without forwarding
    python3 run.py --setup      # Run setup wizard
    python3 run.py --days N     # Search N days back
    python3 run.py --help       # Show help
"""

import sys
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

        if latest_version == VERSION:
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
            except Exception:
                pass

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
    airports = flight_info.get("airports", [])
    dates = flight_info.get("dates", [])
    flights = flight_info.get("flights", [])

    valid_airports = [code for code in airports if code in VALID_AIRPORT_CODES]
    route = " → ".join(valid_airports[:2]) if valid_airports else ""
    date = dates[0] if dates else ""
    flight_num = flights[0] if flights else ""

    # Build display line
    parts = [f"  {conf:<8}"]
    if flight_num:
        parts.append(f"{flight_num:<10}")
    if route:
        parts.append(f"{route:<15}")
    if date:
        parts.append(f"{date}")

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
                conf = flight.get("confirmation", "------")
                flight_info = flight.get("flight_info", {})
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
                conf = flight.get("confirmation", "------")
                flight_info = flight.get("flight_info", {})
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
            conf = item.get("confirmation", "------")
            reason = item.get("reason", "")
            flight_info = item.get("flight_info", {})
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
        print("  │  Each email will be forwarded with an injected header like:")
        print("  │")
        print("  │    ══════════════════════════════════════════")
        print("  │    FLIGHT INFORMATION")
        print("  │    ══════════════════════════════════════════")
        print("  │    Route: JFK → LAX")
        print("  │    Flight Number: AA123")
        print("  │    Departure Date: 2025-03-15")
        print("  │    ══════════════════════════════════════════")
        print("  │")
        print("  │  This helps Flighty correctly parse the flight details.")
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

        for i, flight in enumerate(to_forward):
            conf = flight.get("confirmation", "------")
            flight_info = flight.get("flight_info", {})
            airports = flight_info.get("airports", [])
            dates = flight_info.get("dates", [])
            flights_list = flight_info.get("flights", [])
            email_count = flight.get("email_count", 1)
            email_date = flight.get("email_date")
            valid_airports = [code for code in airports if code in VALID_AIRPORT_CODES]

            route = " → ".join(valid_airports[:2]) if valid_airports else ""
            date = dates[0] if dates else ""
            flight_num = flights_list[0] if flights_list else ""

            print(f"  ┌─ Flight {i+1} of {len(to_forward)} ─────────────────────────────────────")
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
                print(f"  │  ⚡ Multiple emails ({email_count}) - using most recent")
            if flight.get("is_update"):
                print(f"  │  ⚠️  UPDATE: Flight details changed since last import")
            print(f"  └────────────────────────────────────────────────────────────")
            print()

        print("  ═" * 32)
        print()
        print("  ✓ Dry run complete!")
        print()
        print("  What happens next if you run without --dry-run:")
        print("    1. Each email above will be forwarded to Flighty")
        print("    2. A header with route/date/flight info will be added")
        print("    3. Progress is saved after each successful send")
        print("    4. If rate-limited, we'll wait and retry automatically")
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
        conf = flight.get("confirmation", "------")
        flight_info = flight.get("flight_info", {})
        airports = flight_info.get("airports", [])
        dates = flight_info.get("dates", [])
        flights_list = flight_info.get("flights", [])
        valid_airports = [code for code in airports if code in VALID_AIRPORT_CODES]

        route = " → ".join(valid_airports[:2]) if valid_airports else ""
        date = dates[0] if dates else ""
        flight_num = flights_list[0] if flights_list else ""

        # Show what we're sending
        print()
        print(f"  [{i+1}/{len(to_forward)}] Forwarding {conf}...")
        if route or flight_num or date:
            print(f"        Header: ", end="")
            header_parts = []
            if route:
                header_parts.append(route)
            if flight_num:
                header_parts.append(flight_num)
            if date:
                header_parts.append(date)
            print(" | ".join(header_parts))

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
                "date": date
            }
            processed["content_hashes"].add(flight["content_hash"])
            save_processed_flights(processed)
        else:
            print(f"        ✗ Failed to send")
            failed += 1

    print()
    print("  ─" * 35)
    print()
    print("  FORWARDING COMPLETE")
    print()
    print(f"    ✓ Successfully sent: {sent}")
    if failed > 0:
        print(f"    ✗ Failed: {failed} (run again to retry)")
    print()


def run(dry_run=False, days_override=None):
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
        flights, skipped = scan_for_flights(mail, config, folder, processed)
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
    python3 run.py --setup      Run setup wizard
    python3 run.py --reset      Clear processed flights history
    python3 run.py --clean      Clean up corrupt/temp files and start fresh
    python3 run.py --help       Show this help

Examples:
    python3 run.py --days 365           Search 1 year of emails
    python3 run.py --days 180 --dry-run Test 6 months without sending

First time? Run: python3 run.py --setup

Had issues or crashes? Run: python3 run.py --clean
""")


def main():
    """Entry point."""
    args = sys.argv[1:]

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
