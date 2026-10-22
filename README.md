# Honey

Honey is currently bootstrapped with the Bro compiler. the expected development workspace places the Bro project at [`../brolang`](../brolang); consult that project for compiler behavior and documentation.

style: see the [language guidelines](docs/style.md).

build Honey from this directory:

```sh
bro build
```

the parser supports arithmetic expressions with nested parenthesized groups, such as `a + (b - c) * 2 / 3`. groups control precedence without adding AST nodes; inner expression spans exclude the grouping parentheses. parsing accepts integer division syntax, but type checking requires float operands for `/`.

function calls are postfix expressions: `f()`, `f(a, b)`, and `factory()(x)`. calls bind tighter than unary and binary arithmetic and can appear in initializers or as expression statements inside function bodies. argument lists allow a trailing comma; newlines remain statement boundaries. type checking validates callable values, argument counts and argument types.

name resolution (`@source/sema`) runs after parsing. it predeclares package names and function-body locals, allowing forward references and mutually recursive functions. parameters and locals share a function scope; signatures resolve names in the enclosing package scope. shadowing is forbidden, including reuse of builtin type names (`int`, `float`, `string`, `void`); sibling function scopes may independently reuse names.

`sema.resolve` returns an owned binding table indexed by AST `NodeId`, without modifying the AST. `sema.binding` identifies a declaration, builtin type, or invalid binding carrying its diagnostic ID; nodes with no binding return `.none`. release the result with `sema.deinit`. unknown names and duplicate declarations add source-spanned diagnostics; invalid syntax retains its existing diagnostic identity. same-scope duplicates retain the first declaration for recovery, while forbidden inner declarations invalidate that name's uses rather than silently binding them to an outer declaration.

type checking (`@source/sema/check`) runs after name resolution. `check.check` borrows the AST and binding table and returns owned per-node types; query with `check.type_of` and release with `check.deinit`. declarations infer their initializer types, including forward references and function aliases; function types identify the declaration supplying the callable signature. annotations must name builtin types; type names are not ordinary values. `void` is valid as a function result, not as a parameter or stored value.

numeric typing follows contextual rules within Honey's current `int` and `float` families. integer literals can adopt either family; float literals remain float. numeric demands propagate through arithmetic and inferred bindings before undemanded integers default to `int`. conflicting demands are errors; fixed `int` values do not implicitly convert to `float`. `+`, `-`, `*` and unary `-` require compatible numeric operands; `/` requires and supplies float context. `3 / 2`, `3.0 / 2`, and `ratio float :: 3 / 2` are valid; division involving fixed `int` values requires explicit conversion.

type errors carry source spans and diagnostic identities; invalid operands propagate their existing identity without duplicate errors, while independent expressions and arguments are still checked. inference cycles without a type anchor are diagnosed. read-before-initialization and runtime initialization-cycle checks remain separate, unimplemented work. function signatures are checked independently of their bodies; since return statements are not yet parsed, nonvoid functions currently report missing-return diagnostics. lowering and executable generation remain unimplemented.
