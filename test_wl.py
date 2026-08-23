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


def test_import_and_stale_reimport():
    with worklog_root() as d:
        md = os.path.join(d, "work_log.md")
        with open(md, "w") as f:
            f.write(
                "# Work Log\n\n## 2026-06-30\n\n### general\n"
                "- 09:15 [note] first entry (refs: none)\n"
            )
        conn = wl.connect()                       # DB missing -> imports md
        rows = wl._all_entries(conn)
        assert len(rows) == 1 and rows[0].body == "first entry", rows
        conn.close()

        # hand-edit the markdown so it is newer than the DB
        import time
        time.sleep(0.05)
        with open(md, "a") as f:
            f.write("- 10:00 [note] second entry (refs: none)\n")
        conn = wl.connect()                       # md newer -> re-imports
        rows = wl._all_entries(conn)
        assert len(rows) == 2, rows
        conn.close()


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
            ["sh", "-c", f'"{sys.executable}" "{script}" log --limit 0 | head -1'],
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


def test_write_md_refuses_a_render_it_cannot_read_back():
    """The backstop: whatever slips past the input checks must not reach the file.

    work_log.md is the source of record and every command re-imports it when it is
    newer, so an entry the parser cannot read back is permanent, silent loss.
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
            wl.write_md(conn)
            raise AssertionError("expected the round-trip check to refuse the write")
        except SystemExit as ex:
            assert "my project" in str(ex.code), ex.code
        finally:
            conn.close()
        # The file on disk is untouched, so nothing that was safe got lost either.
        assert md.read_text() == before


if __name__ == "__main__":
    tests = sorted(n for n in dir() if n.startswith("test_"))
    for name in tests:
        globals()[name]()
        print(f"ok {name}")
    print(f"all passed ({len(tests)} tests)")
