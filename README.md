# Honey

Honey is currently bootstrapped with the Bro compiler. The expected development workspace places the Bro project at [`../brolang`](../brolang); consult that project for compiler behavior and documentation.

Style: see the [language guidelines](docs/style.md).

Build Honey from this directory:

```sh
bro build
```

The parser supports arithmetic expressions with nested parenthesized groups, such as `a + (b - c) * 2 / 3`. Groups control precedence without adding AST nodes; inner expression spans exclude the grouping parentheses.

Function calls are postfix expressions: `f()`, `f(a, b)`, and `factory()(x)`. Calls bind tighter than unary and binary arithmetic and can appear in initializers or as expression statements inside function bodies. Argument lists allow a trailing comma; newlines remain statement boundaries. Argument/type checking belongs to semantic analysis and is not yet implemented.

Name resolution (`@source/sema`) runs after parsing. It predeclares package names and function-body locals, allowing forward references and mutually recursive functions. Parameters and locals share a function scope; signatures resolve names in the enclosing package scope. Shadowing is forbidden, including reuse of builtin type names (`int`, `float`, `string`, `void`); sibling function scopes may independently reuse names.

`sema.resolve` returns an owned binding table indexed by AST `NodeId`, without modifying the AST. `sema.binding` identifies a declaration, builtin type, or invalid binding carrying its diagnostic ID; nodes with no binding return `.none`. Release the result with `sema.deinit`. Unknown names and duplicate declarations add source-spanned diagnostics; invalid syntax retains its existing diagnostic identity. Same-scope duplicates retain the first declaration for recovery, while forbidden inner declarations invalidate that name's uses rather than silently binding them to an outer declaration. Type checking and read-before-initialization checks remain separate, unimplemented stages.
