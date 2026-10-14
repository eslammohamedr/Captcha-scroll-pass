"""Build script to compile OPPO Verification GUI into a standalone Windows .exe using PyInstaller."""

import subprocess
import sys
from pathlib import Path

def build() -> None:
    print("=" * 70)
    print("Starting Standalone .EXE Compilation via PyInstaller...")
    print("=" * 70)

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconsole",
        "--onefile",
        "--name",
        "OPPO_Checker",
        "--collect-all",
        "playwright",
        "--hidden-import",
        "openpyxl",
        "--hidden-import",
        "cv2",
        "--hidden-import",
        "pandas",
        "--hidden-import",
        "numpy",
        "--hidden-import",
        "requests",
        "--hidden-import",
        "tqdm",
        "--add-data",
        "check_oppo_cv.py;.",
        "--add-data",
        "slider_cv.py;.",
        "gui_app.py",
    ]

    print("Running command:")
    print(" ".join(cmd))
    print("-" * 70)

    proc = subprocess.run(cmd)

    if proc.returncode == 0:
        exe_path = Path("dist") / "OPPO_Checker.exe"
        print("\n" + "=" * 70)
        print(f"SUCCESS! Standalone Executable created at:")
        print(f"   {exe_path.resolve()}")
        if exe_path.exists():
            print(f"   File size: {exe_path.stat().st_size / (1024*1024):.2f} MB")
        print("=" * 70)
    else:
        print(f"\n[ERROR] PyInstaller failed with exit code: {proc.returncode}")
        sys.exit(proc.returncode)

if __name__ == "__main__":
    build()
