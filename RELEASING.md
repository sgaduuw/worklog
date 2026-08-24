<!-- process-version: 2 -->
# Releasing worklog

Derived from [Release process](https://i3dnet.atlassian.net/wiki/spaces/METAL/pages/1766227974),
process version 2. That page is the source of truth and wins on any disagreement. This
file carries the repo-specific parts and the places worklog genuinely cannot follow it.

## Where this repo deviates, deliberately

`sgaduuw/worklog` is a personal public repo with a single author, not an `i3dnet` one.

- **No second approver and no codeowner.** The process puts the human gate on the
  release pull request into `main`, and GitHub will not let an author approve their
  own. That gate cannot exist here, so the audit pass below is not optional and the
  tag message is written before the merge rather than after.
- **No `develop` and no release branches.** Work lands on `main` through short-lived
  branches. Batching buys a one-author repo nothing, and the org-wide ruleset that
  makes the two-branch model necessary does not reach this repo. "Cut a release
  branch" collapses into "tag the commit on `main`".
- **No `CHANGELOG.md`.** The version history is the `## Version history` section of
  `README.md`, curated by milestone rather than by commit.
- **Tags are two-component**, `v0.4`, not `v0.4.0`, matching the tags already pushed.

All four non-negotiables still hold: an annotated tag on `main`, the tag message is the
release note, a release is a decision rather than a merge side effect, and nothing that
changes the diff happens after a review is requested.

## The slots

| Slot | worklog |
| --- | --- |
| Check command, before every push | `ruff check . && python3 test_wl.py` |
| Automated review pass on a pull request | a `/ponytail-review` pass over the diff |
| Audit pass | a `/ponytail-audit` pass over the repo |
| Required CI checks, and what cannot run locally | `.github/workflows/ci.yml` runs the same two commands; nothing needs a runner a laptop lacks |
| Does the tag publish an artifact | No. The tag is the only record |
| Tag and branch naming | `vX.Y`; no release branches |
| Where the version is stamped | Nowhere in code. `README.md`'s version history is the only statement, so it and the tag are cut together |
| Which repo pins this one | None. It is copied into place, not installed |
| Deploy command and ordering | None. `WORKLOG_ROOT` points at the log; the tool runs from the checkout |
| Repo-specific traps | Below |

## Cutting a release

1. Compare this file's `process-version` marker against the page. They must match.
2. `ruff check . && python3 test_wl.py` on `main`, both clean.
3. `/ponytail-audit` over the repo. Surface the findings, do not auto-apply, and do not
   let cosmetic ones block a release.
4. Decide the version. Below `1.0.0` a minor bump may change the on-disk format, and
   this tool's on-disk format is somebody's work log.
5. Add the milestone to `README.md`'s version history, describing behaviour rather than
   commits. Push, and let CI go green.
6. Tag `main`, annotated, with the message in a file so it is reviewable first:

   ```sh
   git tag -a v0.5 -F /tmp/tag-message.txt && git push origin v0.5
   ```

7. Confirm the tag is on the remote and annotated rather than lightweight:

   ```sh
   git ls-remote --tags origin | grep v0.5
   git cat-file -t "$(git rev-parse v0.5)"   # must print `tag`, not `commit`
   ```

## Traps this repo has hit

- **The README announced 0.5 with no tag behind it**, for about an hour on 2026-08-23.
  The version history is the only place a version is stated, so editing it is the
  announcement. Tag in the same sitting, or mark the section unreleased.
- **The lint gate was environmental until 2026-08-23.** `ruff.toml` pins the ruleset and
  `ci.yml` pins the ruff version. If a finding shows up locally that CI does not see, or
  the reverse, that pinning has drifted and the gate means nothing.
- **A release can move the renderer, and `work_log.md` lives outside the repo** with no
  version history of its own. The export is derived now, not the source of record, so
  do not verify against the live log: that would mean running the rescue path
  (`wl import --force`) against the one database that matters. Before tagging any
  change to `parse_markdown` or the `render_*` functions, verify against a copy:

  ```sh
  D=$(mktemp -d)
  cp "${WORKLOG_ROOT:-$HOME/.local/share/worklog}/work_log.md" "$D/"
  WORKLOG_ROOT=$D python3 wl import          # note the entry count
  WORKLOG_ROOT=$D python3 wl render
  WORKLOG_ROOT=$D python3 wl import --force  # must print the same count
  rm -rf "$D"
  ```

  Both counts must equal the live log's, and neither run may report a dropped line.
  The `cp` is the whole check: without it the scratch root holds nothing, both counts
  are zero, and the recipe passes no matter what the renderer does.
