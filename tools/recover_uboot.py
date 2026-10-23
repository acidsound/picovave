#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Recover SMK-37 Pro or FM-1 from mask-ROM UBOOT mode (WL80UBOOT / WL82 UBOOT).

Usage (requires root/sudo for raw USB bulk transfer on macOS):
    sudo .venv-ota/bin/python tools/recover_uboot.py --board smk37
    sudo .venv-ota/bin/python tools/recover_uboot.py --board fm1
"""
import argparse
import hashlib
import os
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1]
UBOOT_TOOL = SRC / "tools" / "jl-uboot-tool" / "jluboottool.py"
CHECK_BIN = SRC / "build" / "check.bin"

BOARDS = {
    "smk37": {
        "name": "SMK-37 Pro",
        "pkg_candidates": [SRC / "build" / "picovave-smk37.fwsc", SRC / "build" / "picovave.fwsc"],
        "app_bin": SRC / "build" / "picovave-smk37.app-area.bin",
        "app_len": 0x99000,
    },
    "fm1": {
        "name": "FM-1",
        "pkg_candidates": [SRC / "build" / "picovave-fm1.fwsc"],
        "app_bin": SRC / "build" / "picovave-fm1.app-area.bin",
        "app_len": 0x8F000,
    },
}

def recover(board_key: str, custom_pkg: Path = None):
    info = BOARDS.get(board_key)
    if not info:
        print(f"Error: Unknown board '{board_key}'. Supported: smk37, fm1")
        return 1

    board_name = info["name"]
    pkg = custom_pkg if (custom_pkg and custom_pkg.exists()) else next((p for p in info["pkg_candidates"] if p.exists()), None)
    if not pkg:
        if custom_pkg:
            print(f"Error: Specified package not found: {custom_pkg}")
        else:
            candidate_names = " or ".join(str(p.relative_to(SRC)) for p in info["pkg_candidates"])
            print(f"Error: Package not found ({candidate_names})!")
            print(f"Run 'python3 tools/build_picovave.py --board {board_key}' first.")
        return 1

    app_bin = info["app_bin"]
    app_len = info["app_len"]

    try:
        pkg_disp = str(pkg.relative_to(SRC))
    except ValueError:
        try:
            pkg_disp = f"~/{pkg.relative_to(Path.home())}"
        except ValueError:
            pkg_disp = pkg.name
    print(f"=== {board_name} mask-ROM UBOOT Recovery ===")
    
    # 1. Prepare flash binary
    print(f"[*] Preparing binary from {pkg_disp}...")
    res = subprocess.run([sys.executable, str(SRC / "tools" / "make_app_area.py"), str(pkg), "-o", str(app_bin)],
                         capture_output=True, text=True)
    if res.returncode != 0:
        print(res.stderr)
        return 1
    print(res.stdout.strip())

    if not app_bin.exists():
        print(f"Error: Failed to generate {app_bin.relative_to(SRC)}")
        return 1

    actual_len = len(app_bin.read_bytes())
    # Determine flash start address: official stock restores write from 0x00000; picovave from 0x04000
    sys.path.insert(0, str(SRC / "tools"))
    import fwsc
    pkg_info = fwsc.parse(pkg.read_bytes())
    product = pkg_info.get("product", "")
    is_official = not any(tag in product for tag in ("_700", "_900", "picovave"))
    start_addr = 0x00000 if (custom_pkg and is_official) else 0x04000

    expected_sha = hashlib.sha256(app_bin.read_bytes()).hexdigest()
    print(f"[*] Flash binary: {actual_len} bytes (0x{actual_len:X}) at 0x{start_addr:05X}, SHA256: {expected_sha}")

    # 2. Check root permissions on macOS
    if sys.platform == "darwin" and os.geteuid() != 0:
        print("\n[!] On macOS, accessing the raw USB UBOOT device requires root privileges.")
        print("Please re-run this script with sudo:")
        print(f"    sudo .venv-ota/bin/python tools/recover_{board_key}.py" + (f" --pkg {pkg_disp}" if custom_pkg else ""))
        print(f"    (or: sudo .venv-ota/bin/python tools/recover_uboot.py --board {board_key}" + (f" --pkg {pkg_disp})\n" if custom_pkg else ")\n"))
        return 1

    # 3. Execute flashing via jluboottool
    print(f"[*] Connecting to JieLi UBOOT device and flashing 0x{start_addr:05X}..0x{start_addr+actual_len:05X}...")
    cmd = [
        sys.executable,
        str(UBOOT_TOOL),
        "--chip", "wl82",
        f"write 0x{start_addr:X} {app_bin}",
        f"read 0x{start_addr:X} 0x{actual_len:X} {CHECK_BIN}",
        "reset"
    ]

    res = subprocess.run(cmd)
    if res.returncode != 0:
        print(f"\n[!] Flashing failed with return code {res.returncode}")
        return res.returncode

    # 4. Verify read-back binary
    if CHECK_BIN.exists():
        actual_sha = hashlib.sha256(CHECK_BIN.read_bytes()).hexdigest()
        CHECK_BIN.unlink(missing_ok=True)
        if actual_sha != expected_sha:
            print(f"\n[!] VERIFY FAILED: flash read-back hash mismatch!")
            print(f"    Expected: {expected_sha}")
            print(f"    Actual:   {actual_sha}")
            return 1
        print("\n[+] Verification successful: 100% byte-for-byte match!")

    print(f"\n[+] {board_name} recovery completed! The device has been reset into picovave.")
    return 0

def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--board", choices=["smk37", "fm1"], default="smk37",
                        help="Target board to recover: smk37 (SMK-37 Pro) or fm1 (FM-1) [default: smk37]")
    parser.add_argument("--pkg", type=Path, default=None,
                        help="Path to custom .fwsc package (e.g. ~/Downloads/FM-1.fwsc) [default: build target]")
    args = parser.parse_args()
    return recover(args.board, custom_pkg=args.pkg)

if __name__ == "__main__":
    sys.exit(main())
