# Honey

Bro and Honey implement the same language; Bro is the prototype compiler used to bootstrap the self-hosted Honey compiler. the features below describe Honey's currently implemented subset. the expected development workspace places Bro at [`../brolang`](../brolang); consult that project for the language grammar and documentation.

style: see the [language guidelines](docs/style.md).

build Honey from this directory:

```sh
bro build
```

run a source file with the interpreter:

```sh
./build/honey run example.hon
```

```hon
add proc(left, right int) int {
    return left + right
}

main proc() int {
    answer :: add(19, 23)
    return answer
}
```

this prints `42`. interpreted `main` must be a top-level function with no parameters; nonvoid results are printed, not used as process exit statuses.

the native bootstrap accepts either a no-argument `main` or `main proc(init process.Init)`. it supplies `init.io` and `init.args` from native startup `argc`/`argv`. startup scans each argument once into a heap-allocated array of borrowed zero-terminated slices (`process.Args.values: [][;0]u8`), without copying strings. the descriptor array and borrowed strings remain valid until `main` returns; startup then frees the descriptors, including on a fallible return. allocation failure exits with status `2`. `init.args.values.len` includes the executable name, and `init.args.values[index]` directly accesses an argument after a length guard. callers can supply their own `process.Args { values = slices[..] }` for testing.

source loading uses the supplied `io.Io` for opening, reading, and closing files. `io.open` accepts a borrowed zero-terminated slice (`[;0]u8`), including `init.args.values[index]` without copying its storage. the bootstrap supports checked sentinel-slice construction with `pointer[..length;0]`. `io.read_all(allocator, reader, maximum)` reads to EOF into a caller-owned `ArrayList(u8)`; release it with `arraylist.deinit`. it frees partial storage on error and consumes one extra byte to distinguish an exact-size input from a limit violation. the driver maps `limit_exceeded` to `source_too_large` at its `u32` source-size limit.

a recursive example is in [programs/factorial.hon](programs/factorial.hon); `./build/honey run programs/factorial.hon` prints `120`.

an iterative example is in [programs/iterative_factorial.hon](programs/iterative_factorial.hon); `./build/honey run programs/iterative_factorial.hon` also prints `120`.

the parser supports arithmetic expressions with nested parenthesized groups, such as `a + (b - c) * 2 / 3`. groups control precedence without adding AST nodes; inner expression spans exclude the grouping parentheses. groups may span lines in any expression position, including returns, initializers, arguments and guards. parsing accepts integer division syntax, but type checking requires float operands for `/`.

function calls are postfix expressions: `f()`, `f(a, b)`, and `factory()(x)`. calls bind tighter than unary and binary arithmetic and can appear in initializers or as expression statements inside function bodies. argument lists allow newlines and a trailing comma; outside parentheses, newlines remain statement boundaries. type checking validates callable values, argument counts and argument types.

`true` and `false` are boolean literals; `bool` is available for parameters, storage and results. `==`, `!=`, `<`, `<=`, `>` and `>=` compare compatible numeric operands and produce `bool`; `==` and `!=` also accept boolean operands. comparisons preserve contextual numeric inference without making their operands boolean. arithmetic binds tighter than relational comparisons, which bind tighter than equality; comparisons associate left to right. boolean arithmetic/ordering and string/function/void comparisons are invalid; there is no implicit numeric-to-boolean conversion.

`!`, `and` and `or` require boolean operands and produce `bool`. prefix `!` and `-` bind tighter than arithmetic; equality binds tighter than `and`, which binds tighter than `or`. binary operators associate left to right, while prefixes nest right to left. operands evaluate left to right: `false and rhs` and `true or rhs` skip the right operand, including calls, lazy global initialization and runtime traps. both operands are still parsed, resolved and type-checked; errors in a skipped operand remain source diagnostics without forcing a runtime trap. this applies in initializers, assignments, returns, call arguments and conditions. [programs/boolean_conditions.hon](programs/boolean_conditions.hon) demonstrates combined predicates; `./build/honey run programs/boolean_conditions.hon` prints `true`.

`if`/`else` supports braced arms, single statements and `else if` chains. conditions must have type `bool`. a braceless guard requires parentheses unless it is a function call: `if (n <= 1) return 1` or `if ready() return`. `else` binds to the nearest `if`; after a braced arm it may start on the next line. branches are represented as AST blocks even when braceless.

`name = expression` updates a local `:=` binding. `::` bindings, parameters, named functions, builtin types and globals are not assignment targets. the established binding type is preserved: assignment inputs do not widen or infer that type, though numeric literals can adopt its float context. function-valued locals accept replacement functions with the same parameter and result types.

`while` repeats a block or single statement while its `bool` condition holds, re-evaluating the condition on every iteration. guards follow the same braced/braceless convention as `if`. loop bodies introduce child scopes; their locals, including locals in nested conditional arms, begin uninitialized on each scope entry.

`for 0..10 |i| { ... }` iterates an ascending, upper-exclusive integer range; `..=` includes the upper bound. a capture is required: `|i|` binds an immutable `int` in the body scope, while `|_|` discards it. bounds resolve in the enclosing scope and are evaluated left-to-right once, so changing a bound variable in the body does not change the range. reversed ranges are empty; equal bounds produce zero iterations with `..` and one with `..=`. inclusive ranges terminate safely at the maximum integer.

range bounds that are compound or unary expressions require their own parentheses, such as `(-2)..(limit + 1)`. a braceless body also requires parentheses around the entire iterable: `for (0..10) |i| total = total + i`. current execution supports direct integer ranges only; first-class range values, collection iteration, pointer captures and index captures remain unimplemented. [programs/range_loop.hon](programs/range_loop.hon) skips `5` while summing `0..10`; `./build/honey run programs/range_loop.hon` prints `40`.

`break` exits the nearest enclosing `for` or `while`; `continue` restarts a `while` condition or advances a `for` range through its guarded update. both are operand-free statements permitted within loop bodies and nested conditional arms; labels and values are unsupported. controls outside loops produce keyword-spanned diagnostics, including in untaken or unreachable code, and trap only if reached. code after a transfer is still resolved and type-checked but does not execute on that path. body locals are reset on re-entry after `continue`; loop transfers do not guarantee a function return. both controls lower to existing IR jumps, without new interpreter instructions or loop frames. [programs/loop_control.hon](programs/loop_control.hon) skips an iteration and exits early; `./build/honey run programs/loop_control.hon` prints `12`.

name resolution (`@source/sema`) runs after parsing. it predeclares package names and each block's direct locals, allowing forward references and mutually recursive functions. parameters and top-level function-body locals share a scope; each conditional arm and loop body introduces a child scope whose names do not escape it. signatures resolve names in the enclosing package scope. shadowing is forbidden, including reuse of builtin type names (`int`, `float`, `bool`, `string`, `void`); sibling scopes may independently reuse names.

`sema.resolve` returns an owned binding table indexed by AST `NodeId`, without modifying the AST. `sema.binding` identifies a declaration, builtin type, or invalid binding carrying its diagnostic ID; nodes with no binding return `.none`. release the result with `sema.deinit`. unknown names and duplicate declarations add source-spanned diagnostics; invalid syntax retains its existing diagnostic identity. same-scope duplicates retain the first declaration for recovery, while forbidden inner declarations invalidate that name's uses rather than silently binding them to an outer declaration.

type checking (`@source/sema/check`) runs after name resolution. `check.check` borrows the AST and binding table and returns owned per-node types; query with `check.type_of` and release with `check.deinit`. declarations infer their initializer types, including forward references and function aliases; function types identify the declaration supplying the callable signature. annotations must name builtin types; type names are not ordinary values. `void` is valid as a function result, not as a parameter or stored value.

numeric typing follows contextual rules within Honey's current `int` and `float` families. integer literals can adopt either family; float literals remain float. numeric demands propagate through arithmetic and inferred bindings before undemanded integers default to `int`. conflicting demands are errors; fixed `int` values do not implicitly convert to `float`. `+`, `-`, `*` and unary `-` require compatible numeric operands; `/` requires and supplies float context. `3 / 2`, `3.0 / 2`, and `ratio float :: 3 / 2` are valid; division involving fixed `int` values requires explicit conversion.

type errors carry source spans and diagnostic identities; invalid operands propagate their existing identity without duplicate errors, while independent expressions, arguments and nested bodies are still checked. inference cycles without a type anchor are diagnosed. function signatures are checked independently of their bodies. bare `return` and same-line `return expression` are supported inside function bodies and must match the declared result type. nonvoid return-path checking is structural: every path must return or terminate in a diagnostic trap, and both conditional successors are considered without constant folding. a missing `else` or a loop permits fallthrough, even for a literal `true` condition; a later return can close that path. statements after a return and untaken branches or loops are still type-checked but are not executed.

`lower.lower` turns the checked AST and binding table into an owned `@source/ir` module. functions, globals, local slots and instruction-result temporaries have explicit IDs; typed instructions execute in source evaluation order and basic blocks end in jump, conditional-branch, return or diagnostic-trap terminators. conditional arms join only when execution can continue; loops use condition/body/exit blocks and backedges. short-circuit expressions use branches and synthetic boolean local slots to merge results; call arguments are packed in the block reached after evaluating them. local slots and temporaries are function-wide, without SSA or phi nodes. literals are parsed and strings decoded during lowering; the module retains no AST, binding-table or source-buffer references. release it with `ir.deinit`; keep and reuse the diagnostic store used during lowering so diagnostic IDs remain valid. the IR is target-independent and is intended to feed later native backends as well as the current interpreter.

`interpreter.run` consumes only IR and returns a value or a diagnostic trap. string values carry a module-local `ir.StringId`, not a slice; resolve bytes with `ir.string_bytes(&module, id)` before releasing the module. decoded bytes and their full backing allocations remain module-owned, without copying strings during calls or returns. heap-backed invocation frames track their current block and resume it after calls or global initialization; loop iterations reuse the invocation's storage without allocating new frames. parameters are initialized on entry and local declarations execute in statement order. premature local reads and assignments trap; assignment evaluates its right-hand side before checking the destination's initialization. globals initialize lazily on their first read, cache their result for that run, and trap on initialization cycles. integers use signed 64-bit storage with checked arithmetic; floats use 64-bit IEEE arithmetic and comparisons, including NaN behavior. numeric literal overflow and float underflow to zero are diagnosed; representable subnormals are accepted. strings support `\\`, `\"`, `\n`, `\r`, `\t`, `\0` and `\xNN` escapes.

recoverable source errors do not stop execution of unaffected code. unused invalid functions, globals and nested bodies retain diagnostic traps; reaching invalid code reports its original diagnostic identity. missing-return traps affect only paths that fall through, not paths that already return or trap. diagnostics include the filename, line and byte column. exit status is `0` for clean execution, `1` for source diagnostics or a runtime trap, and `2` for usage or infrastructure failure. native code generation and compound assignment remain unimplemented.
