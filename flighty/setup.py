"""
Interactive setup wizard for configuring Flighty Email Forwarder.
"""

import getpass

from .config import (
    EMAIL_PROVIDERS,
    CONFIG_FILE,
    DEFAULT_FLIGHTY_EMAIL,
    save_config
)


def clear_screen():
    """Print some newlines to simulate clearing screen."""
    print("\n" * 2)


def print_header(text):
    """Print a header banner."""
    print()
    print("=" * 50)
    print(f"  {text}")
    print("=" * 50)
    print()


def print_step(step, total, description):
    """Print a step indicator."""
    print(f"\n[Step {step}/{total}] {description}")
    print("-" * 40)


def get_input(prompt, default=None, required=True, password=False):
    """Get user input with optional default value.

    Args:
        prompt: The prompt to display
        default: Default value if user presses Enter
        required: Whether a value is required
        password: Whether to mask input

    Returns:
        User input string
    """
    if default:
        display_prompt = f"{prompt} [{default}]: "
    else:
        display_prompt = f"{prompt}: "

    while True:
        if password:
            value = getpass.getpass(display_prompt)
        else:
            value = input(display_prompt).strip()

        if not value and default:
            return default
        elif value:
            return value
        elif not required:
            return ""
        else:
            print("  This field is required. Please enter a value.")


def get_yes_no(prompt, default="y"):
    """Get yes/no input from user.

    Args:
        prompt: The prompt to display
        default: Default value ('y' or 'n')

    Returns:
        Boolean
    """
    default_display = "Y/n" if default.lower() == "y" else "y/N"
    while True:
        value = input(f"{prompt} [{default_display}]: ").strip().lower()
        if not value:
            return default.lower() == "y"
        if value in ("y", "yes"):
            return True
        if value in ("n", "no"):
            return False
        print("  Please enter 'y' or 'n'")


def get_number(prompt, default=None, min_val=1, max_val=None):
    """Get a number from user.

    Args:
        prompt: The prompt to display
        default: Default value
        min_val: Minimum allowed value
        max_val: Maximum allowed value

    Returns:
        Integer
    """
    while True:
        value = get_input(prompt, str(default) if default else None)
        try:
            num = int(value)
            if num < min_val:
                print(f"  Please enter a number >= {min_val}")
                continue
            if max_val and num > max_val:
                print(f"  Please enter a number <= {max_val}")
                continue
            return num
        except ValueError:
            print("  Please enter a valid number")


def run_setup():
    """Run the interactive setup wizard.

    Returns:
        True if setup completed successfully, False otherwise
    """
    clear_screen()
    print_header("Flighty Email Forwarder Setup")

    print("This wizard will help you configure the email forwarder.")
    print("Your flight confirmation emails will be forwarded to Flighty")
    print("so they automatically appear in your Flighty app.")

    total_steps = 6
    config = {}

    # Step 1: Email Provider
    print_step(1, total_steps, "Select Your Email Provider")
    print("Which email service do you use?\n")

    for key, provider in EMAIL_PROVIDERS.items():
        print(f"  {key}. {provider['name']}")

    print()
    while True:
        choice = input("Enter your choice (1-6): ").strip()
        if choice in EMAIL_PROVIDERS:
            break
        print("  Please enter a number between 1 and 6")

    provider = EMAIL_PROVIDERS[choice]
    print(f"\n  Selected: {provider['name']}")

    if provider["help"]:
        print(f"\n  Note: {provider['help']}")

    # Set server info
    if choice == "6":  # Custom
        print("\nEnter your email server details:")
        config["imap_server"] = get_input("IMAP server (e.g., imap.example.com)")
        config["imap_port"] = get_number("IMAP port", 993)
        config["smtp_server"] = get_input("SMTP server (e.g., smtp.example.com)")
        config["smtp_port"] = get_number("SMTP port", 587)
    else:
        config["imap_server"] = provider["imap_server"]
        config["imap_port"] = provider["imap_port"]
        config["smtp_server"] = provider["smtp_server"]
        config["smtp_port"] = provider["smtp_port"]

    # Step 2: Email Credentials
    print_step(2, total_steps, "Enter Your Email Credentials")

    config["email"] = get_input("Your email address")

    print(f"\nFor {provider['name']}, you typically need an App Password.")
    print("This is different from your regular login password.")
    if provider["help"]:
        print(f"\n  {provider['help']}")

    print()
    config["password"] = get_input("App Password (input hidden)", password=True)

    # Step 3: Flighty Email
    print_step(3, total_steps, "Flighty Import Email")

    print("Emails will be forwarded to Flighty's import service.")
    print("The default address works for most users.\n")

    config["flighty_email"] = get_input(
        "Flighty email address",
        default=DEFAULT_FLIGHTY_EMAIL
    )

    # Step 4: Folders to Check
    print_step(4, total_steps, "Email Folders to Search")

    print("Which folder(s) should we search for flight emails?")
    print("You can enter multiple folders separated by commas.")
    print("Common folders: INBOX, Sent, Travel, Receipts\n")

    folders_input = get_input("Folders to search", default="INBOX")
    config["check_folders"] = [f.strip() for f in folders_input.split(",")]

    # Step 5: Time Range
    print_step(5, total_steps, "How Far Back to Search")

    print("How far back should we look for flight emails?")
    print("(Older emails that were already forwarded won't be sent again)\n")

    print("  1. Last 7 days")
    print("  2. Last 30 days")
    print("  3. Last 90 days")
    print("  4. Last 6 months (180 days)")
    print("  5. Last year (365 days)")
    print("  6. Custom number of days")
    print()

    time_choices = {"1": 7, "2": 30, "3": 90, "4": 180, "5": 365}
    while True:
        time_choice = input("Enter your choice (1-6): ").strip()
        if time_choice in time_choices:
            config["days_back"] = time_choices[time_choice]
            break
        elif time_choice == "6":
            config["days_back"] = get_number("Number of days", min_val=1, max_val=3650)
            break
        print("  Please enter a number between 1 and 6")

    print(f"\n  Will search emails from the last {config['days_back']} days")

    # Step 6: Additional Options
    print_step(6, total_steps, "Additional Options")

    config["mark_as_read"] = get_yes_no(
        "Mark emails as read after forwarding?",
        default="n"
    )

    config["processed_file"] = "processed_flights.json"

    # Summary
    clear_screen()
    print_header("Configuration Summary")

    print(f"  Email:          {config['email']}")
    print(f"  Password:       {'*' * 16}")
    print(f"  IMAP Server:    {config['imap_server']}:{config['imap_port']}")
    print(f"  SMTP Server:    {config['smtp_server']}:{config['smtp_port']}")
    print(f"  Forward to:     {config['flighty_email']}")
    print(f"  Folders:        {', '.join(config['check_folders'])}")
    print(f"  Days back:      {config['days_back']}")
    print(f"  Mark as read:   {'Yes' if config['mark_as_read'] else 'No'}")

    print()
    if get_yes_no("Save this configuration?", default="y"):
        # Save config
        save_config(config)

        print(f"\n  Configuration saved to: {CONFIG_FILE}")
        print("\n" + "=" * 50)
        print("  Setup complete!")
        print("=" * 50)
        print("\nNext steps:")
        print("  1. Test with a dry run:")
        print("     python3 run.py --dry-run")
        print()
        print("  2. Run for real:")
        print("     python3 run.py")
        print()
        return True
    else:
        print("\n  Configuration not saved. Run setup.py again to reconfigure.")
        return False


def main():
    """Entry point for setup wizard."""
    try:
        run_setup()
    except KeyboardInterrupt:
        print("\n\n  Setup cancelled.")


if __name__ == "__main__":
    main()
