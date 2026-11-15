"""Bounded search for author A's comments on A's and selected accounts' posts.

Uses documented Instaloader interfaces; not executed or live-tested. Instagram
may require authentication or reject requests. This is not a complete history.
"""
import argparse
import csv
from datetime import timezone
import os
import sys
import tempfile

import instaloader

FIELDS = ["author_username", "post_owner", "post_id", "post_shortcode", "post_url",
          "comment_id", "parent_comment_id", "kind", "timestamp_unix", "timestamp_utc", "text"]
COVERAGE_FIELDS = ["account", "scope", "item_id", "status", "examined", "detail"]


def normalize(name):
    return name.strip().lstrip("@").casefold()


def save_csv(rows, filename, fields):
    destination = os.path.abspath(filename)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", newline="", encoding="utf-8",
                                         dir=os.path.dirname(destination), prefix=".instagram-",
                                         suffix=".tmp", delete=False) as file:
            temporary = file.name
            writer = csv.DictWriter(file, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temporary, destination)
        temporary = None
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


class Collector:
    def __init__(self, args, loader):
        self.args, self.loader = args, loader
        self.matches, self.coverage, self.seen_comments = [], [], set()
        self.examined = 0
        self.comments_file = os.path.join(args.output_dir, "matching_comments.csv")
        self.coverage_file = os.path.join(args.output_dir, "coverage.csv")

    def save_matches(self):
        rows = sorted(self.matches, key=lambda row: (row["post_owner"], row["post_id"],
                                                     row["timestamp_utc"], row["comment_id"]))
        save_csv(rows, self.comments_file, FIELDS)

    def record(self, account, scope, item_id, status, examined=0, detail=""):
        self.coverage.append(dict(account=account, scope=scope, item_id=item_id,
                                  status=status, examined=examined, detail=detail))
        save_csv(self.coverage, self.coverage_file, COVERAGE_FIELDS)

    def walk(self, factory, limit, visit):
        """Bound iterator consumption; reaching the cap conservatively marks a gap."""
        if self.examined >= self.args.max_total_comments:
            return "global_capped", 0, "Whole-run comment/reply examination limit reached"
        try:
            iterator = iter(factory())
        except Exception as error:
            return "failed", 0, str(error)[:600]
        for count in range(limit):
            if self.examined >= self.args.max_total_comments:
                return "global_capped", count, "Whole-run comment/reply examination limit reached"
            try:
                item = next(iterator)
            except StopIteration:
                return "iterator_exhausted", count, "Iterator ended; not proof of complete historical coverage"
            except Exception as error:
                return "failed", count, str(error)[:600]
            visit(item)
        return "capped", limit, "Configured limit reached; additional items were not checked"

    def match(self, comment, account, post, parent_id=""):
        self.examined += 1
        try:
            comment_id = str(comment.id)
            author = comment.owner.username
            if not isinstance(author, str):
                raise ValueError("Missing author username")
            if normalize(author) != self.args.username:
                return
            key = (str(post.mediaid), comment_id)
            if key in self.seen_comments:
                return
            moment = comment.created_at_utc
            moment = moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment.astimezone(timezone.utc)
            row = {"author_username": author, "post_owner": post.owner_username,
                   "post_id": str(post.mediaid), "post_shortcode": post.shortcode,
                   "post_url": f"https://www.instagram.com/p/{post.shortcode}/",
                   "comment_id": comment_id, "parent_comment_id": parent_id,
                   "kind": "reply" if parent_id else "parent_comment",
                   "timestamp_unix": int(moment.timestamp()), "timestamp_utc": moment.isoformat(),
                   "text": comment.text}
        except Exception as error:
            self.record(account, "comment", "", "skipped", 1, "Could not inspect comment: " + str(error)[:500])
            return
        self.seen_comments.add(key)
        self.matches.append(row)
        self.save_matches()

    def collect_post(self, account, post):
        try:
            post_id = str(post.mediaid)
        except Exception as error:
            self.record(account, "post", "", "skipped", detail=str(error)[:600])
            return

        def visit_parent(comment):
            self.match(comment, account, post)
            # Inspect replies even when their parent's author does not match A.
            try:
                parent_id = str(comment.id)
            except Exception as error:
                self.record(account, "replies", post_id, "skipped", detail=str(error)[:600])
                return
            status, count, detail = self.walk(lambda: comment.answers, self.args.max_replies,
                                              lambda reply: self.match(reply, account, post, parent_id))
            self.record(account, "replies", parent_id, status, count, detail)

        status, count, detail = self.walk(post.get_comments, self.args.max_comments, visit_parent)
        self.record(account, "parent_comments", post_id, status, count, detail)

    def collect_account(self, account):
        if self.examined >= self.args.max_total_comments:
            self.record(account, "posts", "", "skipped", detail="Whole-run limit already reached")
            return
        try:
            profile = instaloader.Profile.from_username(self.loader.context, account)
        except Exception as error:
            self.record(account, "lookup", "", "failed", detail=str(error)[:600])
            self.record(account, "posts", "", "skipped", detail="Account lookup failed")
            return
        seen_posts = set()

        def visit_post(post):
            try:
                post_id = str(post.mediaid)
            except Exception as error:
                self.record(account, "post", "", "skipped", detail=str(error)[:600])
                return
            if post_id in seen_posts:
                self.record(account, "post", post_id, "skipped", detail="Duplicate post returned")
                return
            seen_posts.add(post_id)
            self.collect_post(account, post)

        status, count, detail = self.walk(profile.get_posts, self.args.max_posts, visit_post)
        self.record(account, "posts", account, status, count, detail)


def positive_int(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("username", help="Author A whose comments should match")
    parser.add_argument("--accounts", nargs="+", default=[], help="Other post-owner accounts, e.g. B C")
    parser.add_argument("--exclude-self", action="store_true", help="Search only the other accounts' posts")
    parser.add_argument("--max-posts", type=positive_int, default=100, help="Per-account post limit (default 100)")
    parser.add_argument("--max-comments", type=positive_int, default=500, help="Per-post parent-comment limit (default 500)")
    parser.add_argument("--max-replies", type=positive_int, default=100, help="Per-parent reply limit (default 100)")
    parser.add_argument("--max-total-comments", type=positive_int, default=10000,
                        help="Whole-run limit of parent comments plus replies examined (default 10000)")
    parser.add_argument("--output-dir", default="instagram_comment_results",
                        help="Output directory; existing result CSVs there are overwritten")
    parser.add_argument("--login-user", help="Owner of an existing trusted Instaloader session")
    parser.add_argument("--session-file", help="Path to YOUR trusted existing session file; paired with --login-user")
    args = parser.parse_args()
    args.username = normalize(args.username)
    names = ([] if args.exclude_self else [args.username]) + args.accounts
    accounts = list(dict.fromkeys(normalize(value) for value in names))
    if not args.username or not accounts or any(not name for name in accounts):
        parser.error("Provide nonempty usernames and at least one post-owner account")
    if bool(args.login_user) != bool(args.session_file):
        parser.error("--login-user and --session-file must be supplied together")
    print("NOT LIVE-TESTED. Instagram may require authentication or reject requests. Results are NOT a complete history.")
    loader = instaloader.Instaloader(sleep=True, max_connection_attempts=1, request_timeout=30,
                                    fatal_status_codes=[429], download_pictures=False,
                                    download_videos=False, save_metadata=False)
    if args.session_file:
        try:
            loader.load_session_from_file(args.login_user, args.session_file)
        except Exception as error:
            print(f"Could not load explicitly supplied session: {error}. Output files were not changed.")
            return 1
    collector = Collector(args, loader)
    try:
        os.makedirs(args.output_dir, exist_ok=True)
        collector.save_matches()
        collector.record("", "run", "", "started", detail="Bounded scan; not complete historical coverage")
        for account in accounts:
            print(f"Inspecting posts owned by {account}; matching author {args.username}")
            collector.collect_account(account)
        incomplete = any(row["status"] in {"failed", "skipped", "capped", "global_capped"}
                         for row in collector.coverage)
        collector.record("", "run", "", "finished_with_gaps" if incomplete else "bounded_scan_finished",
                         collector.examined, "Consult per-scope records; no completeness claim")
        print(f"Saved {len(collector.matches)} matches. Read coverage.csv for failures and limits.")
        return 1 if incomplete else 0
    except KeyboardInterrupt:
        print("Interrupted. Completed atomic checkpoints remain; active scopes may be unfinished.")
        try:
            collector.record("", "run", "", "interrupted", collector.examined,
                             "Active scopes may not have final coverage records")
        except (OSError, csv.Error):
            pass
        return 130
    except (OSError, csv.Error) as error:
        print(f"Output error: {error}. Last completed checkpoints remain; run incomplete.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
