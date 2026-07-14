# worklog (`wl`)

A tiny SQLite-backed work log. You append entries with `wl add`; `work_log.md`
is a generated, human-readable export (newest day first). The markdown is the
source of record, the `.db` is a rebuildable cache: if `work_log.md` changes
(hand-edit, another session, git pull), the next `wl` command re-imports it
automatically.

No dependencies beyond the Python 3 standard library.

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

# search across all history
./wl log --slug backend
./wl log --ref PROJ-9
./wl log --type decision --since 2026-07-01 --until 2026-07-14

# regenerate work_log.md from the DB, or rebuild the DB from the markdown
./wl render
./wl import

# manage project slugs (see below)
./wl slug ls
./wl slug add backend
./wl slug rm backend
```

### Fields

- `--slug`: project bucket. `general` is the one built-in slug. Register your own
  with `wl slug add` (see [Managing slugs](#managing-slugs)). An unregistered slug
  still logs, but warns and sorts after the registered ones.
- `--type`: one of `ticket`, `pr`, `idea`, `decision`, `blocker`, `note`.
- `--ref` (optional): comma-separated keys, e.g. `PROJ-12,PROJ-13`.
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

## Searching

`wl log` filters by slug, type, ref, and date. For free-text search over the entry
bodies, grep the export:

```sh
grep -i hugepages work_log.md
```

## Health check (optional)

`worklog-healthcheck.sh` is a shell snippet you can wire into a shell startup or
an editor session hook. It warns if the tool is missing/broken or if
`work_log.md` has not changed in 5+ days, so silent logging failures surface
early. Uses BSD `stat` (macOS); adjust for GNU `stat` on Linux.

## Tests

```sh
python3 test_wl.py
```
