#!/usr/bin/env python3
"""Command-line entry point for edss.analyze_edss_employment_balanced_panel."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from edss.analyze_edss_employment_balanced_panel import main

if __name__ == "__main__":
    raise SystemExit(main())
