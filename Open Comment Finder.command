#!/bin/bash
set -e
cd -- "$(dirname -- "$0")"
trap 'echo "Setup or launch failed. Read the error above. Press Return to close."; read -r' ERR
source ./setup_mac.sh
.venv/bin/python app.py
