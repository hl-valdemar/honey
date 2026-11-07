# TODO

- [x] honey-owned test framework.
- [x] migrate Python acceptance suites to Honey.

## direct C interop and standalone projects

goal: build a small raylib game in a sibling project, using Honey's stdlib and direct C calls without bridge code.

1. [ ] implement `f32`/`c_float` end to end, including layout, conversions, compile-time/interpreter semantics and C variadic promotion to double.
2. [ ] implement target-correct C aggregate arguments/results and callbacks on Darwin arm64: record/union layout, homogeneous float aggregates, mixed records and indirect large values. verify layouts and calls in both directions against Clang at `-O0`/`-O2`; diagnose unsupported layouts rather than guessing.
3. [ ] add `honey new`/`honey init` to vendor the toolchain's matching `std/` and core `ffi/c/` beside `source/`, preserving existing files. resolve `@std`/`@ffi/c` from the project; allow project-local modifications while preserving compiler-required contracts. record the original compiler/stdlib version for provenance, not an integrity lock. upgrades must be explicit and preserve user edits; builds never refresh vendored sources.
4. [ ] add project-level native link settings used by the CLI: libraries, search paths, macOS frameworks and runtime library paths, with static/shared linking and actionable missing-library diagnostics.
5. [ ] design `honey ffi` to select headers and declarations, include their transitive type dependencies, and generate `ffi/<name>/` beside `source/`. use Clang's C frontend with explicit target, include paths and defines; support needed typedefs, enums, constants and callbacks. explicitly report unsupported requested macros, inline-only functions, globals or layouts; never silently omit them or generate bridges.
6. [ ] isolate names in one binding package per library/header group, preserving external C symbol spelling. share common C declarations without duplicating nominal type identity; validate compatible redeclarations. diagnose incompatible external symbols across packages: Honey namespaces cannot fix linker-level collisions. handle Honey keywords/name normalization explicitly.
7. [ ] make regeneration deterministic and non-destructive: record inputs, selections and compiler/preprocessor settings; distinguish generated files from handwritten code and refuse unrelated overwrites. keep ownership/lifetime and callback-threading contracts explicit, not inferred from C declarations.
8. [ ] follow the required two-release bootstrap rollout before adopting new language features in compiler/production dependencies. then verify a sibling raylib game using generated bindings, the project-vendored stdlib and native builds; exercise window lifecycle, input, drawing, timing and cleanup.

See [README.md](README.md) for implemented features, current compiler boundaries and verification commands.
