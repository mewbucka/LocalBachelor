#!/usr/bin/env python3
"""Probe the target server environment before deploying (roadmap task 0.6).

Run this ON THE SERVER with the same interpreter the app will run under
(the repo venv's Python, not the system one; results are per-interpreter):

    .venv/bin/python scripts/probe_env.py

Prints PASS/FAIL per check and the actual versions found. Exit code:
0 if all critical checks pass, 1 otherwise. The FTS5 ICU tokenizer check
is optional (unicode61 is the fallback tokenizer, architecture.md section
17 item 3) and never affects the exit code.

If the sqlite-vec shared library is available without the Python package,
point SQLITE_VEC_PATH at it (e.g. /usr/lib/x86_64-linux-gnu/vec0.so).

After a passing run, record the found versions in architecture.md section
17 (Open decisions, item 7).

Output is pure ASCII: the probe runs on servers with arbitrary console
locales, and a UnicodeEncodeError would mask the real check results.
"""

from __future__ import annotations

import os
import platform
import shutil
import sqlite3
import struct
import sys
import tempfile
from dataclasses import dataclass

MIN_PYTHON = (3, 12)


@dataclass
class Check:
    name: str
    required: str
    ok: bool
    found: str
    critical: bool = True
    fix: str = ""


def check_python() -> Check:
    v = sys.version_info
    return Check(
        name="python",
        required=">= 3.12 (locked stack)",
        ok=(v.major, v.minor) >= MIN_PYTHON,
        found=f"{v.major}.{v.minor}.{v.micro} ({platform.python_implementation()})",
        fix="install Python 3.12 (distro package / pyenv / deadsnakes PPA) and re-run the probe with it",
    )


def check_sqlite_wal() -> Check:
    version = sqlite3.sqlite_version
    tmp = tempfile.mkdtemp(prefix="lbc_probe_")
    try:
        conn = sqlite3.connect(os.path.join(tmp, "probe.db"))
        try:
            conn.execute("CREATE TABLE t(x)")
            mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
        finally:
            conn.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return Check(
        name="sqlite_wal",
        required="SQLite with WAL journal mode (single-file DB)",
        ok=str(mode).lower() == "wal",
        found=f"SQLite {version}, journal_mode -> {mode}",
        fix="make the interpreter link a modern SQLite build (WAL is standard since 3.7.0)",
    )


def check_fts5(conn: sqlite3.Connection) -> Check:
    try:
        conn.execute("CREATE VIRTUAL TABLE probe_fts5 USING fts5(content)")
        conn.execute("INSERT INTO probe_fts5 VALUES ('hello fts5 probe')")
        row = conn.execute(
            "SELECT content FROM probe_fts5 WHERE probe_fts5 MATCH 'hello'"
        ).fetchone()
    except sqlite3.Error as exc:
        return Check(
            name="fts5",
            required="FTS5 virtual tables (BM25 half of hybrid retrieval)",
            ok=False,
            found=f"not available: {exc}",
            fix="use a SQLite build with FTS5 (most distro builds include it) so the interpreter links it",
        )
    return Check(
        name="fts5",
        required="FTS5 virtual tables (BM25 half of hybrid retrieval)",
        ok=row is not None,
        found=f"FTS5 create/insert/MATCH ok (SQLite {sqlite3.sqlite_version})",
        fix="use a SQLite build with FTS5",
    )


def _vec_version(conn: sqlite3.Connection) -> str:
    try:
        row = conn.execute("SELECT vec_version()").fetchone()
    except sqlite3.Error:
        return ""
    return str(row[0]) if row else ""


def check_sqlite_vec(conn: sqlite3.Connection) -> Check:
    required = "sqlite-vec extension + vec0 vector tables (vector half of retrieval)"

    def fail(found: str, fix: str = "pip install sqlite-vec in the app venv") -> Check:
        return Check(name="sqlite_vec", required=required, ok=False, found=found, fix=fix)

    try:
        import sqlite_vec
    except ImportError:
        sqlite_vec = None

    if sqlite_vec is not None:
        try:
            # Python 3.11+ disables load_extension by default; the Phase 3
            # vectorstore must do the same before loading sqlite-vec.
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
        except sqlite3.Error as exc:
            return fail(
                f"module installed but extension failed to load: {exc}",
                "the interpreter's SQLite may be built without loadable extensions; "
                "use a Python build that allows them",
            )
    else:
        path = os.environ.get("SQLITE_VEC_PATH")
        if not path:
            return fail(
                "python package 'sqlite-vec' not installed; SQLITE_VEC_PATH not set",
                "pip install sqlite-vec in the app venv, or set SQLITE_VEC_PATH to a vec0 shared library",
            )
        try:
            conn.enable_load_extension(True)
            conn.load_extension(path)
        except sqlite3.Error as exc:
            return fail(f"load_extension({path}) failed: {exc}")

    try:
        conn.execute("CREATE VIRTUAL TABLE probe_vec0 USING vec0(e float[4])")
        blob = struct.pack("<4f", 0.1, 0.2, 0.3, 0.4)
        conn.execute("INSERT INTO probe_vec0(rowid, e) VALUES (1, ?)", (blob,))
        row = conn.execute(
            "SELECT rowid, distance FROM probe_vec0 WHERE e MATCH ? AND k = 1 ORDER BY distance",
            (blob,),
        ).fetchone()
    except sqlite3.Error as exc:
        return fail(f"vec0 virtual table failed: {exc}")
    if row is None or row[0] != 1:
        return fail("vec0 KNN query returned no row")

    ver = _vec_version(conn)
    found = f"sqlite-vec {ver}, vec0 KNN roundtrip ok" if ver else "vec0 KNN roundtrip ok (version unknown)"
    return Check(name="sqlite_vec", required=required, ok=True, found=found)


def check_fts5_icu(conn: sqlite3.Connection) -> Check:
    details = []
    any_ok = False
    for lang in ("uk", "en"):
        try:
            conn.execute(
                f"CREATE VIRTUAL TABLE probe_icu_{lang} USING fts5(content, tokenize='icu {lang}')"
            )
            details.append(f"icu {lang}: ok")
            any_ok = True
        except sqlite3.Error as exc:
            details.append(f"icu {lang}: {exc}")
    return Check(
        name="fts5_icu",
        required="optional: ICU tokenizer (better uk/en tokenization)",
        ok=any_ok,
        found="; ".join(details),
        critical=False,
        fix="not required; FTS5 falls back to the unicode61 tokenizer (architecture.md section 17 item 3); "
        "to enable, use a SQLite build compiled with ICU",
    )


def run_checks() -> list[Check]:
    checks = [check_python(), check_sqlite_wal()]
    conn = sqlite3.connect(":memory:")
    try:
        checks.append(check_fts5(conn))
        checks.append(check_sqlite_vec(conn))
        checks.append(check_fts5_icu(conn))
    finally:
        conn.close()
    return checks


def main() -> int:
    # Defense in depth for exotic console encodings (e.g. a non-ASCII
    # interpreter path): never crash, never mask the check results.
    sys.stdout.reconfigure(errors="replace")
    checks = run_checks()
    name_w = max(len(c.name) for c in checks)
    print("LocalBachelor environment probe (roadmap task 0.6)")
    print(f"  interpreter : {platform.python_version()} ({sys.executable})")
    print(f"  platform    : {platform.platform()}")
    print()
    for c in checks:
        tag = "PASS" if c.ok else ("WARN" if not c.critical else "FAIL")
        kind = "critical" if c.critical else "optional"
        print(f"  [{tag}] {c.name.ljust(name_w)}  {kind:8}  required: {c.required}")
        print(f"          found: {c.found}")
        if not c.ok:
            print(f"          fix:   {c.fix}")
    critical = [c for c in checks if c.critical]
    failed = [c for c in critical if not c.ok]
    print()
    print(f"  critical checks: {len(critical) - len(failed)}/{len(critical)} passed")
    if failed:
        print(f"  RESULT: FAIL: failing critical checks: {', '.join(c.name for c in failed)}")
    else:
        print("  RESULT: OK: all critical checks passed; the host is deployable.")
    print()
    print("  Record the found versions in architecture.md section 17 (Open decisions, item 7).")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
