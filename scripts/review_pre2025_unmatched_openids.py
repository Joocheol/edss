#!/usr/bin/env python3
"""Command-line entry point for edss.review_pre2025_unmatched_openids."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from edss.review_pre2025_unmatched_openids import main

if __name__ == "__main__":
    raise SystemExit(main())
