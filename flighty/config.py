"""
Configuration and data persistence management.
"""

import json
from pathlib import Path

# Default paths (can be overridden)
_DATA_DIR = Path(__file__).parent.parent
CONFIG_FILE = _DATA_DIR / "config.json"
PROCESSED_FILE = _DATA_DIR / "processed_flights.json"

# Email provider presets for setup wizard
EMAIL_PROVIDERS = {
    "1": {
        "name": "AOL",
        "imap_server": "imap.aol.com",
        "imap_port": 993,
        "smtp_server": "smtp.aol.com",
        "smtp_port": 587,
        "help": "Create an App Password at: https://login.aol.com/account/security"
    },
    "2": {
        "name": "Gmail",
        "imap_server": "imap.gmail.com",
        "imap_port": 993,
        "smtp_server": "smtp.gmail.com",
        "smtp_port": 587,
        "help": "Create an App Password at: https://myaccount.google.com/apppasswords"
    },
    "3": {
        "name": "Yahoo",
        "imap_server": "imap.mail.yahoo.com",
        "imap_port": 993,
        "smtp_server": "smtp.mail.yahoo.com",
        "smtp_port": 587,
        "help": "Create an App Password at: https://login.yahoo.com/account/security"
    },
    "4": {
        "name": "Outlook/Hotmail",
        "imap_server": "outlook.office365.com",
        "imap_port": 993,
        "smtp_server": "smtp.office365.com",
        "smtp_port": 587,
        "help": "You may need to enable IMAP in Outlook settings"
    },
    "5": {
        "name": "iCloud",
        "imap_server": "imap.mail.me.com",
        "imap_port": 993,
        "smtp_server": "smtp.mail.me.com",
        "smtp_port": 587,
        "help": "Create an App Password at: https://appleid.apple.com/account/manage"
    },
    "6": {
        "name": "Custom (enter your own servers)",
        "imap_server": "",
        "imap_port": 993,
        "smtp_server": "",
        "smtp_port": 587,
        "help": "Contact your email provider for IMAP/SMTP settings"
    }
}

# Default Flighty import address
DEFAULT_FLIGHTY_EMAIL = "track@my.flightyapp.com"


def load_config(config_file=None):
    """Load configuration from file with error handling.

    Args:
        config_file: Path to config file. Defaults to config.json.

    Returns:
        Config dict or None if not found/invalid.
    """
    if config_file is None:
        config_file = CONFIG_FILE

    config_path = Path(config_file)
    if not config_path.exists():
        return None

    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)

            # Validate required fields
            required = ['email', 'password', 'imap_server', 'smtp_server']
            for field in required:
                if not config.get(field):
                    print(f"Warning: Missing required config field: {field}")
                    return None

            # Set defaults for optional fields
            config.setdefault('days_back', 30)
            config.setdefault('check_folders', ['INBOX'])
            config.setdefault('flighty_email', DEFAULT_FLIGHTY_EMAIL)
            config.setdefault('imap_port', 993)
            config.setdefault('smtp_port', 587)
            return config

    except json.JSONDecodeError as e:
        print(f"Error: config.json is corrupted: {e}")
        print("Please run 'python3 setup.py' to reconfigure.")
        return None
    except Exception as e:
        print(f"Error loading config: {e}")
        return None


def save_config(config, config_file=None):
    """Save configuration to file.

    Args:
        config: Config dict to save.
        config_file: Path to config file. Defaults to config.json.
    """
    if config_file is None:
        config_file = CONFIG_FILE

    with open(config_file, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2)


def load_processed_flights(processed_file=None):
    """Load dictionary of processed flights with error handling and validation.

    Args:
        processed_file: Path to processed file. Defaults to processed_flights.json.

    Returns:
        Dict with 'confirmations' and 'content_hashes' keys.
    """
    if processed_file is None:
        processed_file = PROCESSED_FILE

    processed_path = Path(processed_file)
    default_data = {"confirmations": {}, "content_hashes": set()}

    if not processed_path.exists():
        return default_data

    try:
        with open(processed_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

            # Validate structure
            if not isinstance(data, dict):
                print("Warning: processed_flights.json has invalid format, starting fresh")
                return default_data

            # Ensure required keys exist with proper types
            if "confirmations" not in data or not isinstance(data.get("confirmations"), dict):
                data["confirmations"] = {}

            # Convert lists to sets for faster lookup
            content_hashes = data.get("content_hashes", [])
            if isinstance(content_hashes, list):
                data["content_hashes"] = set(content_hashes)
            elif isinstance(content_hashes, set):
                pass  # Already a set
            else:
                data["content_hashes"] = set()

            return data

    except json.JSONDecodeError as e:
        print(f"Warning: processed_flights.json is corrupted ({e})")
        print("Starting with fresh tracking. Previously imported flights may be re-imported.")
        # Backup corrupt file
        try:
            backup_path = processed_path.with_suffix('.json.bak')
            processed_path.rename(backup_path)
            print(f"Corrupt file backed up to: {backup_path}")
        except Exception:
            pass
        return default_data
    except Exception as e:
        print(f"Warning: Could not load processed flights ({e})")
        print("Starting with fresh tracking.")
        return default_data


def save_processed_flights(processed, processed_file=None):
    """Save processed flights data with atomic write for crash protection.

    Args:
        processed: Dict with 'confirmations' and 'content_hashes' keys.
        processed_file: Path to processed file. Defaults to processed_flights.json.
    """
    if processed_file is None:
        processed_file = PROCESSED_FILE

    processed_path = Path(processed_file)
    save_data = {
        "content_hashes": list(processed.get("content_hashes", set())),
        "confirmations": processed.get("confirmations", {})
    }

    # Write to temp file first, then rename (atomic operation)
    temp_file = processed_path.with_suffix('.json.tmp')
    try:
        with open(temp_file, 'w', encoding='utf-8') as f:
            json.dump(save_data, f, indent=2)

        # Atomic rename
        temp_file.replace(processed_path)
    except Exception as e:
        print(f"\n    Warning: Could not save progress ({e})")
        # Try to clean up temp file
        try:
            if temp_file.exists():
                temp_file.unlink()
        except Exception:
            pass


def reset_processed_flights(processed_file=None):
    """Delete processed flights tracking file.

    Args:
        processed_file: Path to processed file. Defaults to processed_flights.json.

    Returns:
        True if file was deleted, False otherwise.
    """
    if processed_file is None:
        processed_file = PROCESSED_FILE

    processed_path = Path(processed_file)
    if processed_path.exists():
        processed_path.unlink()
        return True
    return False


def clean_data_files(processed_file=None):
    """Clean up potentially corrupt data files.

    Args:
        processed_file: Path to processed file. Defaults to processed_flights.json.

    Returns:
        List of files that were cleaned.
    """
    if processed_file is None:
        processed_file = PROCESSED_FILE

    processed_path = Path(processed_file)
    cleaned = []

    files_to_clean = [
        processed_path,
        processed_path.with_suffix('.json.tmp'),
        processed_path.with_suffix('.json.bak'),
    ]

    for f in files_to_clean:
        if f.exists():
            try:
                f.unlink()
                cleaned.append(f.name)
            except Exception as e:
                print(f"Could not remove {f.name}: {e}")

    return cleaned
