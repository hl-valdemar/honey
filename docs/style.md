# Honey style

these guidelines apply to Honey programs, the compiler, and the standard library. the compiler is currently written in Bro; apply the same conventions using syntax supported by the bootstrap compiler. generated C bindings retain the generator's declarations and formatting.

## names

- use `snake_case` for procedures, variables, constants, fields, and enum members; `PascalCase` for types.
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
- when breaking boolean or arithmetic expressions across lines, put continuation operators at the start of each line. indent operands one level inside the enclosing parentheses and put the closing parenthesis on its own line.
- wrap multiline `if` and `while` guards in parentheses, with the opening body brace after the closing parenthesis, so the guard's end is visually distinct from its body.

```hon
debug.assert(
    node.kind == .invalid_expr
    or node.kind == .invalid_stmt
    or node.kind == .invalid_decl
)

while (
    !next_is(state, .close_paren)
    and !next_is(state, .newline)
    and !next_is(state, .close_curly)
    and !next_is(state, .eof)
) {
    argument :: try parse_expr(state)
    try arraylist.append(&arguments, argument)
    if (!next_is(state, .comma)) break
    state.next_token += 1
}
```

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
- enclose explicit references to parameter identifiers in backticks, for example: “an empty `destination` is a no-op.” leave general conceptual terms unquoted, even when they match a parameter name, for example: “memory must be freed with the same allocator that was used to allocate it.”
- when editing a function, review its existing comments and API documentation against the resulting implementation. update comments that remain useful; remove those that are obsolete or merely restate self-explanatory code. retain contracts and rationale that callers or maintainers cannot infer from the implementation.

## library operations

- reuse an existing standard-library operation when it expresses the work directly.
- use `mem.set(destination, value)` when assigning the same complete typed value to every element of a mutable slice. this preserves the value's fields; it is not a byte-wise zeroing operation.
- keep a loop when only one field is initialized, values differ by element, or initialization shares a pass with other work. a helper should not introduce unnecessary writes or another traversal.

when editing or reviewing code, apply every relevant guideline to the changed code. keep unrelated formatting changes separate.
