#!/usr/bin/env python3
"""Command-line entry point for the KESS public Excel downloader."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kess.download import main


if __name__ == "__main__":
    raise SystemExit(main())
