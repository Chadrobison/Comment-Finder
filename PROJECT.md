# Comment Finder — master project document

## Purpose and agreed scope

Build a free, local desktop app around the supplied `instagram_account_comments.py` for an Apple-silicon Mac. Find author A’s comments and replies on account B’s posts, optionally also A’s posts. Preserve author matching, reply scanning beneath nonmatching parents, bounded collection, duplicate prevention, dates, links, atomic checkpoints and honest coverage reporting.

No cloud hosting, analytics, uploads, account discovery, browser-cookie discovery, password storage, login workflow, proxy rotation, CAPTCHA bypass, or speculative GraphQL queries. Original supplied code is retained in `original/` for reference; it is not the app’s entry point.

## Architecture and dependencies

- `app.py`: Tk/ttk desktop interface. Widgets run on the main thread. One worker thread collects data; a queue transfers progress, result and completion events to the UI.
- `core.py`: validation, bounded traversal, author matching, deduplication, filtering, safe CSV export, coverage and atomic file replacement. Source injection supports offline tests.
- `source.py`: Instaloader adapter using documented `Profile.from_username`, `Profile.get_posts`, `Post.get_comments`, `PostComment.answers`, and explicit `load_session_from_file` interfaces. A scoped Requests `Session.send` guard protects outgoing requests, including redirects and temporary sessions, against cancellation and blocked responses. A custom documented `RateController` retains ordinary request pacing while making waits cancellable and preventing 429 retries.
- `tests/`: synthetic offline regression tests. Tests use temporary output folders and mocked transport; they must not contact Instagram or load a real session.
- `setup_mac.sh`: shared Mac-only dependency setup. It changes only this project’s private environment.
- `Open Comment Finder.command`: Finder launcher; no typed commands are required.
- `Build Mac App.command`: automated PyInstaller build for native Apple silicon, opening the resulting `dist` folder.

Runtime: Python 3.10–3.13 with Tk; Python.org’s Python **3.13 universal2 macOS installer** is recommended. Instaloader is pinned to **4.15.1**; Requests and its dependencies are installed through Instaloader. Packaging uses **PyInstaller 6.16.0**, installed only for a Mac app build. Tk, threading, queue, CSV, JSON and file operations come from Python’s standard library. No local web server is needed.

## Implementation decisions

1. Results are written under `~/Documents/Comment Finder Results/<date-time>_<unique-id>/`, so earlier searches are not overwritten. CSVs and status are replaced atomically after writing and flushing a temporary file. Files are individually atomic, not a multi-file transaction. A crash may leave a completed checkpoint newer than another file; `unfinished` and coverage must be consulted.
2. Dates are UTC, including in the results table. Matching is case-insensitive. Input accepts one optional leading `@`; URLs, spaces, invalid characters and invalid limits get actionable validation messages.
3. Limits apply to examined items, including nonmatching and duplicate items. Defaults: 100 posts/account, 500 parent comments/post, 100 replies/parent, 10,000 comments/replies total. A reached cap is conservatively a coverage gap, without fetching another item to prove whether more exist.
4. Replies are inspected independently of the parent’s author. Duplicate matching comment IDs within the same post are suppressed. Duplicate posts are reported as skipped material.
5. No guessed percentages are shown. Progress reports current account/post, separate comment/reply counts and match count. Filtering is local and applies to already collected rows only; exporting saves currently visible rows. Automatic matching CSV checkpoints contain all collected matches.
6. Cancellation sets an event and blocks subsequent transport sends. An active request or Instaloader built-in pause (up to 15 seconds) can finish before cancellation takes effect; the 30-second network timeout is not a total wall-clock guarantee. Closing the window while running requests cancellation and waits for completion.
7. Session fields remain optional, but the pinned Instaloader 4.15.1 `get_comments` implementation requires a logged-in session to retrieve comments. Anonymous searches will report authentication required when that API is reached. Rate-limit, access and authentication responses stop collection. No automatic retry, access bypass or session discovery is performed. Existing results remain available. Session loading requires an explicit selected path, login username and trust confirmation. Credentials/session contents are not copied into reports.
8. Spreadsheet-sensitive text is prefixed with an apostrophe before CSV writing, including leading formula markers after whitespace. Raw text remains in the in-memory UI. CSV safety may affect literal text representation in spreadsheet applications.
9. Missing or malformed data is reported as skipped material where it can be handled locally; unexpected collection errors stop the run. File-write errors are surfaced separately; a failed write may leave newer results only in memory, so the UI can still export them elsewhere.
10. The implementation uses Instaloader’s documented collection interfaces, plus an explicitly scoped transport safety guard. It does not introduce custom GraphQL query IDs. One worker at a time is enforced because the transport guard temporarily wraps the Requests class within the process.

## Installation and launch

Follow **README.md**. In brief: unzip, install Python 3.13 universal2 from Python.org, and double-click **Open Comment Finder.command**. Setup downloads dependencies into `.venv` on first use. To make a Dock app, double-click **Build Mac App.command**, then move `dist/Comment Finder.app` to Applications.

Use macOS’s normal per-file trust approval if required; do not disable security protections. No administrator action is required by these scripts. The Python installer may ask for its normal installation permission. Packaging runs on macOS. GitHub Actions has now built and verified the native `.app` on an Apple-silicon Mac runner; the local development workspace remains Linux. The app is not signed with a developer identity or notarized. No paid Apple account is required for the local build workflow.

## Reading outcomes

| Status | Meaning |
| --- | --- |
| `unfinished` | A run started but no final outcome has been saved. Active scopes may lack final coverage records. |
| `finished` | Bounded iterators ended without a recorded gap; not a complete-history claim. |
| `limits` | A collection cap was reached; additional material was not checked. |
| `gaps` | Some material was skipped. Inspect `coverage.csv`. |
| `cancelled` | User cancellation stopped the run; partial results remain. |
| `authentication` | Login is required or a session expired. |
| `denied` | Access is forbidden or unavailable to the supplied session. |
| `blocked` | Instagram blocked or rate-limited requests; collection stopped. |
| `error` | Network or unexpected collection error; inspect details. |
| `write_error` | Saving failed; only successful checkpoints are guaranteed to remain. |

No matches means no matches in the material actually checked, never proof that the account has never commented.

## Verification and remaining work

Development and tests are restricted to synthetic data and mocked network responses. No real Instagram searches, login, real sessions, or real account collection are authorized for development. Tests are maintained separately in `tests/`; all generated test results belong in temporary folders, not the user’s real results directory.

Release verification completed on 2026-11-15:

- **37 tests passed**: 19 core tests, 9 transport tests and 9 Tk UI tests. The complete command was `DISPLAY=:99 /workspace/.venv/bin/python -m unittest discover -s tests -v`. On a Mac with dependencies installed, `.venv/bin/python -m unittest discover -s tests -v` runs the same suite without DISPLAY.
- Python compilation passed for app, core, source and tests. `bash -n` passed for both launchers and setup_mac.sh.
- The actual Tk window was launched on Linux using synthetic results and visually inspected. The expanded-settings small-window regression passes; the page scrolls so controls remain reachable. Mac native appearance remains unverified.
- Tests cover validation, UTC dates and actual post owners, nonmatching-parent replies, deduplication, scope/global limits, cancellation, preserved and atomic checkpoints, interrupted coverage, storage/network distinctions, CSV formula protection, filters, details, exports, UI errors, new result folders, and guarded HTTP 429/401/403/challenge responses.
- Network connections were blocked during tests. External browser/folder opening was mocked. No real session was loaded and no Instagram search was run.

The requested Builder, Architect, QA Engineer, Scout, Verifier and ReleaseOps roles participated. Their findings were integrated and the complete suite was rerun after fixes.

Still unverified: live Instagram behavior and availability; loading a genuine user session; the user’s own Mac, Dock behavior, and Gatekeeper approval for an internet-downloaded app. Native macOS Tk tests, Apple-silicon packaging, ad-hoc signature verification and LaunchServices launch now pass in CI. These require the user’s Mac and, for live testing, the user’s own decision to run a search. Offline success does not establish live collection success.

Known limitations: the pinned version’s internal iPhone reply path may return only one page of replies, so iterator exhaustion does not establish full reply coverage. Instagram can change or reject its APIs; comments may be hidden/deleted/unreturned; limits and cancellation produce partial coverage; the app does not resume unfinished searches or reload old CSVs into its table. Existing searches remain readable as CSV/JSON files. Searching creates network requests to Instagram only after Start Search; Open Post intentionally opens the chosen public link in the user’s browser. Dependency installation accesses package repositories. No collected data is uploaded by this app.

## Documentation checked

Official Instaloader documentation was retrieved during development on 2026-11-15:

- [Instaloader API](https://instaloader.github.io/module/instaloader.html): construction, timeouts, fatal status codes, explicit session loading.
- [Structures API](https://instaloader.github.io/module/structures.html): profiles/posts, comments, answers, authors, IDs and UTC timestamps.
- [Context and RateController API](https://instaloader.github.io/module/instaloadercontext.html): query pacing and overridable rate-controller callbacks.
- [Troubleshooting](https://instaloader.github.io/troubleshooting.html): rate-limit behavior and incomplete access.

The current documentation also describes behavior newer than 4.15.1. Installed 4.15.1 source is inspected for the transport integration, and the dependency is pinned so a future upstream change does not silently alter this guard. Upgrading Instaloader requires rerunning transport and collection tests and reviewing these assumptions.


## GitHub app delivery

The user authorized GitHub access and delivery of a working packaged app. The user created the public repository https://github.com/Chadrobison/Comment-Finder
and approved publishing the source. The native build and packaged-app smoke test passed.

`.github/workflows/build-macos.yml` and `scripts/build_mac_ci.sh` automate a native
Apple-silicon macOS 15 build with Python 3.13 and PyInstaller 6.16.0. A successful
version-tag build publishes the `.app` ZIP, checksums, and verification reports as
a GitHub Release. The frozen executable supports `--smoke-test REPORT_PATH`,
which opens the actual GUI with synthetic data, forbids networking/session loads,
checks rendering/filtering and exits. No real Instagram collection occurs in CI.
The packaged app will bundle Python, Tk and dependencies; end users will not need
Python or build commands. It is ad-hoc signed, not Developer ID signed/notarized.

The user approved public source publication. The standard public macOS runner is free; no paid or larger runner was used.


### Native release verification

Commit `2a163eec8c3b6f01027dd234702d24d4565d939b` passed native build run
https://github.com/Chadrobison/Comment-Finder/actions/runs/43772894983 on 2026-11-15.
All 40 offline tests passed on macOS with Python 3.13.17 and Tk 8.6.18. PyInstaller
6.16.0 produced an ARM64 app; architecture and ad-hoc signature checks passed.
LaunchServices (`open -W`, the same app launch system used by Finder) opened the
frozen app. Its smoke report confirmed Darwin/arm64/frozen=true, backend and
certificate resources, visible Tk window, queue progress/results, filtering, and
selected-comment details. No Instagram calls or genuine sessions were used.
The first CI attempt exposed a lipo argument-order mistake; it was corrected
before this successful build. Release v1.0.0 repeats these gates before publishing.


### Published release

Release v1.0.0: https://github.com/Chadrobison/Comment-Finder/releases/tag/v1.0.0

Tag workflow https://github.com/Chadrobison/Comment-Finder/actions/runs/43773021166
passed both build and release jobs. It built/tested on macOS 15.8.2 ARM64, ran all
40 tests successfully and launched the frozen app via LaunchServices. The public
release ZIP was downloaded back into the development workspace and validated:
13,486,431 bytes, 420 ZIP entries, ZIP integrity check passed, expected executable
`Comment Finder.app/Contents/MacOS/Comment Finder` present. SHA-256:
`ed33c05cf90a558b2ef3728b658b7e6dfb7d96a10a6bd1003ff1e95f25f25bca`.
The checksum matches the release's SHA256SUMS.txt. Delivery is now a normal HTTPS
GitHub release asset, not a workspace-path link. Live Instagram use remains
unverified and the app is not Apple-notarized.
