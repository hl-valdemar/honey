# Honey

Honey is currently bootstrapped with the Bro compiler. the expected development workspace places the Bro project at [`../brolang`](../brolang); consult that project for compiler behavior and documentation.

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

this prints `42`. `main` must be a top-level function with no parameters; nonvoid results are printed, not used as process exit statuses. the current macOS bootstrap obtains arguments from the native startup environment.

source loading uses the supplied `io.Io` for opening, reading, and closing files. `io.open` accepts a borrowed zero-terminated slice (`[;0]u8`), including arguments returned by `process.argument` without copying their storage. the bootstrap supports checked sentinel-slice construction with `pointer[..length;0]`. `io.read_all(allocator, reader, maximum)` reads to EOF into a caller-owned `ArrayList(u8)`; release it with `arraylist.deinit`. it frees partial storage on error and consumes one extra byte to distinguish an exact-size input from a limit violation. the driver maps `limit_exceeded` to `source_too_large` at its `u32` source-size limit.

the parser supports arithmetic expressions with nested parenthesized groups, such as `a + (b - c) * 2 / 3`. groups control precedence without adding AST nodes; inner expression spans exclude the grouping parentheses. parsing accepts integer division syntax, but type checking requires float operands for `/`.

function calls are postfix expressions: `f()`, `f(a, b)`, and `factory()(x)`. calls bind tighter than unary and binary arithmetic and can appear in initializers or as expression statements inside function bodies. argument lists allow a trailing comma; newlines remain statement boundaries. type checking validates callable values, argument counts and argument types.

name resolution (`@source/sema`) runs after parsing. it predeclares package names and function-body locals, allowing forward references and mutually recursive functions. parameters and locals share a function scope; signatures resolve names in the enclosing package scope. shadowing is forbidden, including reuse of builtin type names (`int`, `float`, `string`, `void`); sibling function scopes may independently reuse names.

`sema.resolve` returns an owned binding table indexed by AST `NodeId`, without modifying the AST. `sema.binding` identifies a declaration, builtin type, or invalid binding carrying its diagnostic ID; nodes with no binding return `.none`. release the result with `sema.deinit`. unknown names and duplicate declarations add source-spanned diagnostics; invalid syntax retains its existing diagnostic identity. same-scope duplicates retain the first declaration for recovery, while forbidden inner declarations invalidate that name's uses rather than silently binding them to an outer declaration.

type checking (`@source/sema/check`) runs after name resolution. `check.check` borrows the AST and binding table and returns owned per-node types; query with `check.type_of` and release with `check.deinit`. declarations infer their initializer types, including forward references and function aliases; function types identify the declaration supplying the callable signature. annotations must name builtin types; type names are not ordinary values. `void` is valid as a function result, not as a parameter or stored value.

numeric typing follows contextual rules within Honey's current `int` and `float` families. integer literals can adopt either family; float literals remain float. numeric demands propagate through arithmetic and inferred bindings before undemanded integers default to `int`. conflicting demands are errors; fixed `int` values do not implicitly convert to `float`. `+`, `-`, `*` and unary `-` require compatible numeric operands; `/` requires and supplies float context. `3 / 2`, `3.0 / 2`, and `ratio float :: 3 / 2` are valid; division involving fixed `int` values requires explicit conversion.

type errors carry source spans and diagnostic identities; invalid operands propagate their existing identity without duplicate errors, while independent expressions and arguments are still checked. inference cycles without a type anchor are diagnosed. function signatures are checked independently of their bodies. bare `return` and same-line `return expression` are supported inside function bodies; returns must match the declared result type, and nonvoid fallthrough is diagnosed. statements after a return are still checked but are not executed.

`lower.lower` turns the checked AST and binding table into an owned `@source/ir` module. functions, globals, local slots and instruction-result temporaries have explicit IDs; typed instructions execute in source evaluation order and basic blocks end in return or diagnostic-trap terminators. literals are parsed and strings decoded during lowering; the module retains no AST, binding-table or source-buffer references. release it with `ir.deinit`; keep and reuse the diagnostic store used during lowering so diagnostic IDs remain valid. the IR is target-independent and is intended to feed later native backends as well as the current interpreter.

`interpreter.run` consumes only IR and returns a value or a diagnostic trap; returned strings borrow the module. invocation frames are heap-backed and independent; parameters are initialized on entry and locals execute in statement order. premature local reads trap. globals initialize lazily on their first read, cache their result for that run, and trap on initialization cycles. integers use signed 64-bit storage with checked arithmetic; floats use 64-bit IEEE arithmetic. numeric literal overflow and float underflow to zero are diagnosed; representable subnormals are accepted. strings support `\\`, `\"`, `\n`, `\r`, `\t`, `\0` and `\xNN` escapes.

recoverable source errors do not stop execution of unaffected code. unused invalid functions and globals retain diagnostic traps; reaching invalid code reports its original diagnostic identity. diagnostics include the filename, line and byte column. exit status is `0` for clean execution, `1` for source diagnostics or a runtime trap, and `2` for usage or infrastructure failure. native code generation and control-flow/assignment syntax remain unimplemented.
