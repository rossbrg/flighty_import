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
                try:
                    payload = part.get_payload(decode=True)
                    if payload:
                        text = payload.decode('utf-8', errors='replace')
                        if content_type == "text/plain":
                            body = text
                        elif content_type == "text/html":
                            html_body = text
                except Exception:
                    pass
    else:
        try:
            payload = msg.get_payload(decode=True)
            if payload:
                text = payload.decode('utf-8', errors='replace')
                if msg.get_content_type() == "text/plain":
                    body = text
                else:
                    html_body = text
        except Exception:
            pass

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
    try:
        mail = imaplib.IMAP4_SSL(config['imap_server'], config['imap_port'])
        mail.login(config['email'], config['password'])
        return mail
    except imaplib.IMAP4.error as e:
        print(f"\nLogin failed: {e}")
        print("\nMake sure you're using an App Password, not your regular password.")
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
