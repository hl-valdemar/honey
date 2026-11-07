"""After `bro build`, run `python3 tests/testing.py` (Darwin arm64 + Clang)."""

from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
HONEY = ROOT / "build/honey"


def run(*arguments):
    return subprocess.run([str(HONEY), *map(str, arguments)], capture_output=True, timeout=300)


def check():
    with tempfile.TemporaryDirectory(prefix="honey tests ' ; $") as directory:
        project = Path(directory)
        (project / "build.hon").write_text("# project root\n")
        for name in ("std", "ffi"):
            (project / name).symlink_to(ROOT / name, target_is_directory=True)
        package = project / "app"
        package.mkdir()
        dependency = project / "dependency"
        dependency.mkdir()
        unrelated = project / "unrelated"
        unrelated.mkdir()
        (unrelated / "value.hon").write_text("expect proc(value bool) bool { return value }\n")
        (dependency / "value.hon").write_text("value proc() i16 { return 42 }\n")
        (dependency / "value.test.hon").write_text(
            'import "@std/testing"\n'
            'dependency_check test { try testing.expect_equal(i16(42), value()) }\n'
        )
        source = package / "main.hon"
        source.write_text(
            'import "@dependency"\n'
            'test import "@dependency"\n'
            'main proc() i16 { return dependency.value() }\n'
        )
        tests = package / "checks.test.hon"
        tests.write_text(
            'assertions :: import "@std/testing"\n'
            'import "@std/debug"\n'
            'First :: enum { first }\n'
            'Second :: enum { second }\n'
            'first proc() void ! First { return .first }\n'
            'second proc() void ! Second { return .second }\n'
            'passes test { try assertions.expect_equal(19 + 23, 42) }\n'
            'fails test {\n'
            '    defer debug.print("cleanup ran\\n", {})\n'
            '    try assertions.expect(false)\n'
            '    debug.print("unreachable after assertion\\n", {})\n'
            '}\n'
            'first_error test { errdefer debug.print("error cleanup ran\\n", {})\ntry first() }\n'
            'second_error test { try second() }\n'
            'returned_error test { return First.first }\n'
            'Payload :: struct { code i16 }\n'
            'payload proc() void ! Payload { return Payload { code = 42 } }\n'
            'payload_error test { try payload() }\n'
            'traps test { unreachable }\n'
            'after_trap test { try assertions.expect(true) }\n'
        )
        failed = run("test", package)
        assert failed.returncode == 1, (failed.returncode, failed.stdout, failed.stderr)
        assert b"cleanup ran\n" in failed.stderr, failed.stderr
        assert b"error cleanup ran\n" in failed.stderr, failed.stderr
        assert b"unreachable after assertion" not in failed.stderr, failed.stderr
        assert f"{tests.resolve()}:10:9: expectation failed".encode() in failed.stderr, failed.stderr
        assert b"checks.test.hon:after_trap ... ok" in failed.stderr, failed.stderr
        assert b"value.test.hon:dependency_check ... ok" in failed.stderr, failed.stderr
        assert b"3 passed, 6 failed\n" in failed.stderr, failed.stderr
        assert failed.stdout == b"", failed.stdout

        # Production never loads test-only dependencies or test files.
        (dependency / "value.test.hon").write_text("invalid test syntax\n")
        production = run("run", package)
        assert (production.returncode, production.stdout, production.stderr) == (0, b"42\n", b""), production
        malformed = run("test", package)
        assert malformed.returncode == 1 and b"unexpected token" in malformed.stderr, malformed.stderr
        assert b"cleanup ran" not in malformed.stderr, malformed.stderr

        # File inputs remain file-scoped; inline tests ignore the application's main.
        source.write_text(
            'checks :: import "@std/testing"\n'
            'other :: import "@unrelated"\n'
            'forward :: alias checks.expect\n'
            'main proc() i16 { return 42 }\n'
            'inline_check test {\n'
            '    try checks.expect_type(i16, i16(42))\n'
            '    try forward(other.expect(true))\n'
            '    try checks.expect(true, checks.SourceLocation { file = "explicit", line = 7, column = 3 })\n'
            '    try checks.expect_equal([]u8, "same", "same")\n'
            '    value ?i16 :: 42\n'
            '    try checks.expect_equal(value, value)\n'
            '}\n'
        )
        passing = run("test", source)
        assert passing.returncode == 0 and b"1 passed, 0 failed\n" in passing.stderr, passing.stderr
        assert b"checks.test.hon" not in passing.stderr, passing.stderr
        assert passing.stdout == b"", passing.stdout
        empty = run("test", dependency / "value.hon")
        assert empty.returncode == 0 and b"0 passed, 0 failed\n" in empty.stderr, empty.stderr
        usage = run("test", source, "unexpected")
        assert usage.returncode == 2 and b"usage:" in usage.stderr, usage.stderr
        only = project / "only"
        only.mkdir()
        (only / "checks.test.bro").write_text("only_test test {}\n")
        test_only = run("test", only)
        assert test_only.returncode == 0 and b"1 passed, 0 failed\n" in test_only.stderr, test_only.stderr
    print("Honey test discovery, assertions, error propagation, cleanup, traps and production isolation passed")


if __name__ == "__main__":
    check()
