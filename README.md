# worklog (`wl`)

![CI](https://github.com/sgaduuw/worklog/actions/workflows/ci.yml/badge.svg)

A tiny SQLite-backed work log. `work_log.db` is the source of record; `work_log.md`
is a generated, human-readable export of it (newest day first), rewritten on every
`wl add`, `wl edit` and `wl rm`. It is never read back automatically: change an
entry with `wl edit`, remove one with `wl rm` (see [Fixing or deleting an
entry](#fixing-or-deleting-an-entry) below). `wl import` exists only to rebuild the
database from the export, and only when you ask for it.

No dependencies beyond the Python 3 standard library. Linting uses ruff, which is a
lint-time tool rather than a runtime one.

## Install

Drop the folder anywhere and run `./wl`. `wl` is a thin entry point that imports
`worklog.py` next to it, so keep the two co-located. See [Where your log
lives](#where-your-log-lives) below for where the log files end up and how to point
them elsewhere.

## Where your log lives

By default `wl` stores the log under the XDG data directory:
`$XDG_DATA_HOME/worklog` if `XDG_DATA_HOME` is set, otherwise `~/.local/share/worklog`.
Point it elsewhere with `WORKLOG_ROOT`:

```sh
export WORKLOG_ROOT="$HOME/notes"
```

`wl` guarantees a correct write to `work_log.db`, not a surviving one: it does not
back the database up, sync it anywhere, or check that the directory is safe. Storage
is your concern, not the tool's. The practical answer is to make `WORKLOG_ROOT` a
directory under version control and commit `work_log.db` (and the generated
`work_log.md`) like anything else you cannot afford to lose.

## Usage

```sh
# add an entry (timestamp defaults to now)
./wl add --slug general --type note "started the migration"

# with ticket refs and a backfilled time
./wl add --slug backend --type pr --ref PROJ-12,PROJ-13 --at 14:30 "opened the PR"
./wl add --slug backend --type ticket --ref PROJ-9 --at 2026-07-01T09:00 "created task"

# read one day
./wl report --day today
./wl report --day 2026-07-01

# roundup: every day in a range, grouped (handy for a standup or biweekly review)
./wl report --since 2026-07-01 --until 2026-07-14
./wl report --since 2026-07-07                    # open-ended: up to the latest day

# search across all history (newest first, 20 entries unless you say otherwise)
./wl log --slug backend
./wl log --ref PROJ-9
./wl log --type decision --since 2026-07-01 --until 2026-07-14
./wl log --slug backend -n 5     # newest 5
./wl log --slug backend -n 0     # no cap; the whole history

# count instead of list (same filters as `log`; see below)
./wl stats
./wl stats --since 2026-07-01 --top 3
./wl stats --slug backend --when

# fix or delete an entry by id (ids are the first column of `wl log`)
./wl edit 42 --body "opened the PR, not just drafted it"
./wl edit 42 --slug backend --type pr --ref PROJ-12
./wl rm 42

# regenerate work_log.md from the DB, or rebuild the DB from the markdown (rescue path)
./wl render
./wl import
./wl import --force   # allow rebuilding over a database that already holds entries

# manage project slugs (see below)
./wl slug ls
./wl slug add backend
./wl slug rm backend
```

### Fields

- `--slug`: project bucket, one word with no whitespace (it becomes a markdown
  heading that has to read back). `general` is the one built-in slug. Register your own
  with `wl slug add` (see [Managing slugs](#managing-slugs)). An unregistered slug
  still logs, but warns and sorts after the registered ones.
- `--type`: one of `ticket`, `pr`, `idea`, `decision`, `blocker`, `note`.
- `--ref` (optional): comma-separated keys, e.g. `PROJ-12,PROJ-13`. On `wl log` and
  `wl stats`, `--ref` matches whole keys in this column *and* in the entry body, so a
  ticket named only in prose is still found. `PROJ-1` never matches `PROJ-10`. No
  parentheses: they would close the `(refs: ...)` suffix early on re-import.
- `--at` (optional): `HH:MM` (today) or `YYYY-MM-DDTHH:MM` (past day). Defaults to now.

## Managing slugs

Slugs are your project buckets. Manage them at runtime, no config files or source
edits:

```sh
./wl slug ls            # list registered slugs, in display order
./wl slug add backend   # register a slug (appended to the display order)
./wl slug rm backend    # unregister a slug
```

Registration is optional and does two things: it sets the **display order** in
reports (registered slugs appear in the order you added them, then any
unregistered slug alphabetically), and it silences the "unknown slug" warning,
which otherwise fires as a typo guard. Removing a slug that still has entries is
allowed; those entries keep their text and just sort after the registered ones.

Slugs are stored in `work_log.db`, not in `work_log.md`. They are local tooling
config, so they do not travel with the exported markdown; on a new machine,
re-register the slugs you want.

## Fixing or deleting an entry

Change a field with `wl edit`, using the id shown by `wl log`:

```sh
./wl log -n 5                                    # ids are the first column
./wl edit 42 --body "opened the PR, not just drafted it"
```

Pass any of `--slug`, `--type`, `--ref`, `--at`, `--body`; only the fields you pass
change, and the same validation as `wl add` applies, so an edit cannot write what
`add` would have refused. Remove an entry with `wl rm`, which prints it in full, so a
mistake is recoverable by eye:

```sh
./wl rm 42
```

`work_log.md` is not a repair path: the export is rewritten from the database on every
`wl add`, `wl edit`, `wl rm` and `wl render`, so a hand-edit that only deletes a line is
put straight back. A hand-edit that adds or rewrites one is refused instead: the file
then holds an entry the database does not, and rewriting the file from the database
would take that line with it. A hand-edit the parser cannot read back is refused for the
same reason. `wl import` exists to rebuild `work_log.db` from `work_log.md`; it is a
rescue tool for a lost or corrupted database, not part of the normal loop.

No command overwrites the export while the export holds entries the database does not.
The comparison is by entry identity, not by count, so a line rewritten in place is
caught as readily as a line added, and the refusal lists the entries at stake rather
than a number: the newest 20 of them, with a tail line counting the rest, since the
usual trigger is a missing database and every entry in the file is then at stake. It
runs before the command writes anything, so a refusal leaves both copies untouched and
you still get to choose which one to keep: `wl import` to take those entries, or delete
`work_log.md` and `wl render` to discard them. That covers a root holding `work_log.md`
but no `work_log.db` (a fresh machine, a checkout carrying only the markdown), and a
file someone typed a line into, whether or not the line parses: one that does not is
reported by line number and refused, the same way `wl import` reports it, rather than
dropped. An export that cannot be read at all is refused for the same reason. `wl rm`
needs no exception: it is checked before it deletes, while both copies still agree.

In the other direction, `wl import` refuses a database that already holds entries unless
you pass `--force`, and refuses, before deleting anything, a missing `work_log.md`, a
file that reads back as fewer entries than the database already holds, and a file with a
line that looks like an entry and does not parse, reporting every such line. Rows that
`--force` does delete and not replace are all printed before they go, uncapped, the way
`wl rm` prints what it removed, so the loss really is recoverable by eye.

## Searching

`wl log` filters by slug, type, ref, and date, prints newest first, and shows the
newest 20 by default (`-n N`, or `-n 0` for all) with a tail line counting what it
withheld. A ticket key is found whether it sits in `--ref` or only in the body. For
any other free-text search, grep the export:

```sh
grep -i hugepages work_log.md
```

## Stats

`wl stats` aggregates rather than lists, and takes the same filters as `wl log`
(`--slug`, `--type`, `--ref`, `--since`, `--until`), so any slice you can list you can
also count:

```
263 entries, 2026-08-03 to 2026-08-16

by type
  pr        71  ████████████████████████
  decision  67  ███████████████████████
  note      57  ███████████████████
  ticket    53  ██████████████████
  blocker   11  ████
  idea       4  █

by slug
  general  245  ████████████████████████
  backend   14  █
  docs       2  █
  infra      1  █
  website    1  █

by week
  2026-W32  132  ████████████████████████
  2026-W33  131  ████████████████████████

top refs (of 92)
  PROJ-12  39  ████████████████████████
  PROJ-9   20  ████████████
  PROJ-13  19  ████████████
  ... and 89 more
```

Type, slug and ref blocks are sorted biggest first. Week blocks, and the weekday and
hour blocks that `--when` adds, stay in clock order instead: the question there is the
shape over time, and a busiest-first list hides the gaps. Weeks are ISO weeks, which is
how sprint tooling counts them and where `strftime %W` disagrees around New Year.

Bars scale to the largest row in their own block, and any non-zero count gets at least
one block, so a 1-in-500 row shows up instead of rendering blank. An entry can cite
several refs, so that block counts mentions rather than entries; `--top N` caps it
(default 10) and says how many it withheld. Blocks with nothing to show are omitted,
as are weekdays and hours that saw no entries.

## Health check (optional)

`worklog-healthcheck.sh` is a shell snippet you can wire into a shell startup or
an editor session hook. It warns if the tool is missing/broken or if
`work_log.md` has not changed in 5+ days, so silent logging failures surface
early. It finds the log the way `wl` does (`WORKLOG_ROOT`, else
`$XDG_DATA_HOME/worklog`, else `~/.local/share/worklog`); the path to `wl` itself is
a literal at the top of the script, so point it at your checkout. Uses BSD `stat`
(macOS); adjust for GNU `stat` on Linux.

## Tests

```sh
python3 test_wl.py
ruff check .
```

CI runs both on every push and pull request. The ruleset is pinned in `ruff.toml` and
the ruff version in `ci.yml`, so the gate is the same on any machine.

## Version history

Grouped by milestone; see `git log` for the full commit-level detail, and
`RELEASING.md` for how a version gets cut.

- **0.5 (unreleased; no `v0.5` tag yet)** The storage inversion, plus the stats and
  search work that preceded it.
  - `work_log.db` is the source of record and `work_log.md` a generated export, read
    back before every write purely to check it. A hand-edit to the markdown is put back
    on the next render, or refused when putting it back would cost an entry.
    Change entries with `wl edit` and delete them with `wl rm`, naming them by the id
    `wl log` now prints.
  - `wl import` is an explicit rescue path rather than a sync. It rebuilds the database
    from the export, refuses a database that already holds entries without `--force`,
    and refuses a missing file, a file that reads back as fewer entries than the
    database holds, or a line that looks like an entry and does not parse. Every row
    `--force` deletes and does not replace is printed before it goes, uncapped.
  - No command overwrites the export while it holds entries the database does not, nor
    a line that looks like an entry and does not parse, nor when it cannot be read at
    all. Entries are compared by identity rather than by count, so a line rewritten in
    place is caught as readily as a line added, and the refusal names the entries at
    stake, newest first, capped at 20 with a tail counting the rest. The check runs
    before the command writes, so a refusal costs nothing, and `wl rm` needs no
    exception to it.
  - The schema carries a `PRAGMA user_version` stamp and migrates in place, since the
    database can no longer be rebuilt by deleting it.
  - **Upgrading from 0.4 or earlier:** the default root moved from the directory holding
    the tool to `$XDG_DATA_HOME/worklog`, falling back to `~/.local/share/worklog`. Set
    `WORKLOG_ROOT` to your old directory, or move `work_log.md` and `work_log.db` into
    the new root. `wl` warns when it finds a log at the old location and starts an empty
    one rather than moving anything.
  - `wl stats`: counts by type, slug, ISO week and ref, `--when` for weekday and hour.
    `wl log` shows the newest 20 by default (`-n`, `-n 0` for all) and exits quietly on
    a closed pipe. `--ref` also matches ticket keys named only in an entry body. The
    markdown round trip is verified before the file is replaced, and slugs or refs that
    would not survive it are refused at `add`.
- **0.4** Hardening: `--at` now rejects impossible values (`99:99`, month 13)
  instead of storing garbage; test coverage extended to every logic branch;
  internal dedupe (ref formatting, test setup).
- **0.3** Date-range roundups (`report --since/--until`); documented how to fix or
  delete entries (edit `work_log.md`) and how to search (grep the export).
- **0.2** Runtime slug management: `wl slug ls/add/rm`, DB-backed, custom order.
- **0.1** Initial release: SQLite-backed log with `add`, `report`, `log`,
  `render`, `import`; markdown export as source of record. MIT licensed.
