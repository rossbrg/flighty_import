"""
Email handling: IMAP connection, SMTP forwarding, email parsing.
"""

import imaplib
import smtplib
import email
import email.header
import re
import time
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import parsedate_to_datetime

from .airports import VALID_AIRPORT_CODES
from .parser import (
    extract_confirmation_code,
    extract_flight_info,
    generate_content_hash,
    create_flight_fingerprint
)
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

            if "attachment" not in content_disposition:
                text = _decode_payload(part)
                if text:
                    if content_type == "text/plain":
                        body = text
                    elif content_type == "text/html":
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
    """Forward an email to Flighty with retry logic.

    Injects explicit flight data at the top of the email to help Flighty
    parse dates correctly.

    Args:
        config: Config dict with smtp settings and flighty_email
        msg: Original email message object
        from_addr: Original sender address
        subject: Original subject line
        flight_info: Extracted flight info dict (optional)

    Returns:
        True if sent successfully, False otherwise
    """
    forward_msg = MIMEMultipart('mixed')
    forward_msg['From'] = config['email']
    forward_msg['To'] = config['flighty_email']
    forward_msg['Subject'] = f"Fwd: {subject}"

    body, html_body = get_email_body(msg)

    # Build explicit flight data header to help Flighty parse correctly
    flight_data_header = ""
    if flight_info:
        airports = flight_info.get("airports", [])
        dates = flight_info.get("dates", [])
        flight_nums = flight_info.get("flight_numbers", [])

        # Filter to valid airports
        valid_airports = [code for code in airports if code in VALID_AIRPORT_CODES]

        if valid_airports or dates or flight_nums:
            flight_data_header = "=" * 50 + "\n"
            flight_data_header += "FLIGHT INFORMATION\n"
            flight_data_header += "=" * 50 + "\n"

            if valid_airports and len(valid_airports) >= 2:
                flight_data_header += f"Route: {valid_airports[0]} to {valid_airports[1]}\n"
            elif valid_airports:
                flight_data_header += f"Airport: {valid_airports[0]}\n"

            if flight_nums:
                flight_data_header += f"Flight Number: {flight_nums[0]}\n"

            # Add dates with explicit years
            if dates:
                for i, date in enumerate(dates[:2]):  # Max 2 dates
                    label = "Departure Date" if i == 0 else "Return Date"
                    flight_data_header += f"{label}: {date}\n"

            flight_data_header += "=" * 50 + "\n\n"

    forward_text = flight_data_header + f"""
---------- Forwarded message ---------
From: {from_addr}
Date: {msg.get('Date', 'Unknown')}
Subject: {subject}

"""
    if body:
        forward_text += body
        forward_msg.attach(MIMEText(forward_text, 'plain'))

    # Also inject into HTML body if present
    if html_body:
        html_header = ""
        if flight_info:
            airports = flight_info.get("airports", [])
            dates = flight_info.get("dates", [])
            flight_nums = flight_info.get("flight_numbers", [])
            valid_airports = [code for code in airports if code in VALID_AIRPORT_CODES]

            if valid_airports or dates or flight_nums:
                # Use explicit colors that resist email client dark mode inversions
                html_header = """<div style="background-color: #fff3cd !important; padding: 15px; margin-bottom: 20px; border: 2px solid #856404 !important; border-radius: 5px; font-family: Arial, sans-serif;">
<h2 style="margin: 0 0 10px 0; color: #856404 !important; background-color: transparent !important;">✈️ FLIGHT INFORMATION</h2>
<table style="font-size: 14px; color: #333 !important; background-color: transparent !important;">"""

                if valid_airports and len(valid_airports) >= 2:
                    html_header += f'<tr><td style="color: #333 !important; padding: 3px 10px 3px 0;"><strong>Route:</strong></td><td style="color: #333 !important;">{valid_airports[0]} → {valid_airports[1]}</td></tr>'
                elif valid_airports:
                    html_header += f'<tr><td style="color: #333 !important; padding: 3px 10px 3px 0;"><strong>Airport:</strong></td><td style="color: #333 !important;">{valid_airports[0]}</td></tr>'

                if flight_nums:
                    html_header += f'<tr><td style="color: #333 !important; padding: 3px 10px 3px 0;"><strong>Flight Number:</strong></td><td style="color: #333 !important;">{flight_nums[0]}</td></tr>'

                if dates:
                    for i, date in enumerate(dates[:2]):
                        label = "Departure Date" if i == 0 else "Return Date"
                        html_header += f'<tr><td style="color: #333 !important; padding: 3px 10px 3px 0;"><strong>{label}:</strong></td><td style="color: #333 !important;">{date}</td></tr>'

                html_header += "</table></div>"

        # Inject at the start of the HTML body
        if "<body" in html_body.lower():
            html_body = re.sub(r'(<body[^>]*>)', r'\1' + html_header, html_body, flags=re.IGNORECASE)
        else:
            html_body = html_header + html_body

        forward_msg.attach(MIMEText(html_body, 'html'))

    # Retry with increasing delays until it works
    retry_delays = [10, 30, 60, 120, 180, 300]  # Up to 5 minutes wait
    max_attempts = len(retry_delays) + 1

    for attempt in range(max_attempts):
        try:
            with smtplib.SMTP(config['smtp_server'], config['smtp_port'], timeout=60) as server:
                server.starttls()
                server.login(config['email'], config['password'])
                server.send_message(forward_msg)
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
