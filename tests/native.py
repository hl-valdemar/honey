"""Build Honey first with `bro build`, then run `python3 tests/native.py`."""

import os
from pathlib import Path
import subprocess
import tempfile
import textwrap

ROOT = Path(__file__).resolve().parents[1]
HONEY = ROOT / "build/honey"

# Explicit expectations guard against frontend bugs shared by both execution paths.
CASES = {
    "void": ("main proc() void {}", b"", None),
    "signed_limits": (
        "main proc() [2]i64 { return [-9223372036854775808, 9223372036854775807] }",
        b"[-9223372036854775808, 9223372036854775807]\n", None,
    ),
    "float_context": ("main proc() float { return 3 / 2 }", b"1.5\n", None),
    "float_ieee": (
        """
        main proc() bool {
            nan :: 0.0 / 0.0
            inf :: 1.0 / 0.0
            return nan != nan and !(nan == nan) and !(nan < 0.0) and !(nan >= 0.0) and inf > 1.0
        }
        """, b"true\n", None,
    ),
    "float_rounding": (
        "main proc() f64 { return 9007199254740993.0 - 9007199254740992 }", b"0\n", None,
    ),
    "strings": (r'main proc() string { return "a\0b\n\"\\" }', b'a\0b\n"\\\n', None),
    "scalar_call_types": (
        """
        nothing proc() void {}
        twice proc(x f64) f64 { return x * 2 }
        select proc(flag bool, left string, right string) string {
            nothing()
            if flag { return left }
            return right
        }
        main proc() string { return select(twice(1.5) == 3.0, "yes", "no") }
        """, b"yes\n", None,
    ),
    "mutual_recursion": (
        """
        even proc(n int) bool { if n == 0 { return true } return odd(n - 1) }
        odd proc(n int) bool { if n == 0 { return false } return even(n - 1) }
        main proc() bool { return even(20) and odd(19) }
        """, b"true\n", None,
    ),
    "function_values": (
        """
        plus proc(n int) int { return n + 1 }
        minus proc(n int) int { return n - 1 }
        main proc() bool {
            f := plus
            f = minus
            a :: [plus, f]
            return a[0](41) == 42 and a[1](43) == 42
        }
        """, b"true\n", None,
    ),
    "short_circuit": (
        """
        cycle bool :: cycle
        bad proc() bool { return 9223372036854775807 + 1 == 0 }
        main proc() bool { return !(false and bad()) and (true or cycle) }
        """, b"true\n", None,
    ),
    "lazy_globals": (
        """
        first :: identity(later)
        later [2][2]string :: [["one", "two"], ["three", "four"]]
        identity proc(a [2][2]string) [2][2]string { return a }
        main proc() [2][2]string { return identity(identity(first)) }
        """, b"[[one, two], [three, four]]\n", None,
    ),
    "array_copies": (
        """
        seed [2]mut [2]mut i64 :: [[1, 2], [3, 4]]
        identity proc(a [2]mut [2]mut i64) [2]mut [2]mut i64 { return a }
        main proc() [2]mut [2]mut i64 {
            a := identity(seed)
            b := a
            (a[0])[1] = 99
            a[1] = a[0]
            return b
        }
        """, b"[[1, 2], [3, 4]]\n", None,
    ),
    "zero_leaf_arrays": (
        """
        identity proc(a [2][0]bool) [2][0]bool { return a }
        main proc() [2][0]bool { return identity([[], []]) }
        """, b"[[], []]\n", None,
    ),
    "array_branchful_arguments": (
        """
        identity proc(a [2]bool) [2]bool { return a }
        pair proc(a [2]bool, b [2]bool) [2][2]bool { return [a, b] }
        main proc() [2][2]bool {
            return pair(identity([true or false, false and true]), identity([false or true, !false]))
        }
        """, b"[[true, false], [true, true]]\n", None,
    ),
    "unsigned_max_range": (
        """
        main proc() int {
            end usize :: 18446744073709551615
            start usize :: 18446744073709551614
            count := 0
            for start..=end |i| {
                if (i < start) return 0
                count = count + 1
                continue
            }
            return count
        }
        """, b"2\n", None,
    ),
    "unsigned_comparisons": (
        """
        main proc() bool {
            a usize :: 9223372036854775808
            return a > 9223372036854775807 and a * 1 + 1 - 1 == a
        }
        """, b"true\n", None,
    ),
    "signed_range_limits": (
        """
        main proc() int {
            maximum :: 9223372036854775807
            count := 0
            for maximum..=maximum |_| { count = count + 1 }
            for 2..1 |_| { count = 0 }
            for (-2)..0 |_| { count = count + 1 }
            return count
        }
        """, b"3\n", None,
    ),
    "loop_array_storage": (
        """
        main proc() int {
            n := 0
            a [1]mut i64 := [0]
            while n < 10000 {
                row [1]mut i64 := [a[0]]
                row[0] = row[0] + 1
                a = row
                n = n + 1
            }
            return a[0]
        }
        """, b"10000\n", None,
    ),
    "forward_local": (
        "main proc() int {\n before :: after\n after :: 42\n return before\n}",
        b"", b"local value was read before initialization",
    ),
    "forward_assignment": (
        "main proc() int {\n after = 1\n after := 42\n return after\n}",
        b"", b"local value was assigned before initialization",
    ),
    "loop_local_reset": (
        """
        main proc() int {
            n := 0
            while n < 2 {
                n = n + 1
                if (n == 2) return later[0]
                later [1]i64 :: [42]
            }
            return 0
        }
        """, b"", b"local value was read before initialization",
    ),
    "global_cycle": (
        "a [1]i64 :: b\nb [1]i64 :: a\nmain proc() int { return a[0] }",
        b"", b"global initialization cycle",
    ),
    "bounds": (
        "main proc() int {\n a :: [42]\n return a[1]\n}", b"", b"index out of bounds",
    ),
    "empty_bounds": (
        "main proc() int {\n a [0]i64 :: []\n return a[0]\n}", b"", b"index out of bounds",
    ),
    "unsigned_bounds": (
        "main proc() int {\n a [1]mut i64 := [1]\n a[18446744073709551615] = 2\n return 0\n}",
        b"", b"index out of bounds",
    ),
    "assignment_error_order": (
        "main proc() int {\n a [0]mut i64 := []\n a[0] = 9223372036854775807 + 1\n return 0\n}",
        b"", b"integer arithmetic overflow",
    ),
    "signed_add_overflow": (
        "main proc() int { return 9223372036854775807 + 1 }", b"", b"integer arithmetic overflow",
    ),
    "signed_subtract_overflow": (
        "main proc() int { return -9223372036854775808 - 1 }", b"", b"integer arithmetic overflow",
    ),
    "signed_multiply_overflow": (
        "main proc() int { return 9223372036854775807 * 2 }", b"", b"integer arithmetic overflow",
    ),
    "signed_negate_overflow": (
        "main proc() int { return -(-9223372036854775808) }", b"", b"integer arithmetic overflow",
    ),
    "unsigned_subtract_overflow": (
        "main proc() usize {\n zero usize :: 0\n return zero - 1\n}", b"", b"integer arithmetic overflow",
    ),
    "unsigned_add_overflow": (
        "main proc() usize { return 18446744073709551615 + 1 }", b"", b"integer arithmetic overflow",
    ),
    "unsigned_multiply_overflow": (
        "main proc() usize {\n max usize :: 18446744073709551615\n return max * 2\n}",
        b"", b"integer arithmetic overflow",
    ),
    "unused_errors": (
        "bad proc() int { return absent }\nmain proc() int { return 42 }", b"42\n", b"unknown name",
    ),
    "reached_source_error": (
        "main proc() int { return absent }", b"", b"unknown name",
    ),
    "missing_return": (
        "main proc() int { if false { return 42 } }", b"", b"nonvoid function can finish",
    ),
    "missing_entry": ("other proc() void {}", b"", b"program requires a main function"),
    "invalid_unused_signature": (
        "bad proc(a void) void {}\nmain proc() int { return 42 }", b"42\n", b"void cannot be stored",
    ),
}

# Exercise each representation through storage, calls, ranges and checked updates.
for integer_type, bits, signed in (
    ("i8", 8, True), ("i16", 16, True), ("i32", 32, True),
    ("i64", 64, True), ("isize", 64, True),
    ("u8", 8, False), ("u16", 16, False), ("u32", 32, False),
    ("u64", 64, False), ("usize", 64, False),
):
    minimum = -(1 << (bits - 1)) if signed else 0
    maximum = (1 << (bits - int(signed))) - 1
    CASES[f"{integer_type}_storage"] = (
        f"""
        seed [2]mut {integer_type} :: [{minimum}, {maximum}]
        identity proc(a [2]mut {integer_type}) [2]mut {integer_type} {{ return a }}
        main proc() [2]mut {integer_type} {{
            a := identity(seed)
            b := a
            a[0] = 1
            return b
        }}
        """, f"[{minimum}, {maximum}]\n".encode(), None,
    )
    CASES[f"{integer_type}_inclusive_max"] = (
        f"""
        main proc() int {{
            start {integer_type} :: {maximum - 1}
            end {integer_type} :: {maximum}
            count := 0
            for start..=end |i| {{
                if (i < start) return -1
                count = count + 1
                continue
            }}
            return count
        }}
        """, b"2\n", None,
    )
    for operation, left, right in (
        ("+", maximum, 1), ("-", minimum, 1), ("*", maximum, 2),
    ):
        CASES[f"{integer_type}_overflow_{operation}"] = (
            f"main proc() {integer_type} {{ a {integer_type} :: {left}\nreturn a {operation} {right} }}",
            b"", b"integer arithmetic overflow",
        )
    CASES[f"{integer_type}_literal_range"] = (
        f"main proc() {integer_type} {{ return {maximum + 1} }}",
        b"", b"numeric literal is outside",
    )
    if signed:
        CASES[f"{integer_type}_negate_min"] = (
            f"main proc() {integer_type} {{ a {integer_type} :: {minimum}\nreturn -a }}",
            b"", b"integer arithmetic overflow",
        )

CASES.update({
    "integer_casts": (
        """
        identity proc(n u8) u8 { return n }
        main proc() [4]i64 {
            large u64 :: 18446744073709551615
            small := identity(u8(i16(255)))
            if (large != u64(18446744073709551615)) return [0, 0, 0, 0]
            return [i64(small), i64(i8(-128)), i64(u32(4294967295)), i64(u8(255))]
        }
        """,
        b"[255, -128, 4294967295, 255]\n", None,
    ),
    "cast_unsigned_max": ("main proc() u64 { return u64(18446744073709551615) }", b"18446744073709551615\n", None),
    "cast_float_rounding": ("main proc() f64 { return f64(9007199254740993) - 9007199254740992.0 }", b"0\n", None),
    "cast_fractional": ("main proc() [4]i64 { return [i64(i8(-128.9)), i64(u8(-0.9)), i64(u8(255.9)), i64(i8(127.9))] }", b"[-128, 0, 255, 127]\n", None),
    "cast_float_u64_max_below": ("main proc() u64 { return u64(18446744073709549568.0) }", b"18446744073709549568\n", None),
    "cast_float_i64_min": ("main proc() i64 { return i64(-9223372036854775808.0) }", b"-9223372036854775808\n", None),
    "cast_unused_trap": ("bad proc() u8 { return u8(256) }\nmain proc() int { return 42 }", b"42\n", None),
    "cast_short_circuit": ("main proc() bool { return true or u8(256) == 0 }", b"true\n", None),
    "cast_bool_invalid": ("main proc() u8 { return u8(true) }", b"", b""),
    "cast_zero_arguments": ("main proc() u8 { return u8() }", b"", b"argument count"),
    "cast_extra_arguments": ("main proc() u8 { return u8(1, 2) }", b"", b"argument count"),
    "cast_type_as_value": ("main proc() int { u8\nreturn 42 }", b"", b"type name cannot"),
    "integer_no_implicit_narrowing": ("main proc() u8 { a u16 :: 1\nreturn a }", b"", b"return does not match"),
    "integer_no_implicit_signedness": ("main proc() i8 { a u8 :: 1\nreturn a }", b"", b"return does not match"),
    "integer_no_implicit_widening": ("main proc() u16 { a u8 :: 1\nreturn a }", b"", b"return does not match"),
    "integer_assignment_cast": (
        "main proc() u8 { a u8 := 1\nb u16 :: 254\na = u8(b + 1)\nreturn a }",
        b"255\n", None,
    ),
    "integer_function_value": (
        "identity proc(n i16) i16 { return n }\nmain proc() i16 { f :: identity\nreturn f(i16(-32768)) }",
        b"-32768\n", None,
    ),
    "integer_typed_float_cast": (
        "main proc() f64 { a u64 :: 18446744073709551615\nreturn f64(a) - 18446744073709551616.0 }",
        b"0\n", None,
    ),
    "integer_bad_index": ("main proc() i64 { a :: [42]\ni u8 :: 0\nreturn a[i] }", b"", b"array index must have type usize"),
    "integer_cast_index": ("main proc() i64 { a :: [42]\ni u8 :: 0\nreturn a[usize(i)] }", b"42\n", None),
    "integer_cast_unknown": ("main proc() u8 { return u8(missing) }", b"", b"unknown name"),
    "integer_cast_bad_unused": ("bad proc() u8 { return u8(true) }\nmain proc() int { return 42 }", b"42\n", b""),
})
for name, expression in {
    "narrow": "u8(256)",
    "signed_negative": "u8(-1)",
    "signed_high": "i8(128)",
    "unsigned_to_signed": "i64(u64(18446744073709551615))",
    "float_high": "u8(256.0)",
    "float_low": "i8(-129.0)",
    "float_unsigned_negative": "u8(-1.0)",
    "float_signed_limit": "i64(9223372036854775808.0)",
    "float_unsigned_limit": "u64(18446744073709551616.0)",
    "nan": "i32(0.0 / 0.0)",
    "infinity": "u32(1.0 / 0.0)",
}.items():
    CASES[f"cast_trap_{name}"] = (
        f"main proc() i64 {{ return i64({expression}) }}", b"", b"integer arithmetic overflow",
    )


def run(*args, env=None):
    return subprocess.run(args, cwd=ROOT, capture_output=True, timeout=30, env=env)


def outcome(result):
    return result.returncode, result.stdout, result.stderr


def check(source, executable, expected=None):
    interpreted = run(str(HONEY), "run", str(source))
    assert interpreted.returncode in (0, 1), outcome(interpreted)
    if expected is not None:
        stdout, error = expected
        assert interpreted.stdout == stdout, (source.name, outcome(interpreted), stdout)
        assert interpreted.returncode == (1 if error is not None else 0), outcome(interpreted)
        if error is not None:
            assert interpreted.stderr and error in interpreted.stderr, outcome(interpreted)
    for optimization in ("-O0", "-O2"):
        built = run(str(HONEY), "build", str(source), "-o", str(executable), optimization)
        assert built.returncode in (0, 1), (source.name, optimization, outcome(built))
        assert built.stdout == b"", outcome(built)
        assert executable.is_file(), (source.name, outcome(built))
        compiled = run(str(executable))
        assert outcome(compiled) == outcome(interpreted), (
            source.name, optimization, outcome(interpreted), outcome(compiled)
        )
        executable.unlink()


def check_packages(temporary, executable):
    project = temporary / "packages"

    def put(path, text):
        target = project / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        return target

    put("build.hon", "# project-root marker\n")
    app = project / "app"
    main = put("app/main.hon", 'import "../left"\nright :: import "@right"\nimport "../shared"\nmain proc() int { return helper(21) }\n')
    helper = put("app/zhelper.bro", "helper proc(n int) int { return n * 2 }\n")
    put("app/ignored.test.hon", "this is deliberately not valid source\n")
    (app / "directory.hon").mkdir()
    fifo = project / "pipe"
    os.mkfifo(fifo)
    (app / "pipe.hon").symlink_to(fifo)
    put("left/left.hon", 'import "../shared"\nhelper proc() int { return 1 }\nmain proc() int { return 99 }\n')
    put("right/right.hon", 'import "../shared-link"\nhelper proc() int { return 2 }\n')
    shared = put("shared/shared.hon", "helper proc() int { return 3 }\n")
    (project / "shared-link").symlink_to(project / "shared", target_is_directory=True)
    check(app, executable, (b"42\n", None))
    # File inputs retain their old single-file semantics, even inside a package.
    check(main, executable, (b"", b"unknown name"))
    emitted = run(str(HONEY), "emit-llvm", str(app))
    assert emitted.returncode == 0 and emitted.stderr == b"", outcome(emitted)
    shared.write_text("bad proc() int { return missing }\n")
    check(app, executable, (b"42\n", b"unknown name"))
    diagnosed = run(str(HONEY), "run", str(app))
    assert diagnosed.stderr.count(b"unknown name") == 1, outcome(diagnosed)
    assert str(shared.resolve()).encode() + b":1:" in diagnosed.stderr, outcome(diagnosed)
    # Dependency main functions cannot supply an entry for the root package.
    main.write_text('import "../left"\nentry proc() int { return 42 }\n')
    shared.write_text("helper proc() int { return 3 }\n")
    check(app, executable, (b"", b"program requires a main function"))
    # Source scopes merge within a package, never across dependency packages.
    main.write_text('import "../left"\nmain proc() int { return helper() }\n')
    helper.write_text("local_only proc() int { return 7 }\n")
    check(app, executable, (b"", b"unknown name"))
    main.write_text('import "../left"\nmain proc() int { return 42 }\n')
    shared.write_text('import "../left"\n')
    check(app, executable, (b"42\n", b"package import cycle"))
    shared.write_text('import "../shared-link"\n')
    check(app, executable, (b"42\n", b"package import cycle"))
    shared.write_text("helper proc() int { return 3 }\n")
    for imported, message in (
        ("/tmp", b"invalid import path"),
        ("@/tmp", b"invalid import path"),
        ("../absent", b"cannot load package directory"),
        ("../shared/shared.hon", b"cannot load package directory"),
        ("../empty", b"cannot load package directory"),
    ):
        (project / "empty").mkdir(exist_ok=True)
        main.write_text(f'import "{imported}"\nmain proc() int {{ return 42 }}\n')
        check(app, executable, (b"42\n", message))
    main.write_text('test import "../absent"\nmain proc() int { return 42 }\n')
    check(app, executable, (b"42\n", None))
    # Directory order is lexical, not filesystem insertion order; first declaration wins.
    put("duplicate/z.hon", "value :: 99\n")
    put("duplicate/a.hon", "value :: 41\n")
    put("duplicate/main.hon", "main proc() int { return value }\n")
    check(project / "duplicate", executable, (b"41\n", b"duplicate name"))
    # Runtime locations must survive source/frontend cleanup in generated executables.
    main.write_text("main proc() int { return fail() }\n")
    helper.write_text("\nfail proc() int { return 9223372036854775807 + 1 }\n")
    check(app, executable, (b"", b"integer arithmetic overflow"))
    trapped = run(str(HONEY), "run", str(app))
    assert str(helper.resolve()).encode() + b":2:" in trapped.stderr, outcome(trapped)
    empty = run(str(HONEY), "run", str(project / "empty"))
    assert empty.returncode == 2 and b"no production source files" in empty.stderr, outcome(empty)
    special = run(str(HONEY), "run", str(fifo))
    assert special.returncode == 2 and b"cannot open source file" in special.stderr, outcome(special)
    return 15


def check_namespace_packages(temporary, executable):
    project = temporary / "namespaces"

    def put(path, text):
        target = project / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        return target

    put("build.hon", "# project root\n")
    main = put("app/main.hon", """
import "../left"
right :: import "@right"
import "../shared-link"
main proc() int {
    functions :: [left.helper, right.helper]
    selected := left.helper
    selected = right.helper
    return sibling(18) + functions[0]() + selected() + shared.helper()
}
""")
    sibling = put("app/sibling.hon", """
right :: import "../left"
sibling proc(n int) int { return n * 2 + right.helper() - 1 }
""")
    put("left/main.hon", """
import "../shared"
helper proc() int { return shared.helper() - 2 }
identity proc(n int) int { return n }
main proc() int { return 99 }
""")
    put("right/main.hon", """
import "../shared-link"
helper proc() int { return shared.helper() - 1 }
""")
    dependency = put("shared/main.hon", """
helper proc() int { return value }
fail proc() int { return 9223372036854775807 + 1 }
""")
    put("shared/zfunctions.bro", """
value :: 3
identity proc(n f64) f64 { return n }
""")
    put("shared/ignored.test.bro", "this is deliberately not valid source\n")
    (project / "shared-link").symlink_to(project / "shared", target_is_directory=True)
    app = main.parent
    check(app, executable, (b"42\n", None))
    emitted = run(str(HONEY), "emit-llvm", str(app))
    assert emitted.returncode == 0 and emitted.stderr == b"", outcome(emitted)
    sibling.write_text("sibling proc(n int) int { return n * 2 }\n")
    cases = [
        ('import "../left"\nmain proc() int { return left.helper() }', b"1\n", None),
        ('lib :: import "../shared"\nmain proc() f64 { f :: lib.identity\nreturn f(1.5) }', b"1.5\n", None),
        ('lib :: import "../shared"\nmain proc() int { return lib.value }', b"3\n", None),
        ("""
import "../left"
import "../shared"
main proc() bool {
    return left.identity(41) == 41 and shared.identity(1.5) == 1.5
}
""", b"true\n", None),
        ('lib :: import "../shared"\nmain proc() int { return lib.absent() }', b"", b"unknown name"),
        ('lib :: import "../shared"\nmain proc() int { return helper() }', b"", b"unknown name"),
        ('import "../left"\nmain proc() int { return left.shared.helper() }', b"", b"unknown name"),
        ('import "../shared"\nmain proc() int { return shared.helper(1) }', b"", b"argument count"),
        ('import "../shared"\nmain proc() f64 { return shared.identity(true) }', b"", b"incompatible value type"),
        ('import "../shared"\nmain proc() int { shared\nreturn 42 }', b"", b"namespace cannot be used as a value"),
        ('import "../shared"\nmain proc() int { return shared.value.member }', b"", b"requires a package namespace"),
        ('lib :: import "../absent"\nmain proc() int { return lib.helper() }', b"", b"cannot load package directory"),
        ('lib :: import "../left"\nlib :: import "../right"\nmain proc() int { return lib.helper() }', b"1\n", b"duplicate name"),
        ('lib :: import "../shared"\nlib :: 42\nmain proc() int { return lib.helper() }', b"", b"duplicate name"),
        ('lib :: import "../shared"\nmain proc() int { lib :: 42\nreturn lib }', b"", b"duplicate name"),
        ('int :: import "../shared"\nmain proc() int { return 42 }', b"", b"duplicate name"),
        ('import "../shared"\nmain proc() int { shared.value = 42\nreturn 0 }', b"", b"mutable local binding"),
        ('import "../shared"\nmain proc() int { shared = 42\nreturn 0 }', b"", b"mutable local binding"),
        ('import "../shared"\nunused proc() int { return shared.absent() }\nmain proc() int { return 42 }', b"42\n", b"unknown name"),
        ('import "../shared"\nmain proc() int { return shared.fail() }', b"", b"integer arithmetic overflow"),
        ('import "../shared"\nmain proc() int { return shared. }', b"", b"unexpected token"),
    ]
    for text, stdout, error in cases:
        main.write_text(text + "\n")
        check(app, executable, (stdout, error))
    # Imports in a sibling file are invisible even though its declarations are shared.
    main.write_text("main proc() int { return local.helper() }\n")
    sibling.write_text('local :: import "../shared"\nsibling proc(n int) int { return n }\n')
    check(app, executable, (b"", b"unknown name"))
    # A missing package selection reuses the loader diagnostic instead of cascading.
    main.write_text('lib :: import "../absent"\nmain proc() int { return lib.helper() }\n')
    diagnosed = run(str(HONEY), "run", str(app))
    assert diagnosed.stderr.count(b"cannot load package directory") == 1, outcome(diagnosed)
    assert b"unknown name" not in diagnosed.stderr, outcome(diagnosed)
    # Dependency diagnostics and reached traps preserve their original source file.
    main.write_text('import "../shared"\nmain proc() int { return shared.helper() }\n')
    dependency.write_text("helper proc() int { return missing }\n")
    check(app, executable, (b"", b"unknown name"))
    diagnosed = run(str(HONEY), "run", str(app))
    assert str(dependency.resolve()).encode() + b":1:" in diagnosed.stderr, outcome(diagnosed)
    dependency.write_text("\nhelper proc() int { return 9223372036854775807 + 1 }\n")
    check(app, executable, (b"", b"integer arithmetic overflow"))
    trapped = run(str(HONEY), "run", str(app))
    assert str(dependency.resolve()).encode() + b":2:" in trapped.stderr, outcome(trapped)
    # File commands still load dependency siblings, but never their own siblings.
    single = put("single/main.bro", """
import "../shared"
main proc() f64 { return shared.identity(42) }
""")
    put("single/poison.hon", """
main proc() int { return missing }
""")
    check(single, executable, (b"42\n", None))
    emitted = run(str(HONEY), "emit-llvm", str(single))
    assert emitted.returncode == 0 and emitted.stderr == b"", outcome(emitted)
    check(single.parent, executable, (b"42\n", b"duplicate name"))
    return len(cases) + 6


def check_dependency_provenance(temporary, executable):
    project = temporary / "provenance"
    app = project / "app"
    dependency = project / "dependency"
    app.mkdir(parents=True)
    dependency.mkdir()
    main = app / "main.hon"
    source = dependency / "diagnostic.hon"
    # A transitive call must report the leaf's location, not either importing file.
    bridge = project / "bridge"
    bridge.mkdir()
    (bridge / "main.hon").write_text(textwrap.dedent("""
        import "../dependency"
        fail proc() int { return dependency.fail() }
    """).strip() + "\n")
    cases = [
        ("""
            fail proc() int {
                return `
            }
        """, 4, 12, True),
        ("""
            fail proc() int {
                return (1
            }
        """, 4, 14, True),
        ("""
            fail proc() int {
                text :: "é" return missing
            }
        """, 4, 25, True),
        ("""
            fail proc() int {
                if (1) return 0
                return 42
            }
        """, 4, 9, True),
        ("""
            fail proc() int {
                return 9223372036854775808
            }
        """, 4, 12, True),
        ("""
            fail proc() int {
                return 9223372036854775807 + 1
            }
        """, 4, 12, False),
        ("""
            fail proc() int {
                a :: [42]
                return a[1]
            }
        """, 5, 12, False),
        ("""
            fail proc() int {
                before :: after
                after :: 42
                return before
            }
        """, 4, 15, False),
        ("""
            a int :: b
            b int :: a
            fail proc() int { return a }
        """, 4, 10, False),
    ]
    for text, line, column, static in cases:
        source.write_text("\n# utf8: é\n" + textwrap.dedent(text).strip() + "\n")
        location = f"{source.resolve()}:{line}:{column}: ".encode()
        for reached in (False, True):
            main.write_text(textwrap.dedent(f"""
                import "../bridge"
                main proc() int {{
                    if ({str(reached).lower()}) return bridge.fail()
                    return 42
                }}
            """).strip() + "\n")
            interpreted = run(str(HONEY), "run", str(app))
            assert interpreted.returncode == int(static or reached), outcome(interpreted)
            assert interpreted.stdout == (b"" if reached else b"42\n"), outcome(interpreted)
            if static or reached:
                label = b"runtime trap: " if reached else b"error: "
                assert interpreted.stderr.startswith(location + label), outcome(interpreted)
                assert len(interpreted.stderr.splitlines()) == 1, outcome(interpreted)
            else:
                assert interpreted.stderr == b"", outcome(interpreted)
            emitted = run(str(HONEY), "emit-llvm", str(app))
            assert emitted.returncode == int(static), outcome(emitted)
            expected_diagnostics = interpreted.stderr.replace(b"runtime trap: ", b"error: ") if static else b""
            assert emitted.stderr == expected_diagnostics, outcome(emitted)
            for optimization in ("-O0", "-O2"):
                built = run(str(HONEY), "build", str(app), "-o", str(executable), optimization)
                assert outcome(built) == (int(static), b"", expected_diagnostics), outcome(built)
                # Generated diagnostics must not reopen sources at runtime.
                hidden = project.with_name("hidden-provenance")
                project.rename(hidden)
                try:
                    compiled = run(str(executable))
                    assert outcome(compiled) == outcome(interpreted), (
                        optimization, outcome(interpreted), outcome(compiled)
                    )
                finally:
                    hidden.rename(project)
                    executable.unlink()
    return len(cases) * 2


def main():
    with tempfile.TemporaryDirectory(prefix="honey native ' ; $") as directory:
        temporary = Path(directory)
        executable = temporary / "program ' ; $"
        count = 0
        for source in sorted((ROOT / "programs").glob("*.hon")):
            check(source, executable)
            count += 1
        for name, (text, stdout, error) in CASES.items():
            source = temporary / f"{name}.hon"
            source.write_text(textwrap.dedent(text).strip() + "\n")
            check(source, executable, (stdout, error))
            count += 1
        count += check_packages(temporary, executable)
        count += check_namespace_packages(temporary, executable)
        count += check_dependency_provenance(temporary, executable)
        source = ROOT / "programs/factorial.hon"
        emitted = run(str(HONEY), "emit-llvm", str(source))
        assert emitted.returncode == 0 and emitted.stderr == b"", outcome(emitted)
        ir = temporary / "factorial.ll"
        ir.write_bytes(emitted.stdout)
        assembled = run("clang", str(ir), "-o", str(executable))
        assert assembled.returncode == 0, outcome(assembled)
        assert outcome(run(str(executable))) == (0, b"120\n", b"")
        executable.unlink()
        missing_compiler = run(
            str(HONEY), "build", str(source), "-o", str(executable),
            env={**os.environ, "PATH": str(temporary)},
        )
        assert missing_compiler.returncode == 2, outcome(missing_compiler)
        assert b"cannot start native compiler" in missing_compiler.stderr, outcome(missing_compiler)
        assert not executable.exists()
        invalid = run(str(HONEY), "build", str(source), "-o", str(executable), "-O3")
        assert invalid.returncode == 2 and b"usage:" in invalid.stderr, outcome(invalid)
        print(f"{count} programs matched interpreter at -O0 and -O2; LLVM output and CLI errors passed")


if __name__ == "__main__":
    main()
