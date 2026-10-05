# Honey style

these guidelines apply to Honey programs, the compiler, and the standard library. the compiler is currently written in Bro; apply the same conventions using syntax supported by the bootstrap compiler. generated C bindings retain the generator's declarations and formatting.

## names

- use `snake_case` for procedures, variables, fields, and enum members; `PascalCase` for types; `SCREAMING_SNAKE_CASE` for named sentinel constants.
- choose names that describe the value or operation. short loop indices are appropriate when their meaning is local. preserve external ABI names.
- qualify imported operations with their module, such as `mem.alloc` and `ast.node_count`. mark file-local implementation details with `@hide:file`.

## bindings and types

- prefer `::` for bindings that stay fixed; use `:=` for bindings that change, including values mutated through pointers.
- infer types when the initializer makes the intended type clear. write an explicit type when it determines representation, mutability, or a public contract.
- request mutable pointers and slices only when the operation needs to modify their contents.
- give aggregates meaningful defaults. use `undefined` only when every value that can be read is assigned first.

## layout and control flow

- use four-space indentation and put opening braces on the declaration or control-flow line.
- use single-line bodies for simple operations. use braces for multiple statements or nested control flow.
- break long parameter lists and aggregate initializers across lines, with a trailing comma on the last item.
- use early returns for terminal cases so the main path stays shallow.
- register `defer` cleanup immediately after acquiring a resource. use `errdefer` for cleanup needed only until ownership is transferred successfully.

## allocation and ownership

- put comptime parameters first, then the allocator as the first runtime parameter, then ordinary runtime inputs. for example: `duplicate(u32, allocator, source)`. use the same order for allocation, reallocation, and freeing operations.
- keep fixed-size temporary storage local. for dynamic storage, accept an allocator explicitly or use one retained by the object being operated on.
- an allocator argument means caller-controlled dynamic memory, not necessarily ownership of the result or an allocation on every call.
- document whether allocated storage is temporary, returned to the caller, or retained by an object. for caller-owned results, name the cleanup operation and any requirement to use the same allocator.
- release temporary storage before returning, including on error. objects that retain an allocator use it for subsequent allocations and release owned storage through their cleanup operation.

## documentation

- prefer lowercase prose in documentation, including headings and sentence starts. preserve the case of code identifiers, proper names, and acronyms.
- write comments for invariants, ownership, or reasons that the code alone does not explain. use `#!` for API documentation.

## library operations

- reuse an existing standard-library operation when it expresses the work directly.
- use `mem.set(destination, value)` when assigning the same complete typed value to every element of a mutable slice. this preserves the value's fields; it is not a byte-wise zeroing operation.
- keep a loop when only one field is initialized, values differ by element, or initialization shares a pass with other work. a helper should not introduce unnecessary writes or another traversal.

when editing or reviewing code, apply every relevant guideline to the changed code. keep unrelated formatting changes separate.
