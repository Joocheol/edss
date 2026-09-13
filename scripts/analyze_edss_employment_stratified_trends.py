#!/usr/bin/env python3
"""Command-line entry point for edss.analyze_edss_employment_stratified_trends."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from edss.analyze_edss_employment_stratified_trends import main

if __name__ == "__main__":
    raise SystemExit(main())
