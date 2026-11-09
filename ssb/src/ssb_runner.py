"""Backward-compatible entry point for the SSB (Star Schema Benchmark) benchmark CLI."""

from src.ssb.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
