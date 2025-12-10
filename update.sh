#!/bin/bash
# Flighty Email Forwarder - Update Script
# Run this to get the latest version from GitHub

cd "$(dirname "$0")"

echo "Checking for updates..."

# Make sure we're in a git repo
if ! git rev-parse --is-inside-work-tree > /dev/null 2>&1; then
    echo "  Not a git repository. Please clone from GitHub first."
    exit 1
fi

# Fetch latest
git fetch origin 2>/dev/null

# Check if behind
BEHIND=$(git rev-list --count HEAD..origin/main 2>/dev/null || echo "0")

if [ "$BEHIND" = "0" ]; then
    echo "  Already up to date!"
    exit 0
fi

echo "  $BEHIND update(s) available. Downloading..."

# Pull latest (config.json is gitignored so it's safe)
if git pull origin main --quiet; then
    echo "  Updated successfully!"
else
    echo "  Update failed. Try running: git pull origin main"
    exit 1
fi
