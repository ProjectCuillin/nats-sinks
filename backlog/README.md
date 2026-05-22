# Local Backlog Items

GitHub Issues are the live backlog for `nats-sinks`. This directory is an
optional staging area for maintainers who want to define backlog items locally
before syncing them to GitHub.

Backlog item files belong in `backlog/items/*.json`. Each file is validated and
synced by:

```bash
python scripts/sync-backlog-issues.py --dry-run
python scripts/sync-backlog-issues.py
```

The sync script uses the GitHub CLI (`gh`). It creates or updates GitHub Issues
using a hidden backlog identifier in each issue body, so reruns are
idempotent. Closed issues are not reopened unless `--reopen-closed` is passed.
Each item should include `target_release`, using `unscheduled` until the work
is assigned to a concrete release tag.

Use the comment helper for implementation notes and release-gated close-out
comments:

```bash
python scripts/comment-backlog-issue.py --backlog-id example-backlog-item --release v0.4.0 --status started --comment-file .local/backlog-comment.md --dry-run
```

The canonical workflow is documented in
[Backlog Management](https://nats-sinks.readthedocs.io/en/latest/backlog-management/).

Do not put secrets, server addresses, credentials, certificate material,
payloads, Oracle wallet details, live network locators, IP literals, or private
operational context in backlog files or backlog comments. These files are
intended to be committed and synced into public GitHub Issues.
