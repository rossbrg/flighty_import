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
        print()
        print("  ┌─────────────────────────────────────────────────────────┐")
        print("  │  INSTALLING REQUIRED DEPENDENCY                         │")
        print("  └─────────────────────────────────────────────────────────┘")
        print()
        print("  The 'python-dateutil' package is required for date parsing.")
        print("  This is a one-time installation - it won't happen again.")
        print()
        print("  Installing python-dateutil...", end="", flush=True)

        try:
            # Run pip install
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "python-dateutil", "--quiet"],
                capture_output=True,
                text=True,
                timeout=60
            )

            if result.returncode == 0:
                print(" done!")
                print()
                print("  Installation successful! Continuing...")
                print()
                from dateutil import parser as dateutil_parser
                return dateutil_parser
            else:
                print(" failed!")
                print()
                print(f"  Error: {result.stderr[:200] if result.stderr else 'Unknown error'}")
                print()
                print("  Please install manually by running:")
                print("    pip install python-dateutil")
                print()
                return None

        except subprocess.TimeoutExpired:
            print(" timed out!")
            print()
            print("  Installation took too long. Please install manually:")
            print("    pip install python-dateutil")
            print()
            return None

        except Exception as e:
            print(f" failed!")
            print()
            print(f"  Error: {e}")
            print()
            print("  Please install manually by running:")
            print("    pip install python-dateutil")
            print()
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
