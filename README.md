# Honey

Honey is a statically typed language with compile-time execution, an interpreter and a self-hosted compiler. native builds emit LLVM IR and use Clang to produce standalone executables.

**native platform: Apple Silicon macOS only.** Clang must be on `PATH`. foreign C calls require native execution; the interpreter cannot execute them.

## build the compiler

from this checkout, using a working Honey compiler:

```sh
honey build --release
./build/honey run programs/factorial.hon
```

the example prints `120`. keep the bootstrap executable and its matching `std/` and `ffi/c/` libraries; a compiler binary alone is not a complete distribution.

Honey was originally bootstrapped with [Bro](../brolang), which implements the same language. older Bro compilers cannot build the current checkout. compiler releases and source adoption must follow the [two-release bootstrap policy](AGENTS.md#bootstrap-compatibility-required).

## try the language

[programs/factorial.hon](programs/factorial.hon):

```hon
factorial proc(n int) int {
    if (n <= 1) return 1
    return n * factorial(n - 1)
}

main proc() int {
    return factorial(5)
}
```

```sh
./build/honey build programs/factorial.hon -o build/factorial --release
./build/factorial
./build/honey emit-llvm programs/factorial.hon
```

a parameterless `main` prints its result rather than using it as an exit status. native applications can instead use `main proc(init process.Init) i32` from `@std/process` to receive arguments and IO and return an exit status.

Honey supports generics, compile-time type construction, records, tagged unions, optionals, explicit error channels, scoped cleanup, arrays, slices and pointers. `::` declares an immutable binding; `:=` declares a mutable binding. unused locals, parameters and captures are errors; use `_` for intentional discards.

see the [examples](programs/) and [style guide](docs/style.md). the [Bro project](../brolang) contains the language grammar and further documentation; Honey's implemented subset may differ.

## install

```sh
./build/honey install
export PATH="$HOME/.local/bin:$PATH"
```

installation defaults to `~/.local` and release mode. Honey's installation recipe builds two compiler generations, runs compiler tests and validates a standalone project before activating the new compiler. failed validation leaves the active installation unchanged; older bundles are retained.

```sh
honey install --rollback honey
# if the active compiler is broken:
~/.local/opt/honey/previous install --rollback honey
```

set `PREFIX` to choose another installation root and `BOOTSTRAP` to override the input compiler for Honey's self-hosted installation. neither installation nor rollback edits shell configuration.

## create a project

```sh
honey new hello-honey
honey build hello-honey --release
hello-honey/build/app
```

use `honey init` to initialize the current directory. both commands preserve existing files and vendor matching `std/` and `ffi/c/` libraries beside `source/`. those copies belong to the project: edit them locally, preserve compiler-required contracts and upgrade them explicitly. `.honey-origin` records provenance; builds never refresh vendored code.

`build.hon` describes one executable:

```hon
import "@std/build"

config :: build.BuildConfig{
    name = "app",
    source = "source",
    macos_deployment_target = "27.0.1",
}
```

new projects record the host's current macOS version as their deployment minimum. lower it explicitly if needed, while respecting native dependencies' requirements. omitted deployment settings default to `11.0`.

from the project directory or a subdirectory:

```sh
honey build            # debug build: build/app
honey build --release  # optimized build
honey test             # tests in config.source
honey install          # release build installed under $PREFIX (default: ~/.local)
honey install --rollback app
```

`--debug` disables optimization; `--release` enables it. neither disables runtime safety checks or adds source-level debugger metadata. source-file builds require `-o`, with the mode flag last.

`run`, `build`, `emit-llvm` and `test` accept a file or package directory. a file selects only that file; a directory loads its source files together. production commands exclude test files and test-only imports. imports have file-local namespaces: `import "@std/io"` makes `io` available, while `helpers :: import "../helpers"` assigns an explicit alias. `@` paths start at the project root.

## native libraries and C bindings

configure native dependencies in `build.hon` using `libraries`, `lib_paths`, `includes`, `defines`, `links`, `frameworks` and `rpaths`. paths are project-relative unless absolute. `linkage = .static` or `.shared` selects dependency libraries, not the executable's output kind. see [BuildConfig](std/build/build.hon) for the complete configuration interface.

for example, with Homebrew raylib installed:

```sh
honey translate-c raylib --project hello-raylib \
    --header /opt/homebrew/opt/raylib/include/raylib.h --all
```

import the result with `import "@ffi/raylib"`. add `libraries = &["raylib"]` and `lib_paths = &["/opt/homebrew/opt/raylib/lib"]` to the project's `BuildConfig` to link it. the deployment minimum must also satisfy the installed raylib library.

`translate-c` uses Clang and the project's configured target. repeat `--header` for additional headers, or replace `--all` with repeated `--symbol <name>` options for a strict selection. `-I`, `-D`, `--sysroot` and `--target` control preprocessing and targeting.

`--all` reports and omits unsupported declarations while retaining usable bindings; read its diagnostics even when it succeeds. an unsupported explicit `--symbol` fails without installing partial bindings. generation creates no C bridges and does not install or link libraries.

bindings live under `ffi/<name>/`, with shared nominal types under `ffi/__c_types/`. regeneration records its inputs and refuses to overwrite modified generated files or unrelated outputs. keep handwritten helpers separate. C declarations do not establish ownership, callback lifetime or threading contracts; those remain the caller's responsibility.

## tests and compiler development

```sh
./build/honey test                  # compiler and stdlib unit tests
./build/honey test tests/native     # native executable acceptance
./build/honey test tests/self_host  # two-generation production rebuild
```

other acceptance suites are in [tests/](tests/): C ABI, binding generation, projects, installation and the test framework. self-hosting checks rebuild production sources without Bro and exercise the second-generation compiler.

write tests in `.test.hon` files using `name test { ... }` and `@std/testing` assertions such as `try testing.expect_equal(expected, actual)`. each test runs in a separate native child process, so a trap does not prevent later tests from running. `test import "@package"` explicitly includes another package's tests.

current boundaries:

- native code and C aggregate calling conventions target Darwin arm64 only; unsupported C layouts are diagnosed rather than guessed.
- compile-time execution is bounded and cannot call foreign functions. project configuration uses the same restricted evaluator, not shell hooks.
- projects describe a single executable. multiple artifacts, generation/asset tasks and package resolution are deferred; ordinary installation includes only the executable, not assets or native runtime libraries.
- recoverable source errors can still produce output or execute unaffected code. check command status: `0` means success, `1` means source diagnostics or execution/test failure, and `2` means usage or infrastructure failure.

see [TODO.md](TODO.md) for planned work.
