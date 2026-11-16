#!/bin/bash
# Sourced by the two Finder launchers; installs only inside this project folder.
if [ "$(uname -s)" != Darwin ]; then
  echo 'These Finder launchers are for macOS. No virtual machine is needed.'
  return 1
fi
PYTHON=""
for candidate in /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 /Library/Frameworks/Python.framework/Versions/3.12/bin/python3 /opt/homebrew/bin/python3; do
  if [ -x "$candidate" ] && "$candidate" -c 'import tkinter, sys; assert (3, 10) <= sys.version_info[:2] <= (3, 13)' 2>/dev/null; then
    PYTHON="$candidate"
    break
  fi
done
if [ -z "$PYTHON" ]; then
  echo 'Install Python 3.13 for macOS from https://www.python.org/downloads/macos/ (the macOS 64-bit universal2 installer), then reopen this file.'
  open 'https://www.python.org/downloads/macos/'
  return 1
fi
if [ ! -x .venv/bin/python ]; then
  "$PYTHON" -m venv .venv || return 1
fi
.venv/bin/python -c 'import tkinter, sys; assert (3, 10) <= sys.version_info[:2] <= (3, 13)' || return 1
if ! .venv/bin/python -c 'import instaloader; assert instaloader.__version__ == "4.16"' 2>/dev/null; then
  .venv/bin/python -m pip install -r requirements.txt || return 1
fi
