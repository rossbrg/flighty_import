# Flighty Email Forwarder

Automatically find flight booking confirmation emails in your inbox and forward them to [Flighty](https://flightyapp.com) for automatic trip tracking.

## Features

- Connects to any email provider (AOL, Gmail, Yahoo, Outlook, iCloud, or custom IMAP)
- Detects flight confirmations from 15+ airlines
- **Smart deduplication** - groups all emails by confirmation code, forwards only the latest
- **Change detection** - if a flight is modified, automatically re-imports the updated version
- Simple interactive setup - no coding required

## How It Works

The script runs in 4 phases:

1. **Scan** - Searches your email for flight confirmations, groups by confirmation code
2. **Select** - For each booking, picks the most recent email (handles flight changes)
3. **Review** - Shows a summary of what will be imported
4. **Forward** - Sends the selected emails to Flighty

## Supported Airlines

JetBlue, Delta, United, American Airlines, Southwest, Alaska Airlines, Spirit, Frontier, Hawaiian Airlines, Air Canada, British Airways, Lufthansa, Emirates, and generic flight confirmation emails.

## Requirements

- Python 3.6+
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
python3 setup.py
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
| `python3 run.py --setup` | Run the setup wizard |
| `python3 run.py --reset` | Clear processed flights history |
| `python3 run.py --help` | Show help message |

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

### Reset History

If you want to re-import all flights (e.g., starting fresh):

```bash
python3 run.py --reset
```

### Updating

The script automatically checks for updates every time you run it. No manual action needed!

If you need to update manually (e.g., for older versions), run:

```bash
./update.sh
```

## Sample Output

```
============================================================
  FLIGHTY EMAIL FORWARDER
============================================================

  Account:     yourname@aol.com
  Forward to:  track@my.flightyapp.com
  Looking back: 30 days

[Phase 1] Scanning for flight emails...

  Folder: INBOX
    Scanning 250 emails...50...100...150...200 Done! (6 flight emails)

  Found 3 unique confirmation(s)

[Phase 2] Selecting latest version of each flight...

============================================================
  FLIGHT IMPORT SUMMARY
============================================================

  Found 3 unique booking(s):
----------------------------------------------------------

  EJZOSU [NEW]
    Route: MCO -> BOS
    Date: Sun, Dec 07
    Flight: 652
    Emails: 3 found (using latest from 12/05 03:45PM)

  ENEIKV [UPDATE]
    Route: BOS -> JFK
    Date: Mon, Dec 08
    Flight: 123
    Emails: 2 found (using latest from 12/06 10:30AM)

  DJWNTF [SKIP - already imported]
    Route: LAX -> SFO
    Date: Fri, Dec 12
    Flight: 456
    Email: 11/27/2025 08:25PM

----------------------------------------------------------

  Summary:
    New flights to import: 2
    Already imported:      1

============================================================

[Phase 4] Forwarding to Flighty...

  Forwarding: EJZOSU
    MCO -> BOS | Flight 652 | Sun, Dec 07
    Status: Sent!

  Forwarding: ENEIKV
    BOS -> JFK | Flight 123 | Mon, Dec 08
    Status: Sent!

  Successfully forwarded: 2/2
```

## How Deduplication Works

1. **Groups by confirmation code** - All emails with the same booking reference are grouped together
2. **Selects the latest** - Picks the most recent email by timestamp (handles multiple confirmations, reminders, changes)
3. **Detects changes** - If you've already imported a flight but the details changed (new date, route, or flight number), it will re-import the updated version
4. **Content fingerprinting** - Creates a fingerprint of each booking to detect true duplicates vs. changes

## Automation (Optional)

To run automatically on a schedule, add a cron job:

```bash
# Edit crontab
crontab -e

# Run every hour
0 * * * * cd /path/to/flighty_import && python3 run.py >> forwarder.log 2>&1

# Run daily at 8am
0 8 * * * cd /path/to/flighty_import && python3 run.py >> forwarder.log 2>&1
```

## Files

| File | Description |
|------|-------------|
| `setup.py` | Interactive setup wizard |
| `run.py` | Main script to find and forward emails |
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
- Try increasing the "days back" setting in setup
- Check that you're searching the correct folder
- Run with `--dry-run` to see what's being detected

**Wrong route or date showing**
- The script extracts airports from patterns like "Orlando (MCO)" or "MCO -> BOS"
- Dates are extracted from patterns like "Sun, Dec 07" or "December 7, 2025"
- The actual forwarded email contains all original details - Flighty will parse it correctly

**Want to re-import everything**
- Run `python3 run.py --reset` to clear history
- Then run `python3 run.py` to import all flights fresh

## License

MIT License - feel free to modify and share.
