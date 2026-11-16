# Comment Finder — start here

A local desktop app for finding one account’s comments and replies on another account’s posts. It runs directly on your Apple-silicon Mac. No subscription, API purchase, cloud account, or virtual machine is needed.

## Download the Mac app

**[Download the latest Comment Finder for Apple silicon](https://github.com/Chadrobison/Comment-Finder/releases/latest/download/Comment-Finder-macOS-arm64.zip)**

[Release notes and verification reports](https://github.com/Chadrobison/Comment-Finder/releases/latest). The instructions below describe version 1.1.1; check the release version when downloading.
Choose **Comment-Finder-macOS-arm64.zip** from the release assets, unzip it, and drag **Comment Finder.app** into Applications.
The packaged app includes Python and dependencies; no build commands or Python installation are needed.
It targets Apple-silicon Macs with macOS 15 or newer. It is ad-hoc signed, not Apple-notarized; macOS may require its normal per-app Open Anyway approval.

## Source-code setup (alternative)

1. Unzip the download in Finder. Move the **CommentFinder** folder somewhere permanent, such as your Documents folder. Keep its files together.
2. Install **Python 3.13** using the **macOS installer** from [python.org](https://www.python.org/downloads/macos/). Open the downloaded installer and follow its steps. This supplies the desktop window toolkit as well as Python. A Python installation without Tk will not work.
3. Double-click **Open Comment Finder.command** inside the folder. The first launch opens Terminal and installs the free Instaloader dependency into a private `.venv` folder alongside the app. Allow it to finish; then the app window opens. This installation needs internet access, but does not contact Instagram or log in.
4. For later use, double-click the same launcher. You do not need to type any commands. Keep the Terminal window open while using this launcher.

The launchers are unsigned local scripts. If macOS blocks opening one, inspect the included source and use macOS’s normal **Open** / **Open Anyway** approval for this specific file only if you trust it. Depending on macOS version, that option is in **System Settings → Privacy & Security** after an attempted open. Do not disable Gatekeeper, remove quarantine attributes, or turn off other security protections. If no per-file approval is available, stop and retain the displayed error for troubleshooting.

## Make a Finder/Dock app

After installing Python, double-click **Build Mac App.command**. It performs dependency setup automatically and builds a native Apple-silicon **Comment Finder.app**. This may take several minutes and needs internet to download PyInstaller the first time. When Finder opens the `dist` folder, drag **Comment Finder.app** into **Applications**. Open it there, and drag it to the Dock if desired. Future launches of that built app do not need Terminal or the source folder.

This alternative source build must run on your Mac. The downloadable release was built and smoke-tested on an Apple-silicon macOS runner; it is ad-hoc signed but not Developer ID signed or Apple-notarized. The source build remains available if you prefer to build locally. The generated app is for Apple silicon, not Intel Macs. Building with Python running under Rosetta is intentionally rejected.

## Updating from version 1.1.0

Version 1.1.1 updates Instaloader to 4.16 and includes a narrowly scoped metadata request correction after a reported HTTP 400 search failure. The correction comes from an upstream Instaloader change that is still under review; it is not yet part of an official Instaloader release. Quit the old app, download the new ZIP, and replace **Comment Finder.app** in Applications. Your result folders and app-managed remembered session are stored separately and are not replaced. If you use the source launcher instead, it updates the private dependency environment on its next launch.

This update has no live-success guarantee: Instagram can still reject requests or provide incomplete data. If a search fails, the message now identifies the stage (such as profile lookup, post listing, comments or replies), request category, HTTP status and a recognized response signal when available. An HTTP 400 alone does not tell us whether the cause is an API change or an Instagram restriction. If collection stops before examining any comments or replies, the app says so explicitly; it cannot determine whether the account commented. Keep `coverage.csv` and `status.json` from that search; avoid repeatedly starting the same failed search. Development tests use synthetic data only.

## Log in inside the app

Comment retrieval requires a logged-in session with the pinned Instaloader version. You do **not** need to create a session in Terminal.

1. Click **Log in to Instagram…** near the top of the app.
2. Enter **your own Instagram username and password**. This is the account you normally use to access Instagram; it can be different from either account you want to search. The password field is masked and your password is never saved to disk.
3. Leave **Remember this session on this Mac** off to keep the login only for this app session. Turn it on only if you want the app to restore your login next time.
4. Click **Log in**. If Instagram requests two-factor authentication, enter the **Instagram verification code** in the next dialog. The app shows **Logged in as @username** after success.

Remembered session data is sensitive authentication information, saved at `~/Library/Application Support/Comment Finder/session.json` as JSON with user-only file permissions (0600) inside a private folder (0700), not as a password. The app reads only its own saved session on startup; it never searches browser cookies or other apps’ credentials. **Log out** clears the current login and the app’s remembered copy. It does **not** revoke the session on Instagram or log out your other devices. Use Instagram’s own account-security settings if you need server-side session revocation.

Instagram may reject login, require a challenge that this app cannot complete, or rate-limit requests. Follow the message and stop rather than repeatedly retrying. The app does not bypass CAPTCHA or access restrictions. Live login and two-factor authentication remain unverified; development tests use mocked responses only.

## Your first search

1. In **Whose comments?**, enter the author’s username, with or without `@`.
2. In **Search comments on whose posts?**, enter the post owner’s username.
3. Optionally select **Also search their own posts**.
4. Click **Start Search**. Progress shows accounts, posts, examined comments/replies, and matches without guessing a percentage.
5. Select a result to read its full text. **Open Post** opens your browser; text and type filters narrow the table. **Export Results** exports the currently shown rows.

**Cancel** keeps results already collected. An active network request or library pause may need to finish first; network requests have a 30-second timeout, which is not a guaranteed total cancellation deadline. Closing the window during a search requests cancellation and waits for the worker to finish.

## Where results go

Click **Open Results Folder**. Each search has its own dated folder inside **Documents → Comment Finder Results**, containing:

- `matching_comments.csv`: all collected matches, including dates, text, author, post owner/link, comment ID and parent-comment ID.
- `coverage.csv`: what was checked, skipped, interrupted, or limited.
- `status.json`: the run’s status, settings, Instaloader version and metadata-routing correction status. `unfinished` means the run did not record a final outcome, for example after a crash.

CSV cells that could be interpreted as spreadsheet formulas are prefixed with an apostrophe. This is intentional; original comment text remains visible in the app. Exports are snapshots; exporting during a search does not include later matches.

Results describe **only the material checked**. An empty table does not mean the account has never commented. Deleted, hidden, inaccessible, or unreturned material can be missing even if no limit or error is reported. The pinned library may return only one page of replies for some posts; a finished search is never a guarantee that every reply was available.

## Advanced Settings

The default limits examine at most **100 posts per account**, **500 parent comments per post**, **100 replies per parent comment**, and **10,000 comments and replies overall**. They limit examined material, not the number of matches. Reaching a limit is reported conservatively even if it happened to coincide with the last available item.

The normal login button handles authentication in the app. If you already have **your own trusted legacy Instaloader session file**, Advanced Settings still lets you select it and enter its login username instead. Selecting a legacy file takes precedence over the in-app login for the next search; a new in-app login clears that legacy selection. Legacy Instaloader files contain sensitive authentication information and can **execute code when loaded if malicious**. Never load one from an untrusted source. This warning applies to legacy session files, not the app’s JSON format. Neither kind of session grants access you do not already have.

Instagram may require authentication, deny access, block requests, or change its responses. The app stops on those errors or rate limiting and saves partial results where possible. It does not automatically retry a blocked search. Offline tests are not evidence that live Instagram login or collection works; live testing is yours to perform.

See **PROJECT.md** for architecture, decisions, verification details, and remaining limitations.
