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

# Constants
SCRIPT_DIR = Path(__file__).parent
VERSION = __version__
GITHUB_REPO = "drewtwitchell/flighty_import"
UPDATE_FILES = ["run.py", "flighty/__init__.py", "flighty/airports.py",
                "flighty/airlines.py", "flighty/config.py", "flighty/parser.py",
                "flighty/email_handler.py", "flighty/scanner.py", "flighty/setup.py",
                "airport_codes.txt"]


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


def display_previously_imported(processed):
    """Display summary of previously imported flights."""
    confirmations = processed.get("confirmations", {})
    if not confirmations:
        return

    print()
    print("=" * 60)
    print("  PREVIOUSLY IMPORTED FLIGHTS")
    print("=" * 60)
    print()
    print(f"  Found {len(confirmations)} flights already in Flighty:")
    print()

    # Sort by import date, newest first
    sorted_confs = sorted(
        confirmations.items(),
        key=lambda x: x[1].get("imported_at", ""),
        reverse=True
    )

    # Show recent ones
    for conf, data in sorted_confs[:15]:
        route = data.get("route", "")
        date = data.get("date", "")

        display = f"  {conf}"
        if route:
            display += f"  {route}"
        if date:
            display += f"  {date}"
        print(display)

    if len(sorted_confs) > 15:
        print(f"  ... and {len(sorted_confs) - 15} more")
    print()


def display_new_flights(to_forward):
    """Display flights that will be forwarded."""
    if not to_forward:
        return

    print()
    print("=" * 60)
    print("  NEW FLIGHTS TO IMPORT")
    print("=" * 60)
    print()
    print(f"  Found {len(to_forward)} new flights to send to Flighty:")
    print()

    for flight in to_forward[:20]:
        conf = flight.get("confirmation", "------")
        flight_info = flight.get("flight_info", {})
        airports = flight_info.get("airports", [])
        dates = flight_info.get("dates", [])

        valid_airports = [code for code in airports if code in VALID_AIRPORT_CODES]
        route = " → ".join(valid_airports[:2]) if valid_airports else ""
        date = dates[0] if dates else ""

        display = f"  {conf}"
        if route:
            display += f"  {route}"
        if date:
            display += f"  {date}"
        print(display)

    if len(to_forward) > 20:
        print(f"  ... and {len(to_forward) - 20} more")
    print()


def forward_flights(config, to_forward, processed, dry_run):
    """Forward flights to Flighty."""
    if not to_forward:
        print()
        print("=" * 60)
        print("  NO NEW FLIGHTS TO IMPORT")
        print("=" * 60)
        print()
        print("  All your flight emails are already in Flighty!")
        print("  Run this again anytime to check for new flight emails.")
        print()
        return

    print()
    print("=" * 60)
    print("  FORWARDING TO FLIGHTY")
    print("=" * 60)
    print()

    if dry_run:
        print("  DRY RUN MODE - No emails will actually be sent")
        print()
        for i, flight in enumerate(to_forward):
            conf = flight.get("confirmation", "------")
            flight_info = flight.get("flight_info", {})
            airports = flight_info.get("airports", [])
            valid_airports = [code for code in airports if code in VALID_AIRPORT_CODES]
            route = " → ".join(valid_airports[:2]) if valid_airports else "Unknown"

            print(f"  [{i+1}/{len(to_forward)}] {conf} {route} - Would Send")
        print()
        print("  Dry run complete. Run without --dry-run to actually forward.")
        return

    print("  IMPORTANT - PLEASE BE PATIENT:")
    print("  - Email providers (AOL, Yahoo, Gmail, etc.) limit sending speed")
    print("  - If we send too fast, they temporarily block us")
    print("  - When blocked, we wait and automatically retry (up to 5 minutes)")
    print("  - Large batches may take 10-30+ minutes - this is normal!")
    print()
    print("  Do not close this window - your progress is saved after each send.")
    print()
    print("-" * 60)

    sent = 0
    failed = 0

    for i, flight in enumerate(to_forward):
        conf = flight.get("confirmation", "------")
        flight_info = flight.get("flight_info", {})
        airports = flight_info.get("airports", [])
        valid_airports = [code for code in airports if code in VALID_AIRPORT_CODES]
        route = " → ".join(valid_airports[:2]) if valid_airports else "Unknown"

        print(f"  [{i+1}/{len(to_forward)}] {conf} {route}... ", end="", flush=True)

        success = forward_email(
            config,
            flight["msg"],
            flight["from_addr"],
            flight["subject"],
            flight_info=flight_info
        )

        if success:
            print("Sent!")
            sent += 1

            # Save progress immediately
            conf_key = conf if conf else f"unknown_{flight['content_hash']}"
            processed["confirmations"][conf_key] = {
                "imported_at": datetime.now().isoformat(),
                "fingerprint": flight.get("fingerprint", ""),
                "route": route,
                "date": flight_info.get("dates", [""])[0] if flight_info.get("dates") else ""
            }
            processed["content_hashes"].add(flight["content_hash"])
            save_processed_flights(processed)
            print("        (Progress saved)")
        else:
            print("Failed")
            failed += 1

    print()
    print("-" * 60)
    print()
    print("  FORWARDING COMPLETE")
    print()
    print(f"  Successfully sent: {sent} of {len(to_forward)}")
    if failed > 0:
        print(f"  Failed: {failed} (run again to retry)")
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

    # Select latest emails per confirmation
    to_forward, skipped = select_latest_flights(all_flights, processed)

    # Display results
    display_previously_imported(processed)
    display_new_flights(to_forward)

    # Forward to Flighty
    print()
    print("=" * 60)
    print("  STEP 4 OF 4: FORWARDING TO FLIGHTY")
    print("=" * 60)

    forward_flights(config, to_forward, processed, dry_run)

    print()
    print("=" * 60)
    print("  ALL DONE!")
    print("=" * 60)
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
        print("\n" + "-" * 40)
        input("Press Enter to close this window...")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nCancelled by user.")
    except Exception as e:
        print(f"\n\nUnexpected error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        wait_for_keypress()
