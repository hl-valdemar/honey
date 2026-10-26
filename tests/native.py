"""Build Honey first with `bro build`, then run `python3 tests/native.py`."""

import os
from pathlib import Path
import subprocess
import tempfile
import textwrap

ROOT = Path(__file__).resolve().parents[1]
HONEY = ROOT / "build/honey"

# Explicit expectations guard against frontend bugs shared by both execution paths.
CASES = {
    "slice_mutation_alias_calls": (
        """
        update proc(values []mut i16) []mut i16 {
            values[1] = values[0] + 2
            return values[1..]
        }
        main proc() i16 {
            values [4]mut i16 := [0, 40, 0, 0]
            view :: update(values[1..3])
            copy :: view
            copy[0] = copy[0] + 1
            return values[2] - i16(view.len)
        }
        """, b"42\n", None,
    ),
    "slice_empty_one_past_and_pointer_identity": (
        """
        main proc() bool {
            values [2]mut i16 := [40, 2]
            all :: values[..]
            empty :: all[2..2]
            return empty.len == 0 and empty.ptr == all.ptr + 2
        }
        """, b"true\n", None,
    ),
    "slice_nested_record_storage": (
        """
        Row :: struct { values []mut i16 }
        identity proc(row Row) Row { return row }
        main proc() i16 {
            values [2]mut i16 := [40, 2]
            rows [1]Row :: [{ values = values[..] }]
            selected :: identity(rows[0])
            selected.values[0] = selected.values[0] + selected.values[1]
            return values[0]
        }
        """, b"42\n", None,
    ),
    "slice_empty_mutable_literal": (
        "main proc() usize { values []mut i16 :: &[]\nreturn values.len }",
        b"0\n", None,
    ),
    "slice_string_sentinel": (
        """
        read proc(text [;0]u8) u8 {
            suffix :: text[1..;0]
            return suffix.ptr[suffix.len] + suffix[0]
        }
        main proc() u8 { return read("!*") }
        """, b"42\n", None,
    ),
    "slice_checked_sentinel": (
        """
        main proc() u8 {
            values [3]mut u8 := [40, 2, 0]
            pointer *mut u8 :: &values[0]
            text [;0]mut u8 :: pointer[..2;0]
            text[0] = text[0] + text[1]
            plain []u8 :: text
            return plain[0]
        }
        """, b"42\n", None,
    ),
    "slice_reversed_bounds": (
        "main proc() usize { a :: [1, 2]\nreturn a[2..1].len }",
        b"", b"slice",
    ),
    "slice_excessive_bounds": (
        "main proc() usize { a :: [1, 2]\nreturn a[..3].len }",
        b"", b"slice",
    ),
    "slice_index_at_length": (
        "main proc() i64 { a :: [40, 2]\ns :: a[..]\nreturn s[s.len] }",
        b"", b"slice",
    ),
    "slice_sentinel_mismatch": (
        "main proc() usize { a :: [1, 2, 3]\nreturn a[..2;0].len }",
        b"", b"sentinel",
    ),
    "slice_readonly_write_rejected": (
        "main proc() int { a :: [40, 2]\ns :: a[..]\ns[0] = 42\nreturn 42 }",
        b"", b"writable",
    ),
    "optional_values_storage_calls": (
        """
        Box :: struct { value ?i16 }
        choose proc(value ?i16) ?i16 { return value }
        main proc() i16 {
            box Box := { value = 40 }
            options [2]mut ?i16 := [null, 2]
            options[0] = choose(box.value)
            return options[0]? + options[1]?
        }
        """, b"42\n", None,
    ),
    "optional_capture_and_null_transition": (
        """
        main proc() i16 {
            value ?i16 := 40
            total i16 := 0
            while value |payload| {
                total = total + payload
                value = null
                continue
            }
            if value |unused| return unused
            if (value != null) return 0
            value = 2
            if value |payload| return total + payload
            return 0
        }
        """, b"42\n", None,
    ),
    "optional_pointer_identity_and_mutation": (
        """
        main proc() i16 {
            value i16 := 40
            pointer ?@mut i16 := &value
            if pointer |present| present^ = present^ + 2
            readonly ?@i16 :: pointer
            if (readonly == null) return 0
            return readonly?^
        }
        """, b"42\n", None,
    ),
    "optional_null_unwrap": (
        "main proc() i16 { value ?i16 :: null\nreturn value? }",
        b"", b"null",
    ),
    "optional_untaken_unwrap": (
        "main proc() i16 { value ?i16 :: null\nif (false) return value?\nreturn 42 }",
        b"42\n", None,
    ),
    "memory_layout_padded_aggregates": (
        """
        Padded :: struct { tag u8, value u64 }
        Empty :: struct {}
        main proc() bool {
            return (
                sizeof!(Padded) == 16 and alignof!(Padded) == 8
                and sizeof!([3]Padded) == 48 and sizeof!([]u8) == 16
                and sizeof!(?@i16) == 8 and sizeof!(Empty) == 0
            )
        }
        """, b"true\n", None,
    ),
    "memory_pointer_cast_byte_alias": (
        """
        main proc() u16 {
            value u16 := 40
            bytes *mut u8 :: ptrcast!(&value)
            bytes[0] = bytes[0] + 2
            whole @mut u16 :: ptrcast!(bytes)
            return whole^
        }
        """, b"42\n", None,
    ),
    "memory_constcast_slice": (
        """
        main proc() i16 {
            values [2]mut i16 := [40, 2]
            readonly []i16 :: values[..]
            writable []mut i16 :: constcast!(readonly)
            writable[0] = writable[0] + writable[1]
            return values[0]
        }
        """, b"42\n", None,
    ),
    "memory_typed_copy_and_set": (
        """
        Pair :: struct { number i16, ready bool }
        main proc() i16 {
            source [2]Pair :: [{ number = 40, ready = true }, { number = 2, ready = false }]
            target [2]mut Pair := [{ number = 0, ready = false }, { number = 0, ready = false }]
            memcopy!(target[..], source[..])
            saved :: target[0]
            memset!(target[..], { number = 21, ready = true })
            if (!saved.ready or !target[1].ready) return 0
            return target[0].number + target[1].number
        }
        """, b"42\n", None,
    ),
    "memory_copy_overlap": (
        "main proc() int { a [3]mut i16 := [1, 2, 3]\nmemcopy!(a[1..], a[..2])\nreturn 42 }",
        b"", b"overlap",
    ),
    "memory_copy_length_mismatch": (
        "main proc() int { a [3]mut i16 := [1, 2, 3]\nmemcopy!(a[..1], a[1..])\nreturn 42 }",
        b"", b"slice",
    ),
    "memory_empty_copy_and_set": (
        "main proc() int { a [0]mut i16 := []\nmemcopy!(a[..], a[..])\nmemset!(a[..], 42)\nreturn 42 }",
        b"42\n", None,
    ),
    "optional_nested_presence": (
        "main proc() i16 { value ??i16 :: 42\nreturn value?? }", b"42\n", None,
    ),
    "optional_callback_capture_from_call": (
        """
        increment proc(value i16) i16 { return value + 2 }
        factory proc() ?@proc(value i16) i16 { return increment }
        main proc() i16 {
            if factory() |callback| return callback(40)
            return 0
        }
        """, b"42\n", None,
    ),
    "slice_recursive_record": (
        """
        Node :: struct { value i16, children []Node }
        main proc() i16 {
            children [0]Node :: []
            node Node :: { value = 42, children = children[..] }
            return node.value + i16(node.children.len)
        }
        """, b"42\n", None,
    ),
    "slice_full_width_sentinel": (
        """
        main proc() usize {
            values [2]u64 :: [42, 18446744073709551615]
            sentinel [;18446744073709551615]u64 :: values[..1;18446744073709551615]
            return sentinel.len
        }
        """, b"1\n", None,
    ),
    "memory_pointer_cast_preserves_readonly": (
        "main proc() int { value i16 :: 40\npointer *mut u8 :: ptrcast!(&value)\nreturn 42 }",
        b"", b"operator requires",
    ),
    "slice_sentinel_strengthening_rejected": (
        "main proc() int { a :: [1, 0]\ns []i64 :: a[..1]\nt [;0]i64 :: s\nreturn 42 }",
        b"", b"incompatible",
    ),
    "slice_fixed_signed_index_rejected": (
        "main proc() i64 { a :: [42]\ns :: a[..]\ni i16 :: 0\nreturn s[i] }",
        b"", b"index",
    ),
    "optional_nonoptional_capture_rejected": (
        "main proc() int { if true |payload| return 1\nreturn 42 }",
        b"", b"condition",
    ),
    "memory_foreign_main_rejected": (
        "main c_func() i16", b"", b"main must",
    ),
    "memory_opaque_dereference_rejected": (
        "main proc() int { value i16 := 42\npointer *mut anyopaque :: ptrcast!(&value)\ndiscard :: pointer^\nreturn 42 }",
        b"", b"dereference",
    ),
    "memory_opaque_index_rejected": (
        "main proc() int { value i16 := 42\npointer *mut anyopaque :: ptrcast!(&value)\ndiscard :: pointer[0]\nreturn 42 }",
        b"", b"indexing",
    ),
    "memory_opaque_slice_rejected": (
        "main proc() int { value i16 := 42\npointer *mut anyopaque :: ptrcast!(&value)\ndiscard :: pointer[..1]\nreturn 42 }",
        b"", b"operator requires",
    ),
    "memory_opaque_arithmetic_rejected": (
        "main proc() int { value i16 := 42\npointer *mut anyopaque :: ptrcast!(&value)\ndiscard :: pointer + 1\nreturn 42 }",
        b"", b"operator requires",
    ),
    "slice_contextual_borrow_preserves_readonly": (
        "main proc() i16 { values [1]i16 :: [40]\nview []mut i16 :: &values\nview[0] = 42\nreturn values[0] }",
        b"", b"incompatible",
    ),
    "slice_contextual_scalar_borrow_rejected": (
        "main proc() i16 { value i16 := 42\nview []mut i16 :: &value\nreturn view[0] }",
        b"", b"incompatible",
    ),
    "slice_implicit_borrow_preserves_binding_mutability": (
        "main proc() i16 { values [1]mut i16 :: [40]\nview []mut i16 :: values\nview[0] = 42\nreturn values[0] }",
        b"", b"incompatible",
    ),
    "callback_concrete_vtable": (
        """
        VTable :: struct { run @proc(context @mut State, amount i16) i16 }
        State :: struct { value i16, vtable @VTable }
        add proc(context @mut State, amount i16) i16 {
            context.value = context.value + amount
            return context.value
        }
        table VTable :: { run = add }
        main proc() i16 {
            state State := { value = 40, vtable = &table }
            return state.vtable.run(&state, 2)
        }
        """, b"42\n", None,
    ),
    "callback_pass_return_store_replace": (
        """
        Callback :: alias @proc(value i16) i16
        Holder :: struct { run Callback }
        increment proc(value i16) i16 { return value + 1 }
        twice proc(value i16) i16 { return value + value }
        choose proc(first Callback, second Callback, flag bool) Callback {
            if (flag) return first
            return second
        }
        apply proc(callback Callback, value i16) i16 { return callback(value) }
        main proc() i16 {
            callback @proc(n i16) i16 := increment
            callback = twice
            holder Holder := { run = increment }
            holder.run = callback
            callbacks [2]mut Callback := [increment, twice]
            callbacks[0] = choose(holder.run, increment, true)
            return apply(callbacks[0], 21)
        }
        """, b"42\n", None,
    ),
    "callback_nested_aggregate_arguments_and_result": (
        """
        Payload :: struct { values [2]i16, pointer @mut i16 }
        Callback :: alias @proc(input Payload, rows [2][2]i16) Payload
        transform proc(input Payload, rows [2][2]i16) Payload {
            input.pointer^ = input.pointer^ + rows[1][0]
            return { values = [input.values[0] + rows[0][1], rows[1][1]], pointer = input.pointer }
        }
        select proc(callback Callback) Callback { return callback }
        main proc() [3]i16 {
            value i16 := 40
            callback Callback :: select(transform)
            result :: callback({ values = [40, 0], pointer = &value }, [[0, 2], [2, 42]])
            return [result.values[0], result.values[1], result.pointer^]
        }
        """, b"[42, 42, 42]\n", None,
    ),
    "callback_storage_address_is_data_pointer": (
        """
        Callback :: alias @proc(value i16) i16
        increment proc(value i16) i16 { return value + 1 }
        twice proc(value i16) i16 { return value + value }
        replace proc(slot @mut Callback, callback Callback) void { slot^ = callback }
        main proc() i16 {
            callback Callback := increment
            replace(&callback, twice)
            return (&callback)^(21)
        }
        """, b"42\n", None,
    ),
    "callback_structural_inline_aggregate_signature": (
        """
        identity proc(value struct { values [2]i16, pointer @i16 }) struct { values [2]i16, pointer @i16 } {
            return value
        }
        main proc() i16 {
            number i16 :: 2
            callback @proc(input struct { values [2]i16, pointer @i16 }) struct { values [2]i16, pointer @i16 } :: identity
            result :: callback({ values = [40, 0], pointer = &number })
            return result.values[0] + result.pointer^
        }
        """, b"42\n", None,
    ),
    "callback_parameter_signature_mismatch": (
        "wrong proc(value u16) i16 { return 42 }\nmain proc() i16 { callback @proc(value i16) i16 :: wrong\nreturn callback(1) }",
        b"", b"incompatible value type",
    ),
    "callback_result_signature_mismatch": (
        "wrong proc(value i16) u16 { return 42 }\nmain proc() i16 { callback @proc(value i16) i16 :: wrong\nreturn callback(1) }",
        b"", b"incompatible value type",
    ),
    "callback_nested_signature_mismatch": (
        """
        wrong proc(value struct { rows [2][2]u16 }) i16 { return 42 }
        main proc() i16 {
            callback @proc(value struct { rows [2][2]i16 }) i16 :: wrong
            return 42
        }
        """, b"", b"incompatible value type",
    ),
    "callback_pointer_mutability_signature_mismatch": (
        "wrong proc(value @mut i16) i16 { return value^ }\nmain proc() i16 { callback @proc(value @i16) i16 :: wrong\nreturn 42 }",
        b"", b"incompatible value type",
    ),
    "callback_wrong_argument_type": (
        "identity proc(value i16) i16 { return value }\nmain proc() i16 { callback @proc(value i16) i16 :: identity\nreturn callback(true) }",
        b"", b"incompatible value type",
    ),
    "callback_wrong_argument_count": (
        "identity proc(value i16) i16 { return value }\nmain proc() i16 { callback @proc(value i16) i16 :: identity\nreturn callback() }",
        b"", b"argument count does not match",
    ),
    "callback_readonly_vtable_assignment": (
        """
        VTable :: struct { run @proc(value i16) i16 }
        identity proc(value i16) i16 { return value }
        replace proc(table @VTable) void { table.run = identity }
        main proc() i16 {
            table VTable := { run = identity }
            replace(&table)
            return 42
        }
        """, b"", b"pointer assignment requires a mutable pointee",
    ),
    "callback_readonly_array_assignment": (
        "identity proc(value i16) i16 { return value }\nmain proc() i16 { callbacks [1]@proc(value i16) i16 := [identity]\ncallbacks[0] = identity\nreturn 42 }",
        b"", b"indexed assignment requires a writable array element",
    ),
    "callback_invalid_annotation_recovery": (
        "bad proc(callback @proc(value void) i16) void {}\nmain proc() i16 { return 42 }",
        b"42\n", b"void cannot be stored",
    ),
    "callback_mutable_signature_rejected": (
        "bad proc(callback @mut proc(value i16) i16) void {}\nmain proc() i16 { return 42 }",
        b"42\n", b"pointer requires a supported concrete pointee type",
    ),
    "callback_many_signature_rejected": (
        "bad proc(callback *proc(value i16) i16) void {}\nmain proc() i16 { return 42 }",
        b"42\n", b"pointer requires a supported concrete pointee type",
    ),
    "pointer_assignment_evaluates_rhs_first_once": (
        """
        Box :: struct { value i16, count i16 }
        target proc(p @mut Box) @mut Box {
            p.count = p.count + 1
            return p
        }
        input proc(p @mut Box) i16 {
            p.count = p.count + 10
            return p.count
        }
        main proc() [2]i16 {
            box Box := { value = 0, count = 0 }
            target(&box).value = input(&box)
            return [box.value, box.count]
        }
        """, b"[10, 11]\n", None,
    ),
    "pointer_recursive_record_type": (
        """
        Node :: struct { value i16, next @Node }
        read_next proc(p @Node) i16 { return p.next.value }
        main proc() i16 { return 42 }
        """, b"42\n", None,
    ),
    "pointer_many_dereference": (
        """
        read proc(p *i16) i16 { return p^ }
        main proc() i16 {
            values [1]mut i16 := [42]
            return read((&values).ptr)
        }
        """, b"42\n", None,
    ),
    "pointer_many_from_array_element": (
        """
        main proc() i16 {
            values [3]mut i16 := [1, 2, 3]
            pointer *mut i16 :: &values[0]
            pointer[2] = 42
            return values[2]
        }
        """, b"42\n", None,
    ),
    "pointer_readonly_child_type_rejected": (
        "read proc(p @u16) u16 { return p^ }\nmain proc() u16 { value i16 := 42\nreturn read(&value) }",
        b"", b"incompatible value type",
    ),
    "pointer_scalar_alias_across_calls": (
        """
        identity proc(p @mut i16) @mut i16 { return p }
        update proc(p @mut i16) void { p^ = p^ + 2 }
        main proc() i16 {
            value i16 := 40
            pointer :: identity(&value)
            update(pointer)
            return value
        }
        """, b"42\n", None,
    ),
    "pointer_readonly_parameter": (
        """
        read proc(p @i16) i16 { return p^ }
        main proc() i16 {
            value i16 := 42
            return read(&value)
        }
        """, b"42\n", None,
    ),
    "pointer_record_alias_and_value_copy": (
        """
        Point :: struct { x i16, y i16 }
        set proc(p @mut Point) void { p.x = 40 }
        main proc() i16 {
            point Point := { x = 1, y = 2 }
            pointer :: &point
            copy :: pointer^
            set(pointer)
            if (copy.x != 1) return -1
            return point.x + pointer.y
        }
        """, b"42\n", None,
    ),
    "pointer_array_and_many_index": (
        """
        update proc(p @mut [3]mut i16) void {
            p[1] = p[0] + 2
            values :: p.ptr
            values[2] = values[1]
        }
        main proc() i16 {
            values [3]mut i16 := [40, 0, 0]
            update(&values)
            if ((&values).len != 3) return -1
            return values[2]
        }
        """, b"42\n", None,
    ),
    "pointer_aggregate_copy_keeps_alias": (
        """
        Holder :: struct { pointer @mut i16 }
        identity proc(h Holder) Holder { return h }
        main proc() i16 {
            value i16 := 40
            first :: Holder { pointer = &value }
            second :: identity(first)
            pointers [2]@mut i16 :: [first.pointer, second.pointer]
            pointers[1]^ = pointers[0]^ + 2
            return value
        }
        """, b"42\n", None,
    ),
    "pointer_global_returned_from_callee": (
        """
        value i16 :: 42
        address proc() @i16 { return &value }
        recurse proc(p @i16, n i16) i16 {
            if (n == 0) return p^
            return recurse(p, n - 1)
        }
        main proc() i16 { return recurse(address(), 20) }
        """, b"42\n", None,
    ),
    "pointer_location_identity": (
        """
        Point :: struct { x i16, y i16 }
        identity proc(p @mut i16) @mut i16 { return p }
        main proc() bool {
            point Point := { x = 40, y = 2 }
            values [2]mut i16 := [40, 2]
            first :: &point
            second :: &point
            return (
                first == second
                and identity(&point.x) == &point.x
                and &point.x != &point.y
                and (&values).ptr == (&values).ptr
            )
        }
        """, b"true\n", None,
    ),
    "pointer_union_roundtrip": (
        """
        Storage :: union { pointer @mut i16, same @mut i16 }
        main proc() i16 {
            value i16 := 40
            storage :: Storage { pointer = &value }
            pointer :: storage.same
            pointer^ = pointer^ + 2
            return value
        }
        """, b"42\n", None,
    ),
    "pointer_nested_array_aggregate": (
        """
        Point :: struct { x i16, y i16 }
        main proc() i16 {
            values [2]mut Point := [Point { x = 40, y = 2 }, Point { x = 0, y = 0 }]
            pointer :: (&values).ptr
            pointer[1] = pointer[0]
            pointer[0].x = 9
            return values[1].x + values[1].y
        }
        """, b"42\n", None,
    ),
    "pointer_to_pointer": (
        """
        set proc(p @mut @mut i16) void { p^^ = 42 }
        main proc() i16 {
            value i16 := 0
            pointer := &value
            set(&pointer)
            return value
        }
        """, b"42\n", None,
    ),
    "pointer_repeated_address_in_loop": (
        """
        main proc() i16 {
            value i16 := 0
            for 0..42 |i| {
                pointer :: &value
                pointer^ = i16(i) + 1
            }
            return value
        }
        """, b"42\n", None,
    ),
    "pointer_zero_sized_array_elements": (
        """
        Empty :: struct {}
        main proc() bool {
            values [2]mut Empty := [Empty {}, Empty {}]
            pointer :: (&values).ptr
            pointer[1] = pointer[0]
            return (&values).len == 2
        }
        """, b"true\n", None,
    ),
    "pointer_array_bounds_trap": (
        "main proc() i16 { values [1]mut i16 := [42]\nreturn (&values)[1] }",
        b"", b"index out of bounds",
    ),
    "pointer_address_before_initialization": (
        "main proc() i16 { pointer :: &value\nvalue i16 := 42\nreturn pointer^ }",
        b"", b"read before initialization",
    ),
    "pointer_constant_is_readonly": (
        "main proc() i16 { value i16 :: 40\npointer :: &value\npointer^ = 42\nreturn value }",
        b"", b"",
    ),
    "pointer_readonly_parameter_write_rejected": (
        "set proc(p @i16) void { p^ = 42 }\nmain proc() i16 { value i16 := 0\nset(&value)\nreturn value }",
        b"", b"",
    ),
    "pointer_array_readonly_elements": (
        "main proc() i16 { values [1]i16 := [40]\npointer :: &values\npointer[0] = 42\nreturn values[0] }",
        b"", b"",
    ),
    "pointer_readonly_not_mutable_argument": (
        "set proc(p @mut i16) void { p^ = 42 }\nmain proc() i16 { value i16 :: 0\nset(&value)\nreturn value }",
        b"", b"incompatible value type",
    ),
    "pointer_nonpointer_dereference_rejected": (
        "main proc() i16 { value i16 :: 42\nreturn value^ }",
        b"", b"dereference requires",
    ),
    "pointer_void_pointee_rejected": (
        "unused proc(p @void) void {}\nmain proc() int { return 42 }",
        b"42\n", b"pointer requires",
    ),
    "pointer_temporary_address_rejected": (
        "main proc() i16 { pointer :: &(40 + 2)\nreturn pointer^ }",
        b"", b"address-of requires",
    ),
    "pointer_unused_invalid_path": (
        "unused proc() int { return 42^ }\nmain proc() int { return 42 }",
        b"42\n", b"dereference requires",
    ),
    "pointer_addition_preserves_alias_and_mutability": (
        """
        advance proc(p *mut i16, count usize) *mut i16 { return p + count }
        main proc() [3]i16 {
            values [3]mut i16 := [1, 2, 3]
            pointer :: advance((&values).ptr, 1)
            pointer^ = 40
            (pointer + 1)^ = pointer^ + 2
            return [values[0], values[1], values[2]]
        }
        """, b"[1, 40, 42]\n", None,
    ),
    "pointer_addition_aggregate_stride_and_identity": (
        """
        Point :: struct { x i16, y i64 }
        main proc() bool {
            values [3]mut Point := [
                Point { x = 1, y = 2 }, Point { x = 3, y = 4 }, Point { x = 5, y = 6 },
            ]
            pointer :: (&values).ptr
            next :: pointer + (1 + 1)
            next.y = 42
            last *mut Point :: &values[2]
            return (next == last and values[2].y == 42
                and pointer + 3 == next + 1)
        }
        """, b"true\n", None,
    ),
    "pointer_addition_evaluates_operands_once_in_order": (
        """
        first proc(count @mut usize, values *mut i16) *mut i16 {
            count^ = count^ + 1
            return values
        }
        second proc(count @mut usize) usize {
            previous :: count^
            count^ = count^ + 1
            return previous
        }
        main proc() [2]i16 {
            count usize := 0
            values [2]mut i16 := [1, 42]
            pointer :: first(&count, (&values).ptr) + second(&count)
            return [pointer^, i16(count)]
        }
        """, b"[42, 2]\n", None,
    ),
    "pointer_addition_zero_sized_elements": (
        """
        Empty :: struct {}
        main proc() bool {
            values [2]mut Empty := [Empty {}, Empty {}]
            pointer :: (&values).ptr
            return pointer + 2 == pointer
        }
        """, b"true\n", None,
    ),
    "pointer_addition_requires_usize_offset": (
        "main proc() i16 { values [2]mut i16 := [1, 42]\noffset i16 :: 1\nreturn ((&values).ptr + offset)^ }",
        b"", b"",
    ),
    "pointer_addition_single_pointer_rejected": (
        "main proc() i16 { value i16 := 42\nreturn (&value + 1)^ }",
        b"", b"",
    ),
    "pointer_subtraction_rejected": (
        "main proc() i16 { values [2]mut i16 := [1, 42]\nreturn ((&values).ptr - 1)^ }",
        b"", b"",
    ),
    "pointer_reversed_addition_rejected": (
        "main proc() i16 { values [2]mut i16 := [1, 42]\nreturn (1 + (&values).ptr)^ }",
        b"", b"",
    ),
    "pointer_addition_preserves_readonly_pointee": (
        "main proc() i16 { values [2]i16 := [1, 42]\npointer :: (&values).ptr + 1\npointer^ = 7\nreturn values[1] }",
        b"", b"",
    ),
    "pointer_addition_unused_error_recovers": (
        "bad proc(p *i16) *i16 { return p + true }\nmain proc() i16 { return 42 }",
        b"42\n", b"",
    ),
    "match_float_ranges": (
        """
        score proc(n f64) i16 {
            return match n { (-1.5)..0.5: 1, 0.5..=2.5: 42, else: 0 }
        }
        main proc() [3]i16 { return [score(-1.0), score(2.5), score(3.0)] }
        """, b"[1, 42, 0]\n", None,
    ),
    "distinct_float_literal_context": (
        """
        Amount :: distinct f64
        main proc() f64 {
            first Amount :: 1.5
            second :: 0.5 + first
            return f64(second + 40)
        }
        """, b"42\n", None,
    ),
    "distinct_enum_construction": (
        "E :: enum { a, b }\nD :: distinct E\nmain proc() bool { return D(E.a) == D(E.a) }",
        b"true\n", None,
    ),
    "union_aligned_record_payload": (
        """
        Pair :: struct { a u8, b u64 }
        U :: union { pair Pair, words [2]mut u64 }
        main proc() [2]u64 {
            value := U { words = [1, 42] }
            value.pair.b = 99
            copy :: value
            value.words[1] = 7
            return [u64(copy.pair.a), copy.pair.b]
        }
        """, b"[1, 99]\n", None,
    ),
    "tagged_union_result": (
        "C :: union(enum) { empty void, value struct { x u8, y bool } }\nmain proc() C { return .value { x = 42, y = true } }",
        b"{{42, true}}\n", None,
    ),
    "tagged_union_void_result": (
        "C :: union(enum) { empty void, value u8 }\nmain proc() C { return .empty }",
        b"{}\n", None,
    ),
    "distinct_boolean_equality": (
        "Flag :: distinct bool\nmain proc() bool { return Flag(true) == Flag(true) and bool(Flag(true)) and !bool(Flag(false)) }",
        b"true\n", None,
    ),
    "match_aggregate_result": (
        """
        E :: enum { a, b }
        Point :: struct { x i16 }
        choose proc(e E) Point { return match e { .a: Point { x = 42 }, .b: Point { x = 9 } } }
        main proc() i16 { return choose(.a).x }
        """, b"42\n", None,
    ),
    "enum_context_and_values": (
        """
        Token :: enum(i16) { if = -2, else, return = 40 }
        identity proc(t Token) Token { return t }
        main proc() i16 {
            values [2]Token :: [.if, Token.return]
            if (identity(values[0]) == .if) return i16(values[1]) + 2
            return 0
        }
        """, b"42\n", None,
    ),
    "enum_unsigned_backing": (
        "E :: enum(u64) { high = 18446744073709551615 }\nmain proc() u64 { return u64(E.high) }",
        b"18446744073709551615\n", None,
    ),
    "distinct_arithmetic_and_layers": (
        """
        Id :: distinct u8
        Wrapped :: distinct Id
        Alias :: alias Id
        identity proc(n Id) Id { return n }
        main proc() u8 {
            n Alias := identity(Alias(40))
            n = n + 2
            return u8(Id(Wrapped(n)))
        }
        """, b"42\n", None,
    ),
    "distinct_aggregate_copy": (
        """
        Point :: struct { x i16 }
        Wrapped :: distinct Point
        identity proc(w Wrapped) Wrapped { return w }
        main proc() i16 {
            p Point := { x = 42 }
            w :: identity(Wrapped(p))
            p.x = 9
            copy :: Point(w)
            return copy.x
        }
        """, b"42\n", None,
    ),
    "alias_function_and_global_identity": (
        """
        value i16 :: 41
        add proc(n i16) i16 { return n + 1 }
        invoke :: alias add
        other :: alias value
        main proc() i16 { return invoke(other) }
        """, b"42\n", None,
    ),
    "distinct_overflow": (
        "Id :: distinct u8\nmain proc() Id { a Id :: 255\nreturn a + 1 }",
        b"", b"integer arithmetic overflow",
    ),
    "distinct_constructor_range": (
        "Id :: distinct u8\nmain proc() Id { return Id(256) }",
        b"", b"integer arithmetic overflow",
    ),
    "distinct_implicit_rejected": (
        "Id :: distinct u8\nmain proc() Id { n u8 :: 42\nreturn n }",
        b"", b"return does not match",
    ),
    "distinct_identity_rejected": (
        "A :: distinct u8\nB :: distinct u8\nmain proc() A { return B(42) }",
        b"", b"return does not match",
    ),
    "union_scalar_overlay": (
        """
        Bits :: union { integer u64, real f64 }
        main proc() f64 {
            b := Bits { integer = 4607182418800017408 }
            copy :: b
            b.real = 2.0
            return copy.real + b.real
        }
        """, b"3\n", None,
    ),
    "union_nested_layout_and_copy": (
        """
        Pair :: struct { low u8, high u8 }
        Bits :: union { pair Pair, integer u16 }
        Box :: struct { values [2]mut Bits }
        identity proc(b Box) Box { return b }
        main proc() [3]u16 {
            b Box := { values = [Bits { integer = 513 }, Bits { pair = { low = 3, high = 4 } }] }
            saved :: identity(b)
            b.values[0].pair.high = 9
            return [saved.values[0].integer, saved.values[1].integer, b.values[0].integer]
        }
        """, b"[513, 1027, 2305]\n", None,
    ),
    "tagged_union_match_copy_and_mutation": (
        """
        Tag :: enum(i16) { empty = -1, number = 42, pair = 90 }
        Choice :: union(Tag) { pair struct { x i16, y i16 = 2 }, number i16, empty void }
        score proc(c Choice) i16 {
            return match c {
                .number |n|: n
                .pair |p|: p.x + p.y
                .empty: 0
            }
        }
        main proc() [3]i16 {
            a Choice := .pair { x = 40 }
            b :: a
            a.pair.x = 9
            c Choice :: .number{42}
            return [score(b), score(a), score(c)]
        }
        """, b"[42, 11, 42]\n", None,
    ),
    "tagged_union_void_and_group_capture": (
        """
        Choice :: union(enum) { left u8, right u8, empty void }
        score proc(c Choice) u8 {
            match c {
                .left, .right |n|: return n
                .empty: return 0
            }
        }
        main proc() u8 { a Choice :: .empty
            return score(Choice { right = 42 }) + score(a) }
        """, b"42\n", None,
    ),
    "tagged_union_inactive_read": (
        "C :: union(enum) { a u8, b u8 }\nmain proc() u8 { c :: C { a = 42 }\nreturn c.b }",
        b"", b"tagged union field is not active",
    ),
    "tagged_union_inactive_write": (
        "C :: union(enum) { a u8, b u8 }\nmain proc() u8 { c := C { a = 42 }\nc.b = 9\nreturn c.a }",
        b"", b"tagged union field is not active",
    ),
    "match_expression_context_and_laziness": (
        """
        Kind :: enum { first, second }
        poison i16 :: 32767 + 1
        choose proc() Kind { return .second }
        main proc() i16 {
            result i16 :: match choose() { .first: poison, .second: 42 }
            return result
        }
        """, b"42\n", None,
    ),
    "match_scalar_ranges_and_loop_control": (
        """
        main proc() i16 {
            sum i16 := 0
            for 0..10 |i| {
                match i {
                    0..3: continue
                    3, 4: sum = sum + 1
                    5..=7: sum = sum + 10
                    else: break
                }
            }
            return sum
        }
        """, b"32\n", None,
    ),
    "match_boolean_exhaustive": (
        "main proc() i16 { return match true { false: 1, true: 42 } }",
        b"42\n", None,
    ),
    "match_non_exhaustive": (
        "E :: enum { a, b }\nmain proc() i16 { return match E.a { .a: 42 } }",
        b"", b"match is not exhaustive",
    ),
    "match_overlapping_range": (
        "main proc() i16 { return match 3 { 0..=3: 1, 3..5: 2, else: 42 } }",
        b"", b"overlapping match pattern",
    ),
    "enum_invalid_value": (
        "E :: enum(u8) { a = 256 }\nmain proc() E { return .a }",
        b"", b"representable constant integer",
    ),
    "enum_nominal_identity": (
        "A :: enum { a }\nB :: enum { a }\nmain proc() A { return B.a }",
        b"", b"return does not match",
    ),
    "union_requires_one_member": (
        "U :: union { a u8, b u8 }\nmain proc() U { return U { a = 1, b = 2 } }",
        b"", b"exactly one member",
    ),
    "record_defaults_and_copies": (
        """
        Point :: struct { x i16, y i16 = 2 }
        seed Point :: { x = 40 }
        identity proc(p Point) Point { return p }
        main proc() [3]i16 {
            a := identity(seed)
            b := a
            a.x = 9
            return [b.x + b.y, a.x, seed.x]
        }
        """, b"[42, 9, 40]\n", None,
    ),
    "contextual_record_arguments": (
        """
        Point :: struct { x u8, y u8 = 2 }
        add proc(p Point) u8 { return p.x + p.y }
        main proc() u8 { return add({ x = 40 }) }
        """, b"42\n", None,
    ),
    "nested_records_arrays_tuples": (
        """
        Point :: struct { x i16, y i16 = 2 }
        Box :: struct { points [2]mut Point, pair struct { u8, bool }, empty struct {} }
        identity proc(b Box) Box { return b }
        main proc() Box {
            a Box := { points = [{ x = 1 }, { x = 40 }], pair = {255, true}, empty = {} }
            b :: identity(a)
            a.points[0].x = 9
            a.pair.0 = 8
            return b
        }
        """, b"{[{1, 2}, {40, 2}], {255, true}, {}}\n", None,
    ),
    "anonymous_records_and_numeric_fields": (
        """
        main proc() i64 {
            a := { x = 40, pair = {{2, true}, false} }
            b :: a
            a.x = 1
            return b.x + b.pair.0.0
        }
        """, b"42\n", None,
    ),
    "record_temporary_fields_and_guards": (
        """
        Point :: struct { x u8, y u8 = 2 }
        make proc() Point { return { x = 40 } }
        main proc() u8 {
            flag :: { ready = true }
            if flag.ready { return make().x + make().y }
            return 0
        }
        """, b"42\n", None,
    ),
    "zero_cell_records": (
        """
        Empty :: struct {}
        Box :: struct { values [3]Empty, empty Empty }
        main proc() Box { return { values = [{}, {}, {}], empty = {} } }
        """, b"{[{}, {}, {}], {}}\n", None,
    ),
    "record_default_override": (
        """
        Point :: struct { x i64 = 9223372036854775807 + 1 }
        main proc() i64 { p :: Point { x = 42 }
            return p.x }
        """, b"42\n", None,
    ),
    "record_default_trap": (
        """
        Point :: struct { x i64 = 9223372036854775807 + 1 }
        main proc() i64 { p :: Point {}
            return p.x }
        """, b"", b"integer arithmetic overflow",
    ),
    "record_branchful_default": (
        """
        Point :: struct { x bool = false and (9223372036854775807 + 1 == 0) }
        main proc() bool { p :: Point {}
            return !p.x }
        """, b"true\n", None,
    ),
    "record_explicit_evaluation_order": (
        """
        cycle i64 :: cycle
        Point :: struct { x i64, y i64 }
        main proc() Point { return Point { y = 9223372036854775807 + 1, x = cycle } }
        """, b"", b"integer arithmetic overflow",
    ),
    "record_explicit_before_default": (
        """
        cycle i64 :: cycle
        Point :: struct { x i64 = cycle, y i64 }
        main proc() Point { return Point { y = 9223372036854775807 + 1 } }
        """, b"", b"integer arithmetic overflow",
    ),
    "record_assignment_rhs_before_place": (
        """
        Point :: struct { xs [1]mut i64 }
        main proc() Point { p Point := { xs = [0] }
            p.xs[1] = 9223372036854775807 + 1
            return p }
        """, b"", b"integer arithmetic overflow",
    ),
    "record_reordered_structural_assignment": (
        "main proc() int { a := { x = 1, y = 2 }\na = { y = 40, x = 2 }\nreturn a.x + a.y }",
        b"42\n", None,
    ),
    "record_local_type_and_loop": (
        """
        main proc() i16 {
            Point :: struct { x i16 }
            p Point := { x = 0 }
            for 0..100 |i| {
                copy := p
                copy.x = p.x + 1
                p = copy
            }
            return p.x
        }
        """, b"100\n", None,
    ),
    "record_function_field": (
        """
        plus proc(n i64) i64 { return n + 1 }
        minus proc(n i64) i64 { return n - 1 }
        main proc() i64 { p := { f = plus }
            p.f = minus
            return p.f(43) }
        """, b"42\n", None,
    ),
    "record_nominal_mismatch": (
        "A :: struct { x i64 }\nB :: struct { x i64 }\nmain proc() A { b :: B { x = 42 }\nreturn b }",
        b"", b"",
    ),
    "record_missing_field": (
        "Point :: struct { x i64, y i64 }\nmain proc() Point { return { x = 42 } }", b"", b"",
    ),
    "record_duplicate_field": (
        "Point :: struct { x i64 }\nmain proc() Point { return { x = 1, x = 2 } }", b"", b"",
    ),
    "record_unknown_field": (
        "Point :: struct { x i64 }\nmain proc() Point { return { x = 1, z = 2 } }", b"", b"",
    ),
    "record_wrong_field_type": (
        "Point :: struct { x u8 }\nmain proc() Point { n u16 :: 42\nreturn { x = n } }", b"", b"",
    ),
    "record_readonly_field": (
        "Point :: struct { x i64 }\nmain proc() i64 { p :: Point { x = 1 }\np.x = 2\nreturn p.x }",
        b"", b"",
    ),
    "record_readonly_array_field": (
        "Point :: struct { xs [1]i64 }\nmain proc() i64 { p Point := { xs = [1] }\np.xs[0] = 2\nreturn p.xs[0] }",
        b"", b"",
    ),
    "record_parameter_readonly": (
        "Point :: struct { x i64 }\nmutate proc(p Point) i64 { p.x = 2\nreturn p.x }\nmain proc() i64 { return mutate({ x = 1 }) }",
        b"", b"",
    ),
    "record_recursive_layout": (
        "Point :: struct { next Point }\nmain proc() int { return 42 }", b"42\n", b"",
    ),
    "record_tuple_arity": (
        "main proc() struct { i64, bool } { return {42,} }", b"", b"",
    ),
    "record_unused_bad_body": (
        "Point :: struct { x i64 }\nbad proc() Point { return { missing = 1 } }\nmain proc() i64 { return 42 }",
        b"42\n", b"",
    ),
    "record_field_bounds": (
        "Point :: struct { xs [1]i64 }\nmain proc() i64 { p :: Point { xs = [42] }\nreturn p.xs[1] }",
        b"", b"index out of bounds",
    ),
    "record_type_not_value": (
        "Point :: struct { x i64 }\nmain proc() i64 { Point\nreturn 42 }", b"", b"type name cannot",
    ),
    "void": ("main proc() void {}", b"", None),
    "signed_limits": (
        "main proc() [2]i64 { return [-9223372036854775808, 9223372036854775807] }",
        b"[-9223372036854775808, 9223372036854775807]\n", None,
    ),
    "float_context": ("main proc() float { return 3 / 2 }", b"1.5\n", None),
    "float_ieee": (
        """
        main proc() bool {
            nan :: 0.0 / 0.0
            inf :: 1.0 / 0.0
            return nan != nan and !(nan == nan) and !(nan < 0.0) and !(nan >= 0.0) and inf > 1.0
        }
        """, b"true\n", None,
    ),
    "float_rounding": (
        "main proc() f64 { return 9007199254740993.0 - 9007199254740992 }", b"0\n", None,
    ),
    "strings": (r'main proc() string { return "a\0b\n\"\\" }', b'a\0b\n"\\\n', None),
    "scalar_call_types": (
        """
        nothing proc() void {}
        twice proc(x f64) f64 { return x * 2 }
        select proc(flag bool, left string, right string) string {
            nothing()
            if flag { return left }
            return right
        }
        main proc() string { return select(twice(1.5) == 3.0, "yes", "no") }
        """, b"yes\n", None,
    ),
    "mutual_recursion": (
        """
        even proc(n int) bool { if n == 0 { return true } return odd(n - 1) }
        odd proc(n int) bool { if n == 0 { return false } return even(n - 1) }
        main proc() bool { return even(20) and odd(19) }
        """, b"true\n", None,
    ),
    "function_values": (
        """
        plus proc(n int) int { return n + 1 }
        minus proc(n int) int { return n - 1 }
        main proc() bool {
            f := plus
            f = minus
            a :: [plus, f]
            return a[0](41) == 42 and a[1](43) == 42
        }
        """, b"true\n", None,
    ),
    "short_circuit": (
        """
        cycle bool :: cycle
        bad proc() bool { return 9223372036854775807 + 1 == 0 }
        main proc() bool { return !(false and bad()) and (true or cycle) }
        """, b"true\n", None,
    ),
    "lazy_globals": (
        """
        first :: identity(later)
        later [2][2]string :: [["one", "two"], ["three", "four"]]
        identity proc(a [2][2]string) [2][2]string { return a }
        main proc() [2][2]string { return identity(identity(first)) }
        """, b"[[one, two], [three, four]]\n", None,
    ),
    "array_copies": (
        """
        seed [2]mut [2]mut i64 :: [[1, 2], [3, 4]]
        identity proc(a [2]mut [2]mut i64) [2]mut [2]mut i64 { return a }
        main proc() [2]mut [2]mut i64 {
            a := identity(seed)
            b := a
            (a[0])[1] = 99
            a[1] = a[0]
            return b
        }
        """, b"[[1, 2], [3, 4]]\n", None,
    ),
    "zero_leaf_arrays": (
        """
        identity proc(a [2][0]bool) [2][0]bool { return a }
        main proc() [2][0]bool { return identity([[], []]) }
        """, b"[[], []]\n", None,
    ),
    "array_branchful_arguments": (
        """
        identity proc(a [2]bool) [2]bool { return a }
        pair proc(a [2]bool, b [2]bool) [2][2]bool { return [a, b] }
        main proc() [2][2]bool {
            return pair(identity([true or false, false and true]), identity([false or true, !false]))
        }
        """, b"[[true, false], [true, true]]\n", None,
    ),
    "unsigned_max_range": (
        """
        main proc() int {
            end usize :: 18446744073709551615
            start usize :: 18446744073709551614
            count := 0
            for start..=end |i| {
                if (i < start) return 0
                count = count + 1
                continue
            }
            return count
        }
        """, b"2\n", None,
    ),
    "unsigned_comparisons": (
        """
        main proc() bool {
            a usize :: 9223372036854775808
            return a > 9223372036854775807 and a * 1 + 1 - 1 == a
        }
        """, b"true\n", None,
    ),
    "signed_range_limits": (
        """
        main proc() int {
            maximum :: 9223372036854775807
            count := 0
            for maximum..=maximum |_| { count = count + 1 }
            for 2..1 |_| { count = 0 }
            for (-2)..0 |_| { count = count + 1 }
            return count
        }
        """, b"3\n", None,
    ),
    "loop_array_storage": (
        """
        main proc() int {
            n := 0
            a [1]mut i64 := [0]
            while n < 10000 {
                row [1]mut i64 := [a[0]]
                row[0] = row[0] + 1
                a = row
                n = n + 1
            }
            return a[0]
        }
        """, b"10000\n", None,
    ),
    "forward_local": (
        "main proc() int {\n before :: after\n after :: 42\n return before\n}",
        b"", b"local value was read before initialization",
    ),
    "forward_assignment": (
        "main proc() int {\n after = 1\n after := 42\n return after\n}",
        b"", b"local value was assigned before initialization",
    ),
    "loop_local_reset": (
        """
        main proc() int {
            n := 0
            while n < 2 {
                n = n + 1
                if (n == 2) return later[0]
                later [1]i64 :: [42]
            }
            return 0
        }
        """, b"", b"local value was read before initialization",
    ),
    "global_cycle": (
        "a [1]i64 :: b\nb [1]i64 :: a\nmain proc() int { return a[0] }",
        b"", b"global initialization cycle",
    ),
    "bounds": (
        "main proc() int {\n a :: [42]\n return a[1]\n}", b"", b"index out of bounds",
    ),
    "empty_bounds": (
        "main proc() int {\n a [0]i64 :: []\n return a[0]\n}", b"", b"index out of bounds",
    ),
    "unsigned_bounds": (
        "main proc() int {\n a [1]mut i64 := [1]\n a[18446744073709551615] = 2\n return 0\n}",
        b"", b"index out of bounds",
    ),
    "assignment_error_order": (
        "main proc() int {\n a [0]mut i64 := []\n a[0] = 9223372036854775807 + 1\n return 0\n}",
        b"", b"integer arithmetic overflow",
    ),
    "signed_add_overflow": (
        "main proc() int { return 9223372036854775807 + 1 }", b"", b"integer arithmetic overflow",
    ),
    "signed_subtract_overflow": (
        "main proc() int { return -9223372036854775808 - 1 }", b"", b"integer arithmetic overflow",
    ),
    "signed_multiply_overflow": (
        "main proc() int { return 9223372036854775807 * 2 }", b"", b"integer arithmetic overflow",
    ),
    "signed_negate_overflow": (
        "main proc() int { return -(-9223372036854775808) }", b"", b"integer arithmetic overflow",
    ),
    "unsigned_subtract_overflow": (
        "main proc() usize {\n zero usize :: 0\n return zero - 1\n}", b"", b"integer arithmetic overflow",
    ),
    "unsigned_add_overflow": (
        "main proc() usize { return 18446744073709551615 + 1 }", b"", b"integer arithmetic overflow",
    ),
    "unsigned_multiply_overflow": (
        "main proc() usize {\n max usize :: 18446744073709551615\n return max * 2\n}",
        b"", b"integer arithmetic overflow",
    ),
    "unused_errors": (
        "bad proc() int { return absent }\nmain proc() int { return 42 }", b"42\n", b"unknown name",
    ),
    "reached_source_error": (
        "main proc() int { return absent }", b"", b"unknown name",
    ),
    "missing_return": (
        "main proc() int { if false { return 42 } }", b"", b"nonvoid function can finish",
    ),
    "missing_entry": ("other proc() void {}", b"", b"program requires a main function"),
    "invalid_unused_signature": (
        "bad proc(a void) void {}\nmain proc() int { return 42 }", b"42\n", b"void cannot be stored",
    ),
}

# Exercise each representation through storage, calls, ranges and checked updates.
for integer_type, bits, signed in (
    ("i8", 8, True), ("i16", 16, True), ("i32", 32, True),
    ("i64", 64, True), ("isize", 64, True),
    ("u8", 8, False), ("u16", 16, False), ("u32", 32, False),
    ("u64", 64, False), ("usize", 64, False),
):
    minimum = -(1 << (bits - 1)) if signed else 0
    maximum = (1 << (bits - int(signed))) - 1
    CASES[f"{integer_type}_storage"] = (
        f"""
        seed [2]mut {integer_type} :: [{minimum}, {maximum}]
        identity proc(a [2]mut {integer_type}) [2]mut {integer_type} {{ return a }}
        main proc() [2]mut {integer_type} {{
            a := identity(seed)
            b := a
            a[0] = 1
            return b
        }}
        """, f"[{minimum}, {maximum}]\n".encode(), None,
    )
    CASES[f"{integer_type}_inclusive_max"] = (
        f"""
        main proc() int {{
            start {integer_type} :: {maximum - 1}
            end {integer_type} :: {maximum}
            count := 0
            for start..=end |i| {{
                if (i < start) return -1
                count = count + 1
                continue
            }}
            return count
        }}
        """, b"2\n", None,
    )
    for operation, left, right in (
        ("+", maximum, 1), ("-", minimum, 1), ("*", maximum, 2),
    ):
        CASES[f"{integer_type}_overflow_{operation}"] = (
            f"main proc() {integer_type} {{ a {integer_type} :: {left}\nreturn a {operation} {right} }}",
            b"", b"integer arithmetic overflow",
        )
    CASES[f"{integer_type}_literal_range"] = (
        f"main proc() {integer_type} {{ return {maximum + 1} }}",
        b"", b"numeric literal is outside",
    )
    if signed:
        CASES[f"{integer_type}_negate_min"] = (
            f"main proc() {integer_type} {{ a {integer_type} :: {minimum}\nreturn -a }}",
            b"", b"integer arithmetic overflow",
        )

CASES.update({
    "integer_casts": (
        """
        identity proc(n u8) u8 { return n }
        main proc() [4]i64 {
            large u64 :: 18446744073709551615
            small := identity(u8(i16(255)))
            if (large != u64(18446744073709551615)) return [0, 0, 0, 0]
            return [i64(small), i64(i8(-128)), i64(u32(4294967295)), i64(u8(255))]
        }
        """,
        b"[255, -128, 4294967295, 255]\n", None,
    ),
    "cast_unsigned_max": ("main proc() u64 { return u64(18446744073709551615) }", b"18446744073709551615\n", None),
    "cast_float_rounding": ("main proc() f64 { return f64(9007199254740993) - 9007199254740992.0 }", b"0\n", None),
    "cast_fractional": ("main proc() [4]i64 { return [i64(i8(-128.9)), i64(u8(-0.9)), i64(u8(255.9)), i64(i8(127.9))] }", b"[-128, 0, 255, 127]\n", None),
    "cast_float_u64_max_below": ("main proc() u64 { return u64(18446744073709549568.0) }", b"18446744073709549568\n", None),
    "cast_float_i64_min": ("main proc() i64 { return i64(-9223372036854775808.0) }", b"-9223372036854775808\n", None),
    "cast_unused_trap": ("bad proc() u8 { return u8(256) }\nmain proc() int { return 42 }", b"42\n", None),
    "cast_short_circuit": ("main proc() bool { return true or u8(256) == 0 }", b"true\n", None),
    "cast_bool_invalid": ("main proc() u8 { return u8(true) }", b"", b""),
    "cast_zero_arguments": ("main proc() u8 { return u8() }", b"", b"argument count"),
    "cast_extra_arguments": ("main proc() u8 { return u8(1, 2) }", b"", b"argument count"),
    "cast_type_as_value": ("main proc() int { u8\nreturn 42 }", b"", b"type name cannot"),
    "integer_no_implicit_narrowing": ("main proc() u8 { a u16 :: 1\nreturn a }", b"", b"return does not match"),
    "integer_no_implicit_signedness": ("main proc() i8 { a u8 :: 1\nreturn a }", b"", b"return does not match"),
    "integer_no_implicit_widening": ("main proc() u16 { a u8 :: 1\nreturn a }", b"", b"return does not match"),
    "integer_assignment_cast": (
        "main proc() u8 { a u8 := 1\nb u16 :: 254\na = u8(b + 1)\nreturn a }",
        b"255\n", None,
    ),
    "integer_function_value": (
        "identity proc(n i16) i16 { return n }\nmain proc() i16 { f :: identity\nreturn f(i16(-32768)) }",
        b"-32768\n", None,
    ),
    "integer_typed_float_cast": (
        "main proc() f64 { a u64 :: 18446744073709551615\nreturn f64(a) - 18446744073709551616.0 }",
        b"0\n", None,
    ),
    "integer_bad_index": ("main proc() i64 { a :: [42]\ni u8 :: 0\nreturn a[i] }", b"", b"array index must have type usize"),
    "integer_cast_index": ("main proc() i64 { a :: [42]\ni u8 :: 0\nreturn a[usize(i)] }", b"42\n", None),
    "integer_cast_unknown": ("main proc() u8 { return u8(missing) }", b"", b"unknown name"),
    "integer_cast_bad_unused": ("bad proc() u8 { return u8(true) }\nmain proc() int { return 42 }", b"42\n", b""),
})
for name, expression in {
    "narrow": "u8(256)",
    "signed_negative": "u8(-1)",
    "signed_high": "i8(128)",
    "unsigned_to_signed": "i64(u64(18446744073709551615))",
    "float_high": "u8(256.0)",
    "float_low": "i8(-129.0)",
    "float_unsigned_negative": "u8(-1.0)",
    "float_signed_limit": "i64(9223372036854775808.0)",
    "float_unsigned_limit": "u64(18446744073709551616.0)",
    "nan": "i32(0.0 / 0.0)",
    "infinity": "u32(1.0 / 0.0)",
}.items():
    CASES[f"cast_trap_{name}"] = (
        f"main proc() i64 {{ return i64({expression}) }}", b"", b"integer arithmetic overflow",
    )


def run(*args, env=None):
    return subprocess.run(args, cwd=ROOT, capture_output=True, timeout=30, env=env)


def outcome(result):
    return result.returncode, result.stdout, result.stderr


def check(source, executable, expected=None):
    interpreted = run(str(HONEY), "run", str(source))
    assert interpreted.returncode in (0, 1), outcome(interpreted)
    if expected is not None:
        stdout, error = expected
        assert interpreted.stdout == stdout, (source.name, outcome(interpreted), stdout)
        assert interpreted.returncode == (1 if error is not None else 0), outcome(interpreted)
        if error is not None:
            assert interpreted.stderr and error in interpreted.stderr, outcome(interpreted)
    for optimization in ("-O0", "-O2"):
        built = run(str(HONEY), "build", str(source), "-o", str(executable), optimization)
        assert built.returncode in (0, 1), (source.name, optimization, outcome(built))
        assert built.stdout == b"", outcome(built)
        assert executable.is_file(), (source.name, outcome(built))
        compiled = run(str(executable))
        assert outcome(compiled) == outcome(interpreted), (
            source.name, optimization, outcome(interpreted), outcome(compiled)
        )
        executable.unlink()


def check_packages(temporary, executable):
    project = temporary / "packages"

    def put(path, text):
        target = project / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        return target

    put("build.hon", "# project-root marker\n")
    app = project / "app"
    main = put("app/main.hon", 'import "../left"\nright :: import "@right"\nimport "../shared"\nmain proc() int { return helper(21) }\n')
    helper = put("app/zhelper.bro", "helper proc(n int) int { return n * 2 }\n")
    put("app/ignored.test.hon", "this is deliberately not valid source\n")
    (app / "directory.hon").mkdir()
    fifo = project / "pipe"
    os.mkfifo(fifo)
    (app / "pipe.hon").symlink_to(fifo)
    put("left/left.hon", 'import "../shared"\nhelper proc() int { return 1 }\nmain proc() int { return 99 }\n')
    put("right/right.hon", 'import "../shared-link"\nhelper proc() int { return 2 }\n')
    shared = put("shared/shared.hon", "helper proc() int { return 3 }\n")
    (project / "shared-link").symlink_to(project / "shared", target_is_directory=True)
    check(app, executable, (b"42\n", None))
    # File inputs retain their old single-file semantics, even inside a package.
    check(main, executable, (b"", b"unknown name"))
    emitted = run(str(HONEY), "emit-llvm", str(app))
    assert emitted.returncode == 0 and emitted.stderr == b"", outcome(emitted)
    shared.write_text("bad proc() int { return missing }\n")
    check(app, executable, (b"42\n", b"unknown name"))
    diagnosed = run(str(HONEY), "run", str(app))
    assert diagnosed.stderr.count(b"unknown name") == 1, outcome(diagnosed)
    assert str(shared.resolve()).encode() + b":1:" in diagnosed.stderr, outcome(diagnosed)
    # Dependency main functions cannot supply an entry for the root package.
    main.write_text('import "../left"\nentry proc() int { return 42 }\n')
    shared.write_text("helper proc() int { return 3 }\n")
    check(app, executable, (b"", b"program requires a main function"))
    # Source scopes merge within a package, never across dependency packages.
    main.write_text('import "../left"\nmain proc() int { return helper() }\n')
    helper.write_text("local_only proc() int { return 7 }\n")
    check(app, executable, (b"", b"unknown name"))
    main.write_text('import "../left"\nmain proc() int { return 42 }\n')
    shared.write_text('import "../left"\n')
    check(app, executable, (b"42\n", b"package import cycle"))
    shared.write_text('import "../shared-link"\n')
    check(app, executable, (b"42\n", b"package import cycle"))
    shared.write_text("helper proc() int { return 3 }\n")
    for imported, message in (
        ("/tmp", b"invalid import path"),
        ("@/tmp", b"invalid import path"),
        ("../absent", b"cannot load package directory"),
        ("../shared/shared.hon", b"cannot load package directory"),
        ("../empty", b"cannot load package directory"),
    ):
        (project / "empty").mkdir(exist_ok=True)
        main.write_text(f'import "{imported}"\nmain proc() int {{ return 42 }}\n')
        check(app, executable, (b"42\n", message))
    main.write_text('test import "../absent"\nmain proc() int { return 42 }\n')
    check(app, executable, (b"42\n", None))
    # Directory order is lexical, not filesystem insertion order; first declaration wins.
    put("duplicate/z.hon", "value :: 99\n")
    put("duplicate/a.hon", "value :: 41\n")
    put("duplicate/main.hon", "main proc() int { return value }\n")
    check(project / "duplicate", executable, (b"41\n", b"duplicate name"))
    # Runtime locations must survive source/frontend cleanup in generated executables.
    main.write_text("main proc() int { return fail() }\n")
    helper.write_text("\nfail proc() int { return 9223372036854775807 + 1 }\n")
    check(app, executable, (b"", b"integer arithmetic overflow"))
    trapped = run(str(HONEY), "run", str(app))
    assert str(helper.resolve()).encode() + b":2:" in trapped.stderr, outcome(trapped)
    empty = run(str(HONEY), "run", str(project / "empty"))
    assert empty.returncode == 2 and b"no production source files" in empty.stderr, outcome(empty)
    special = run(str(HONEY), "run", str(fifo))
    assert special.returncode == 2 and b"cannot open source file" in special.stderr, outcome(special)
    return 15


def check_namespace_packages(temporary, executable):
    project = temporary / "namespaces"

    def put(path, text):
        target = project / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        return target

    put("build.hon", "# project root\n")
    main = put("app/main.hon", """
import "../left"
right :: import "@right"
import "../shared-link"
main proc() int {
    functions :: [left.helper, right.helper]
    selected := left.helper
    selected = right.helper
    return sibling(18) + functions[0]() + selected() + shared.helper()
}
""")
    sibling = put("app/sibling.hon", """
right :: import "../left"
sibling proc(n int) int { return n * 2 + right.helper() - 1 }
""")
    put("left/main.hon", """
import "../shared"
helper proc() int { return shared.helper() - 2 }
identity proc(n int) int { return n }
main proc() int { return 99 }
""")
    put("right/main.hon", """
import "../shared-link"
helper proc() int { return shared.helper() - 1 }
""")
    dependency = put("shared/main.hon", """
helper proc() int { return value }
fail proc() int { return 9223372036854775807 + 1 }
""")
    put("shared/zfunctions.bro", """
value :: 3
identity proc(n f64) f64 { return n }
""")
    put("shared/ignored.test.bro", "this is deliberately not valid source\n")
    (project / "shared-link").symlink_to(project / "shared", target_is_directory=True)
    app = main.parent
    check(app, executable, (b"42\n", None))
    emitted = run(str(HONEY), "emit-llvm", str(app))
    assert emitted.returncode == 0 and emitted.stderr == b"", outcome(emitted)
    sibling.write_text("sibling proc(n int) int { return n * 2 }\n")
    cases = [
        ('import "../left"\nmain proc() int { return left.helper() }', b"1\n", None),
        ('lib :: import "../shared"\nmain proc() f64 { f :: lib.identity\nreturn f(1.5) }', b"1.5\n", None),
        ('lib :: import "../shared"\nmain proc() int { return lib.value }', b"3\n", None),
        ("""
import "../left"
import "../shared"
main proc() bool {
    return left.identity(41) == 41 and shared.identity(1.5) == 1.5
}
""", b"true\n", None),
        ('lib :: import "../shared"\nmain proc() int { return lib.absent() }', b"", b"unknown name"),
        ('lib :: import "../shared"\nmain proc() int { return helper() }', b"", b"unknown name"),
        ('import "../left"\nmain proc() int { return left.shared.helper() }', b"", b"unknown name"),
        ('import "../shared"\nmain proc() int { return shared.helper(1) }', b"", b"argument count"),
        ('import "../shared"\nmain proc() f64 { return shared.identity(true) }', b"", b"incompatible value type"),
        ('import "../shared"\nmain proc() int { shared\nreturn 42 }', b"", b"namespace cannot be used as a value"),
        ('import "../shared"\nmain proc() int { return shared.value.member }', b"", b""),
        ('lib :: import "../absent"\nmain proc() int { return lib.helper() }', b"", b"cannot load package directory"),
        ('lib :: import "../left"\nlib :: import "../right"\nmain proc() int { return lib.helper() }', b"1\n", b"duplicate name"),
        ('lib :: import "../shared"\nlib :: 42\nmain proc() int { return lib.helper() }', b"", b"duplicate name"),
        ('lib :: import "../shared"\nmain proc() int { lib :: 42\nreturn lib }', b"", b"duplicate name"),
        ('int :: import "../shared"\nmain proc() int { return 42 }', b"", b"duplicate name"),
        ('import "../shared"\nmain proc() int { shared.value = 42\nreturn 0 }', b"", b"mutable local binding"),
        ('import "../shared"\nmain proc() int { shared = 42\nreturn 0 }', b"", b"mutable local binding"),
        ('import "../shared"\nunused proc() int { return shared.absent() }\nmain proc() int { return 42 }', b"42\n", b"unknown name"),
        ('import "../shared"\nmain proc() int { return shared.fail() }', b"", b"integer arithmetic overflow"),
        ('import "../shared"\nmain proc() int { return shared. }', b"", b"unexpected token"),
    ]
    for text, stdout, error in cases:
        main.write_text(text + "\n")
        check(app, executable, (stdout, error))
    # Imports in a sibling file are invisible even though its declarations are shared.
    main.write_text("main proc() int { return local.helper() }\n")
    sibling.write_text('local :: import "../shared"\nsibling proc(n int) int { return n }\n')
    check(app, executable, (b"", b"unknown name"))
    # A missing package selection reuses the loader diagnostic instead of cascading.
    main.write_text('lib :: import "../absent"\nmain proc() int { return lib.helper() }\n')
    diagnosed = run(str(HONEY), "run", str(app))
    assert diagnosed.stderr.count(b"cannot load package directory") == 1, outcome(diagnosed)
    assert b"unknown name" not in diagnosed.stderr, outcome(diagnosed)
    # Dependency diagnostics and reached traps preserve their original source file.
    main.write_text('import "../shared"\nmain proc() int { return shared.helper() }\n')
    dependency.write_text("helper proc() int { return missing }\n")
    check(app, executable, (b"", b"unknown name"))
    diagnosed = run(str(HONEY), "run", str(app))
    assert str(dependency.resolve()).encode() + b":1:" in diagnosed.stderr, outcome(diagnosed)
    dependency.write_text("\nhelper proc() int { return 9223372036854775807 + 1 }\n")
    check(app, executable, (b"", b"integer arithmetic overflow"))
    trapped = run(str(HONEY), "run", str(app))
    assert str(dependency.resolve()).encode() + b":2:" in trapped.stderr, outcome(trapped)
    # File commands still load dependency siblings, but never their own siblings.
    single = put("single/main.bro", """
import "../shared"
main proc() f64 { return shared.identity(42) }
""")
    put("single/poison.hon", """
main proc() int { return missing }
""")
    check(single, executable, (b"42\n", None))
    emitted = run(str(HONEY), "emit-llvm", str(single))
    assert emitted.returncode == 0 and emitted.stderr == b"", outcome(emitted)
    check(single.parent, executable, (b"42\n", b"duplicate name"))
    return len(cases) + 6


def check_record_packages(temporary, executable):
    project = temporary / "records"
    app, library = project / "app", project / "library"
    app.mkdir(parents=True)
    library.mkdir()
    types = library / "types.hon"
    types.write_text("Point :: struct { x i16, y i16 = 2 }\n")
    (library / "functions.hon").write_text("identity proc(p Point) Point { return p }\n")
    (app / "main.hon").write_text(textwrap.dedent("""
        lib :: import "../library"
        main proc() i16 {
            p lib.Point := lib.Point { x = 40 }
            q :: lib.identity(p)
            p.x = 1
            return q.x + q.y
        }
    """).strip() + "\n")
    check(app, executable, (b"42\n", None))
    (library / "functions.hon").write_text(
        "set proc(p @mut Point) void { p.x = 40 }\n"
        "identity proc(p @mut Point) @mut Point { return p }\n"
    )
    (app / "main.hon").write_text(textwrap.dedent("""
        lib :: import "../library"
        main proc() i16 {
            p lib.Point := { x = 1 }
            pointer :: lib.identity(&p)
            lib.set(pointer)
            return pointer.x + p.y
        }
    """).strip() + "\n")
    check(app, executable, (b"42\n", None))
    types.write_text("Point :: struct { x i16, y i16 = 32767 + 1 }\n")
    check(app, executable, (b"", b"integer arithmetic overflow"))
    trapped = run(str(HONEY), "run", str(app))
    assert trapped.stderr.startswith(f"{types.resolve()}:1:".encode()), outcome(trapped)
    types.write_text(
        "Point :: struct { x i16, y i16 = 2 }\n"
        "Callback :: alias @proc(p @mut Point, amount i16) i16\n"
        "VTable :: struct { run Callback }\n"
    )
    (library / "functions.hon").write_text(
        "increment proc(p @mut Point, amount i16) i16 { p.x = p.x + amount\nreturn p.x + p.y }\n"
        "invoke proc(table VTable, values *mut Point) i16 { return table.run(&(values + 1)^, 40) }\n"
    )
    (app / "main.hon").write_text(textwrap.dedent("""
        lib :: import "../library"
        main proc() i16 {
            values [2]mut lib.Point := [lib.Point { x = 7 }, lib.Point { x = 0 }]
            callback lib.Callback :: lib.increment
            table :: lib.VTable { run = callback }
            result :: lib.invoke(table, (&values).ptr)
            if (values[0].x != 7 or values[1].x != 40) return -1
            return result
        }
    """).strip() + "\n")
    check(app, executable, (b"42\n", None))
    return 4


def check_data_model_packages(temporary, executable):
    project = temporary / "data-models"
    app, library = project / "app", project / "library"
    app.mkdir(parents=True)
    library.mkdir()
    (library / "types.hon").write_text(textwrap.dedent("""
        Kind :: enum { number, empty }
        Id :: distinct i16
        Choice :: union(Kind) { empty void, number Id }
        Count :: alias Id
        seed Id :: Id(41)
    """).strip() + "\n")
    (library / "functions.hon").write_text(textwrap.dedent("""
        identity proc(c Choice) Choice { return c }
        score proc(c Choice) Id {
            return match c { .number |n|: n + 1, .empty: Id(0) }
        }
        invoke :: alias score
    """).strip() + "\n")
    source = app / "main.hon"
    source.write_text(textwrap.dedent("""
        lib :: import "../library"
        Alias :: alias lib.Choice
        call :: alias lib.invoke
        shared :: alias lib.seed
        main proc() i16 {
            value Alias :: { number = shared }
            return i16(call(lib.identity(value)))
        }
    """).strip() + "\n")
    check(app, executable, (b"42\n", None))
    # Distinct and enum identity remain package/declaration specific.
    for declaration, body in (
        ("Id :: distinct i16", "return lib.seed"),
        ("Kind :: enum { number, empty }", "return lib.Kind.number"),
    ):
        result = declaration.split()[0]
        source.write_text(
            'lib :: import "../library"\n'
            + declaration + f"\nmain proc() {result} {{ {body} }}\n"
        )
        check(app, executable, (b"", b"return does not match"))
    return 3


def check_dependency_provenance(temporary, executable):
    project = temporary / "provenance"
    app = project / "app"
    dependency = project / "dependency"
    app.mkdir(parents=True)
    dependency.mkdir()
    main = app / "main.hon"
    source = dependency / "diagnostic.hon"
    # A transitive call must report the leaf's location, not either importing file.
    bridge = project / "bridge"
    bridge.mkdir()
    (bridge / "main.hon").write_text(textwrap.dedent("""
        import "../dependency"
        fail proc() int { return dependency.fail() }
    """).strip() + "\n")
    cases = [
        ("""
            fail proc() int {
                return `
            }
        """, 4, 12, True),
        ("""
            fail proc() int {
                return (1
            }
        """, 4, 14, True),
        ("""
            fail proc() int {
                text :: "é" return missing
            }
        """, 4, 25, True),
        ("""
            fail proc() int {
                if (1) return 0
                return 42
            }
        """, 4, 9, True),
        ("""
            fail proc() int {
                return 9223372036854775808
            }
        """, 4, 12, True),
        ("""
            fail proc() int {
                return 9223372036854775807 + 1
            }
        """, 4, 12, False),
        ("""
            fail proc() int {
                a :: [42]
                return a[1]
            }
        """, 5, 12, False),
        ("""
            fail proc() int {
                a :: [42]
                pointer :: &a
                return pointer[1]
            }
        """, 6, 12, False),
        ("""
            fail proc() int {
                before :: after
                after :: 42
                return before
            }
        """, 4, 15, False),
        ("""
            a int :: b
            b int :: a
            fail proc() int { return a }
        """, 4, 10, False),
    ]
    for text, line, column, static in cases:
        source.write_text("\n# utf8: é\n" + textwrap.dedent(text).strip() + "\n")
        location = f"{source.resolve()}:{line}:{column}: ".encode()
        for reached in (False, True):
            main.write_text(textwrap.dedent(f"""
                import "../bridge"
                main proc() int {{
                    if ({str(reached).lower()}) return bridge.fail()
                    return 42
                }}
            """).strip() + "\n")
            interpreted = run(str(HONEY), "run", str(app))
            assert interpreted.returncode == int(static or reached), outcome(interpreted)
            assert interpreted.stdout == (b"" if reached else b"42\n"), outcome(interpreted)
            if static or reached:
                label = b"runtime trap: " if reached else b"error: "
                assert interpreted.stderr.startswith(location + label), outcome(interpreted)
                assert len(interpreted.stderr.splitlines()) == 1, outcome(interpreted)
            else:
                assert interpreted.stderr == b"", outcome(interpreted)
            emitted = run(str(HONEY), "emit-llvm", str(app))
            assert emitted.returncode == int(static), outcome(emitted)
            expected_diagnostics = interpreted.stderr.replace(b"runtime trap: ", b"error: ") if static else b""
            assert emitted.stderr == expected_diagnostics, outcome(emitted)
            for optimization in ("-O0", "-O2"):
                built = run(str(HONEY), "build", str(app), "-o", str(executable), optimization)
                assert outcome(built) == (int(static), b"", expected_diagnostics), outcome(built)
                # Generated diagnostics must not reopen sources at runtime.
                hidden = project.with_name("hidden-provenance")
                project.rename(hidden)
                try:
                    compiled = run(str(executable))
                    assert outcome(compiled) == outcome(interpreted), (
                        optimization, outcome(interpreted), outcome(compiled)
                    )
                finally:
                    hidden.rename(project)
                    executable.unlink()
    return len(cases) * 2

def check_allocator_libc(temporary, executable):
    source = temporary / "allocator_libc.hon"
    source.write_text(textwrap.dedent("""
        malloc c_func(size c_ulong) ?*mut anyopaque
        realloc c_func(memory ?*mut anyopaque, size c_ulong) ?*mut anyopaque
        free c_func(memory ?*mut anyopaque) void
        posix_memalign c_func(output *mut ?*mut anyopaque, alignment c_ulong, size c_ulong) c_int
        VTable :: struct {
            alloc @proc(context ?@mut anyopaque, size usize, alignment usize) ?*mut u8
            release @proc(context ?@mut anyopaque, memory ?*mut u8) void
        }
        Allocator :: struct { context ?@mut anyopaque, vtable @VTable }
        allocate proc(_ ?@mut anyopaque, size usize, alignment usize) ?*mut u8 {
            if (alignment <= 16) return ptrcast!(malloc(c_ulong(size)))
            output [1]mut ?*mut anyopaque := [null]
            status :: posix_memalign((&output).ptr, c_ulong(alignment), c_ulong(size))
            if (status != 0) return null
            return ptrcast!(output[0])
        }
        release proc(_ ?@mut anyopaque, memory ?*mut u8) void { free(memory) }
        table VTable :: { alloc = allocate, release = release }
        main proc() i16 {
            allocator Allocator :: { context = null, vtable = &table }
            memory :: allocator.vtable.alloc(allocator.context, 4, 8)
            if memory |bytes| {
                values *mut i16 :: ptrcast!(bytes)
                values[0] = 40
                values[1] = 2
                resized ?*mut i16 :: ptrcast!(realloc(bytes, 8))
                if resized |items| {
                    aligned :: allocator.vtable.alloc(null, 8, 64)
                    if aligned |other| {
                        target *mut i16 :: ptrcast!(other)
                        memcopy!(target[..2], items[..2])
                        answer :: target[0] + target[1]
                        allocator.vtable.release(null, other)
                        free(items)
                        return answer
                    }
                    free(items)
                    return -1
                }
                free(bytes)
                return -2
            }
            return -3
        }
    """).strip() + "\n")
    interpreted = run(str(HONEY), "run", str(source))
    assert interpreted.returncode == 1 and interpreted.stdout == b"", outcome(interpreted)
    assert b"foreign function calls are unsupported by the interpreter" in interpreted.stderr, outcome(interpreted)
    assert len(interpreted.stderr.splitlines()) == 1, outcome(interpreted)
    for optimization in ("-O0", "-O2"):
        built = run(str(HONEY), "build", str(source), "-o", str(executable), optimization)
        assert outcome(built) == (0, b"", b""), outcome(built)
        assert outcome(run(str(executable))) == (0, b"42\n", b""), optimization
        executable.unlink()
    return 1



def main():
    with tempfile.TemporaryDirectory(prefix="honey native ' ; $") as directory:
        temporary = Path(directory)
        executable = temporary / "program ' ; $"
        count = 0
        for source in sorted((ROOT / "programs").glob("*.hon")):
            check(source, executable)
            count += 1
        for name, (text, stdout, error) in CASES.items():
            source = temporary / f"{name}.hon"
            source.write_text(textwrap.dedent(text).strip() + "\n")
            check(source, executable, (stdout, error))
            count += 1
        allocator_count = check_allocator_libc(temporary, executable)
        count += check_packages(temporary, executable)
        count += check_namespace_packages(temporary, executable)
        count += check_record_packages(temporary, executable)
        count += check_data_model_packages(temporary, executable)
        count += check_dependency_provenance(temporary, executable)
        source = ROOT / "programs/factorial.hon"
        emitted = run(str(HONEY), "emit-llvm", str(source))
        assert emitted.returncode == 0 and emitted.stderr == b"", outcome(emitted)
        ir = temporary / "factorial.ll"
        ir.write_bytes(emitted.stdout)
        assembled = run("clang", str(ir), "-o", str(executable))
        assert assembled.returncode == 0, outcome(assembled)
        assert outcome(run(str(executable))) == (0, b"120\n", b"")
        executable.unlink()
        missing_compiler = run(
            str(HONEY), "build", str(source), "-o", str(executable),
            env={**os.environ, "PATH": str(temporary)},
        )
        assert missing_compiler.returncode == 2, outcome(missing_compiler)
        assert b"cannot start native compiler" in missing_compiler.stderr, outcome(missing_compiler)
        assert not executable.exists()
        invalid = run(str(HONEY), "build", str(source), "-o", str(executable), "-O3")
        assert invalid.returncode == 2 and b"usage:" in invalid.stderr, outcome(invalid)
        print(f"{count} programs matched interpreter at -O0 and -O2; {allocator_count} native allocator program, LLVM output and CLI errors passed")


if __name__ == "__main__":
    main()
