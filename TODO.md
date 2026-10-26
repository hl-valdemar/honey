# self-hosting

- [ ] milestone: Honey compiles its production compiler, then that compiler compiles itself again and builds/runs the example programs.

## current boundary

directory packages support file-local import namespaces and postfix declaration
selection through the interpreter and native backend. concrete 8/16/32/64-bit
integers, checked numeric conversions, records/tuples, construction/defaults,
enums, distinct/alias types, overlapping untagged/tagged unions, field reads/writes
and exhaustive statement/value matching work through both execution paths.
source data pointers/references support aliasing, mutation, calls and aggregate
storage; explicit callback signatures, many-pointer addition, mutable/sentinel
slices, optionals and layout/cast/memory builtins work through both execution paths.
native scalar/pointer `c_func` declarations support allocator-required libc calls;
executed foreign calls remain diagnostic traps in the interpreter.
explicit fallible channels, composed error sets, `try`/`catch` and scoped LIFO
`defer`/`errdefer` run through both paths; source errors are values, not diagnostic
traps or compiler infrastructure failures.
the driver retains frontend storage and source provenance through
`@source/compilation`, with compilation-wide AST node identities.
file commands retain single-file behavior; directory commands compile the
package's production files and load its dependencies in separate scopes.

## completed milestone: scalar-function packages

verified: compile and run a small multi-file directory package importing a second
package of scalar functions, through both the interpreter and native backend.
using the existing standard library is a later acceptance gate: even `std/mem`
needs records, pointers, generics and C calls.

- [x] retain source/package identity and source ownership across the compilation; keep one identifier pool and unambiguous declaration IDs.
- [x] load directory packages once per canonical path, with deterministic file discovery, package scopes and root-only entry selection. follow Bro's existing relative and project-root `@` import rules; diagnose cycles rather than recurse indefinitely.
- [x] finish postfix namespace selection and resolve file-local import aliases without injecting dependency names into the importing package; bare and aliased import declarations are already parsed for dependency discovery.
- [x] reuse ordinary scalar signature checking, function values and IR calls for selected declarations; namespaces have no runtime representation.
- [x] preserve originating filenames and spans in dependency diagnostics and native runtime traps; retain demand-driven diagnostic recovery.
- [x] verify sibling source files, transitive/shared imports, same-spelled declarations in different packages, alias conflicts, missing packages/members, argument errors and dependency-file diagnostics. preserve existing single-file commands.

visibility follows Bro's public-by-default contract; unsupported visibility
attributes must be diagnosed, never ignored. the scalar-function milestone did not
include record fields or imported types; those now work for the record/tuple slice.
test discovery and FFI remain outside these completed slices.

## subsequent dependency gates

- [x] concrete data models: required integer widths/conversions, records/tuples and construction, fields, enums, distinct/alias types, unions and matching. examples: `source/lexer/token.hon`, `source/sema/resolve.hon`, `std/mem/mem.hon`.
  - [x] native integer widths (`i8`–`i64`, `u8`–`u64`, `isize`/`usize`) and checked explicit numeric conversions, including float truncation/range checks and integer-to-float rounding. preserve exact types through arrays, calls and range captures; retain strict implicit conversion rules.
  - [x] records/tuples, typed/contextual/anonymous construction, field defaults and reads/writes, nested aggregate value copies, imported record types and constructors through interpreter/native execution.
  - [x] nominal enums and distinct types, transparent declaration/type aliases, overlapping untagged/tagged unions, contextual payload construction, active-field checks and exhaustive scalar/enum/tagged-union matching through interpreter/native execution. pointer captures, inline specialization and yielding value blocks remain with their respective later gates; error-set composition is covered below.
- [x] memory representation: source pointers/references, mutable and sentinel slices, optionals, layout/cast builtins and allocator-required libc calls. internal IR array addresses are not source pointers. examples: `std/mem/mem.hon`, `std/arraylist/arraylist.hon`.
  - [x] source data pointers/references: `@T`/`*T`, `mut`, address-taking and dereference, pointer-aware fields/indexing, array `.len`/`.ptr`, readonly weakening, location equality, recursive pointer-linked records and shared pointees across interpreter/native calls and aggregate copies.
  - [x] function-pointer signatures: explicit `@proc(...) Result`, structural signature checking, callbacks in parameters/results/records/arrays, vtable calls and imported signature aliases; many-pointer `+ usize` arithmetic preserves pointee type, mutability, stride and location identity through interpreter/native execution.
  - [x] mutable/sentinel slices with checked bounds, borrowed aliases and string views; optional values/pointers/callbacks, null checks, unwrap and conditional/loop captures; target layout, pointer/const casts and typed memory copy/set through interpreter/native execution. scalar/pointer C ABI declarations build/run allocator callbacks using `malloc`, `realloc`, `free` and `posix_memalign`; arbitrary interpreter FFI, generic standard-library compilation and native startup/IO remain with later gates.
- [x] fallible results and cleanup: explicit `T ! E`, error-set composition, `try`/`catch`, `defer`/`errdefer`, error captures and matched error residuals through interpreter/native execution; infrastructure errors remain distinct from diagnostic traps. fallible `void` may complete on fallthrough; unhandled entry errors exit with status `1`. inferred `T!` signatures and yielding catch blocks remain with inference/value-control-flow work. examples: `source/main.hon`, `source/sema/check/check.hon`.
- [ ] generic specialization and compile-time execution/reflection sufficient for existing `ArrayList`, hash maps, `enummap`, `meta` and `io.print`; include type/value parameters and compile-time generated storage.
- [ ] remaining source constructs: collection/index iteration, expression-valued control flow and `yield`, compound updates and multiline strings. examples: `source/parser/parse.hon`, `source/llvm/runtime.hon`.
- [ ] standard-library milestone: compile/run a multi-package program exercising existing allocation and IO, including error cleanup; no replacement miniature standard library.
- [ ] native driver integration: required C ABI declarations, `process.Init` startup arguments/IO, actual CLI exit statuses, file loading, temporary files and spawning Clang. Honey-generated programs currently accept only parameterless `main` and print its result; Bro's bootstrap startup support is not Honey backend support.
- [ ] self-hosting proof: bootstrap-built Honey builds the production compiler (excluding test-only imports); that executable rebuilds the compiler; the rebuilt compiler passes representative interpreter/native examples and CLI/error scenarios without Bro at runtime.

the gates may require interleaving (notably allocation, errors and generics).
extend checking, IR and native emission together; accepting syntax alone does
not complete a gate. additional targets, a package registry, a replacement
linker and interpreter execution of arbitrary FFI are not prerequisites.