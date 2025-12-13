"""
Email handling: IMAP connection, SMTP forwarding, email parsing.
"""

import imaplib
import logging
import smtplib
import email
import email.header
import re
import time
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import parsedate_to_datetime

logger = logging.getLogger(__name__)

from .airports import VALID_AIRPORT_CODES
from .parser import extract_flight_info
from .airlines import is_flight_email


def decode_header_value(value):
    """Decode an email header value (handles encoded headers).

    Args:
        value: Raw header value

    Returns:
        Decoded string
    """
    if not value:
        return ""
    try:
        decoded_parts = email.header.decode_header(value)
        return ''.join(
            part.decode(charset or 'utf-8', errors='replace') if isinstance(part, bytes) else part
            for part, charset in decoded_parts
        )
    except Exception:
        return str(value)


def _decode_payload(part):
    """Decode an email part's payload with proper charset handling.

    Args:
        part: email.message.Message part

    Returns:
        Decoded string or empty string on failure
    """
    try:
        payload = part.get_payload(decode=True)
        if not payload:
            return ""

        # Try to get the charset from the email part
        charset = part.get_content_charset()

        # Common charset aliases and fallbacks
        charset_attempts = []
        if charset:
            charset_attempts.append(charset.lower())
            # Handle common aliases
            if charset.lower() in ('iso-8859-1', 'latin-1', 'latin1'):
                charset_attempts.extend(['iso-8859-1', 'cp1252', 'windows-1252'])
            elif charset.lower() in ('windows-1252', 'cp1252'):
                charset_attempts.extend(['cp1252', 'iso-8859-1'])

        # Always try these common encodings as fallbacks
        charset_attempts.extend(['utf-8', 'iso-8859-1', 'cp1252', 'ascii'])

        # Remove duplicates while preserving order
        seen = set()
        unique_charsets = []
        for c in charset_attempts:
            if c not in seen:
                seen.add(c)
                unique_charsets.append(c)

        # Try each charset
        for cs in unique_charsets:
            try:
                return payload.decode(cs)
            except (UnicodeDecodeError, LookupError):
                continue

        # Last resort: decode with replacement characters
        return payload.decode('utf-8', errors='replace')

    except Exception:
        return ""


def get_email_body(msg):
    """Extract the email body (plain text and HTML).

    Args:
        msg: email.message.Message object

    Returns:
        Tuple of (plain_text_body, html_body)
    """
    body = ""
    html_body = ""

    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition", ""))

            # Skip attachments
            if "attachment" in content_disposition:
                continue

            # Skip multipart containers - they don't have payload we can decode
            if content_type.startswith("multipart/"):
                continue

            text = _decode_payload(part)
            if text:
                if content_type == "text/plain":
                    # Prefer larger plain text (some emails have multiple text/plain parts)
                    if len(text) > len(body):
                        body = text
                elif content_type == "text/html":
                    # Prefer larger HTML (some emails have multiple text/html parts)
                    if len(text) > len(html_body):
                        html_body = text
    else:
        text = _decode_payload(msg)
        if text:
            if msg.get_content_type() == "text/plain":
                body = text
            else:
                html_body = text

    return body, html_body


def parse_email_date(date_str):
    """Parse email date header into datetime.

    Args:
        date_str: Date string from email header

    Returns:
        datetime object or datetime.min if parsing fails
    """
    try:
        return parsedate_to_datetime(date_str)
    except Exception:
        return datetime.min


def connect_imap(config):
    """Connect to the IMAP server.

    Args:
        config: Config dict with imap_server, imap_port, email, password

    Returns:
        IMAP4_SSL connection or None on failure
    """
    import socket

    try:
        # Set socket timeout for connection
        socket.setdefaulttimeout(60)
        mail = imaplib.IMAP4_SSL(config['imap_server'], config['imap_port'])
        mail.login(config['email'], config['password'])
        # Reset to no timeout for long operations
        socket.setdefaulttimeout(None)
        return mail

    except imaplib.IMAP4.error as e:
        error_str = str(e).lower()
        print()
        print("  ╔════════════════════════════════════════════════════════════╗")
        print("  ║  LOGIN FAILED                                              ║")
        print("  ╚════════════════════════════════════════════════════════════╝")
        print()
        print(f"  Error: {e}")
        print()

        if 'invalid' in error_str or 'authentication' in error_str or 'credential' in error_str:
            print("  This usually means:")
            print("    • You're using your regular password instead of an App Password")
            print("    • The App Password was entered incorrectly")
            print()
            print("  To fix: Run 'python3 run.py --setup' and enter a new App Password")
        elif 'disabled' in error_str or 'imap' in error_str:
            print("  This usually means IMAP access is disabled in your email settings.")
            print("  Enable IMAP access in your email provider's settings.")
        else:
            print("  Make sure you're using an App Password, not your regular password.")
            print("  Run 'python3 run.py --setup' to reconfigure.")

        print()
        return None

    except socket.timeout:
        print()
        print("  ╔════════════════════════════════════════════════════════════╗")
        print("  ║  CONNECTION TIMED OUT                                      ║")
        print("  ╚════════════════════════════════════════════════════════════╝")
        print()
        print("  Could not connect to the email server within 60 seconds.")
        print()
        print("  This could mean:")
        print("    • Your internet connection is slow or unstable")
        print("    • The email server is temporarily unavailable")
        print("    • A firewall is blocking the connection")
        print()
        print("  Try again in a few minutes.")
        print()
        return None

    except socket.gaierror as e:
        print()
        print("  ╔════════════════════════════════════════════════════════════╗")
        print("  ║  SERVER NOT FOUND                                          ║")
        print("  ╚════════════════════════════════════════════════════════════╝")
        print()
        print(f"  Could not find server: {config['imap_server']}")
        print()
        print("  This could mean:")
        print("    • No internet connection")
        print("    • The server address is incorrect")
        print()
        print("  Run 'python3 run.py --setup' to check your settings.")
        print()
        return None

    except ConnectionRefusedError:
        print()
        print("  ╔════════════════════════════════════════════════════════════╗")
        print("  ║  CONNECTION REFUSED                                        ║")
        print("  ╚════════════════════════════════════════════════════════════╝")
        print()
        print(f"  The server {config['imap_server']} refused the connection.")
        print()
        print("  This could mean:")
        print("    • The port number is incorrect (should be 993 for IMAP SSL)")
        print("    • The server doesn't allow IMAP connections")
        print()
        print("  Run 'python3 run.py --setup' to check your settings.")
        print()
        return None

    except Exception as e:
        print()
        print("  ╔════════════════════════════════════════════════════════════╗")
        print("  ║  CONNECTION ERROR                                          ║")
        print("  ╚════════════════════════════════════════════════════════════╝")
        print()
        print(f"  Unexpected error: {e}")
        print()
        print("  Try running 'python3 run.py --setup' to reconfigure,")
        print("  or try again in a few minutes.")
        print()
        return None


def forward_email(config, msg, from_addr, subject, flight_info=None):
    """Send a clean flight confirmation email to Flighty.

    Creates a new, simple email with just the flight data instead of
    forwarding the messy original airline email.

    Args:
        config: Config dict with smtp settings and flighty_email
        msg: Original email message object (kept for compatibility, not used)
        from_addr: Original sender address (kept for compatibility, not used)
        subject: Original subject line (kept for compatibility, not used)
        flight_info: Extracted flight info dict

    Returns:
        True if sent successfully, False otherwise
    """
    from .airports import get_airport_display

    # Extract flight details
    segments = flight_info.get("segments", []) if flight_info else []
    confirmation = flight_info.get("confirmation", "") if flight_info else ""

    # Determine airline from flight number
    airline = "Unknown Airline"
    if segments and segments[0].get("flight_number"):
        fn = segments[0]["flight_number"]
        if fn.startswith("B6"):
            airline = "JetBlue"
        elif fn.startswith("DL"):
            airline = "Delta"
        elif fn.startswith("AA"):
            airline = "American Airlines"
        elif fn.startswith("UA"):
            airline = "United Airlines"
        elif fn.startswith("WN"):
            airline = "Southwest Airlines"
        elif fn.startswith("AS"):
            airline = "Alaska Airlines"
        elif fn.startswith("NK"):
            airline = "Spirit Airlines"
        elif fn.startswith("F9"):
            airline = "Frontier Airlines"

    # Create subject line
    if confirmation:
        email_subject = f"Flight Confirmation {confirmation}"
    elif segments:
        origin = segments[0].get("origin", "")
        dest = segments[0].get("destination", "")
        email_subject = f"Flight Confirmation {origin} to {dest}"
    else:
        email_subject = "Flight Confirmation"

    # Build plain text body
    text_body = ""

    if confirmation:
        text_body += f"Confirmation Code: {confirmation}\n"
    text_body += f"Airline: {airline}\n"
    text_body += "\n"

    for i, seg in enumerate(segments):
        origin = seg.get("origin", "")
        dest = seg.get("destination", "")
        flight_num = seg.get("flight_number", "")
        date = seg.get("date", "")

        origin_display = get_airport_display(origin) if origin else ""
        dest_display = get_airport_display(dest) if dest else ""

        if len(segments) > 1:
            text_body += f"Flight {i + 1}:\n"

        if flight_num:
            text_body += f"  Flight: {flight_num}\n"
        if origin_display:
            text_body += f"  From: {origin_display}\n"
        if dest_display:
            text_body += f"  To: {dest_display}\n"
        if date:
            text_body += f"  Date: {date}\n"

        text_body += "\n"

    # Build HTML body
    html_body = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>{email_subject}</title>
</head>
<body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
    <div style="background-color: #f8f9fa; border-radius: 8px; padding: 20px; margin-bottom: 20px;">
        <h1 style="color: #333; margin: 0 0 10px 0; font-size: 24px;">Flight Confirmation</h1>
"""

    if confirmation:
        html_body += f'        <p style="font-size: 18px; margin: 5px 0;"><strong>Confirmation:</strong> <span style="font-family: monospace; font-size: 20px; color: #0066cc;">{confirmation}</span></p>\n'

    html_body += f'        <p style="font-size: 14px; color: #666; margin: 5px 0;">{airline}</p>\n'
    html_body += '    </div>\n\n'

    for i, seg in enumerate(segments):
        origin = seg.get("origin", "")
        dest = seg.get("destination", "")
        flight_num = seg.get("flight_number", "")
        date = seg.get("date", "")

        origin_display = get_airport_display(origin) if origin else ""
        dest_display = get_airport_display(dest) if dest else ""

        html_body += '    <div style="background-color: #fff; border: 1px solid #ddd; border-radius: 8px; padding: 15px; margin-bottom: 15px;">\n'

        if len(segments) > 1:
            html_body += f'        <h2 style="color: #333; margin: 0 0 10px 0; font-size: 16px;">Flight {i + 1}</h2>\n'

        if flight_num:
            html_body += f'        <p style="margin: 5px 0;"><strong>Flight:</strong> {flight_num}</p>\n'
        if origin_display:
            html_body += f'        <p style="margin: 5px 0;"><strong>From:</strong> {origin_display}</p>\n'
        if dest_display:
            html_body += f'        <p style="margin: 5px 0;"><strong>To:</strong> {dest_display}</p>\n'
        if date:
            html_body += f'        <p style="margin: 5px 0;"><strong>Date:</strong> {date}</p>\n'

        html_body += '    </div>\n\n'

    html_body += """</body>
</html>"""

    # Create the email message
    flight_msg = MIMEMultipart('alternative')
    flight_msg['From'] = config['email']
    flight_msg['To'] = config['flighty_email']
    flight_msg['Subject'] = email_subject

    # Attach both plain text and HTML versions
    flight_msg.attach(MIMEText(text_body, 'plain'))
    flight_msg.attach(MIMEText(html_body, 'html'))

    # Retry with increasing delays until it works
    retry_delays = [10, 30, 60, 120, 180, 300]  # Up to 5 minutes wait
    max_attempts = len(retry_delays) + 1

    for attempt in range(max_attempts):
        try:
            with smtplib.SMTP(config['smtp_server'], config['smtp_port'], timeout=60) as server:
                server.starttls()
                server.login(config['email'], config['password'])
                server.send_message(flight_msg)
            return True  # Success
        except Exception as e:
            error_msg = str(e).lower()

            # Check if this is a rate limit / connection error (recoverable)
            is_rate_limit = any(x in error_msg for x in [
                'rate', 'limit', 'too many', 'try again', 'temporarily',
                '421', '450', '451', '452', '454', '554',
                'connection', 'closed', 'reset', 'refused', 'timeout'
            ])

            if attempt < max_attempts - 1:
                wait_time = retry_delays[attempt]
                wait_mins = wait_time // 60
                wait_secs = wait_time % 60

                print()  # New line for clarity
                if is_rate_limit:
                    print(f"        BLOCKED by email provider (they limit sending speed)")
                    print(f"        Error: {str(e)[:100]}")
                    if wait_mins > 0:
                        print(f"        Waiting {wait_mins} min {wait_secs} sec then retrying (attempt {attempt + 2} of {max_attempts})...", end="", flush=True)
                    else:
                        print(f"        Waiting {wait_secs} sec then retrying (attempt {attempt + 2} of {max_attempts})...", end="", flush=True)
                else:
                    print(f"        Connection error: {str(e)[:100]}")
                    if wait_mins > 0:
                        print(f"        Waiting {wait_mins} min {wait_secs} sec then retrying (attempt {attempt + 2} of {max_attempts})...", end="", flush=True)
                    else:
                        print(f"        Waiting {wait_secs} sec then retrying (attempt {attempt + 2} of {max_attempts})...", end="", flush=True)

                time.sleep(wait_time)
                print(" retrying now...", end="", flush=True)
            else:
                # All retries exhausted
                print()
                print(f"        FAILED after {max_attempts} attempts")
                print(f"        Final error: {str(e)}")
                print(f"        This email will be skipped - run again later to retry")
                return False

    return False
