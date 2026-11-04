"""After `bro build`, run `python3 tests/self_host.py` (Darwin arm64 + Clang)."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile

import native


ROOT = native.ROOT


def main():
    clang = shutil.which("clang")
    assert clang is not None, "Clang is required"
    with tempfile.TemporaryDirectory(prefix="honey self host ' ; $") as directory:
        temporary = Path(directory)
        binaries = temporary / "bin"
        binaries.mkdir()
        (binaries / "clang").symlink_to(clang)
        # Only Clang is discoverable: neither generation may invoke Bro.
        os.environ["PATH"] = str(binaries)
        project = temporary / "project"
        project.mkdir()
        (project / "build.hon").write_text("# project root\n")
        for package in ("source", "std", "ffi"):
            shutil.copytree(
                ROOT / package, project / package,
                ignore=shutil.ignore_patterns("*.test.hon", "*.test.bro"),
            )
        shutil.rmtree(project / "std/testing")
        compiler = ROOT / "build/honey"
        for generation in (1, 2):
            output = ROOT / "build" / f"honey-stage{generation}"
            built = subprocess.run(
                [str(compiler), "build", str(project / "source"), "-o", str(output), "-O2"],
                cwd=project, capture_output=True, timeout=3600,
            )
            assert native.outcome(built) == (0, b"", b""), (
                f"stage {generation} build exited {built.returncode}\n"
                + built.stdout.decode(errors="backslashreplace")
                + built.stderr.decode(errors="backslashreplace")
            )
            compiler = output
            print(f"stage {generation}: production compiler built without Bro", flush=True)

        native.HONEY = compiler
        executable = temporary / "program ' ; $"
        for name, expected in (
            ("factorial", b"120\n"), ("iterative_factorial", b"120\n"),
            ("array_sum", b"17\n"), ("range_loop", b"40\n"),
            ("loop_control", b"12\n"), ("boolean_conditions", b"true\n"),
            ("temperature", b"68\n"),
        ):
            native.check(ROOT / "programs" / f"{name}.hon", executable, (expected, None))
        native.check_packages(temporary, executable)
        native.check_dependency_provenance(temporary, executable)
        native.check_io_packages(temporary, executable)
        source = ROOT / "programs/factorial.hon"
        emitted = native.run(str(compiler), "emit-llvm", str(source))
        assert emitted.returncode == 0 and emitted.stderr == b"", native.outcome(emitted)
        llvm = temporary / "factorial.ll"
        llvm.write_bytes(emitted.stdout)
        assembled = native.run("clang", str(llvm), "-o", str(executable))
        assert assembled.returncode == 0, native.outcome(assembled)
        assert native.outcome(native.run(str(executable))) == (0, b"120\n", b"")
        executable.unlink()
        for arguments in ((), ("build",), ("build", str(source), "-o", str(executable), "-O3")):
            invalid = native.run(str(compiler), *arguments)
            assert invalid.returncode == 2 and b"usage:" in invalid.stderr, native.outcome(invalid)
        missing = native.run(str(compiler), "run", str(temporary / "missing.hon"))
        assert missing.returncode == 2 and b"infrastructure error:" in missing.stderr, native.outcome(missing)
        failed = native.run(
            str(compiler), "build", str(source), "-o", str(executable),
            env={**os.environ, "PATH": str(temporary)},
        )
        assert failed.returncode == 2 and b"cannot start native compiler" in failed.stderr, native.outcome(failed)
        assert not executable.exists()
        print("stage 2: interpreter/native examples, packages, provenance, formatted IO, LLVM and CLI errors passed")


if __name__ == "__main__":
    main()
