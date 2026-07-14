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

# search across all history
./wl log --slug backend
./wl log --ref PROJ-9
./wl log --type decision --since 2026-07-01 --until 2026-07-14

# regenerate work_log.md from the DB, or rebuild the DB from the markdown
./wl render
./wl import
```

### Fields

- `--slug`: project bucket. `general` is the one built-in canonical slug (sorts
  first); any other slug is accepted (with a warning) and sorts alphabetically
  after it. Add your own canonical slugs to `SLUG_ORDER` in `worklog.py`.
- `--type`: one of `ticket`, `pr`, `idea`, `decision`, `blocker`, `note`.
- `--ref` (optional): comma-separated keys, e.g. `PROJ-12,PROJ-13`.
- `--at` (optional): `HH:MM` (today) or `YYYY-MM-DDTHH:MM` (past day). Defaults to now.

## Health check (optional)

`worklog-healthcheck.sh` is a shell snippet you can wire into a shell startup or
an editor session hook. It warns if the tool is missing/broken or if
`work_log.md` has not changed in 5+ days, so silent logging failures surface
early. Uses BSD `stat` (macOS); adjust for GNU `stat` on Linux.

## Tests

```sh
python3 test_wl.py
```
