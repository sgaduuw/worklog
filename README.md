# worklog (`wl`)

![CI](https://github.com/sgaduuw/worklog/actions/workflows/ci.yml/badge.svg)

A tiny SQLite-backed work log. You append entries with `wl add`; `work_log.md`
is a generated, human-readable export (newest day first). The markdown is the
source of record, the `.db` is a rebuildable cache: if `work_log.md` changes
(hand-edit, another session, git pull), the next `wl` command re-imports it
automatically.

No dependencies beyond the Python 3 standard library. Linting uses ruff, which is a
lint-time tool rather than a runtime one.

## Install

Drop the folder anywhere and run `./wl`. `wl` is a thin entry point that imports
`worklog.py` next to it, so keep the two co-located.

By default the log files live in the **parent** directory of the tool
(`../work_log.md`, `../work_log.db`). Point them elsewhere with `WORKLOG_ROOT`:

```sh
export WORKLOG_ROOT="$HOME/notes"
```

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

# regenerate work_log.md from the DB, or rebuild the DB from the markdown
./wl render
./wl import

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

There is no `wl edit`/`wl rm` command, and it doesn't need one: `work_log.md` is
the editable source of record. Open it in your editor, fix or delete the line, and
save. The next `wl` command re-imports the markdown (it re-imports whenever the
file is newer than the DB), so your change is picked up automatically. To force it
immediately, run `wl import`.

Because the markdown is the source of record, anything written to it that the parser
cannot read back would be lost on that re-import, silently. So `wl` renders the file,
parses it back, and compares before replacing anything: if an entry would not survive,
it refuses to write and names the entry. Note the guard runs on write, not on import,
so a hand-edit that breaks a line is still dropped when the file is read back. Keep
entry lines in the shape `- HH:MM [type] body (refs: ...)`.

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
early. Uses BSD `stat` (macOS); adjust for GNU `stat` on Linux.

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

- **0.5 (unreleased; no `v0.5` tag yet)** `wl stats`: counts by type, slug, ISO week and ref, `--when` for weekday
  and hour. `wl log` shows the newest 20 by default (`-n`, `-n 0` for all) and exits
  quietly on a closed pipe. `--ref` also matches ticket keys named only in an entry
  body. The markdown round trip is verified before the file is replaced, and slugs or
  refs that would not survive it are refused at `add`.
- **0.4** Hardening: `--at` now rejects impossible values (`99:99`, month 13)
  instead of storing garbage; test coverage extended to every logic branch;
  internal dedupe (ref formatting, test setup).
- **0.3** Date-range roundups (`report --since/--until`); documented how to fix or
  delete entries (edit `work_log.md`) and how to search (grep the export).
- **0.2** Runtime slug management: `wl slug ls/add/rm`, DB-backed, custom order.
- **0.1** Initial release: SQLite-backed log with `add`, `report`, `log`,
  `render`, `import`; markdown export as source of record. MIT licensed.
