"""Backward-compatible entry point for the TPC-H benchmark CLI."""

from tpch.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
