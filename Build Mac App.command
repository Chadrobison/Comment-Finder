#!/bin/bash
set -e
cd -- "$(dirname -- "$0")"
trap 'echo "Build failed. Read the error above. Press Return to close."; read -r' ERR
source ./setup_mac.sh
.venv/bin/python -c 'import platform; assert platform.machine() == "arm64", "Please use the native Apple-silicon Python installer and run Terminal without Rosetta."'
.venv/bin/python -m pip install 'pyinstaller==6.16.0'
.venv/bin/python -m PyInstaller --noconfirm --clean --windowed --target-arch arm64 --name 'Comment Finder' --osx-bundle-identifier local.commentfinder.desktop app.py
open dist
echo 'Build complete. Drag Comment Finder.app from this folder into Applications. You can then add it to the Dock.'
read -r -p 'Press Return to close.'
