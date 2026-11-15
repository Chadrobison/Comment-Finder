# Comment Finder — start here

A local desktop app for finding one account’s comments and replies on another account’s posts. It runs directly on your Apple-silicon Mac. No subscription, API purchase, cloud account, or virtual machine is needed.

## Download the Mac app

The native Apple-silicon build passed its [Mac verification](https://github.com/Chadrobison/Comment-Finder/actions/runs/43772894983).
**[Download Comment Finder v1.0.0 for Apple silicon](https://github.com/Chadrobison/Comment-Finder/releases/download/v1.0.0/Comment-Finder-macOS-arm64.zip)**

[Release notes and verification reports](https://github.com/Chadrobison/Comment-Finder/releases/tag/v1.0.0).
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
- `status.json`: the run’s status and settings. `unfinished` means the run did not record a final outcome, for example after a crash.

CSV cells that could be interpreted as spreadsheet formulas are prefixed with an apostrophe. This is intentional; original comment text remains visible in the app. Exports are snapshots; exporting during a search does not include later matches.

Results describe **only the material checked**. An empty table does not mean the account has never commented. Deleted, hidden, inaccessible, or unreturned material can be missing even if no limit or error is reported. The pinned library may return only one page of replies for some posts; a finished search is never a guarantee that every reply was available.

## Advanced Settings

The default limits examine at most **100 posts per account**, **500 parent comments per post**, **100 replies per parent comment**, and **10,000 comments and replies overall**. They limit examined material, not the number of matches. Reaching a limit is reported conservatively even if it happened to coincide with the last available item.

The session fields are optional, but **the pinned Instaloader 4.15.1 requires a logged-in session to retrieve comments**. Without one, the app can report that authentication is required rather than returning comment results. To collect comments, choose **your own existing, trusted Instaloader session file** and enter its login username. The app does not find browser cookies, create sessions, or store passwords. Session files contain sensitive authentication information; a malicious Instaloader session file can **execute code when loaded**. Never load one from an untrusted source. A session cannot grant access you do not already have.

Instagram may require authentication, deny access, block requests, or change its responses. The app stops on those errors or rate limiting and saves partial results where possible. It does not automatically retry a blocked search. Offline tests are not evidence that live Instagram collection works; live testing is yours to perform.

See **PROJECT.md** for architecture, decisions, verification details, and remaining limitations.
