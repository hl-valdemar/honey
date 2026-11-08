# TODO

- [x] honey-owned test framework.
- [x] migrate Python acceptance suites to Honey.

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

`../honey-pong` builds without warnings with a macOS 15.0 minimum; its physics check passes, and user-confirmed graphical execution clears the displayless-host acceptance blocker. compiler unit/native/ABI/project/binding suites and two-generation self-hosting pass, with no new feature syntax adopted in compiler production sources. the required two-release bootstrap rollout remains a release/adoption gate: publish compiler support, then pin that published release before adopting new features in compiler or production dependencies.

## language-native build toolchain

goal: ordinary Honey configuration with a small interface; describe artifacts rather than manually wiring a public step graph.

- [ ] evolve the existing `build.BuildConfig` rather than introducing a builder framework. describe sources, native dependencies and generated inputs; keep compilation, linking and ordering internal.
- [ ] provide conventional `honey build`, `honey test` and `honey install` project commands without requiring explicit step registration or repeated artifact declarations.
- [ ] allow ordinary Honey helpers and platform choices to produce configuration values. configuration evaluation must not execute commands, download dependencies or install files.
- [ ] integrate the validated, versioned user-local installation flow: preserve bootstraps and older installations, validate candidates before atomic activation, and retain rollback. keep compiler bootstrap/validation as a compiler-specific recipe and recovery available outside the compiler executable.
- [ ] add multiple-artifact configuration when a real project needs it; keep single-artifact projects simple.
- [ ] add custom generation/asset tasks when needed, with explicit inputs and outputs. infer dependencies from artifact/output references, not declaration order or shell-command inspection; do not cache undeclared effects as if they were tracked.

initial scope: existing declarative configuration, conventional commands and safe installation. defer plugins, package resolution and a public step graph. preserve project-vendored library control and the required bootstrap rollout policy.

See [README.md](README.md) for implemented features, current compiler boundaries and verification commands.
