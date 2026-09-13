#!/usr/bin/env python3
"""Command-line entry point for edss.match_edss_employment_2022_2023_school_counts."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from edss.match_edss_employment_2022_2023_school_counts import main

if __name__ == "__main__":
    raise SystemExit(main())
