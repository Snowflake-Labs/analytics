"""Backward-compatible entry point for the SSB benchmark CLI."""

from ssb.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
