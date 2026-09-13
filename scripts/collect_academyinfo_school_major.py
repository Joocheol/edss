#!/usr/bin/env python3
"""Command-line entry point for edss.collect_academyinfo_school_major."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from edss.collect_academyinfo_school_major import main

if __name__ == "__main__":
    raise SystemExit(main())
