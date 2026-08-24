"""Self-checks for worklog.py. Run: python3 test_wl.py"""
import io
import os
import pathlib
import subprocess
import sys
import tempfile
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from datetime import datetime

import worklog as wl


@contextmanager
def worklog_root():
    """Point wl at a throwaway root (via WORKLOG_ROOT) for the duration of a test."""
    with tempfile.TemporaryDirectory() as d:
        os.environ["WORKLOG_ROOT"] = d
        try:
            yield d
        finally:
            del os.environ["WORKLOG_ROOT"]


class _NS:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def test_resolve_at():
    fixed = datetime(2026, 6, 29, 10, 0, 0)
    assert wl.resolve_at("17:32", now=fixed) == "2026-06-29T17:32:00"
    assert wl.resolve_at("2026-06-29T17:32") == "2026-06-29T17:32:00"
    assert wl.resolve_at(None, now=fixed) == "2026-06-29T10:00:00"
    # reject both wrong shapes AND right-shape-impossible-value inputs
    for bad in ("nonsense", "5pm", "2026-06-29", "99:99 maybe",
                "99:99", "24:00", "2026-13-45T00:00", "2026-06-29T25:61"):
        try:
            wl.resolve_at(bad)
            raise AssertionError(f"expected ValueError for {bad!r}")
        except ValueError:
            pass


def test_normalize_refs():
    assert wl.normalize_refs("PROJ-1, PROJ-2") == "PROJ-1,PROJ-2"
    assert wl.normalize_refs("PROJ-1,PROJ-2") == "PROJ-1,PROJ-2"
    assert wl.normalize_refs(" PROJ-1 ") == "PROJ-1"
    assert wl.normalize_refs("") == ""
    assert wl.normalize_refs(None) == ""
    # empty segments (trailing comma, doubled comma, all-whitespace) are dropped
    assert wl.normalize_refs("PROJ-1,,PROJ-2") == "PROJ-1,PROJ-2"
    assert wl.normalize_refs("PROJ-1,") == "PROJ-1"
    assert wl.normalize_refs("  ,  ") == ""


def test_parse_markdown():
    md = (
        "# Work Log\n\nsome preamble line ignored\n\n"
        "## 2026-06-30\n\n"
        "### general\n"
        "- 09:15 [pr] trio for repo (refs: PROJ-4337, PROJ-2636)\n"
        "- 10:00 [note] created the log file\n"          # no refs suffix
        "\n### database\n"
        "- 11:05 [note] validation done (refs: none)\n"  # explicit none
    )
    entries = wl.parse_markdown(md)
    assert len(entries) == 3, entries
    e0 = entries[0]
    assert e0 == wl.Entry("2026-06-30T09:15:00", "general", "pr",
                          "PROJ-4337,PROJ-2636", "trio for repo"), e0
    assert entries[1].refs == "", entries[1]        # missing suffix
    assert entries[1].body == "created the log file"
    assert entries[2].slug == "database"
    assert entries[2].refs == ""                    # 'none' -> ''


def _sample_entries():
    return [
        wl.Entry("2026-06-29T11:39:00", "backend", "ticket", "PROJ-4416", "created hugepage task"),
        wl.Entry("2026-06-30T09:15:00", "general", "pr", "PROJ-4337", "trio for repo"),
        wl.Entry("2026-06-30T11:05:00", "database", "note", "", "validation done"),
        wl.Entry("2026-06-30T09:20:00", "general", "note", "PROJ-4337", "dependabot scan"),
    ]


def test_header_does_not_promise_hand_editing():
    # Casefolded: the header text uses "Hand-edits" (capital H), so a bare
    # "hand-edit" substring check never matches and the assertion passes
    # vacuously no matter what the header says.
    text = wl.HEADER_BLOCK.casefold()
    assert "hand-edit" not in text or "ignored" in text
    assert "wl edit" in wl.HEADER_BLOCK


def test_render_ordering():
    out = wl.render_markdown(_sample_entries(), ["general"])
    # newest day first
    assert out.index("## 2026-06-30") < out.index("## 2026-06-29")
    # within 2026-06-30: general (canonical first) before database
    assert out.index("### general") < out.index("### database")
    # within general: 09:15 before 09:20 (ts ascending)
    assert out.index("09:15 [pr]") < out.index("09:20 [note]")
    # empty refs render as 'none'
    assert "(refs: none)" in out
    assert "(refs: PROJ-4337)" in out


def test_roundtrip_idempotent():
    once = wl.render_markdown(_sample_entries(), ["general"])
    twice = wl.render_markdown(wl.parse_markdown(once), ["general"])
    assert once == twice, f"render not a fixed point:\n--- once ---\n{once}\n--- twice ---\n{twice}"


def test_roundtrip_preserves_entries():
    # render -> parse must return the entries intact, incl. awkward bodies
    order = ["general"]
    entries = [
        wl.Entry("2026-07-01T09:00:00", "general", "note", "PROJ-1,PROJ-2",
                 "colons: and [brackets] and a note-ish word in body"),
        wl.Entry("2026-07-01T09:05:00", "general", "pr", "", "plain body"),
    ]
    got = wl.parse_markdown(wl.render_markdown(entries, order))
    assert got == entries, got


def test_render_custom_order():
    order = ["general", "backend"]                          # 'infra' is unregistered
    entries = [
        wl.Entry("2026-07-01T09:00:00", "infra", "note", "", "c"),
        wl.Entry("2026-07-01T09:01:00", "general", "note", "", "a"),
        wl.Entry("2026-07-01T09:02:00", "backend", "note", "", "b"),
    ]
    out = wl.render_markdown(entries, order)
    # registered slugs in pos order, unregistered slug sorts last
    assert out.index("### general") < out.index("### backend") < out.index("### infra")


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
        # A render never absorbs the file, whatever the mtimes say, but it does not
        # silently overwrite it either: the mangled line is an entry the database has
        # never held, and the counts tie at one, which is what used to let it through.
        try:
            wl.cmd_render(_NS())
            raise AssertionError("expected SystemExit: the export holds an entry the DB lacks")
        except SystemExit as ex:
            assert "from the markdown" in str(ex.code), ex.code
        assert "from the markdown" in md.read_text()
        # Discarding it is the user's decision, and once taken the render is from the
        # database alone.
        md.unlink()
        wl.cmd_render(_NS())
        assert "from the database" in md.read_text()
        assert "from the markdown" not in md.read_text()


def test_import_is_gated_and_loud():
    """An unreadable line refuses the whole import, empty database or not.

    A partial import used to be committed on an empty database and the reader told to
    fix the file and import again, which stopped being true at the next `wl add`: that
    rewrites the export from the partial database and deletes the unreadable line for
    good, exit 0, silently.
    """
    with worklog_root() as d:
        md = pathlib.Path(d, "work_log.md")
        md.write_text("# Work Log\n\n## 2026-07-01\n\n### general\n"
                      "- 09:00 [note] good line (refs: none)\n"
                      "- 09:01 [note oops unparseable\n"
                      "- not even a timestamp\n")
        buf, err = io.StringIO(), io.StringIO()
        try:
            with redirect_stdout(buf), redirect_stderr(err):
                wl.cmd_import(_NS(force=False))
            raise AssertionError("expected a non-zero exit when lines were dropped")
        except SystemExit as ex:
            assert ex.code not in (0, None), ex.code
        # Line 6 is the good entry; 7 and 8 are the two that must be reported.
        assert "line 7" in err.getvalue(), err.getvalue()
        assert "line 8" in err.getvalue(), err.getvalue()
        # And nothing was written, so the file stays the only copy and stays fixable.
        conn = wl.connect()
        assert wl._all_entries(conn) == [], wl._all_entries(conn)
        conn.close()
        # A non-empty database is not clobbered without --force. The row goes in behind
        # the CLI's back because the export above holds a good line the database does
        # not, which is precisely what `wl add` now refuses to write over.
        conn = wl.connect()
        conn.execute("INSERT INTO entries(ts,slug,type,refs,body) VALUES(?,?,?,?,?)",
                     ("2026-07-01T09:00:00", "general", "note", "", "in the database"))
        conn.commit()
        conn.close()
        try:
            wl.cmd_import(_NS(force=False))
            raise AssertionError("expected SystemExit on a non-empty database")
        except SystemExit as ex:
            assert "--force" in str(ex.code), ex.code


def test_add_and_report():
    with worklog_root() as d:
        wl.cmd_add(_NS(slug="general", type="note", ref="PROJ-1, PROJ-2",
                       at="2026-06-30T09:00", body="hello world"))
        text = pathlib.Path(d, "work_log.md").read_text()
        assert "## 2026-06-30" in text
        assert "- 09:00 [note] hello world (refs: PROJ-1, PROJ-2)" in text
        buf = io.StringIO()
        with redirect_stdout(buf):
            wl.cmd_report(_NS(day="2026-06-30", since=None, until=None))
        assert "### general" in buf.getvalue()
        assert "09:00 [note] hello world" in buf.getvalue()


def test_add_body_sanitized():
    # a multi-line body must collapse to one line, else it breaks the md line format
    with worklog_root() as d:
        wl.cmd_add(_NS(slug="general", type="note", ref="",
                       at="2026-07-01T09:00", body="  line one\nline two  "))
        text = pathlib.Path(d, "work_log.md").read_text()
        # flattened to one line and stripped of surrounding whitespace
        assert "- 09:00 [note] line one line two (refs: none)" in text
        # str.splitlines() breaks on \r, \v, \f and \x1c-\x1e too, so replacing only
        # \n let one of those into the database, where it fails the round-trip guard
        # forever: every later `wl add` exits 1 naming the poisoned entry, and the
        # export stops tracking the database.
        wl.cmd_add(_NS(slug="general", type="note", ref="",
                       at="2026-07-01T09:01", body="carriage\rreturn\vand\ffriends"))
        text = pathlib.Path(d, "work_log.md").read_text()
        assert "- 09:01 [note] carriage return and friends (refs: none)" in text


def test_log_filters():
    with worklog_root():
        wl.cmd_add(_NS(slug="general", type="note", ref="PROJ-1",
                       at="2026-06-30T09:00", body="alpha"))
        wl.cmd_add(_NS(slug="backend", type="pr", ref="PROJ-2",
                       at="2026-06-30T10:00", body="beta"))
        wl.cmd_add(_NS(slug="general", type="note", ref="PROJ-10",
                       at="2026-07-01T09:00", body="gamma"))

        def run(**kw):
            base = {"slug": None, "type": None, "ref": None,
                    "since": None, "until": None, "limit": 0}
            base.update(kw)
            buf = io.StringIO()
            with redirect_stdout(buf):
                wl.cmd_log(_NS(**base))
            return buf.getvalue()

        out = run(slug="backend")
        assert "beta" in out and "alpha" not in out and "gamma" not in out

        # type filter (only 'beta' is a pr)
        out = run(type="pr")
        assert "beta" in out and "alpha" not in out and "gamma" not in out

        # combined filters are ANDed
        out = run(slug="general", type="note")
        assert "alpha" in out and "gamma" in out and "beta" not in out

        # ref matches per key, not raw substring: PROJ-1 must NOT match PROJ-10
        out = run(ref="PROJ-1")
        assert "alpha" in out and "gamma" not in out and "beta" not in out

        # since is inclusive of its bound and excludes earlier days
        out = run(since="2026-07-01")
        assert "gamma" in out and "alpha" not in out and "beta" not in out
        out = run(since="2026-06-30")
        assert "alpha" in out and "beta" in out and "gamma" in out

        # until is inclusive of its bound and excludes later days
        out = run(until="2026-06-30")
        assert "alpha" in out and "beta" in out and "gamma" not in out


def test_iso_week():
    # ISO weeks, so 2026-01-01 (a Thursday) belongs to week 1 of 2026 ...
    assert wl.iso_week("2026-01-01") == "2026-W01"
    # ... but 2027-01-01 (a Friday) still belongs to week 53 of 2026, which is
    # exactly where strftime %W would have disagreed.
    assert wl.iso_week("2027-01-01") == "2026-W53"
    assert wl.iso_week("2026-08-13") == "2026-W33"


def test_stats_counts_and_filters():
    with worklog_root():
        wl.cmd_add(_NS(slug="general", type="note", ref="PROJ-1,PROJ-2",
                       at="2026-06-30T09:00", body="alpha"))
        wl.cmd_add(_NS(slug="backend", type="pr", ref="PROJ-2",
                       at="2026-06-30T10:00", body="beta"))
        wl.cmd_add(_NS(slug="general", type="note", ref="",
                       at="2026-07-06T09:00", body="gamma"))

        def run(**kw):
            base = {"slug": None, "type": None, "ref": None,
                    "since": None, "until": None, "top": 10, "when": False}
            base.update(kw)
            buf = io.StringIO()
            with redirect_stdout(buf):
                wl.cmd_stats(_NS(**base))
            return buf.getvalue()

        out = run()
        assert "3 entries, 2026-06-30 to 2026-07-06" in out
        assert "note  2" in out and "pr    1" in out
        # PROJ-2 is cited by two entries, PROJ-1 by one: refs count per mention.
        assert "PROJ-2  2" in out and "PROJ-1  1" in out
        # Two distinct days one week apart must land in two different ISO weeks.
        assert "2026-W27" in out and "2026-W28" in out

        # The shared filter applies here exactly as it does to `log`.
        out = run(slug="backend")
        assert "1 entries" in out and "PROJ-1" not in out

        # An empty selection reports rather than dividing by zero on the widths.
        assert run(slug="nonexistent") == "(no entries)\n"

        # --top truncates and says how many it hid.
        out = run(top=1)
        assert "PROJ-2  1" in out or "PROJ-2  2" in out
        assert "... and 1 more" in out

        # --when is opt-in and adds clock-order blocks. 2026-06-30 is a Tuesday and
        # 2026-07-06 a Monday, so both must appear with Mon printed before Tue.
        assert "by weekday" not in run()
        out = run(when=True)
        assert "by hour" in out
        assert out.index("Mon") < out.index("Tue")
        assert "Wed" not in out  # weekdays with no entries are omitted, not zero-filled


def test_bar_scaling():
    # The largest row fills the width; a non-zero row never renders empty, which is
    # what keeps a 1-of-500 count visible instead of silently blank.
    assert wl._bar(10, 10, width=8) == "█" * 8
    assert wl._bar(1, 1000, width=8) == "█"
    assert wl._bar(0, 10, width=8) == ""


def test_report_range():
    with worklog_root():
        for at, body in (("2026-07-01T09:00", "day one"),
                         ("2026-07-02T09:00", "day two"),
                         ("2026-07-04T09:00", "day four")):
            wl.cmd_add(_NS(slug="general", type="note", ref="", at=at, body=body))

        def report(**kw):
            base = {"day": "today", "since": None, "until": None}
            base.update(kw)
            buf = io.StringIO()
            with redirect_stdout(buf):
                wl.cmd_report(_NS(**base))
            return buf.getvalue()

        # range spans multiple days, grouped newest-first, out-of-range excluded
        out = report(since="2026-07-02", until="2026-07-04")
        assert "day two" in out and "day four" in out and "day one" not in out
        assert out.index("## 2026-07-04") < out.index("## 2026-07-02")  # newest first

        # open-ended --since (no --until) reaches the latest day
        out = report(since="2026-07-04")
        assert "day four" in out and "day two" not in out

        # empty range reports cleanly
        assert "no entries" in report(since="2025-01-01", until="2025-12-31")


def test_report_empty_day():
    with worklog_root():
        buf = io.StringIO()
        with redirect_stdout(buf):
            wl.cmd_report(_NS(day="2020-01-01", since=None, until=None))
        assert "no entries" in buf.getvalue()


def test_main_add():
    with worklog_root() as d:
        buf = io.StringIO()
        with redirect_stdout(buf):
            wl.main(["add", "--slug", "general", "--type", "pr",
                     "--ref", "PROJ-9", "--at", "2026-06-30T12:00", "did a thing"])
        assert "- 12:00 [pr] did a thing (refs: PROJ-9)" in buf.getvalue()
        text = pathlib.Path(d, "work_log.md").read_text()
        assert "did a thing" in text


def test_validation_exits():
    with worklog_root():
        for argv in (
            ["add", "--slug", "general", "--type", "bogus", "x"],
            ["add", "--slug", "general", "--type", "note", "--at", "5pm", "x"],
        ):
            try:
                wl.main(argv)
                raise AssertionError(f"expected SystemExit for {argv}")
            except SystemExit as ex:
                assert ex.code not in (0, None), ex.code


def test_add_unknown_slug_warns_but_logs():
    with worklog_root() as d:
        err = io.StringIO()
        with redirect_stderr(err):
            wl.main(["add", "--slug", "mystery", "--type", "note",
                     "--at", "2026-07-01T09:00", "still logged"])
        assert "unknown slug" in err.getvalue()          # typo guard fired
        text = pathlib.Path(d, "work_log.md").read_text()
        assert "still logged" in text                    # entry written anyway


def test_slug_add_ls_rm():
    with worklog_root():
        def run(*argv):
            buf = io.StringIO()
            with redirect_stdout(buf):
                wl.main(["slug", *argv])
            return buf.getvalue()

        assert run("ls").split() == ["general"]        # seeded default
        run("add", "backend")
        run("add", "infra")
        assert run("ls").split() == ["general", "backend", "infra"]  # pos order

        assert "already registered" in run("add", "backend")
        assert run("ls").split() == ["general", "backend", "infra"]  # unchanged

        run("rm", "backend")
        assert run("ls").split() == ["general", "infra"]
        assert "was not registered" in run("rm", "nope")


def test_known_slugs_order():
    with worklog_root():
        conn = wl.connect()                            # seeds general at pos 0
        conn.execute("INSERT INTO slugs(name, pos) VALUES('zeta', 5), ('alpha', 1)")
        conn.commit()
        assert wl.known_slugs(conn) == ["general", "alpha", "zeta"]  # by pos, not name
        conn.close()


def test_slug_rm_with_entries():
    with worklog_root():
        wl.main(["slug", "add", "backend"])
        wl.cmd_add(_NS(slug="backend", type="note", ref="",
                       at="2026-07-01T09:00", body="x"))
        buf = io.StringIO()
        with redirect_stdout(buf):
            wl.main(["slug", "rm", "backend"])
        out = buf.getvalue()
        assert "removed slug 'backend'" in out
        assert "1 existing entries will now sort as unknown" in out


def test_slug_missing_name_exits():
    with worklog_root():
        for argv in (["slug", "add"], ["slug", "rm"]):
            try:
                wl.main(argv)
                raise AssertionError(f"expected SystemExit for {argv}")
            except SystemExit as ex:
                assert ex.code not in (0, None), ex.code



def test_log_limit():
    with worklog_root():
        for hh, body in (("09", "oldest"), ("10", "middle"), ("11", "newest")):
            wl.cmd_add(_NS(slug="general", type="note", ref="",
                           at=f"2026-07-01T{hh}:00", body=body))

        def run(limit):
            buf = io.StringIO()
            with redirect_stdout(buf):
                wl.cmd_log(_NS(slug=None, type=None, ref=None, since=None,
                               until=None, limit=limit))
            return buf.getvalue()

        # Output is newest-first, so a limit keeps the newest and drops the oldest.
        out = run(2)
        assert "newest" in out and "middle" in out and "oldest" not in out
        # Truncation is never silent: the count says what was withheld.
        assert "... and 1 more" in out

        # 0 means all, and then there is nothing to announce.
        out = run(0)
        assert "oldest" in out and "more" not in out


def test_log_ref_matches_body_mentions():
    with worklog_root():
        wl.cmd_add(_NS(slug="general", type="note", ref="PROJ-9",
                       at="2026-07-01T09:00", body="in the refs column"))
        wl.cmd_add(_NS(slug="general", type="note", ref="",
                       at="2026-07-01T10:00", body="mentioned PROJ-9 in prose only"))
        wl.cmd_add(_NS(slug="general", type="note", ref="",
                       at="2026-07-01T11:00", body="mentioned PROJ-10, a longer key"))

        def run(ref):
            buf = io.StringIO()
            with redirect_stdout(buf):
                wl.cmd_log(_NS(slug=None, type=None, ref=ref, since=None,
                               until=None, limit=0))
            return buf.getvalue()

        out = run("PROJ-9")
        assert "refs column" in out and "prose only" in out
        # Whole keys only, in the body as in the refs column: PROJ-9 is not PROJ-10.
        assert "longer key" not in out
        assert "longer key" in run("PROJ-10")


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


def test_log_survives_a_closed_pipe():
    """`wl log --limit 0 | head -1` must exit quietly, not print a traceback.

    Needs enough output to overflow the ~64 KB pipe buffer, or the writer finishes
    before the reader is gone and no SIGPIPE is ever delivered.
    """
    with worklog_root() as d:
        lines = "".join(f"- {i // 60:02d}:{i % 60:02d} [note] {'x' * 500} (refs: none)\n"
                        for i in range(300))
        (pathlib.Path(d) / "work_log.md").write_text(
            f"{wl.HEADER_BLOCK}\n\n## 2026-07-01\n\n### general\n{lines}")
        script = pathlib.Path(__file__).resolve().parent / "wl"
        proc = subprocess.run(
            ["sh", "-c", f'"{sys.executable}" "{script}" import >/dev/null && '
                         f'"{sys.executable}" "{script}" log --limit 0 | head -1'],
            capture_output=True, text=True, env={**os.environ, "WORKLOG_ROOT": d},
            check=False,
        )
        assert proc.stderr == "", proc.stderr



def test_a_refs_shaped_body_survives_the_round_trip():
    """A body that itself ends in "(refs: ...)" must not be parsed away.

    This is not hypothetical: the 2026-06-22 17:18 entry in the real log had its tail
    moved into the refs column, leaving refs = 'FM-4357) (refs: FM-4359'.
    """
    with worklog_root() as d:
        wl.cmd_add(_NS(slug="general", type="note", ref="FM-2",
                       at="2026-07-01T09:00", body="closed it (refs: FM-1)"))
        text = (pathlib.Path(d) / "work_log.md").read_text()
        entries = wl.parse_markdown(text)
        assert len(entries) == 1, entries
        assert entries[0].body == "closed it (refs: FM-1)", entries[0].body
        assert entries[0].refs == "FM-2", entries[0].refs


def test_add_rejects_a_slug_the_parser_cannot_read_back():
    with worklog_root():
        for name in ("my project", "", "two words"):
            try:
                wl.cmd_add(_NS(slug=name, type="note", ref="",
                               at="2026-07-01T09:00", body="doomed"))
                raise AssertionError(f"expected SystemExit for slug {name!r}")
            except SystemExit as ex:
                assert ex.code not in (0, None), ex.code
        # `slug add` guards the same alphabet, so the registry cannot hold one either.
        try:
            wl.main(["slug", "add", "my project"])
            raise AssertionError("expected SystemExit")
        except SystemExit as ex:
            assert ex.code not in (0, None), ex.code


def test_add_rejects_parens_in_refs():
    with worklog_root():
        try:
            wl.cmd_add(_NS(slug="general", type="note", ref="FM-1)",
                           at="2026-07-01T09:00", body="x"))
            raise AssertionError("expected SystemExit")
        except SystemExit as ex:
            assert ex.code not in (0, None), ex.code


def test_export_refuses_a_render_it_cannot_read_back():
    """The backstop: whatever slips past the input checks must not reach the file.

    The database is the source of record, but work_log.md is what `wl import` rebuilds
    it from if it is ever lost, so an entry the parser cannot read back would make that
    rescue copy silently incomplete.
    """
    with worklog_root() as d:
        wl.cmd_add(_NS(slug="general", type="note", ref="",
                       at="2026-07-01T09:00", body="keeper"))
        md = pathlib.Path(d) / "work_log.md"
        before = md.read_text()
        conn = wl.connect()
        # Inserted behind cmd_add's back, the way a hand-edited DB or a future bug
        # would: "### my project" is not readable by _SLUG_RE, so on the next import
        # every entry under that heading would be dropped.
        conn.execute("INSERT INTO entries(ts,slug,type,refs,body) VALUES(?,?,?,?,?)",
                     ("2026-07-01T10:00", "my project", "note", "", "doomed"))
        conn.commit()
        try:
            wl.export_md(conn)
            raise AssertionError("expected the round-trip check to refuse the write")
        except SystemExit as ex:
            assert "my project" in str(ex.code), ex.code
        finally:
            conn.close()
        # The file on disk is untouched, so nothing that was safe got lost either.
        assert md.read_text() == before


def test_export_refuses_to_shrink_an_existing_log():
    """A database holding fewer entries than the export must not overwrite it.

    The count is what matters, not emptiness. A root that has work_log.md but no
    work_log.db is the common case now that the default root is XDG (a fresh machine, a
    checkout carrying only the markdown), and `wl render` there used to replace a
    five-entry file with the header alone, exit 0, and say nothing.
    """
    with worklog_root() as d:
        md = pathlib.Path(d, "work_log.md")
        md.write_text(f"{wl.HEADER_BLOCK}\n\n## 2026-07-01\n\n### general\n"
                      + "".join(f"- 09:0{i} [note] entry {i} (refs: none)\n"
                                for i in range(5)))
        before = md.read_text()
        try:
            wl.cmd_render(_NS())
            raise AssertionError("expected SystemExit: DB empty, file holds entries")
        except SystemExit as ex:
            assert "wl import" in str(ex.code), ex.code
        assert md.read_text() == before
        # Adopt the file, and the same render is simply correct: equal counts never
        # refuse, or the guard would make the tool unusable rather than safe.
        with redirect_stdout(io.StringIO()):
            wl.cmd_import(_NS(force=False))
            wl.cmd_render(_NS())
        assert "entry 4" in md.read_text()
        # One line typed straight into the export is the same failure at full size:
        # every other entry is in the database, so only the difference changes.
        md.write_text(md.read_text() + "- 09:09 [note] typed into the file (refs: none)\n")
        try:
            wl.cmd_render(_NS())
            raise AssertionError("expected SystemExit: the file holds one entry more")
        except SystemExit as ex:
            assert "wl import" in str(ex.code), ex.code
        assert "typed into the file" in md.read_text()


def test_a_single_add_cannot_destroy_a_one_entry_export():
    """One entry in the export, none in the database: the first `wl add` must refuse.

    The counts tie at one the instant the row is committed, so a guard that compares
    after the insert sees a database that matches the file and overwrites it. The
    original entry is then in neither copy, exit 0, nothing printed.
    """
    with worklog_root() as d:
        md = pathlib.Path(d, "work_log.md")
        md.write_text(f"{wl.HEADER_BLOCK}\n\n## 2026-07-01\n\n### general\n"
                      "- 09:00 [note] ORIGINAL (refs: none)\n")
        before = md.read_text()
        raised = False
        try:
            with redirect_stdout(io.StringIO()):
                wl.cmd_add(_NS(slug="general", type="note", ref="",
                               at="2026-07-02T09:00", body="the newcomer"))
        except SystemExit as ex:
            raised = True
            assert "wl import" in str(ex.code), ex.code
        if not raised:
            raise AssertionError("the add overwrote an export the database did not hold")
        assert md.read_text() == before, md.read_text()
        # Nothing was written. A refusal that banks the row is what makes the counts
        # tie on the next attempt, which is how the guard talks itself into the write.
        conn = wl.connect()
        assert wl._all_entries(conn) == [], wl._all_entries(conn)
        conn.close()


def test_adds_cannot_equalise_their_way_past_the_export_guard():
    """Five entries in the export, none in the database: no number of adds may pass.

    A guard that compares after the insert refuses adds one to four and commits each
    row regardless. The fifth equalises the counts, passes, and takes all five original
    entries with it.
    """
    with worklog_root() as d:
        md = pathlib.Path(d, "work_log.md")
        md.write_text(f"{wl.HEADER_BLOCK}\n\n## 2026-07-01\n\n### general\n"
                      + "".join(f"- 09:0{i} [note] ORIGINAL {i} (refs: none)\n"
                                for i in range(5)))
        before = md.read_text()
        assert before.count("ORIGINAL") == 5, before
        for i in range(5):
            raised = False
            try:
                with redirect_stdout(io.StringIO()):
                    wl.cmd_add(_NS(slug="general", type="note", ref="",
                                   at="2026-07-02T09:00", body=f"newcomer {i}"))
            except SystemExit as ex:
                raised = True
                assert "wl import" in str(ex.code), ex.code
            if not raised:
                raise AssertionError(f"add {i} was allowed to overwrite the export")
            assert md.read_text().count("ORIGINAL") == 5, f"after add {i}: {md.read_text()}"
        assert md.read_text() == before, md.read_text()
        conn = wl.connect()
        assert wl._all_entries(conn) == [], wl._all_entries(conn)
        conn.close()


def test_a_hand_edit_at_equal_count_is_refused():
    """A line rewritten in place changes no count, and must still not be overwritten.

    Three entries on each side, so a count comparison sees two copies that agree and
    replaces the edited line with the row it was edited away from. Exit 0, nothing said,
    and the only copy of that wording is gone.
    """
    with worklog_root() as d:
        for i in range(3):
            with redirect_stdout(io.StringIO()):
                wl.cmd_add(_NS(slug="general", type="note", ref="",
                               at=f"2026-07-01T09:0{i}", body=f"entry {i}"))
        md = pathlib.Path(d, "work_log.md")
        md.write_text(md.read_text().replace("entry 1", "REWRITTEN BY HAND"))
        before = md.read_text()
        assert before.count("\n- ") == 3, before      # equal to the database, not ahead
        raised = False
        try:
            with redirect_stdout(io.StringIO()):
                wl.cmd_add(_NS(slug="general", type="note", ref="",
                               at="2026-07-02T09:00", body="the newcomer"))
        except SystemExit as ex:
            raised = True
            # The refusal names the entry at stake, not a quantity: a count is what the
            # reader cannot act on when deciding which copy to keep.
            assert "REWRITTEN BY HAND" in str(ex.code), ex.code
            assert "wl import" in str(ex.code), ex.code
        if not raised:
            raise AssertionError("the add overwrote a hand-edited line at equal count")
        assert md.read_text() == before, md.read_text()
        conn = wl.connect()
        assert len(wl._all_entries(conn)) == 3, wl._all_entries(conn)
        conn.close()


def test_an_export_behind_on_count_can_still_hold_an_orphan():
    """Four rows, three exported lines, one of them the database has never held.

    Three is not more than four, so a count comparison passes and the render deletes the
    orphan from the only copy that had it. How many entries each side holds says nothing
    about whether they are the same entries.
    """
    with worklog_root() as d:
        for i in range(4):
            with redirect_stdout(io.StringIO()):
                wl.cmd_add(_NS(slug="general", type="note", ref="",
                               at=f"2026-07-01T09:0{i}", body=f"entry {i}"))
        md = pathlib.Path(d, "work_log.md")
        kept = "\n".join(ln for ln in md.read_text().splitlines()
                         if "entry 2" not in ln and "entry 3" not in ln)
        md.write_text(f"{kept}\n- 09:09 [note] ONLY IN THE FILE (refs: none)\n")
        before = md.read_text()
        assert before.count("\n- ") == 3, before      # genuinely behind the database
        raised = False
        try:
            with redirect_stdout(io.StringIO()):
                wl.cmd_add(_NS(slug="general", type="note", ref="",
                               at="2026-07-02T09:00", body="the newcomer"))
        except SystemExit as ex:
            raised = True
            assert "ONLY IN THE FILE" in str(ex.code), ex.code
            # The two entries the file is missing are the database's business, not a
            # reason to refuse, so they must not be listed as at risk.
            assert "entry 2" not in str(ex.code), ex.code
        if not raised:
            raise AssertionError("the add destroyed an orphan in a file behind on count")
        assert md.read_text() == before, md.read_text()
        conn = wl.connect()
        assert len(wl._all_entries(conn)) == 4, wl._all_entries(conn)
        conn.close()


def test_identical_entries_are_not_collapsed_by_the_guard():
    """Two byte-identical entries are two entries on both sides of the comparison.

    Comparing sets of identities would collapse them, so an export holding the line
    twice against a database holding it once would look equal and lose a copy. And a
    database that legitimately holds both must not be refused against its own export.
    """
    with worklog_root() as d:
        md = pathlib.Path(d, "work_log.md")
        for _ in range(2):
            with redirect_stdout(io.StringIO()):
                wl.cmd_add(_NS(slug="general", type="note", ref="",
                               at="2026-07-01T09:00", body="the same thing twice"))
        assert md.read_text().count("the same thing twice") == 2, md.read_text()
        # A duplicate the database really holds is not a refusal: the next add proceeds.
        with redirect_stdout(io.StringIO()):
            wl.cmd_add(_NS(slug="general", type="note", ref="",
                           at="2026-07-01T09:01", body="a third"))
        conn = wl.connect()
        assert len(wl._all_entries(conn)) == 3, wl._all_entries(conn)
        conn.close()
        # A third copy of the same line, in the file only. Identical to two the database
        # does hold, and still an entry it does not.
        md.write_text(f"{md.read_text()}- 09:00 [note] the same thing twice (refs: none)\n")
        before = md.read_text()
        try:
            wl.cmd_render(_NS())
            raise AssertionError("expected SystemExit: the third copy is in the file alone")
        except SystemExit as ex:
            assert "the same thing twice" in str(ex.code), ex.code
            assert "holds 1 entry" in str(ex.code), ex.code
        assert md.read_text() == before, md.read_text()


def test_an_unreadable_export_refuses_rather_than_tracebacks():
    """Non-UTF-8 bytes in work_log.md must reach the user as a refusal, not a traceback.

    The count check reads the file on every command that re-renders it, where the read
    used to happen only against an empty database. An undecodable export made `wl add`
    die with an unhandled UnicodeDecodeError, and because the row was committed first,
    the export silently stopped tracking the database while the user got a stack trace.
    """
    with worklog_root() as d:
        md = pathlib.Path(d, "work_log.md")
        md.write_bytes(f"{wl.HEADER_BLOCK}\n\n## 2026-07-01\n\n### general\n".encode()
                       + b"- 09:00 [note] caf\xe9 (refs: none)\n")
        before = md.read_bytes()
        raised = False
        try:
            with redirect_stdout(io.StringIO()):
                wl.cmd_add(_NS(slug="general", type="note", ref="",
                               at="2026-07-02T09:00", body="the newcomer"))
        except SystemExit as ex:
            raised = True
            assert "cannot read" in str(ex.code), ex.code
        if not raised:
            raise AssertionError("expected SystemExit rather than a traceback")
        assert md.read_bytes() == before
        # Refused before the insert, so there is no committed row to explain away.
        conn = wl.connect()
        assert wl._all_entries(conn) == [], wl._all_entries(conn)
        conn.close()


def test_export_is_flushed_to_disk():
    """os.replace is atomic for visibility, not durable. The export is the rescue copy
    of a database that is now the only record, so it must be fsynced before the
    replace, not just written and left for the OS to flush on its own schedule.
    """
    with worklog_root():
        wl.cmd_add(_NS(slug="general", type="note", ref="",
                       at="2026-07-01T09:00", body="x"))
        calls = []
        real_fsync, real_replace = os.fsync, os.replace

        def fake_fsync(fd):
            calls.append("fsync")
            return real_fsync(fd)

        def fake_replace(*a, **kw):
            calls.append("replace")
            return real_replace(*a, **kw)

        os.fsync, os.replace = fake_fsync, fake_replace
        try:
            conn = wl.connect()
            wl.export_md(conn)
            conn.close()
        finally:
            os.fsync, os.replace = real_fsync, real_replace
        assert calls == ["fsync", "replace"], calls


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


def test_rm_on_the_last_entry_empties_the_export():
    """Removing the only entry must succeed rather than be refused.

    The export guard refuses to overwrite a file holding entries the database does not.
    `wl rm` passes it on its own merits rather than opting out: the check runs before
    the DELETE, where both copies still hold the one entry.
    """
    with worklog_root() as d:
        wl.cmd_add(_NS(slug="general", type="note", ref="",
                       at="2026-07-01T09:00", body="only entry"))
        buf = io.StringIO()
        with redirect_stdout(buf):
            wl.cmd_rm(_NS(id=1))
        assert "only entry" in buf.getvalue()
        text = pathlib.Path(d, "work_log.md").read_text()
        assert "only entry" not in text


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
        # `edit` sanitises a body exactly as `add` does, including the whitespace
        # classes str.splitlines() breaks on beyond \n.
        edit(body="carriage\rreturn")
        assert "[decision] carriage return (refs" in pathlib.Path(d, "work_log.md").read_text()
        edit(body="after")
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


def test_edit_unknown_slug_warns_but_edits():
    """`edit` shares add's typo guard, not just its validators.

    The warning exists because `--slug typoo` is accepted by every check `add` makes;
    an edit that could not warn was a silent way to file an entry under a bucket
    nothing else uses.
    """
    with worklog_root() as d:
        with redirect_stdout(io.StringIO()):
            wl.cmd_add(_NS(slug="general", type="note", ref="",
                           at="2026-07-01T09:00", body="filed"))
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            wl.cmd_edit(_NS(id=1, slug="mystery", type=None, ref=None,
                            at=None, body=None))
        assert "unknown slug" in err.getvalue(), err.getvalue()
        assert "### mystery" in pathlib.Path(d, "work_log.md").read_text()


def test_import_force_scans_before_it_deletes():
    """`--force` on a non-empty database must not delete before it knows the file is readable.

    The old order committed the DELETE and the parseable subset before reporting what it
    skipped, so on a non-empty database an unparseable line took its DB row down with it
    and the message telling the reader to fix the file and import again was already false.
    """
    with worklog_root() as d:
        wl.cmd_add(_NS(slug="general", type="note", ref="",
                       at="2026-07-01T09:00", body="already in the db"))
        md = pathlib.Path(d, "work_log.md")
        md.write_text("# Work Log\n\n## 2026-07-01\n\n### general\n"
                      "- 09:00 [note] good line (refs: none)\n"
                      "- 09:01 [note oops unparseable\n")
        try:
            wl.cmd_import(_NS(force=True))
            raise AssertionError("expected SystemExit before anything was deleted")
        except SystemExit as ex:
            assert "delete" in str(ex.code), ex.code
        conn = wl.connect()
        rows = wl._all_entries(conn)
        assert len(rows) == 1 and rows[0].body == "already in the db", rows
        conn.close()

        # Once the file is clean, --force does its job.
        md.write_text("# Work Log\n\n## 2026-07-01\n\n### general\n"
                      "- 09:00 [note] fresh (refs: none)\n")
        wl.cmd_import(_NS(force=True))
        conn = wl.connect()
        rows = wl._all_entries(conn)
        assert len(rows) == 1 and rows[0].body == "fresh", rows
        conn.close()


def test_import_refuses_to_shrink_the_database():
    """The rescue path must never be the thing that erases the log.

    `wl import --force` against a missing (or truncated, or header-only) work_log.md ran
    DELETE FROM entries, inserted nothing, committed, and printed "(0 entries)" with exit
    0. The scan-before-delete gate did not catch it, because a file holding no entries
    also holds no lines that fail to parse. `--force` does not mean "empty my log".
    """
    with worklog_root() as d:
        for body in ("one", "two", "three"):
            with redirect_stdout(io.StringIO()):
                wl.cmd_add(_NS(slug="general", type="note", ref="",
                               at="2026-07-01T09:00", body=body))
        md = pathlib.Path(d, "work_log.md")
        md.unlink()
        try:
            wl.cmd_import(_NS(force=True))
            raise AssertionError("expected SystemExit: there is no file to import")
        except SystemExit as ex:
            assert "does not exist" in str(ex.code), ex.code
        # A header-only file parses to zero entries, which is not a reason to empty a
        # database holding three.
        md.write_text(f"{wl.HEADER_BLOCK}\n")
        try:
            wl.cmd_import(_NS(force=True))
            raise AssertionError("expected SystemExit: the file holds fewer entries")
        except SystemExit as ex:
            # Not a bare "3": a tempdir path can hold one, and the assertion would
            # then pass on any message at all.
            assert "already holds 3" in str(ex.code), ex.code
        conn = wl.connect()
        assert len(wl._all_entries(conn)) == 3, wl._all_entries(conn)
        conn.close()


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
    with worklog_root() as d, \
            tempfile.TemporaryDirectory() as xdg, \
            tempfile.TemporaryDirectory() as chosen:
        # A legacy log next to the tool, and nothing at the new default, is exactly the
        # trap: without a warning the tool starts an empty log and abandons the real one.
        # XDG_DATA_HOME is pinned to an empty tempdir rather than left unset: with it
        # unset, db_path() falls through to the *real* machine's default location, so
        # this test would silently stop exercising the warning on any machine that has
        # already adopted the new default (db_path().exists() would be True there).
        del os.environ["WORKLOG_ROOT"]
        os.environ["XDG_DATA_HOME"] = xdg
        try:
            legacy = pathlib.Path(d, "work_log.db")
            legacy.write_bytes(b"")
            buf = io.StringIO()
            with redirect_stderr(buf):
                wl.warn_if_legacy_log_ignored(legacy_root=pathlib.Path(d))
            out = buf.getvalue()
            assert "WORKLOG_ROOT=" in out and str(d) in out
            # Silent when the caller has already chosen a root. `chosen` is a fresh,
            # empty tempdir with no work_log.db of its own, so this can only pass
            # because of the WORKLOG_ROOT check, not incidentally via db_path().exists().
            os.environ["WORKLOG_ROOT"] = chosen
            buf = io.StringIO()
            with redirect_stderr(buf):
                wl.warn_if_legacy_log_ignored(legacy_root=pathlib.Path(d))
            assert buf.getvalue() == ""
        finally:
            os.environ.pop("XDG_DATA_HOME", None)
            os.environ["WORKLOG_ROOT"] = d


def test_legacy_warning_repeats_until_the_new_log_has_entries():
    """The warning must survive an ordinary command being run in between two checks.

    warn_if_legacy_log_ignored used to key off db_path().exists(). But connect()
    creates that file via ensure_root() on any command, including a read, so the
    first command a user ever runs (even `wl report`) silences the warning for good
    while the legacy log sits untouched. It must key on the new log holding no
    entries, which keeps warning until the user actually acts on it.
    """
    with worklog_root() as d, tempfile.TemporaryDirectory() as xdg:
        del os.environ["WORKLOG_ROOT"]
        os.environ["XDG_DATA_HOME"] = xdg
        try:
            legacy = pathlib.Path(d, "work_log.db")
            legacy.write_bytes(b"")

            def warned():
                buf = io.StringIO()
                with redirect_stderr(buf):
                    wl.warn_if_legacy_log_ignored(legacy_root=pathlib.Path(d))
                return "WORKLOG_ROOT=" in buf.getvalue()

            assert warned()
            # Simulate an ordinary command: it opens (and thereby creates) the new,
            # still-empty database, the way `wl report` would on a first run.
            wl.connect().close()
            assert warned()
        finally:
            os.environ.pop("XDG_DATA_HOME", None)
            os.environ["WORKLOG_ROOT"] = d


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


if __name__ == "__main__":
    tests = sorted(n for n in dir() if n.startswith("test_"))
    for name in tests:
        globals()[name]()
        print(f"ok {name}")
    print(f"all passed ({len(tests)} tests)")
