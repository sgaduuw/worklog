# Spec: invert the storage, then package it

**Status:** agreed 2026-08-23. **Owner:** Eelco. **Repo:** `sgaduuw/worklog`.

## The two decisions this spec implements

1. **SQLite becomes the source of record. `work_log.md` becomes a pure export**, written
   on every mutation and never read back during normal operation.
2. **The tool gets packaged and published to PyPI**, so `wl` is installable rather than
   copied.

Both were chosen over the alternatives I recommended (markdown-only with a file lock;
pipx-from-git without publishing). The costs below are the consequences of that choice,
not objections to it.

## Why the current shape has to change either way

`work_log.md` is read back whenever its mtime beats the DB's, and `_import_into` clears
the table before re-inserting whatever parsed. Three measured consequences:

- A slug containing a space was accepted, rendered a heading the parser cannot read, and
  the entry was destroyed by the next command.
- A body ending in `(refs: FM-1)` had its tail parsed into the refs column. One real
  entry was corrupted this way for two months.
- 1055 of 1056 rows had their seconds silently flattened, because the markdown does not
  render them.

The guard added in `8179b94` verifies the round trip before writing, but the read path is
still unguarded and the guard only looks for losses, never for phantoms.

## What inverting actually costs

| Consequence | What it forces |
| --- | --- |
| Hand-editing stops being the repair path | `wl edit` and `wl rm` become required, so entries need addressable identity. The `id` column already exists and is currently never exposed |
| The DB can no longer be rebuilt by deleting it | Schema changes must migrate in place: `PRAGMA user_version` plus a numbered list |
| The export is derived, so the DB is the only copy | `wl import` survives as an explicit rescue path, never automatic, and the round-trip guard is what keeps the export rescuable |
| The failure ordering inverts | A failed export no longer means a lost entry. Commit the DB first, then export; a failed export warns loudly and exits non-zero, and the entry is safe |
| Packaging moves the code | `root()` returns `Path(__file__).parent.parent`, which lands in site-packages once installed, and moves the log the moment the source layout changes |

## Scope

**In.** `root()` resolution and a legacy-location warning; `user_version` migrations; the
inversion itself; `wl edit`; `wl rm`; ids in `wl log`; a loud, gated `wl import`; `fsync`
on the export; the package split; `pyproject.toml`; PyPI publishing; the doc and
`RELEASING.md` rewrites the inversion forces.

**Out.** An ORM (see below). A symmetric round-trip guard, which the inversion makes
cosmetic since the export is no longer read in normal operation. Backup of the log
itself: that is the user's concern, and the README will say so rather than the tool
pretending to solve it. `develop`, release branches, CODEOWNERS: a one-author repo cannot
staff the human gate, as `RELEASING.md` already records.

## Decisions, with the alternative rejected

- **Migrations: `PRAGMA user_version` and a tuple of scripts, not an ORM.** One table plus
  a two-column lookup table, no relationship between them, and no query anywhere that
  filters or sorts in SQL. An ORM would replace 21 lines of stdlib with a dependency, a
  model layer and a venv, against a promise of no runtime dependencies. Revisit when a
  second entity with a real relationship appears, or when aggregates move into SQL.
- **Default root: XDG, not next to the tool and not the CWD.** `$XDG_DATA_HOME/worklog`,
  falling back to `~/.local/share/worklog`, with `WORKLOG_ROOT` still winning. The CWD was
  rejected because a work log that changes with your shell's directory is a different log
  every time.
- **A legacy warning, not an auto-migration.** Moving somebody's only copy of their data
  without being asked is worse than telling them where it is. Non-fatal, because a
  genuine first run has no legacy log and must not be blocked.
- **`wl rm` does not prompt.** Prompts break non-interactive callers, which is most of
  them here. It prints the removed entry in full instead, so a mistake is re-addable by
  eye.
- **Ids stay out of the export.** They are database identity, not log content, and putting
  them in the markdown would make the export's shape depend on insertion order.

## Delivery: two plans

The decisions interlock, so the spec is one document, but the work splits at a natural
seam and the second half would otherwise be planned against code that is about to be
rewritten.

- **Plan 1, storage inversion and correctness.** Tasks 1 to 6.
  `docs/2026-08-23-storage-inversion-plan.md`. Ships a working, tested tool where the DB
  is authoritative, entries are editable and removable, and imports are loud.
- **Plan 2, split and packaging.** Tasks 7 to 10, written once plan 1 lands:
  7. Split `worklog.py` into a `worklog/` package: `markdown.py`, `store.py`,
     `report.py`, `cli.py`, `__init__.py`. Absolute imports throughout. Update the
     legacy-root expression, which the split changes.
  8. `pyproject.toml`, setuptools backend, version read from `worklog.__version__`,
     `[project.scripts] wl = "worklog.cli:main"`, and `wl --version`.
  9. Check the `worklog` name is free on PyPI, add a trusted-publishing workflow on tag
     push, renumber to three-component semver from `0.5.0`, and update `RELEASING.md`:
     the tag now publishes an artifact, so step 14's verification changes.
  10. Rewrite the README install section for pip, pipx and clone, and state that the
      export is the rescue path rather than the record.
