# TODO

- [x] honey-owned test framework.
- [x] migrate Python acceptance suites to Honey.
- [x] make unused local declarations an error.
- [x] make debug/release build flags more obvious: `--debug` and `--release` replace public `-O<N>` options; build defaults to debug, install defaults to release.
- [x] make the README actually useful and readable.
    - trim all irrelevant information.
    - keep language concise and salient.
- [ ] consider whether a parameterless `main` should continue to print its result instead of returning it as program exit status.
    - should probably _not_ print the status; it should behave as expected - return value is exit status.

## direct C interop and standalone projects

goal: build a small raylib game in a sibling project, using Honey's stdlib and direct C calls without bridge code.

1. [x] implement `f32`/`c_float` end to end, including layout, conversions, compile-time/interpreter semantics and C variadic promotion to double.
2. [x] implement target-correct C aggregate arguments/results and callbacks on Darwin arm64: record/union layout, homogeneous float aggregates, mixed records and indirect large values. verify layouts and calls in both directions against Clang at `-O0`/`-O2`; diagnose unsupported layouts rather than guessing.
3. [x] add `honey new`/`honey init` to vendor the toolchain's matching `std/` and core `ffi/c/` beside `source/`, preserving existing files. resolve `@std`/`@ffi/c` from the project; allow project-local modifications while preserving compiler-required contracts. record the original compiler/stdlib version for provenance, not an integrity lock. upgrades must be explicit and preserve user edits; builds never refresh vendored sources.
4. [x] add project-level native link settings used by the CLI: libraries, search paths, macOS frameworks and runtime library paths, with static/shared linking and actionable missing-library diagnostics. configure the minimum macOS deployment version consistently for LLVM emission and native linking.
5. [x] implement `honey translate-c` to select headers and declarations, include their transitive type dependencies, and generate `ffi/<name>/` beside `source/`. use Clang's C frontend with explicit target, include paths and defines; support needed typedefs, enums, constants and callbacks. explicitly report unsupported requested macros, inline-only functions, globals or layouts; never silently omit them or generate bridges.
6. [x] isolate names in one binding package per library/header group, preserving external C symbol spelling. share common C declarations without duplicating nominal type identity; validate compatible redeclarations. diagnose incompatible external symbols across packages: Honey namespaces cannot fix linker-level collisions. handle Honey keywords/name normalization explicitly.
7. [x] make regeneration deterministic and non-destructive: record inputs, selections and compiler/preprocessor settings; distinguish generated files from handwritten code and refuse unrelated overwrites. keep ownership/lifetime and callback-threading contracts explicit, not inferred from C declarations.
8. [x] verify a sibling raylib game using generated bindings, the project-vendored stdlib and native builds without C bridges. user confirmed `../honey-pong` runs successfully on a Mac with an active display.

whole-header generation: `translate-c --all` exports supported declarations/macros from selected headers and all required transitive types, reporting each unsupported root while retaining usable bindings. the CLI defaults to the project's configured target without resolving native libraries; `--target` remains an explicit override. strict `--symbol` selection remains available. exporting unrelated declarations from the entire include tree is deferred until needed.

generated record signatures use C aliases; shared records have stable, readable C-name/origin-qualified identifiers. anonymous records derive names from typedefs or containing fields where possible. manifest v2 prevents mixed old/new naming schemes; migrate generated packages together after backing up local edits.

`../honey-pong` builds without warnings with a macOS 15.0 minimum; its physics check passes, and user-confirmed graphical execution clears the displayless-host acceptance blocker. compiler unit/native/ABI/project/binding suites and two-generation self-hosting pass, with no new feature syntax adopted in compiler production sources. the required two-release bootstrap rollout remains a release/adoption gate: publish compiler support, then pin that published release before adopting new features in compiler or production dependencies.

## language-native build toolchain

goal: ordinary Honey configuration with a small interface; describe artifacts rather than manually wiring a public step graph.

- [x] retain `build.BuildConfig` for a single executable's source and native inputs; keep compilation/linking internal. existing generated C/object/archive files may be supplied through `links`; generation and tracked output references stay with the deferred tasks below.
- [x] provide conventional `honey build`, `honey test` and `honey install` project commands without explicit step registration or repeated artifact declarations. pathless test selects `config.source`, including from a nested working directory.
- [x] evaluate ordinary Honey helpers and explicit deployment choices as configuration values, with foreign execution blocked. configuration evaluation does not execute installation recipes or commands; native platform support remains Darwin arm64.
- [x] implement installation and rollback natively in Honey, preserving older bundles and atomically activating only successful candidates. optional `installation :: build.InstallConfig` declares a version and Honey's self-hosted bootstrap/validation recipe; ordinary projects build only their executable. allocate readable `<version>-install.<N>` directories under the install lock without mutating release versions. retained compilers perform config-free rollback even when the active compiler is broken; no shell installers or hooks remain.
- [ ] add multiple-artifact configuration when a real project needs it; keep single-artifact projects simple.
- [ ] add custom generation/asset tasks when needed, with explicit inputs and outputs. infer dependencies from artifact/output references, not declaration order or shell-command inspection; do not cache undeclared effects as if they were tracked.

initial scope implemented: existing declarative configuration, conventional commands and safe installation. multiple artifacts, custom generation, plugins, package resolution and a public step graph remain deferred. preserve project-vendored library control and the required bootstrap rollout policy.

See [README.md](README.md) for implemented features, current compiler boundaries and verification commands.
