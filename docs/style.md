# Honey style

These guidelines apply to Honey programs, the compiler, and the standard library. The compiler is currently written in Bro; apply the same conventions using syntax supported by the bootstrap compiler.

## Names

- Use `snake_case` for procedures, variables, fields, and enum members; `PascalCase` for types; `SCREAMING_SNAKE_CASE` for named sentinel constants.
- Choose names that describe the value or operation. Short loop indices are appropriate when their meaning is local.
- Qualify imported operations with their module, such as `mem.alloc` and `ast.node_count`. Mark file-local implementation details with `@hide:file`.

## Bindings and types

- Prefer `::` for bindings that stay fixed; use `:=` for bindings that change.
- Infer types when the initializer makes the intended type clear. Write an explicit type when it determines representation, mutability, or a public contract.
- Request mutable pointers and slices only when the operation needs to modify their contents.
- Give aggregates meaningful defaults. Use `undefined` only when every value that can be read is assigned first.

## Layout and control flow

- Use four-space indentation and put opening braces on the declaration or control-flow line.
- Use single-line bodies for simple operations. Use braces for multiple statements or nested control flow.
- Break long parameter lists and aggregate initializers across lines, with a trailing comma on the last item.
- Use early returns for terminal cases so the main path stays shallow.
- Register `defer` cleanup immediately after acquiring a resource. Use `errdefer` for cleanup needed only until ownership is transferred successfully.
- Write comments for invariants, ownership, or reasons that the code alone does not explain. Use `#!` for API documentation.

## Library operations

- Reuse an existing standard-library operation when it expresses the work directly.
- Use `mem.set(destination, value)` when assigning the same complete typed value to every element of a mutable slice. This preserves the value's fields; it is not a byte-wise zeroing operation.
- Keep a loop when only one field is initialized, values differ by element, or initialization shares a pass with other work. A helper should not introduce unnecessary writes or another traversal.

When editing or reviewing code, apply every relevant guideline to the changed code. Keep unrelated formatting changes separate.
