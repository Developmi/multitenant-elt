"""multitenant-elt - CLI entry point."""

import sys

from dotenv import load_dotenv

from agency_analytics.cli import cli

load_dotenv()

if __name__ == "__main__":
    sys.exit(cli())
