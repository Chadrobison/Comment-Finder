#!/bin/bash
# Native CI-only build. Tests are synthetic; no Instagram request/session is used.
set -euo pipefail
cd -- "$(dirname -- "$0")/.."
mkdir -p release
python - <<'PY'
import platform, sys, tkinter
assert sys.platform == 'darwin', 'Build requires macOS'
assert platform.machine() == 'arm64', 'Build requires native Apple silicon'
root = tkinter.Tk()
root.update()
print('Native Tk initialized:', root.tk.call('info', 'patchlevel'))
root.destroy()
PY
python -m unittest discover -s tests -v 2>&1 | tee release/offline-tests.log
python -m compileall -q app.py core.py source.py tests
python -m PyInstaller --noconfirm --clean --windowed --target-arch arm64 \
  --name 'Comment Finder' --osx-bundle-identifier local.commentfinder.desktop app.py \
  2>&1 | tee release/pyinstaller.log
APP_PATH="$PWD/dist/Comment Finder.app"
APP_EXECUTABLE="$APP_PATH/Contents/MacOS/Comment Finder"
export APP_PATH APP_EXECUTABLE
lipo -verify_arch arm64 "$APP_EXECUTABLE"
codesign --verify --deep --strict "$APP_PATH"
# LaunchServices opens the .app as Finder would; smoke mode never starts collection.
python - <<'PY'
import json, os, pathlib, subprocess
report = pathlib.Path('release/packaged-smoke.json').resolve()
report.unlink(missing_ok=True)
subprocess.run(['open', '-n', '-W', os.environ['APP_PATH'], '--args', '--smoke-test', str(report)],
               check=True, timeout=90)
assert report.is_file(), 'Packaged app did not create its smoke report'
data = json.loads(report.read_text())
assert data.get('status') == 'passed', data
assert data.get('frozen') is True, data
assert data.get('platform') == 'Darwin', data
assert data.get('architecture') == 'arm64', data
print(json.dumps(data, indent=2))
PY
python - <<'PY'
import importlib.metadata, json, os, pathlib, platform, subprocess, sys, tkinter
report = {
    'status': 'passed',
    'commit': os.environ.get('GITHUB_SHA', 'local'),
    'workflow_run': os.environ.get('GITHUB_RUN_ID', 'local'),
    'macos': platform.mac_ver()[0],
    'architecture': platform.machine(),
    'python': sys.version,
    'tk': tkinter.TkVersion,
    'instaloader': importlib.metadata.version('instaloader'),
    'pyinstaller': importlib.metadata.version('pyinstaller'),
    'offline_tests': 'passed; see offline-tests.log',
    'packaged_app_smoke': 'passed; see packaged-smoke.json',
    'live_instagram_tested': False,
    'real_session_loaded': False,
    'signing': 'ad-hoc; no Developer ID identity or Apple notarization',
}
pathlib.Path('release/verification.json').write_text(json.dumps(report, indent=2) + '\n')
PY
cat > release/README-MAC.txt <<'TXT'
Comment Finder — Apple-silicon Mac app

Unzip Comment-Finder-macOS-arm64.zip in Finder. Drag Comment Finder.app to
Applications, open it, and optionally add it to your Dock. No Python installation
or typed Terminal commands are needed for this packaged app.

Built/tested on macOS 15 and Apple silicon. Older macOS versions and Intel Macs
are not verified. The app is ad-hoc signed, not Developer ID signed or notarized.
If macOS blocks it, review the source and use the normal per-app Open Anyway
approval in System Settings > Privacy & Security only if you trust this app.
Do not disable Gatekeeper or remove quarantine attributes. If per-app approval
is unavailable, stop and report the displayed error.

Offline tests and a packaged-app smoke check passed. No real Instagram searches,
login, account collection, or real sessions were used. Live collection remains
unverified. Instaloader 4.15.1 requires a logged-in session for comment retrieval.
Only use your own trusted existing session: malicious session files can execute
code. The app never searches for browser cookies or stores passwords.

Enter author A in Whose comments? and account B in Search comments on whose posts?.
Advanced Settings contains limits and optional session-file selection.
Results only describe material checked, never a complete history. Cancellation
preserves collected results and may wait for an active network request to finish.
Results and coverage are saved to Documents/Comment Finder Results.
TXT
ditto -c -k --sequesterRsrc --keepParent "$APP_PATH" release/Comment-Finder-macOS-arm64.zip
(cd release && shasum -a 256 Comment-Finder-macOS-arm64.zip > SHA256SUMS.txt)
