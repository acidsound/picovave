#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Recover SMK-37 Pro from mask-ROM UBOOT mode (WL80UBOOT / WL82 UBOOT).

Usage (requires root/sudo for raw USB bulk transfer on macOS):
    sudo .venv-ota/bin/python tools/recover_smk37.py
"""
import argparse
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SRC / "tools"))
from recover_uboot import recover

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pkg", type=Path, default=None, help="Custom .fwsc package path (e.g. ~/Downloads/SMK-37 Pro_015.fwsc)")
    args = parser.parse_args()
    sys.exit(recover("smk37", custom_pkg=args.pkg))
