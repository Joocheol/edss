#!/usr/bin/env python3
"""Command-line entry point for edss.audit_edss_full_panel_keys."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from edss.audit_edss_full_panel_keys import main

if __name__ == "__main__":
    raise SystemExit(main())
