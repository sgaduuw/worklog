# Storage Inversion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make SQLite the source of record and `work_log.md` a pure export, with editable and removable entries, in-place schema migrations, and imports that report what they drop.

**Architecture:** `connect()` stops re-importing the markdown, so the DB is authoritative and migrated in place via `PRAGMA user_version`. Mutations commit to the DB first and then rewrite the export, so a failed export costs a stale file rather than a lost entry. `wl import` survives as an explicit, gated rescue path, which is why the existing round-trip guard stays: it keeps the export re-readable.

**Tech Stack:** Python 3 standard library only. `sqlite3`, `argparse`, `pathlib`, `tempfile`, `re`. No runtime dependencies, no ORM.

**Spec:** `docs/2026-08-23-storage-inversion-spec.md`

## Global Constraints

- **Runtime dependencies: none.** stdlib only. `ruff` is lint-time.
- **Gate before every commit:** `ruff check . && python3 test_wl.py`, both clean. Ruleset is `ruff.toml`, `line-length = 100`.
- **Tests** live in `test_wl.py`, use plain `assert`, and run with `python3 test_wl.py`. No frameworks, no fixtures. Helpers already there: `worklog_root()` (context manager, points `WORKLOG_ROOT` at a tempdir) and `_NS(**kw)` (argparse namespace stand-in). A single test runs with `python3 -c "import test_wl; test_wl.test_name()"`.
- **Every new test must be seen to fail before the implementation exists.** A step for it is written into every task.
- **`raise AssertionError(...)`, never `assert False`:** `python -O` strips asserts. Enforced by ruff B011.
- **Commits:** Conventional Commits, `type(scope): summary`, one commit per task, `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>` as the last line.
- **Prose:** no em-dashes or double dashes, in code comments or docs.
- **Comments explain why, not what.**
- **The export must stay parseable by `wl import`.** The guard in `write_md`/`export_md` enforces this; do not weaken it.

---

### Task 1: `root()` resolves to XDG, and shouts about a legacy log

The current expression is `Path(__file__).resolve().parent.parent`, which is `~/Projects` today because `worklog.py` sits in `~/Projects/worklog/`. Both later tasks break it: the package split makes it resolve to the repo, and a `pip install` makes it resolve into site-packages. Fixing it first means nothing else silently relocates somebody's log.

**Files:**
- Modify: `worklog.py:168-170` (`root()`), `worklog.py:193-210` (`connect()`, for the mkdir), `worklog.py:459` (`main()`, to call the warning once)
- Test: `test_wl.py`

**Interfaces:**
- Produces: `data_dir() -> Path`, `root() -> Path` (unchanged signature), `ensure_root() -> Path`, `warn_if_legacy_log_ignored() -> None`, module constant `_LEGACY_ROOT: Path`

- [ ] **Step 1: Write the failing tests**

```python
def test_root_resolution():
    saved = {k: os.environ.get(k) for k in ("WORKLOG_ROOT", "XDG_DATA_HOME")}
    try:
        os.environ["WORKLOG_ROOT"] = "/tmp/explicit"
        assert wl.root() == pathlib.Path("/tmp/explicit")
        # WORKLOG_ROOT wins over XDG, so an explicit choice is never second-guessed.
        os.environ["XDG_DATA_HOME"] = "/tmp/xdg"
        assert wl.root() == pathlib.Path("/tmp/explicit")
        del os.environ["WORKLOG_ROOT"]
        assert wl.root() == pathlib.Path("/tmp/xdg/worklog")
        del os.environ["XDG_DATA_HOME"]
        # The fallback is the XDG default, not the tool's own directory: once wl is
        # installed, the old expression pointed into site-packages.
        assert wl.root() == pathlib.Path.home() / ".local/share/worklog"
    finally:
        for k, v in saved.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v


def test_root_is_created_on_demand():
    with tempfile.TemporaryDirectory() as d:
        target = pathlib.Path(d, "not", "there", "yet")
        os.environ["WORKLOG_ROOT"] = str(target)
        try:
            wl.cmd_add(_NS(slug="general", type="note", ref="",
                           at="2026-07-01T09:00", body="first ever entry"))
            assert (target / "work_log.md").exists()
            assert (target / "work_log.db").exists()
        finally:
            del os.environ["WORKLOG_ROOT"]


def test_legacy_log_warning():
    with worklog_root() as d:
        # A legacy log next to the tool, and nothing at the new default, is exactly the
        # trap: without a warning the tool starts an empty log and abandons the real one.
        del os.environ["WORKLOG_ROOT"]
        try:
            legacy = pathlib.Path(d, "work_log.db")
            legacy.write_bytes(b"")
            buf = io.StringIO()
            with redirect_stderr(buf):
                wl.warn_if_legacy_log_ignored(legacy_root=pathlib.Path(d))
            out = buf.getvalue()
            assert "WORKLOG_ROOT=" in out and str(d) in out
            # Silent when the caller has already chosen a root.
            os.environ["WORKLOG_ROOT"] = d
            buf = io.StringIO()
            with redirect_stderr(buf):
                wl.warn_if_legacy_log_ignored(legacy_root=pathlib.Path(d))
            assert buf.getvalue() == ""
        finally:
            os.environ["WORKLOG_ROOT"] = d
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python3 -c "import test_wl; test_wl.test_root_resolution()"`
Expected: FAIL, `AssertionError`, because `root()` still returns the tool's parent directory.
Run the other two the same way. `test_legacy_log_warning` must fail with `AttributeError: module 'worklog' has no attribute 'warn_if_legacy_log_ignored'`.

- [ ] **Step 3: Implement**

Replace `worklog.py:168-170` with:

```python
# ponytail: the pre-0.5 default was the tool's own parent directory. Kept only to warn
# people off it; delete this constant and warn_if_legacy_log_ignored() once nobody is
# running a pre-0.5 layout. Note the package split changes what this expression means.
_LEGACY_ROOT = Path(__file__).resolve().parent.parent


def data_dir():
    """Where the log lives when WORKLOG_ROOT is unset.

    XDG rather than beside the tool: once `wl` is installed from a package the old
    expression resolves into site-packages, and it silently relocated the log whenever
    the source layout changed, which is a data-loss shape rather than an inconvenience.
    """
    xdg = os.environ.get("XDG_DATA_HOME")
    return Path(xdg) / "worklog" if xdg else Path.home() / ".local" / "share" / "worklog"


def root():
    env = os.environ.get("WORKLOG_ROOT")
    return Path(env) if env else data_dir()


def ensure_root():
    """The root may not exist yet: the XDG default usually does not on a first run."""
    r = root()
    r.mkdir(parents=True, exist_ok=True)
    return r


def warn_if_legacy_log_ignored(legacy_root=None):
    """Point at a pre-0.5 log this install would otherwise ignore, and keep going.

    Non-fatal on purpose: a genuine first run has no legacy log and must not be blocked,
    and moving somebody's only copy of their data unasked is worse than telling them
    where it is.
    """
    if os.environ.get("WORKLOG_ROOT") or db_path().exists():
        return
    legacy = (legacy_root or _LEGACY_ROOT) / "work_log.db"
    if legacy.exists():
        print(f"warning: {legacy} holds a log this install ignores. "
              f"Set WORKLOG_ROOT={legacy.parent} to keep using it, "
              f"or move it to {root()}.", file=sys.stderr)
```

In `connect()`, replace `db = db_path()` with:

```python
    ensure_root()
    db = db_path()
```

As the first statement of `main()`, before `signal.signal(...)`:

```python
    warn_if_legacy_log_ignored()
```

- [ ] **Step 4: Run the full suite**

Run: `ruff check . && python3 test_wl.py`
Expected: `All checks passed!` and `all passed (33 tests)`.

- [ ] **Step 5: Commit**

```bash
git add worklog.py test_wl.py
git commit -m "fix(root): resolve the log to XDG, and warn about a pre-0.5 location

The default root was Path(__file__).parent.parent, so it moved with the source
layout and would resolve into site-packages once installed. Both the package
split and packaging break it, and the failure is silent: a fresh empty log at a
new location while the real one is abandoned.

WORKLOG_ROOT still wins; the fallback is XDG. A legacy log beside the tool now
produces a loud pointer rather than being ignored, and the root is created on
demand because the XDG default will not exist on a first run.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: In-place schema migrations

Once the DB is authoritative, a schema change can no longer be handled by deleting the file and re-importing the markdown. This lands before the inversion so that the mechanism exists the moment the data stops being reconstructible.

**Files:**
- Modify: `worklog.py:151-165` (`_SCHEMA`), `worklog.py:193-210` (`connect()`)
- Test: `test_wl.py`

**Interfaces:**
- Consumes: `db_path()`, `ensure_root()` from Task 1
- Produces: `SCHEMA_VERSION: int`, `_MIGRATIONS: tuple[str, ...]`, `migrate(conn) -> None`

- [ ] **Step 1: Write the failing tests**

```python
def test_migrate_stamps_a_fresh_db():
    with worklog_root():
        conn = wl.connect()
        assert conn.execute("PRAGMA user_version").fetchone()[0] == wl.SCHEMA_VERSION
        conn.close()


def test_migrate_adopts_an_unstamped_db_without_losing_rows():
    with worklog_root():
        conn = wl.connect()
        conn.execute("INSERT INTO entries(ts,slug,type,refs,body) VALUES(?,?,?,?,?)",
                     ("2026-07-01T09:00:00", "general", "note", "", "keeper"))
        # Pre-migration databases exist in the field stamped 0. Adopting one must be a
        # stamp, not a rebuild: there is no longer a markdown file to rebuild from.
        conn.execute("PRAGMA user_version = 0")
        conn.commit()
        wl.migrate(conn)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == wl.SCHEMA_VERSION
        assert conn.execute("SELECT body FROM entries").fetchone()[0] == "keeper"
        conn.close()


def test_migrate_refuses_a_future_schema():
    with worklog_root():
        conn = wl.connect()
        conn.execute(f"PRAGMA user_version = {wl.SCHEMA_VERSION + 1}")
        conn.commit()
        try:
            wl.migrate(conn)
            raise AssertionError("expected SystemExit on a newer schema")
        except SystemExit as ex:
            assert "newer" in str(ex.code), ex.code
        finally:
            conn.close()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python3 -c "import test_wl; test_wl.test_migrate_stamps_a_fresh_db()"`
Expected: FAIL with `AttributeError: module 'worklog' has no attribute 'SCHEMA_VERSION'`.

- [ ] **Step 3: Implement**

Replace the `_SCHEMA` block at `worklog.py:151-165` with:

```python
SCHEMA_VERSION = 1  # bump by one for every script appended to _MIGRATIONS

# Index 0 takes a database from version 0 to 1, index 1 from 1 to 2, and so on. Each
# script must be safe to run against a database that already has the shape it creates,
# because a pre-migration DB is adopted by stamping rather than rebuilding.
#
# This is deliberately not an ORM: one table, one two-column lookup table, no
# relationship between them, and no query anywhere that filters or sorts in SQL. A
# numbered list of statements is smaller than a model layer. Revisit when a second
# entity with a real relationship appears.
_MIGRATIONS = (
    """
    CREATE TABLE IF NOT EXISTS entries(
      id   INTEGER PRIMARY KEY,
      ts   TEXT NOT NULL,
      slug TEXT NOT NULL,
      type TEXT NOT NULL,
      refs TEXT NOT NULL DEFAULT '',
      body TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_entries_ts ON entries(ts);
    CREATE TABLE IF NOT EXISTS slugs(
      name TEXT PRIMARY KEY,
      pos  INTEGER NOT NULL   -- display order, ascending
    );
    """,
)


def migrate(conn):
    """Bring the database up to SCHEMA_VERSION, then stamp it.

    Refusing a newer schema matters more than it looks: the database is the only copy of
    the data now, so an older build writing against a shape it does not understand is
    how rows get silently dropped.
    """
    have = conn.execute("PRAGMA user_version").fetchone()[0]
    if have > SCHEMA_VERSION:
        sys.exit(f"error: {db_path()} was written by a newer wl (schema {have}; this "
                 f"build knows {SCHEMA_VERSION}). Upgrade wl rather than downgrading the log.")
    for script in _MIGRATIONS[have:SCHEMA_VERSION]:
        conn.executescript(script)
    if have < SCHEMA_VERSION:
        # PRAGMA will not take a bound parameter, and SCHEMA_VERSION is our own int.
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()
```

In `connect()`, replace `conn.executescript(_SCHEMA)` with `migrate(conn)`.

- [ ] **Step 4: Run the full suite**

Run: `ruff check . && python3 test_wl.py`
Expected: clean, `all passed (36 tests)`.

- [ ] **Step 5: Commit**

```bash
git add worklog.py test_wl.py
git commit -m "feat(store): migrate the schema in place, keyed on PRAGMA user_version

The database is about to become the source of record, so a schema change can no
longer be handled by deleting the file and re-importing the markdown. Migrations
are a numbered tuple of scripts applied on connect, and a database stamped newer
than this build knows is refused rather than written to.

Not an ORM, and the comment says why: one table plus a lookup table, no
relationship, and no query that filters or sorts in SQL.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Invert it, and make the rescue path loud

**Files:**
- Modify: `worklog.py:74-91` (`parse_markdown`), `worklog.py:181-191` (`_import_into`), `worklog.py:193-210` (`connect()`), `worklog.py:228-250` (`write_md`, renamed), `worklog.py:416` (`cmd_import`), `worklog.py` argparse block for `import`
- Test: `test_wl.py`

**Interfaces:**
- Consumes: `migrate(conn)` from Task 2
- Produces: `scan_markdown(text) -> (list[Entry], list[tuple[int, str]])`, `parse_markdown(text) -> list[Entry]` (unchanged signature, now a wrapper), `export_md(conn) -> None` (renamed from `write_md`)

- [ ] **Step 1: Write the failing tests**

```python
def test_the_database_is_authoritative():
    with worklog_root() as d:
        wl.cmd_add(_NS(slug="general", type="note", ref="",
                       at="2026-07-01T09:00", body="from the database"))
        md = pathlib.Path(d, "work_log.md")
        # Mangle the export, and make it newer than the DB so the old staleness check
        # would have re-imported it. Nothing may read it back.
        md.write_text("# Work Log\n\n## 2026-07-01\n\n### general\n"
                      "- 09:00 [note] from the markdown (refs: none)\n")
        os.utime(md, (9e9, 9e9))
        buf = io.StringIO()
        with redirect_stdout(buf):
            wl.cmd_log(_NS(slug=None, type=None, ref=None, since=None,
                           until=None, limit=0))
        out = buf.getvalue()
        assert "from the database" in out
        assert "from the markdown" not in out
        # And a render overwrites the mangled file rather than absorbing it.
        wl.cmd_render(_NS())
        assert "from the database" in md.read_text()
        assert "from the markdown" not in md.read_text()


def test_import_is_gated_and_loud():
    with worklog_root() as d:
        md = pathlib.Path(d, "work_log.md")
        md.write_text("# Work Log\n\n## 2026-07-01\n\n### general\n"
                      "- 09:00 [note] good line (refs: none)\n"
                      "- 09:01 [note oops unparseable\n"
                      "- not even a timestamp\n")
        # An empty database imports, and says what it could not read.
        buf, err = io.StringIO(), io.StringIO()
        try:
            with redirect_stdout(buf), redirect_stderr(err):
                wl.cmd_import(_NS(force=False))
            raise AssertionError("expected a non-zero exit when lines were dropped")
        except SystemExit as ex:
            assert ex.code not in (0, None), ex.code
        assert "(1 entries)" in buf.getvalue(), buf.getvalue()
        # Line 6 is the good entry; 7 and 8 are the two that must be reported.
        assert "line 7" in err.getvalue(), err.getvalue()
        assert "line 8" in err.getvalue(), err.getvalue()
        # A non-empty database is not clobbered without --force.
        try:
            wl.cmd_import(_NS(force=False))
            raise AssertionError("expected SystemExit on a non-empty database")
        except SystemExit as ex:
            assert "--force" in str(ex.code), ex.code
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python3 -c "import test_wl; test_wl.test_the_database_is_authoritative()"`
Expected: FAIL on `"from the markdown" not in out`, because `connect()` still re-imports.
Run: `python3 -c "import test_wl; test_wl.test_import_is_gated_and_loud()"`
Expected: FAIL with `AssertionError: expected a non-zero exit when lines were dropped`.

- [ ] **Step 3: Implement**

Replace `parse_markdown` at `worklog.py:74-91` with a scanner plus a wrapper:

```python
_CANDIDATE_RE = re.compile(r"^- ")


def scan_markdown(text):
    """Parse a rendered export, and report the lines that looked like entries but were not.

    A dropped line used to be silent, which is how a hand-edited file could delete an
    entry nobody noticed. Returns (entries, skipped) with skipped as [(lineno, line)].
    """
    entries, skipped, day, slug = [], [], None, None
    for n, line in enumerate(text.splitlines(), 1):
        m = _DAY_RE.match(line)
        if m:
            day, slug = m.group(1), None
            continue
        m = _SLUG_RE.match(line)
        if m:
            slug = m.group(1)
            continue
        m = _ENTRY_RE.match(line)
        if m and day and slug:
            hhmm, typ, body, refs = m.groups()
            refs = "" if not refs or refs.strip().lower() == "none" else normalize_refs(refs)
            entries.append(Entry(f"{day}T{hhmm}:00", slug, typ, refs, body.rstrip()))
        elif _CANDIDATE_RE.match(line):
            skipped.append((n, line))
    return entries, skipped


def parse_markdown(text):
    """Entries only. Use scan_markdown when the dropped lines matter."""
    return scan_markdown(text)[0]
```

Replace `_import_into` at `worklog.py:181-191` with a version that returns its report:

```python
def _import_into(conn):
    """Rebuild the entries table from the export. Returns (imported, skipped).

    This is the rescue path, not part of the normal loop: the database is the source of
    record and the markdown is derived from it. It stays because the export is the only
    human-readable copy, so it is what a lost database is rebuilt from.
    """
    conn.execute("DELETE FROM entries")
    md = md_path()
    entries, skipped = scan_markdown(md.read_text()) if md.exists() else ([], [])
    conn.executemany("INSERT INTO entries(ts,slug,type,refs,body) VALUES(?,?,?,?,?)",
                     [(e.ts, e.slug, e.type, e.refs, e.body) for e in entries])
    conn.commit()
    return len(entries), skipped
```

In `connect()`, delete the `stale` computation and the `if stale: _import_into(conn)` branch entirely, so the body reads:

```python
def connect():
    """Open the database, migrating it if needed. The markdown is never read back here.

    Inverted on 2026-08-23: the database is the source of record and work_log.md is an
    export. Re-importing on an mtime comparison is what let a malformed line, a slug
    with a space, or a body ending in "(refs: ...)" delete or corrupt entries.
    """
    ensure_root()
    conn = sqlite3.connect(str(db_path()))
    conn.execute("PRAGMA busy_timeout = 5000")
    migrate(conn)
    if not conn.execute("SELECT 1 FROM slugs LIMIT 1").fetchone():
        conn.execute("INSERT INTO slugs(name, pos) VALUES('general', 0)")
        conn.commit()
    return conn
```

Rename `write_md` to `export_md` (four call sites: `cmd_add`, `cmd_render`, and any added later) and replace its docstring and failure message, since the failure no longer costs an entry:

```python
def export_md(conn):
    """Render every entry and atomically replace work_log.md, if it reads back intact.

    The export is derived, so a refused write costs a stale file rather than data. It is
    still verified, because `wl import` parses this file and it is what a lost database
    is rebuilt from: an export that cannot be read back is an export that cannot rescue
    anything.
    """
```

and its `sys.exit` message:

```python
        sys.exit(f"error: work_log.md not replaced; {len(lost)} entr"
                 f"{'y' if len(lost) == 1 else 'ies'} would not survive `wl import`:\n{listing}\n"
                 "The database is unaffected. Fix the entry with `wl edit`, then `wl render`.")
```

Replace `cmd_import` at `worklog.py:416` with:

```python
def cmd_import(args):
    conn = connect()
    n = conn.execute("SELECT count(*) FROM entries").fetchone()[0]
    if n and not args.force:
        conn.close()
        sys.exit(f"error: {db_path()} already holds {n} entries and is the source of "
                 "record. `wl import` rebuilds it from work_log.md and is a rescue "
                 "path, not a sync. Pass --force if that is what you want.")
    imported, skipped = _import_into(conn)
    conn.close()
    print(f"imported {md_path()} -> {db_path()} ({imported} entries)")
    if skipped:
        for lineno, line in skipped:
            print(f"warning: line {lineno} not read: {line[:90]}", file=sys.stderr)
        sys.exit(f"error: {len(skipped)} line(s) looked like entries and were not "
                 "imported. Fix them in work_log.md and import again.")
```

In `main()`, give the `import` subparser its flag:

```python
    im = sub.add_parser("import", help="rescue: rebuild the DB from work_log.md")
    im.add_argument("--force", action="store_true",
                    help="allow rebuilding over a database that already has entries")
    im.set_defaults(fn=cmd_import)
```

Then deal with three existing tests this task invalidates:

**Delete `test_import_and_stale_reimport`.** Both halves assert removed behaviour: that
`connect()` imports when the DB is missing, and that it re-imports when the markdown is
newer. Neither is true now, and the replacement coverage is `test_the_database_is_authoritative`
plus `test_import_is_gated_and_loud`.

**Reseed `test_log_survives_a_closed_pipe` through the database.** It writes 300 fat
entries into `work_log.md` and then runs `wl log`, which after the inversion returns
nothing: the test would still pass while proving nothing, which is worse than failing.
Import them first, in the same shell:

```python
        proc = subprocess.run(
            ["sh", "-c", f'"{sys.executable}" "{script}" import >/dev/null && '
                         f'"{sys.executable}" "{script}" log --limit 0 | head -1'],
            capture_output=True, text=True, env={**os.environ, "WORKLOG_ROOT": d},
            check=False,
        )
```

**Follow the rename at `test_wl.py:541`,** where `test_write_md_refuses_a_render_it_cannot_read_back`
calls `wl.write_md(conn)`. Rename the test to `test_export_refuses_a_render_it_cannot_read_back`
and the call to `wl.export_md(conn)`.

- [ ] **Step 4: Run the full suite**

Run: `ruff check . && python3 test_wl.py`
Expected: clean, `all passed (37 tests)`.

- [ ] **Step 5: Commit**

```bash
git add worklog.py test_wl.py
git commit -m "feat(store)!: make the database authoritative, and the markdown an export

connect() no longer re-imports work_log.md when its mtime beats the database's.
That comparison is what let a slug with a space, a body ending in \"(refs: ...)\"
or any malformed line delete or corrupt entries, silently, on the next command.

wl import survives as the rescue path: it is how a lost database is rebuilt from
the only human-readable copy. So it is now explicit rather than automatic, it
refuses a non-empty database without --force, and it reports every line that
looked like an entry and was not read, with line numbers, exiting non-zero.

write_md becomes export_md. Its guard stays, with a better reason than before: an
export that cannot be parsed back is an export that cannot rescue anything.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Expose ids, and add `wl rm`

Hand-editing is no longer the repair path, so entries need addressable identity. The `id` column has existed since the first commit and has never been read.

**Files:**
- Modify: `worklog.py:21-26` (`Entry`), `worklog.py:213-215` (`_all_entries`), `worklog.py:325` (`cmd_log`), `worklog.py` argparse block
- Test: `test_wl.py`

**Interfaces:**
- Consumes: `export_md(conn)` from Task 3
- Produces: `Entry` with a trailing `id: int = None` field, `cmd_rm(args)`

- [ ] **Step 1: Write the failing tests**

```python
def test_log_shows_ids():
    with worklog_root():
        wl.cmd_add(_NS(slug="general", type="note", ref="",
                       at="2026-07-01T09:00", body="addressable"))
        buf = io.StringIO()
        with redirect_stdout(buf):
            wl.cmd_log(_NS(slug=None, type=None, ref=None, since=None,
                           until=None, limit=0))
        # The id has to be visible, because it is the only way to name an entry for
        # `wl edit` and `wl rm`.
        assert buf.getvalue().split()[0] == "1", buf.getvalue()


def test_rm_removes_one_entry_and_updates_the_export():
    with worklog_root() as d:
        for body in ("keeper", "doomed"):
            wl.cmd_add(_NS(slug="general", type="note", ref="",
                           at="2026-07-01T09:00", body=body))
        buf = io.StringIO()
        with redirect_stdout(buf):
            wl.cmd_rm(_NS(id=2))
        assert "doomed" in buf.getvalue()   # printed in full, so a mistake is re-addable
        text = pathlib.Path(d, "work_log.md").read_text()
        assert "keeper" in text and "doomed" not in text
        try:
            wl.cmd_rm(_NS(id=999))
            raise AssertionError("expected SystemExit for an unknown id")
        except SystemExit as ex:
            assert "999" in str(ex.code), ex.code
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python3 -c "import test_wl; test_wl.test_log_shows_ids()"`
Expected: FAIL, the first field is the date, not an id.
Run: `python3 -c "import test_wl; test_wl.test_rm_removes_one_entry_and_updates_the_export()"`
Expected: FAIL with `AttributeError: module 'worklog' has no attribute 'cmd_rm'`.

- [ ] **Step 3: Implement**

Give `Entry` a trailing optional field, so `parse_markdown`'s five-argument construction is untouched:

```python
class Entry(NamedTuple):
    ts: str    # ISO local 'YYYY-MM-DDTHH:MM:SS'; day = ts[:10]
    slug: str
    type: str
    refs: str  # comma-joined keys, '' if none
    body: str
    id: int = None  # database identity; None for entries parsed out of the export
```

`_all_entries` selects it:

```python
def _all_entries(conn):
    rows = conn.execute("SELECT ts,slug,type,refs,body,id FROM entries").fetchall()
    return [Entry(*r) for r in rows]
```

`cmd_log`'s print gains the id as the first field:

```python
    for e in shown:
        print(f"{e.id} {e.ts[:10]} {e.ts[11:16]} [{e.slug}] [{e.type}] "
              f"{e.body} (refs: {fmt_refs(e.refs)})")
```

Add the command, next to `cmd_add`:

```python
def cmd_rm(args):
    """Delete one entry by id, printing it in full first.

    No confirmation prompt: prompts break non-interactive callers, which is most of
    them here. Printing the entry is what makes a mistake recoverable, by eye.
    """
    conn = connect()
    row = conn.execute("SELECT ts,slug,type,refs,body,id FROM entries WHERE id = ?",
                       (args.id,)).fetchone()
    if not row:
        conn.close()
        sys.exit(f"error: no entry with id {args.id}")
    e = Entry(*row)
    conn.execute("DELETE FROM entries WHERE id = ?", (args.id,))
    conn.commit()
    export_md(conn)
    conn.close()
    print(f"removed {e.id}: {e.ts[:16]} [{e.slug}] [{e.type}] {e.body} "
          f"(refs: {fmt_refs(e.refs)})")
```

And its parser, after the `log` block in `main()`:

```python
    rm = sub.add_parser("rm", help="delete one entry by id (see `wl log`)")
    rm.add_argument("id", type=int)
    rm.set_defaults(fn=cmd_rm)
```

- [ ] **Step 4: Run the full suite**

Run: `ruff check . && python3 test_wl.py`
Expected: clean, `all passed (39 tests)`.

- [ ] **Step 5: Commit**

```bash
git add worklog.py test_wl.py
git commit -m "feat(cli): expose entry ids and add \`wl rm\`

Hand-editing work_log.md stopped being the repair path when the database became
authoritative, so entries need a name. The id column has existed since the first
commit and was never read, because the old re-import rebuilt the table and made
ids meaningless. It is stable now.

wl log prints it as the first field, and wl rm deletes one entry, printing it in
full first so a mistake is re-addable by eye. Ids stay out of the export: they are
database identity, not log content.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: `wl edit`

**Files:**
- Modify: `worklog.py` (new `cmd_edit`, argparse block)
- Test: `test_wl.py`

**Interfaces:**
- Consumes: `cmd_rm`'s row-lookup shape, `check_slug`, `check_refs`, `resolve_at`, `TYPES`, `export_md`
- Produces: `cmd_edit(args)`

- [ ] **Step 1: Write the failing test**

```python
def test_edit_changes_one_field_at_a_time():
    with worklog_root() as d:
        wl.cmd_add(_NS(slug="general", type="note", ref="PROJ-1",
                       at="2026-07-01T09:00", body="before"))

        def edit(**kw):
            base = {"id": 1, "slug": None, "type": None, "ref": None,
                    "at": None, "body": None}
            base.update(kw)
            buf = io.StringIO()
            with redirect_stdout(buf):
                wl.cmd_edit(_NS(**base))
            return buf.getvalue()

        assert "after" in edit(body="after")
        assert "decision" in edit(type="decision")
        text = pathlib.Path(d, "work_log.md").read_text()
        assert "after" in text and "before" not in text and "[decision]" in text
        # Untouched fields keep their values.
        assert "PROJ-1" in edit(at="2026-07-02T10:00")
        assert "2026-07-02" in pathlib.Path(d, "work_log.md").read_text()
        # The same validation as `add`, so an edit cannot write what add refuses.
        for kw in ({"slug": "two words"}, {"ref": "PROJ-1)"},
                   {"type": "nonsense"}, {"at": "99:99"}):
            try:
                edit(**kw)
                raise AssertionError(f"expected SystemExit for {kw}")
            except SystemExit as ex:
                assert ex.code not in (0, None), ex.code
        # Nothing to change is an error, not a silent no-op.
        try:
            edit()
            raise AssertionError("expected SystemExit when no field was given")
        except SystemExit as ex:
            assert "nothing" in str(ex.code).lower(), ex.code
        try:
            edit(id=999, body="x")
            raise AssertionError("expected SystemExit for an unknown id")
        except SystemExit as ex:
            assert "999" in str(ex.code), ex.code
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -c "import test_wl; test_wl.test_edit_changes_one_field_at_a_time()"`
Expected: FAIL with `AttributeError: module 'worklog' has no attribute 'cmd_edit'`.

- [ ] **Step 3: Implement**

```python
def cmd_edit(args):
    """Change named fields of one entry, validating exactly as `add` does.

    Sharing the checks is the point: an edit that could write a slug `add` refuses would
    put an entry in the database that the export cannot represent.
    """
    fields = {}
    if args.slug is not None:
        check_slug(args.slug)
        fields["slug"] = args.slug
    if args.type is not None:
        if args.type not in TYPES:
            sys.exit(f"error: bad --type {args.type!r}; valid: {', '.join(TYPES)}")
        fields["type"] = args.type
    if args.ref is not None:
        refs = normalize_refs(args.ref)
        check_refs(refs)
        fields["refs"] = refs
    if args.at is not None:
        try:
            fields["ts"] = resolve_at(args.at)
        except ValueError as e:
            sys.exit(f"error: {e}")
    if args.body is not None:
        fields["body"] = args.body.replace("\n", " ").strip()
    if not fields:
        sys.exit("error: nothing to change; pass at least one of "
                 "--slug, --type, --ref, --at, --body")
    conn = connect()
    if not conn.execute("SELECT 1 FROM entries WHERE id = ?", (args.id,)).fetchone():
        conn.close()
        sys.exit(f"error: no entry with id {args.id}")
    assignments = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE entries SET {assignments} WHERE id = ?",
                 (*fields.values(), args.id))
    conn.commit()
    export_md(conn)
    row = conn.execute("SELECT ts,slug,type,refs,body,id FROM entries WHERE id = ?",
                       (args.id,)).fetchone()
    conn.close()
    e = Entry(*row)
    print(f"{e.id} {e.ts[:16]} [{e.slug}] [{e.type}] {e.body} (refs: {fmt_refs(e.refs)})")
```

The column names in `assignments` come from the literal dict keys above, never from user
input, so the f-string cannot carry an injection. Values stay bound.

Parser, after the `rm` block:

```python
    ed = sub.add_parser("edit", help="change fields of one entry by id")
    ed.add_argument("id", type=int)
    ed.add_argument("--slug")
    ed.add_argument("--type")
    ed.add_argument("--ref")
    ed.add_argument("--at", help="HH:MM (today) or YYYY-MM-DDTHH:MM")
    ed.add_argument("--body")
    ed.set_defaults(fn=cmd_edit)
```

- [ ] **Step 4: Run the full suite**

Run: `ruff check . && python3 test_wl.py`
Expected: clean, `all passed (40 tests)`.

- [ ] **Step 5: Commit**

```bash
git add worklog.py test_wl.py
git commit -m "feat(cli): add \`wl edit\` for a named field of one entry

The last piece the inversion owes: with the markdown derived, a typo or a wrong
slug had no supported repair. Edit validates through the same check_slug,
check_refs, TYPES and resolve_at as add, because an edit that could write what
add refuses would put an entry in the database the export cannot represent.

Column names come from literal dict keys, never from input, so the assignment
f-string carries no injection; values stay bound.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: `fsync` the export, and rewrite every claim the inversion falsified

The docs currently say the markdown is the source of record, in the README, in the header of every generated file, and in `RELEASING.md`'s trap list. All three are now wrong, and the header is wrong in shipped output.

**Files:**
- Modify: `worklog.py:115-118` (`HEADER_BLOCK`), `worklog.py` (`export_md`, for the fsync), `README.md`, `RELEASING.md`
- Test: `test_wl.py`

**Interfaces:**
- Consumes: everything above. Produces nothing new.

- [ ] **Step 1: Write the failing test**

```python
def test_export_is_flushed_to_disk():
    # os.replace is atomic for visibility, not durable. The export is the rescue copy of
    # a database that is now the only record, so it is worth an fsync.
    src = pathlib.Path(wl.__file__).read_text()
    body = src[src.index("def export_md("):]
    body = body[:body.index("\ndef ")]
    if "fsync" not in body:
        raise AssertionError("export_md must fsync the temp file before os.replace")
    assert body.index("fsync") < body.index("os.replace")


def test_header_does_not_promise_hand_editing():
    # The header ships inside every generated file, so a false claim there is the most
    # widely read prose in the project.
    assert "hand-edit" not in wl.HEADER_BLOCK or "ignored" in wl.HEADER_BLOCK
    assert "wl edit" in wl.HEADER_BLOCK
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python3 -c "import test_wl; test_wl.test_export_is_flushed_to_disk()"`
Expected: FAIL with `AssertionError: export_md must fsync the temp file before os.replace`.
Run: `python3 -c "import test_wl; test_wl.test_header_does_not_promise_hand_editing()"`
Expected: FAIL, the header still says a hand-edit is re-imported.

- [ ] **Step 3: Implement**

In `export_md`, replace the write block with:

```python
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())   # atomic replace still leaves the content unflushed
    os.replace(tmp, str(target))
```

Replace `HEADER_BLOCK`:

```python
HEADER_BLOCK = (
    "# Work Log\n\n"
    "Generated by `wl` from work_log.db, which is the source of record; newest day\n"
    "first. Add entries with `wl add`, change them with `wl edit`, remove them with\n"
    "`wl rm`. Hand-edits here are ignored and overwritten on the next render."
)
```

In `README.md`: the intro currently says the markdown is the source of record and the
`.db` is a rebuildable cache. Invert both sentences. Replace the "Fixing or deleting an
entry" section with `wl edit` and `wl rm` usage, and state that `wl import` is a rescue
path that rebuilds the database from the export, refuses a non-empty database without
`--force`, and reports lines it could not read. Add `edit`, `rm` and the `--force` flag to
the usage block. Add one paragraph under a new "Where your log lives" heading covering
`WORKLOG_ROOT`, the XDG default, and the recommendation that the root be a directory
under version control, since the tool guarantees a correct write rather than a surviving
one.

In `RELEASING.md`, the third trap says a release can move the renderer and the log lives
outside the repo with no history. Keep it, and add that the export is now derived, so a
renderer change is verified by `wl render` followed by `wl import --force` into a scratch
`WORKLOG_ROOT` and an entry count comparison, rather than against the live log.

- [ ] **Step 4: Run the full suite, then verify against the real log**

Run: `ruff check . && python3 test_wl.py`
Expected: clean, `all passed (42 tests)`.

Then, without touching the live log:

```bash
D=$(mktemp -d) && cp ~/Projects/work_log.md "$D/"
WORKLOG_ROOT=$D python3 wl import          # expect the entry count, and no warnings
WORKLOG_ROOT=$D python3 wl render
WORKLOG_ROOT=$D python3 wl import --force  # expect the same count, twice in a row
rm -rf "$D"
```
Expected: the same entry count from both imports, no dropped-line warnings, exit 0.

- [ ] **Step 5: Commit**

```bash
git add worklog.py test_wl.py README.md RELEASING.md
git commit -m "docs+fix(export): fsync it, and stop calling the markdown the record

The inversion falsified three documents at once: the README intro, RELEASING.md's
trap list, and the header inside every generated file, which is the most widely
read prose in the project. All three now say the database is the record and the
export is derived, and the header points at wl edit and wl rm instead of an
editor.

export_md also fsyncs before os.replace. The replace is atomic for visibility but
not durable, and the export is the rescue copy of a database that is now the only
record.

Verified against the real 1060-entry log in a scratch WORKLOG_ROOT: import,
render, import --force, same count both times, no dropped lines.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## After this plan

Plan 2 (tasks 7 to 10: package split, `pyproject.toml`, PyPI trusted publishing, install
docs) is outlined in the spec and gets written once this lands, so it is planned against
the code that will exist rather than the code that does.

Two items stay deliberately out, recorded so they are choices rather than oversights:
the symmetric round-trip guard, which the inversion makes cosmetic, and backup of the log
itself, which is the user's concern and gets a README recommendation instead.
