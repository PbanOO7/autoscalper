#!/usr/bin/env python3
"""Entry point for the Nifty50 Options AutoScalper.

Usage:
    python main.py                    # Uses default config.yaml
    python main.py --config my.yaml   # Uses custom config file
    python main.py --paper            # Force paper trading mode
    python main.py --live             # Force live trading mode
"""

import argparse
import os
import sys

from scalper.orchestrator import main as run_scalper


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Nifty50 Options AutoScalper with DHAN API",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py                         Start with default config (paper mode)
  python main.py --config prod.yaml      Start with custom config
  python main.py --live                  Force live trading mode
  python main.py --paper                 Force paper trading mode

Environment Variables:
  DHAN_CLIENT_ID        DHAN API Client ID
  DHAN_ACCESS_TOKEN     DHAN API Access Token
  SCALPER_CONFIG_PATH   Path to config file
  SCALPER_LIVE_MODE     Set to 'true' for live mode
        """,
    )

    parser.add_argument(
        "--config", "-c",
        type=str,
        default=None,
        help="Path to configuration YAML file (default: config.yaml)",
    )
    parser.add_argument(
        "--paper",
        action="store_true",
        help="Force paper trading mode (overrides config)",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Force live trading mode (overrides config, requires DHAN credentials)",
    )

    return parser.parse_args()


def main() -> None:
    """Main entry point."""
    args = parse_args()

    # Handle mode overrides via CLI flags
    if args.live:
        os.environ["SCALPER_LIVE_MODE"] = "true"
    elif args.paper:
        os.environ.pop("SCALPER_LIVE_MODE", None)

    config_path = args.config
    if config_path and not os.path.exists(config_path):
        print(f"Error: Config file not found: {config_path}", file=sys.stderr)
        sys.exit(1)

    run_scalper(config_path)


if __name__ == "__main__":
    main()
