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

## allocation and ownership

- put comptime parameters first, then the allocator as the first runtime parameter, then ordinary runtime inputs. for example: `duplicate(u32, allocator, source)`. use the same order for allocation, reallocation, and freeing operations.
- keep fixed-size temporary storage local. for dynamic storage, accept an allocator explicitly or use one retained by the object being operated on.
- an allocator argument means caller-controlled dynamic memory, not necessarily ownership of the result or an allocation on every call.
- document whether allocated storage is temporary, returned to the caller, or retained by an object. for caller-owned results, name the cleanup operation and any requirement to use the same allocator.
- release temporary storage before returning, including on error. objects that retain an allocator use it for subsequent allocations and release owned storage through their cleanup operation.

## documentation

- prefer lowercase prose in documentation, including headings and sentence starts. preserve the case of code identifiers, proper names, and acronyms.
- write comments for invariants, ownership, or reasons that the code alone does not explain. use `#!` for API documentation.

## Library operations

- Reuse an existing standard-library operation when it expresses the work directly.
- Use `mem.set(destination, value)` when assigning the same complete typed value to every element of a mutable slice. This preserves the value's fields; it is not a byte-wise zeroing operation.
- Keep a loop when only one field is initialized, values differ by element, or initialization shares a pass with other work. A helper should not introduce unnecessary writes or another traversal.

When editing or reviewing code, apply every relevant guideline to the changed code. Keep unrelated formatting changes separate.
