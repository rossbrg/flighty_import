"""
PDF Report Generation for Flight Summary.

Creates a PDF summary of flights grouped by year, month, and day.
"""

import re
from datetime import datetime
from pathlib import Path

from .deps import ensure_reportlab

# Auto-install reportlab if needed
HAS_REPORTLAB = ensure_reportlab()

if HAS_REPORTLAB:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak


def parse_date_components(date_str):
    """Extract year, month, day from date string like 'April 28, 2025' or ISO date.

    Returns:
        Tuple of (year, month_num, month_name, day)
    """
    if not date_str:
        return (9999, 0, "Unknown", 0)

    month_names = ['January', 'February', 'March', 'April', 'May', 'June',
                   'July', 'August', 'September', 'October', 'November', 'December']
    month_order = {name: i+1 for i, name in enumerate(month_names)}

    # Try ISO format first (YYYY-MM-DD)
    iso_match = re.match(r'(\d{4})-(\d{2})-(\d{2})', date_str)
    if iso_match:
        year = int(iso_match.group(1))
        month_num = int(iso_match.group(2))
        day = int(iso_match.group(3))
        month_name = month_names[month_num - 1] if 1 <= month_num <= 12 else 'Unknown'
        return (year, month_num, month_name, day)

    # Try "Month DD, YYYY" format
    match = re.match(r'(\w+)\s+(\d{1,2}),?\s*(\d{4})', date_str)
    if match:
        month_name = match.group(1)
        day = int(match.group(2))
        year = int(match.group(3))
        month_num = month_order.get(month_name, 0)
        return (year, month_num, month_name, day)

    # Try "DD Mon YYYY" format (like "03 Dec 2015")
    match = re.match(r'(\d{1,2})\s+(\w+)\s+(\d{4})', date_str)
    if match:
        day = int(match.group(1))
        month_name = match.group(2)
        year = int(match.group(3))
        # Handle abbreviated month names
        for full_name in month_names:
            if full_name.lower().startswith(month_name.lower()[:3]):
                month_name = full_name
                break
        month_num = month_order.get(month_name, 0)
        return (year, month_num, month_name, day)

    # Try "Month YYYY" format
    match = re.match(r'(\w+)\s+(\d{4})', date_str)
    if match:
        month_name = match.group(1)
        year = int(match.group(2))
        month_num = month_order.get(month_name, 0)
        return (year, month_num, month_name, 0)

    return (9999, 0, "Unknown", 0)


def parse_month_year(date_str):
    """Extract month and year from date string (backwards compatibility)."""
    year, month_num, month_name, _ = parse_date_components(date_str)
    return (month_name, year, month_num)


def group_flights_by_year_month(flights):
    """Group flights by year and month.

    Args:
        flights: List of flight dicts with flight_info containing dates

    Returns:
        Dict of year -> Dict of (month_num, month_name) -> list of flights, sorted by date
    """
    from collections import defaultdict

    month_names = ['January', 'February', 'March', 'April', 'May', 'June',
                   'July', 'August', 'September', 'October', 'November', 'December']

    flights_by_year = defaultdict(lambda: defaultdict(list))

    for flight in flights:
        flight_info = flight.get("flight_info") or {}

        # Try ISO date first, then display dates
        iso_date = flight_info.get("iso_date")
        dates = flight_info.get("dates") or []
        date_str = iso_date or (dates[0] if dates else "")

        # Fall back to email_date if no flight date available
        if not date_str and flight.get("email_date"):
            email_date = flight.get("email_date")
            # Format email_date as a date string
            try:
                date_str = email_date.strftime("%Y-%m-%d")
                # Also populate dates for display
                flight_info["dates"] = [email_date.strftime("%B %d, %Y")]
                flight["flight_info"] = flight_info
            except:
                pass

        year, month_num, month_name, day = parse_date_components(date_str)

        # Store day for sorting within month
        flight['_sort_day'] = day
        flight['_sort_date'] = date_str

        flights_by_year[year][(month_num, month_name)].append(flight)

    # Sort flights within each month by day
    for year in flights_by_year:
        for month_key in flights_by_year[year]:
            flights_by_year[year][month_key].sort(key=lambda f: f.get('_sort_day', 0))

    # Convert to sorted regular dicts
    result = {}
    for year in sorted(flights_by_year.keys()):
        result[year] = {}
        for month_key in sorted(flights_by_year[year].keys()):
            result[year][month_key] = flights_by_year[year][month_key]

    return result


def group_flights_by_month(flights):
    """Group flights by month-year (backwards compatibility).

    Args:
        flights: List of flight dicts with flight_info containing dates

    Returns:
        Dict of (year, month_num, month_name) -> list of flights, sorted
    """
    from collections import defaultdict

    flights_by_month = defaultdict(list)

    for flight in flights:
        flight_info = flight.get("flight_info") or {}

        # Try ISO date first, then display dates
        iso_date = flight_info.get("iso_date")
        dates = flight_info.get("dates") or []
        date_str = iso_date or (dates[0] if dates else "")

        month_name, year, month_num = parse_month_year(date_str)
        key = (year, month_num, month_name)
        flights_by_month[key].append(flight)

    return dict(sorted(flights_by_month.items()))


def generate_pdf_report(flights, output_path, title="Flight Summary"):
    """Generate a PDF report of flights grouped by year and month.

    Args:
        flights: List of flight dicts
        output_path: Path to save the PDF
        title: Title for the report

    Returns:
        Path to the generated PDF or None on failure
    """
    from .airports import get_airport_display, VALID_AIRPORT_CODES

    if not flights:
        print("      No flights to include in PDF")
        return None

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if not HAS_REPORTLAB:
        # Fall back to text report
        print("      (reportlab not available, generating text file instead)")
        return generate_text_report(flights, output_path.with_suffix('.txt'), title)

    # Group flights by year and month
    flights_by_year = group_flights_by_year_month(flights)

    if not flights_by_year:
        print("      No flights grouped")
        return None

    # Create PDF
    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=letter,
        rightMargin=0.5*inch,
        leftMargin=0.5*inch,
        topMargin=0.5*inch,
        bottomMargin=0.5*inch
    )

    styles = getSampleStyleSheet()

    # Custom styles - clean, modern look
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=24,
        spaceAfter=6,
        fontName='Helvetica-Bold',
        textColor=colors.HexColor('#1a1a1a'),
        alignment=1  # Center
    )

    subtitle_style = ParagraphStyle(
        'Subtitle',
        parent=styles['Normal'],
        fontSize=10,
        textColor=colors.HexColor('#666666'),
        alignment=1  # Center
    )

    year_style = ParagraphStyle(
        'YearHeader',
        parent=styles['Heading1'],
        fontSize=20,
        spaceBefore=10,
        spaceAfter=8,
        fontName='Helvetica-Bold',
        textColor=colors.HexColor('#2c3e50')
    )

    month_style = ParagraphStyle(
        'MonthHeader',
        parent=styles['Heading2'],
        fontSize=12,
        spaceBefore=12,
        spaceAfter=6,
        fontName='Helvetica-Bold',
        textColor=colors.HexColor('#34495e')
    )

    story = []

    # Title
    story.append(Paragraph(title, title_style))
    story.append(Spacer(1, 4))

    # Summary line
    total_flights = len(flights)
    total_years = len(flights_by_year)
    year_range = f"{min(flights_by_year.keys())} - {max(flights_by_year.keys())}" if flights_by_year else "N/A"
    story.append(Paragraph(f"{total_flights} flights  •  {year_range}  •  {total_years} years", subtitle_style))
    story.append(Spacer(1, 24))

    # Flights by year and month
    first_year = True
    for year, months_dict in flights_by_year.items():
        # Page break between years (except first)
        if not first_year:
            story.append(PageBreak())
        first_year = False

        # Count flights this year
        year_flight_count = sum(len(flights) for flights in months_dict.values())

        # Year header
        story.append(Paragraph(f"{year}", year_style))
        story.append(Paragraph(f"{year_flight_count} flights", subtitle_style))

        for (month_num, month_name), month_flights in months_dict.items():
            # Month header
            story.append(Paragraph(f"{month_name}", month_style))

            # Build table data
            table_data = [['Date', 'Confirmation', 'Flight', 'Route']]

            for flight in month_flights:
                flight_info = flight.get("flight_info") or {}
                conf = flight.get("confirmation") or "------"

                # Get flight number
                flight_nums = flight_info.get("flight_numbers") or []
                flight_num = flight_nums[0] if flight_nums else ""

                # Get route
                route_tuple = flight_info.get("route")
                dest_only = flight_info.get("dest_only")
                airports = flight_info.get("airports") or []

                if route_tuple:
                    # Full route available
                    route = f"{route_tuple[0]} -> {route_tuple[1]}"
                elif dest_only:
                    # Only destination known (from older check-in emails)
                    route = f"-> {dest_only}"
                elif airports:
                    # Try to build route from airports list
                    valid_airports = [code for code in airports if code in VALID_AIRPORT_CODES]
                    if len(valid_airports) >= 2:
                        route = f"{valid_airports[0]} -> {valid_airports[1]}"
                    elif valid_airports:
                        route = f"-> {valid_airports[0]}"
                    else:
                        route = ""
                else:
                    route = ""

                # Get date - show just day for cleaner look within month
                dates = flight_info.get("dates") or []
                date_str = dates[0] if dates else ""
                # Extract just day number if possible
                _, _, _, day = parse_date_components(date_str)
                if day > 0:
                    display_date = f"{month_name[:3]} {day}"
                else:
                    display_date = date_str[:15] if date_str else ""

                table_data.append([display_date, conf, flight_num, route])

            # Create table - clean minimal style
            table = Table(table_data, colWidths=[0.8*inch, 1.0*inch, 0.7*inch, 2.5*inch])
            table.setStyle(TableStyle([
                # Header row
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 9),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor('#666666')),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 6),
                ('LINEBELOW', (0, 0), (-1, 0), 1, colors.HexColor('#cccccc')),
                # Data rows
                ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
                ('FONTSIZE', (0, 1), (-1, -1), 9),
                ('TEXTCOLOR', (0, 1), (-1, -1), colors.HexColor('#333333')),
                ('TOPPADDING', (0, 1), (-1, -1), 4),
                ('BOTTOMPADDING', (0, 1), (-1, -1), 4),
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                # Subtle row lines
                ('LINEBELOW', (0, 1), (-1, -2), 0.5, colors.HexColor('#eeeeee')),
            ]))

            story.append(table)
            story.append(Spacer(1, 15))

    # Build PDF
    try:
        doc.build(story)
        return output_path
    except Exception as e:
        print(f"      Error generating PDF: {e}")
        # Fall back to text report
        return generate_text_report(flights, output_path.with_suffix('.txt'), title)


def generate_text_report(flights, output_path, title="Flight Summary"):
    """Generate a plain text report of flights grouped by month.

    Args:
        flights: List of flight dicts
        output_path: Path to save the text file
        title: Title for the report

    Returns:
        Path to the generated file or None on failure
    """
    from .airports import get_airport_display, VALID_AIRPORT_CODES

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    flights_by_month = group_flights_by_month(flights)

    lines = []
    lines.append("=" * 70)
    lines.append(f"  {title}")
    lines.append("=" * 70)
    lines.append(f"  Generated: {datetime.now().strftime('%B %d, %Y at %I:%M %p')}")
    lines.append(f"  Total Flights: {len(flights)}")
    lines.append("")

    for (year, month_num, month_name), month_flights in flights_by_month.items():
        lines.append("")
        lines.append("=" * 70)
        lines.append(f"  {month_name.upper()} {year}  ({len(month_flights)} flights)")
        lines.append("=" * 70)
        lines.append("")

        for flight in month_flights:
            flight_info = flight.get("flight_info") or {}
            conf = flight.get("confirmation") or "------"

            flight_nums = flight_info.get("flight_numbers") or []
            flight_num = flight_nums[0] if flight_nums else ""

            route_tuple = flight_info.get("route")
            airports = flight_info.get("airports") or []

            if route_tuple:
                valid_airports = list(route_tuple)
            else:
                valid_airports = [code for code in airports if code in VALID_AIRPORT_CODES]

            if len(valid_airports) >= 2:
                origin = get_airport_display(valid_airports[0])
                dest = get_airport_display(valid_airports[1])
                route = f"{origin} -> {dest}"
            elif valid_airports:
                route = get_airport_display(valid_airports[0])
            else:
                route = ""

            dates = flight_info.get("dates") or []
            date_str = dates[0] if dates else ""

            lines.append(f"  {conf:<10} {flight_num:<8} {route}")
            if date_str:
                lines.append(f"             Date: {date_str}")
            lines.append("")

    lines.append("")
    lines.append("=" * 70)

    try:
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))
        return output_path
    except Exception as e:
        print(f"      Error generating report: {e}")
        return None
