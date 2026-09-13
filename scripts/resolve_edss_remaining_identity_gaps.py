#!/usr/bin/env python3
"""Command-line entry point for edss.resolve_edss_remaining_identity_gaps."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from edss.resolve_edss_remaining_identity_gaps import main

if __name__ == "__main__":
    raise SystemExit(main())
