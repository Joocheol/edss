#!/usr/bin/env python3
"""Command-line entry point for edss.analyze_academyinfo_graduate_name_coverage."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from edss.analyze_academyinfo_graduate_name_coverage import main

if __name__ == "__main__":
    raise SystemExit(main())
