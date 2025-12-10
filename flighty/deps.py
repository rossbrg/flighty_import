"""
Dependency management - auto-installs required packages.
"""

import subprocess
import sys


def ensure_dateutil():
    """Ensure python-dateutil is installed, auto-install if missing.

    Returns:
        The dateutil.parser module
    """
    try:
        from dateutil import parser as dateutil_parser
        return dateutil_parser
    except ImportError:
        print("  Installing required package: python-dateutil...", end="", flush=True)
        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", "python-dateutil", "-q"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            print(" done!")
            from dateutil import parser as dateutil_parser
            return dateutil_parser
        except Exception as e:
            print(f" failed: {e}")
            print("  Please install manually: pip install python-dateutil")
            return None


# Cache the imported module
_dateutil_parser = None


def get_dateutil_parser():
    """Get the dateutil parser module (cached).

    Returns:
        The dateutil.parser module or None if unavailable
    """
    global _dateutil_parser
    if _dateutil_parser is None:
        _dateutil_parser = ensure_dateutil()
    return _dateutil_parser
