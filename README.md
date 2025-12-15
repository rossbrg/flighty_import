# Flighty Email Forwarder

Automatically find flight booking confirmation emails in your inbox and forward them to [Flighty](https://flightyapp.com) for automatic trip tracking.

## Features

- **Multi-platform** - Works on Mac, Windows, and Linux
- **Auto-installs dependencies** - Automatically installs `python-dateutil` if needed (one-time)
- **Auto-updates** - Automatically downloads the latest version when you run it
- Connects to any email provider (AOL, Gmail, Yahoo, Outlook, iCloud, or custom IMAP)
- Detects flight confirmations from 75+ airlines, booking sites, and travel services
- **Optimized searching** - Uses combined IMAP queries for faster scanning
- **Smart deduplication** - Groups all emails by confirmation code, forwards only the latest
- **Same-day updates** - If you get multiple emails for the same flight, uses the most recent
- **Change detection** - If a flight is modified, automatically re-imports the updated version
- **Crash protection** - Saves progress after each email, recovers from errors automatically
- **Rate limit handling** - Automatically retries when email providers throttle sending
- **9,800+ airport codes** - Full IATA database with common word filtering
- **City name recognition** - Understands "Boston to Las Vegas" and converts to airport codes
- **Marketing email filtering** - Automatically ignores promotional emails from airlines
- **Smart flight merging** - Groups related emails by confirmation code or route+date
- **Original email forwarding** - Forwards the actual airline emails to Flighty (no modifications)
- **PDF summary generation** - Creates a PDF report of all flights grouped by month in `raw/` directory

## How It Works

The script runs in 4 phases:

1. **Update Check** - Checks GitHub for new version and auto-updates if available
2. **Connect** - Connects to your email via IMAP
3. **Scan** - Searches your email for flight confirmations (optimized batch queries)
4. **Forward** - Shows all found flights, then sends selected emails to Flighty

### What You'll See

```
============================================================
  SCAN RESULTS
============================================================

  ┌─ NEW FLIGHTS TO FORWARD: 3 ─────────────────────────────
  │
  │  NEW:
  │    ABC123   AA100      JFK → LAX        2025-03-15
  │    DEF456   UA200      SFO → ORD        2025-04-01
  │
  │  UPDATES (flight details changed):
  │    GHI789   DL300      ATL → MIA        2025-05-20  [UPDATE]
  │
  └───────────────────────────────────────────────────────────

  ┌─ ALREADY IN FLIGHTY: 12 ────────────────────────────────
  │    XYZ999   LAX → JFK        2025-02-10
  │    ...
  └───────────────────────────────────────────────────────────

  SUMMARY:
    • New flights to forward:    3
    • Already in Flighty:        12
    • Duplicate emails merged:   4
      (Multiple emails for same confirmation - using latest)
```

## Supported Airlines & Booking Sites

**US Airlines:** JetBlue, Delta, United, American Airlines, Southwest, Alaska Airlines, Spirit, Frontier, Hawaiian Airlines, Sun Country, Allegiant, Breeze Airways

**European Airlines:** British Airways, Lufthansa, Air France, KLM, Virgin Atlantic, Icelandair, Norwegian, Ryanair, easyJet, Vueling, Iberia, Finnair, SAS, Swiss, Austrian, TAP, Aegean, LOT, Brussels Airlines

**Middle East/Africa:** Emirates, Etihad, Qatar Airways, Turkish Airlines, Saudia, Royal Air Maroc, Ethiopian Airlines, Kenya Airways, EgyptAir

**Asia/Pacific:** Qantas, Singapore Airlines, Cathay Pacific, JAL, ANA, Korean Air, Asiana, Thai Airways, Vietnam Airlines, Air China, China Eastern, China Southern, Hainan Airlines, Air India, Malaysia Airlines, Garuda, AirAsia, Scoot, Jetstar, Philippine Airlines

**Americas:** Air Canada, WestJet, Avianca, LATAM, Aeromexico, Copa, Azul, GOL, Volaris, VivaAerobus

**Booking Sites:** Expedia, Kayak, Priceline, Orbitz, Travelocity, CheapOair, Hopper, Google Travel, Booking.com, Trip.com, Skyscanner, Momondo, Kiwi.com, FlightAware, StudentUniverse, Cheapflights, FareCompare, Airfarewatchdog

**Corporate Travel & Expense:** Concur, Egencia, TripActions, Navan, Brex, Ramp, Divvy, Airbase, TravelBank, Deem, TravelPerk, Lola, Upside, Spotnana, FlightFox

**Credit Card Travel Portals:** Chase, American Express, Capital One, Citi, Barclays, Wells Fargo, US Bank

**Travel Agencies:** Flight Centre, Carlson Wagonlit, BCD Travel, World Travel Inc, Travel Leaders, Frosch

## Requirements

- Python 3.8+
- An email account with IMAP access
- An App Password from your email provider (not your regular password)

## Installation

```bash
git clone https://github.com/drewtwitchell/flighty_import.git
cd flighty_import
```

That's it! The script will auto-install `python-dateutil` on first run if needed.

## Setup

Run the interactive setup wizard:

```bash
python3 run.py --setup
```

The wizard will ask you for:
1. **Email provider** - Select from AOL, Gmail, Yahoo, Outlook, iCloud, or enter custom IMAP settings
2. **Email address** - Your full email address
3. **App Password** - A special password for third-party apps (see below)
4. **Flighty email** - Where to forward emails (default: `track@my.flightyapp.com`)
5. **Folders to search** - Which email folders to scan (default: INBOX)
6. **Time range** - How far back to search for emails

### Getting an App Password

Most email providers require an "App Password" instead of your regular password:

| Provider | How to get App Password |
|----------|------------------------|
| AOL | [AOL Account Security](https://login.aol.com/account/security) - Generate app password |
| Gmail | [Google App Passwords](https://myaccount.google.com/apppasswords) (requires 2FA) |
| Yahoo | [Yahoo Account Security](https://login.yahoo.com/account/security) - Generate app password |
| Outlook | May work with regular password, or use [Microsoft Account](https://account.microsoft.com/security) |
| iCloud | [Apple ID](https://appleid.apple.com/account/manage) - App-Specific Passwords |

## Usage

### Quick Reference

| Command | Description |
|---------|-------------|
| `python3 run.py` | Run and forward flight emails (auto-updates first) |
| `python3 run.py --dry-run` | Test without forwarding (see what would be sent) |
| `python3 run.py --days N` | Search N days back (e.g., `--days 365` for 1 year) |
| `python3 run.py --full-scan` | Full historical scan via POP3 (AOL accounts only) |
| `python3 run.py --setup` | Run the setup wizard |
| `python3 run.py --reset` | Clear processed flights history |
| `python3 run.py --clean` | Clean up corrupt/temp files and start fresh |
| `python3 run.py --help` | Show help message |

You can combine options: `python3 run.py --days 180 --dry-run`

### Full Historical Scan (AOL)

AOL limits IMAP to approximately 10,000 recent emails. If you have an AOL account with years of flight history, use the POP3 full scan:

```bash
python3 run.py --full-scan
```

Or run the POP3 scanner directly for more control:

```bash
python3 pop3_full_scan.py              # Scan all messages
python3 pop3_full_scan.py --resume     # Resume from last position
python3 pop3_full_scan.py --batch 5000 # Process 5000 messages then stop
python3 pop3_full_scan.py --pdf        # Generate PDF from saved results
python3 pop3_full_scan.py --status     # Show current progress
python3 pop3_full_scan.py --clear      # Clear saved progress
```

**Features:**
- Scans entire mailbox history (10+ years)
- Saves progress every 500 messages - can stop and resume anytime
- Auto-reconnects if connection drops
- Generates PDF report grouped by year/month

### Test Mode (Dry Run)

See what emails would be forwarded without actually sending anything:

```bash
python3 run.py --dry-run
```

This shows you:
- All emails that would be forwarded (with From, Subject, and flight details)
- Flights already in Flighty
- Duplicate emails that were merged
- A PDF summary is also generated in the `raw/` directory

### Normal Mode

Find and forward flight emails to Flighty:

```bash
python3 run.py
```

### Searching More Days

To search further back than your configured setting, use `--days`:

```bash
python3 run.py --days 365    # Search 1 year
python3 run.py --days 180    # Search 6 months
```

**Performance Note:** Searching 1 year typically takes:
- 2-5 minutes if your email server supports batch queries (Gmail, Outlook, iCloud)
- 10-15 minutes if fallback to individual searches is needed (AOL, Yahoo)

## Upgrading

The script auto-updates when you run it! It will:
1. Check GitHub for a newer version
2. Download all updated files
3. Restart with the new version

If you have an older version, just run `python3 run.py` and it will upgrade automatically.

**Manual upgrade:**
```bash
cd flighty_import
git pull
```

## Project Structure

```
flighty_import/
├── run.py                  # Main entry point
├── pop3_full_scan.py       # POP3 full mailbox scanner (for AOL)
├── flighty/                # Python package
│   ├── __init__.py         # Package version
│   ├── airports.py         # Airport codes and display
│   ├── airlines.py         # Airline detection patterns
│   ├── config.py           # Configuration management
│   ├── deps.py             # Dependency auto-installation
│   ├── parser.py           # Flight info extraction (uses dateutil)
│   ├── email_handler.py    # IMAP/SMTP handling with retry logic
│   ├── scanner.py          # Optimized email scanning
│   ├── pdf_report.py       # PDF summary generation
│   └── setup.py            # Setup wizard
├── raw/                    # PDF summaries saved here (auto-created)
├── airport_codes.txt       # 9,800+ IATA airport codes
├── pyproject.toml          # Python packaging config
├── VERSION                 # Version number
└── README.md               # This file
```

## Files (User Data)

| File | Description |
|------|-------------|
| `config.json` | Your configuration (created by setup, not tracked in git) |
| `processed_flights.json` | Tracks imported flights (not tracked in git) |

## Privacy & Security

- Your credentials are stored locally in `config.json`
- No data is sent anywhere except to your email provider and Flighty
- No emails are stored locally - only scanned and forwarded
- Sensitive files (`config.json`, `processed_flights.json`) are excluded from git

## Troubleshooting

**Login failed**
- Make sure you're using an App Password, not your regular password
- Check that IMAP is enabled in your email settings

**No emails found**
- Try increasing the "days back" setting: `python3 run.py --days 365`
- Check that you're searching the correct folder
- Run with `--dry-run` to see what's being detected

**Slow scanning**
- Some email servers (AOL, Yahoo) don't support batch queries
- The script will automatically fall back to individual searches
- You'll see: "Note: Your email server required individual searches"

**Rate limited / blocked**
- Email providers limit how fast you can send
- The script automatically waits and retries (up to 5 minutes)
- Large batches (50+ flights) may take 10-30 minutes - this is normal

**Want to re-import everything**
- Run `python3 run.py --reset` to clear history
- Then run `python3 run.py` to import all flights fresh

**Script crashed or had errors**
- Run `python3 run.py --clean` to remove any corrupt data files
- Then run `python3 run.py` to start fresh
- The script saves progress after each successful forward, so you won't lose data

**Dependency installation failed**
- The script tries to auto-install `python-dateutil`
- If it fails, install manually: `pip install python-dateutil`

## Automation (Optional)

To run automatically on a schedule, add a cron job:

```bash
# Edit crontab
crontab -e

# Run daily at 8am
0 8 * * * cd /path/to/flighty_import && python3 run.py >> forwarder.log 2>&1
```

## Version History

- **v2.53.0** - POP3 full mailbox scanner for AOL accounts (bypasses IMAP 10k message limit), automatic IMAP limitation detection, `--full-scan` option, resumable scanning with progress saving, PDF reports grouped by year/month/day
- **v2.51.0** - Auto-install reportlab for PDF generation, generate comprehensive PDF of all flights (new + already imported) upfront, improved error handling for failed sends
- **v2.50.0** - Original email forwarding: sends actual airline emails to Flighty (no modifications), adds PDF summary generation grouped by month
- **v2.49.0** - Clean email generation: creates simple emails with just flight data instead of forwarding messy airline emails
- **v2.48.0** - Multi-airline support: Added Delta email format, Cape Air codeshare patterns, removed dead code
- **v2.27.1** - Fix datetime comparison error in sorting
- **v2.27.0** - Refactor flight processing with proper verification
- **v2.26.0** - Fix route merging from unrelated emails
- **v2.25.0** - Require verification for flights without confirmation codes
- **v2.24.0** - Exclude false positive airport codes
- **v2.19.0** - Improved context-based false positive detection for airport codes (APR, LLC, etc.)
- **v2.18.0** - Fix email body extraction for non-UTF-8 encodings
- **v2.16.0** - Added --debug flag for detailed extraction logging
- **v2.14.0** - Major fix: reject English words as confirmation codes (SEARCH, HOTELS, etc.)
- **v2.10.0** - Marketing email filtering (ignores promotional emails from airlines/travel sites)
- **v2.9.0** - City name recognition ("Boston to Las Vegas" → BOS, LAS), smart flight merging
- **v2.8.0** - Expanded to 75+ airlines/services (Brex, Ramp, regional airlines, travel agencies)
- **v2.5.0** - Optimized IMAP searching (92% fewer queries), automatic fallback for all servers
- **v2.2.0** - Added python-dateutil for robust date parsing, auto-installs dependencies
- **v2.0.0** - Major rewrite with improved parsing and flight detection

## License

MIT License - feel free to modify and share.
