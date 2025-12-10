# Flighty Email Forwarder

Automatically find flight booking confirmation emails in your inbox and forward them to [Flighty](https://flightyapp.com) for automatic trip tracking.

## Features

- **Multi-platform** - Works on Mac, Windows, and Linux
- **No dependencies** - Uses only Python standard library (no pip install needed)
- **Auto-updates** - Automatically downloads the latest version when you run it
- Connects to any email provider (AOL, Gmail, Yahoo, Outlook, iCloud, or custom IMAP)
- Detects flight confirmations from 15+ airlines and booking sites
- **Smart deduplication** - groups all emails by confirmation code, forwards only the latest
- **Change detection** - if a flight is modified, automatically re-imports the updated version
- **Crash protection** - saves progress after each email, recovers from errors automatically
- **9,800+ airport codes** - full IATA database with common word filtering
- Shows flight details including route, date with year, time, and flight number
- Simple interactive setup - no coding required

## How It Works

The script runs in 4 phases:

1. **Update Check** - Checks GitHub for new version and auto-updates if available
2. **Connect** - Connects to your email via IMAP
3. **Scan** - Searches your email for flight confirmations
4. **Forward** - Sends the selected emails to Flighty

## Supported Airlines & Booking Sites

**Airlines:** JetBlue, Delta, United, American Airlines, Southwest, Alaska Airlines, Spirit, Frontier, Hawaiian Airlines, Air Canada, British Airways, Lufthansa, Emirates, Air France, KLM, Qantas, and 20+ more international carriers.

**Booking Sites:** Expedia, Kayak, Priceline, Orbitz, Google Travel, Hopper, and more.

## Requirements

- Python 3.8+
- An email account with IMAP access
- An App Password from your email provider (not your regular password)

## Installation

```bash
git clone https://github.com/drewtwitchell/flighty_import.git
cd flighty_import
```

No additional dependencies required - uses only Python standard library.

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
| `python3 run.py --setup` | Run the setup wizard |
| `python3 run.py --reset` | Clear processed flights history |
| `python3 run.py --clean` | Clean up corrupt/temp files and start fresh |
| `python3 run.py --help` | Show help message |

You can combine options: `python3 run.py --days 180 --dry-run`

### Test Mode (Dry Run)

See what emails would be forwarded without actually sending anything:

```bash
python3 run.py --dry-run
```

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

## Project Structure

```
flighty_import/
├── run.py                  # Main entry point
├── flighty/                # Python package
│   ├── __init__.py         # Package version
│   ├── airports.py         # Airport codes and display
│   ├── airlines.py         # Airline detection patterns
│   ├── config.py           # Configuration management
│   ├── parser.py           # Flight info extraction
│   ├── email_handler.py    # IMAP/SMTP handling
│   ├── scanner.py          # Email scanning
│   └── setup.py            # Setup wizard
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
- Sensitive files (`config.json`, `processed_flights.json`) are excluded from git

## Troubleshooting

**Login failed**
- Make sure you're using an App Password, not your regular password
- Check that IMAP is enabled in your email settings

**No emails found**
- Try increasing the "days back" setting: `python3 run.py --days 365`
- Check that you're searching the correct folder
- Run with `--dry-run` to see what's being detected

**Want to re-import everything**
- Run `python3 run.py --reset` to clear history
- Then run `python3 run.py` to import all flights fresh

**Script crashed or had errors**
- Run `python3 run.py --clean` to remove any corrupt data files
- Then run `python3 run.py` to start fresh
- The script saves progress after each successful forward, so you won't lose data

## Automation (Optional)

To run automatically on a schedule, add a cron job:

```bash
# Edit crontab
crontab -e

# Run daily at 8am
0 8 * * * cd /path/to/flighty_import && python3 run.py >> forwarder.log 2>&1
```

## License

MIT License - feel free to modify and share.
