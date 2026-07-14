"""Self-checks for worklog.py. Run: python3 test_wl.py"""
import io
import os
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime

import worklog as wl


def test_resolve_at():
    fixed = datetime(2026, 6, 29, 10, 0, 0)
    assert wl.resolve_at("17:32", now=fixed) == "2026-06-29T17:32:00"
    assert wl.resolve_at("2026-06-29T17:32") == "2026-06-29T17:32:00"
    assert wl.resolve_at(None, now=fixed) == "2026-06-29T10:00:00"
    for bad in ("nonsense", "5pm", "2026-06-29", "99:99 maybe"):
        try:
            wl.resolve_at(bad)
            assert False, f"expected ValueError for {bad!r}"
        except ValueError:
            pass


def test_normalize_refs():
    assert wl.normalize_refs("PROJ-1, PROJ-2") == "PROJ-1,PROJ-2"
    assert wl.normalize_refs("PROJ-1,PROJ-2") == "PROJ-1,PROJ-2"
    assert wl.normalize_refs(" PROJ-1 ") == "PROJ-1"
    assert wl.normalize_refs("") == ""
    assert wl.normalize_refs(None) == ""


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


def test_import_and_stale_reimport():
    with tempfile.TemporaryDirectory() as d:
        os.environ["WORKLOG_ROOT"] = d
        try:
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
        finally:
            del os.environ["WORKLOG_ROOT"]


class _NS:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def test_add_and_report():
    with tempfile.TemporaryDirectory() as d:
        os.environ["WORKLOG_ROOT"] = d
        try:
            wl.cmd_add(_NS(slug="general", type="note", ref="PROJ-1, PROJ-2",
                           at="2026-06-30T09:00", body="hello world"))
            text = open(os.path.join(d, "work_log.md")).read()
            assert "## 2026-06-30" in text
            assert "- 09:00 [note] hello world (refs: PROJ-1, PROJ-2)" in text
            buf = io.StringIO()
            with redirect_stdout(buf):
                wl.cmd_report(_NS(day="2026-06-30"))
            assert "### general" in buf.getvalue()
            assert "09:00 [note] hello world" in buf.getvalue()
        finally:
            del os.environ["WORKLOG_ROOT"]


def test_log_filters():
    with tempfile.TemporaryDirectory() as d:
        os.environ["WORKLOG_ROOT"] = d
        try:
            wl.cmd_add(_NS(slug="general", type="note", ref="PROJ-1",
                           at="2026-06-30T09:00", body="alpha"))
            wl.cmd_add(_NS(slug="backend", type="pr", ref="PROJ-2",
                           at="2026-06-30T10:00", body="beta"))
            wl.cmd_add(_NS(slug="general", type="note", ref="PROJ-10",
                           at="2026-07-01T09:00", body="gamma"))

            def run(**kw):
                base = dict(slug=None, type=None, ref=None, since=None, until=None)
                base.update(kw)
                buf = io.StringIO()
                with redirect_stdout(buf):
                    wl.cmd_log(_NS(**base))
                return buf.getvalue()

            out = run(slug="backend")
            assert "beta" in out and "alpha" not in out and "gamma" not in out

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
        finally:
            del os.environ["WORKLOG_ROOT"]


def test_main_add():
    with tempfile.TemporaryDirectory() as d:
        os.environ["WORKLOG_ROOT"] = d
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                wl.main(["add", "--slug", "general", "--type", "pr",
                         "--ref", "PROJ-9", "--at", "2026-06-30T12:00", "did a thing"])
            assert "- 12:00 [pr] did a thing (refs: PROJ-9)" in buf.getvalue()
            text = open(os.path.join(d, "work_log.md")).read()
            assert "did a thing" in text
        finally:
            del os.environ["WORKLOG_ROOT"]


def test_validation_exits():
    with tempfile.TemporaryDirectory() as d:
        os.environ["WORKLOG_ROOT"] = d
        try:
            for argv in (
                ["add", "--slug", "general", "--type", "bogus", "x"],
                ["add", "--slug", "general", "--type", "note", "--at", "5pm", "x"],
            ):
                try:
                    wl.main(argv)
                    assert False, f"expected SystemExit for {argv}"
                except SystemExit as ex:
                    assert ex.code not in (0, None), ex.code
        finally:
            del os.environ["WORKLOG_ROOT"]


def test_slug_add_ls_rm():
    with tempfile.TemporaryDirectory() as d:
        os.environ["WORKLOG_ROOT"] = d
        try:
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
        finally:
            del os.environ["WORKLOG_ROOT"]


def test_known_slugs_order():
    with tempfile.TemporaryDirectory() as d:
        os.environ["WORKLOG_ROOT"] = d
        try:
            conn = wl.connect()                            # seeds general at pos 0
            conn.execute("INSERT INTO slugs(name, pos) VALUES('zeta', 5), ('alpha', 1)")
            conn.commit()
            assert wl.known_slugs(conn) == ["general", "alpha", "zeta"]  # by pos, not name
            conn.close()
        finally:
            del os.environ["WORKLOG_ROOT"]


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


def test_add_unknown_slug_warns_but_logs():
    with tempfile.TemporaryDirectory() as d:
        os.environ["WORKLOG_ROOT"] = d
        try:
            err = io.StringIO()
            with redirect_stderr(err):
                wl.main(["add", "--slug", "mystery", "--type", "note",
                         "--at", "2026-07-01T09:00", "still logged"])
            assert "unknown slug" in err.getvalue()          # typo guard fired
            text = open(os.path.join(d, "work_log.md")).read()
            assert "still logged" in text                    # entry written anyway
        finally:
            del os.environ["WORKLOG_ROOT"]


if __name__ == "__main__":
    tests = sorted(n for n in dir() if n.startswith("test_"))
    for name in tests:
        globals()[name]()
        print(f"ok {name}")
    print(f"all passed ({len(tests)} tests)")
