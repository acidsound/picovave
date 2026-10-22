#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Recover FM-1 from mask-ROM UBOOT mode (WL80UBOOT / WL82 UBOOT).

Usage (requires root/sudo for raw USB bulk transfer on macOS):
    sudo .venv-ota/bin/python tools/recover_fm1.py
"""
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SRC / "tools"))
from recover_uboot import recover

if __name__ == "__main__":
    sys.exit(recover("fm1"))
