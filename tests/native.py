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
    "struct_type_literal_fields_contextual_defaults_and_required_null": (
        """
        present proc() ??i16 { return 2 }
        Record :: struct_type!(
            .auto,
            {"number", "ready", "text", "maybe", "required"},
            {i16, bool, string, ?i16, ?i16},
            {null, true, "ok", present(), null},
        )
        main proc() i16 {
            value Record :: { number = 40, required = 2 }
            if (!value.ready or value.text.len != 2) return -1
            if (value.maybe? != 2) return -2
            return value.number + value.required?
        }
        """, b"42\n", None,
    ),
    "struct_type_computed_arrays_snapshots_and_nominal_cache_identity": (
        """
        names proc() [2]mut string {
            result [2]mut string := ["", ""]
            result[0] = "value"
            result[1] = "bias"
            return result
        }
        change proc(input [2]mut type, T type) [2]mut type {
            copied := input
            copied[0] = T
            return copied
        }
        Generated proc(T type, bias i16) type {
            fields := names()
            types [2]mut type := undefined
            types[0] = i8
            types[1] = i16
            changed := change(types, T)
            if (types[0] != i8 or changed[0] != T) return bool
            defaults [2]mut ?i16 := undefined
            defaults[0] = null
            defaults[1] = bias
            Result :: struct_type!(.auto, fields, changed, defaults)
            fields[0] = "replaced"
            types[1] = bool
            changed[0] = bool
            defaults[1] = 0
            return Result
        }
        Other proc() type {
            return struct_type!(.auto, ["value", "bias"], [i16, i16], [null, 2])
        }
        Selected :: alias Generated(i16, 2)
        main proc() i16 {
            if (Selected != Generated(i16, 2)) return -1
            if (Selected == Generated(i16, 3) or Selected == Other()) return -2
            value Selected :: { value = 40 }
            same Generated(i16, 2) :: value
            return same.value + same.bias
        }
        """, b"42\n", None,
    ),
    "struct_type_tuple_helper_nested_aggregate_defaults_and_copy_isolation": (
        """
        Child :: struct { values [2]mut i8, maybe ?i8 = null }
        names proc() struct { string, string } { return {"rows", "bias"} }
        Generated proc() type {
            return struct_type!(
                .auto,
                names(),
                [[2]mut Child, i8],
                [[{ values = [19, 0] }, { values = [0, 23], maybe = 2 }], 127],
            )
        }
        copy proc(value Generated()) Generated() { return value }
        main proc() i16 {
            first Generated() := {}
            saved :: copy(first)
            first.rows[0].values[0] = 0
            first.rows[1].maybe = null
            second Generated() :: {}
            if (saved.rows[1].maybe? != 2 or second.bias != 127) return -1
            return i16(saved.rows[0].values[0]) + i16(second.rows[1].values[1])
        }
        """, b"42\n", None,
    ),
    "struct_type_empty_auto_and_nested_c_layout": (
        """
        Layout :: enum { auto, c }
        Tag :: enum(u8) { ready = 7 }
        Empty :: struct_type!(.auto, [], [], [])
        Inner :: struct_type!(.c, ["value"], [i16], [null])
        Packet :: struct_type!(
            Layout.c,
            {"tag", "items", "pointer", "maybe"},
            {Tag, [2]Inner, @i16, ?@i16},
            {.ready, null, null, null},
        )
        identity proc(value Empty) Empty { return value }
        main proc() i16 {
            empty Empty :: identity({})
            if (sizeof!(Packet) != 24 or alignof!(Packet) != 8) return -3
            extra i16 :: 2
            packet Packet :: {
                items = [{ value = 19 }, { value = 21 }],
                pointer = &extra,
                maybe = null,
            }
            if (packet.tag != .ready) return -1
            if packet.maybe |_| return -2
            return packet.items[0].value + packet.items[1].value + packet.pointer^
        }
        """, b"42\n", None,
    ),
    "struct_type_required_optional_is_not_a_null_default": (
        """
        Record :: struct_type!(.auto, ["required"], [?i16], [null])
        main proc() i16 {
            value Record :: {}
            return 42
        }
        """, b"", b"missing a required field",
    ),
    "struct_type_default_preserves_narrow_field_arithmetic": (
        """
        Record :: struct_type!(.auto, ["value"], [i8], [127])
        main proc() i16 {
            value Record := {}
            value.value = value.value + 1
            return i16(value.value)
        }
        """, b"", b"integer arithmetic overflow",
    ),
    "struct_type_runtime_dependencies_are_not_captured": (
        """
        read proc(value i16) i16 {
            Record :: struct_type!(.auto, ["value"], [i16], [value])
            result Record :: {}
            return result.value
        }
        main proc() i16 { return read(42) }
        """, b"", b"compile-time value",
    ),
    "struct_type_incomplete_array_snapshot_stays_incomplete": (
        """
        Generated proc() type {
            types [2]mut type := undefined
            types[0] = i16
            saved :: types
            types[1] = i16
            return struct_type!(.auto, ["first", "second"], saved, [null, null])
        }
        main proc() i16 {
            value Generated() :: { first = 19, second = 23 }
            return value.first + value.second
        }
        """, b"", b"initialization",
    ),
    "type_records_local_schema_defaults_and_owned_copies": (
        """
        Storage proc() type {
            Config :: struct { child type = i8, count usize = 2, text string = "ok" }
            config Config := {}
            original :: config
            field!(config, "child") = i16
            config.count = config.count + 1
            config.text = "owned"
            if (original.child != i8 or original.count != 2 or original.text.len != 2) return bool
            return struct { values [config.count]config.child, bias original.child = 2 }
        }
        main proc() i16 {
            value Storage() :: { values = [19, 21, 0] }
            same Storage() :: value
            return same.values[0] + same.values[1] + i16(same.bias)
        }
        """, b"42\n", None,
    ),
    "type_records_nested_arrays_borrowed_iteration_and_capture_copies": (
        """
        Storage proc() type {
            Item :: struct { child type, values [1]mut usize }
            config struct { items [2]mut Item, bias struct { value i16 } } := {
                items = [{ child = i8, values = [1] }, { child = bool, values = [1] }],
                bias = { value = 0 },
            }
            original :: config
            for config.items |item, index| {
                config.items[1].child = i16
                config.items[index].values[0] = index + 1
                config.bias.value = config.bias.value + 1
                if (index == 0 and item.child != i8) return bool
                if (index == 1 and item.child != i16) return bool
                copied := item
                copied.child = u8
                if (item.child == u8) return bool
            }
            if (original.items[1].child != bool or original.items[1].values[0] != 1) return bool
            return struct { values [config.items[1].values[0]]config.items[1].child, bias i16 = config.bias.value }
        }
        main proc() i16 {
            value Storage() :: { values = [19, 21] }
            return value.values[0] + value.values[1] + value.bias
        }
        """, b"42\n", None,
    ),
    "type_records_inferred_tuples_nested_replacement_and_nominal_leaves": (
        """
        Storage proc() type {
            Child :: struct { value i16 }
            config := { nested = { child = Child, count = usize(1) }, tuple = {i8, usize(2)} }
            saved :: config
            config.nested = saved.nested
            config.tuple.0 = i16
            config.nested.count = config.tuple.1
            if (saved.tuple.0 != i8 or config.nested.child != Child) return bool
            Element :: field!(config.tuple, "0")
            return struct { first config.nested.child, second Child, bias Element = 2 }
        }
        main proc() i16 {
            value Storage() :: { first = { value = 19 }, second = { value = 21 } }
            copied := value.first
            copied = value.second
            return value.first.value + copied.value + value.bias
        }
        """, b"42\n", None,
    ),
    "type_record_procedure_owned_results_inputs_and_cache_identity": (
        """
        Config :: struct { child type, counts [2]mut usize, bias i16 = 2 }
        config proc() Config { return { child = i16, counts = [1, 2] } }
        change proc(input Config) Config {
            copied := input
            copied.child = u8
            copied.counts[1] = 3
            return copied
        }
        Storage proc(input Config) type {
            saved :: input
            changed :: change(input)
            if (input.child != i16 or saved.counts[1] != 2) return bool
            if (changed.child != u8 or changed.counts[1] != 3) return bool
            return struct { values [input.counts[1]]input.child, bias i16 = input.bias }
        }
        Selected :: alias Storage(config())
        main proc() i16 {
            value Selected :: { values = [19, 21] }
            same Storage({ child = i16, counts = [1, 2] }) :: value
            return same.values[0] + same.values[1] + same.bias
        }
        """, b"42\n", None,
    ),
    "type_record_procedure_nested_array_snapshots_and_nominal_leaves": (
        """
        Item :: struct { child type, counts [1]mut usize }
        Config :: struct { items [2]mut Item }
        config proc() Config {
            Child :: struct { value i16 }
            return { items = [{ child = Child, counts = [1] }, { child = Child, counts = [2] }] }
        }
        change proc(input Config) Config {
            copied := input
            copied.items[0].child = u8
            copied.items[1].counts[0] = 3
            return copied
        }
        Storage proc(input Config) type {
            changed :: change(input)
            if (input.items[0].child != input.items[1].child) return bool
            if (input.items[0].child == changed.items[0].child) return bool
            if (input.items[1].counts[0] != 2 or changed.items[1].counts[0] != 3) return bool
            return struct { first input.items[0].child, second input.items[1].child }
        }
        main proc() i16 {
            value Storage(config()) :: { first = { value = 19 }, second = { value = 23 } }
            same Storage(config()) :: value
            copied := same.first
            copied = same.second
            return same.first.value + copied.value
        }
        """, b"42\n", None,
    ),
    "type_record_procedure_tuple_results_and_erased_runtime_inputs": (
        """
        Pair :: struct { type, usize }
        pair proc() Pair { return {i16, 2} }
        copy proc(input Pair) Pair { return input }
        Element proc(input Pair) type { return field!(input, "0") }
        add proc($input Pair, value Element(input)) Element(input) {
            return value + i16(field!(input, "1"))
        }
        forward proc(value i16) i16 { return add(copy(pair()), value) }
        main proc() i16 {
            if (add({i16, usize(1)}, 40) != 41) return 0
            return forward(40)
        }
        """, b"42\n", None,
    ),
    "type_record_procedure_dependent_annotations_and_erased_cache_values": (
        """
        config proc($T type, value T) struct { child type, bias T, detail struct { bias T } } {
            return { child = T, bias = value, detail = { bias = value } }
        }
        Storage proc($T type, input struct { child type, bias T, detail struct { bias T } }) type {
            return struct { value input.child, bias T = input.detail.bias }
        }
        add proc($input struct { child type, bias i16, detail struct { bias i16 } }, value input.child) input.child {
            return value + input.detail.bias
        }
        main proc() i16 {
            value Storage(i16, config(i16, 2)) :: { value = 40 }
            same Storage(i16, { child = i16, bias = 2, detail = { bias = 2 } }) :: value
            if (add(config(i16, 1), same.value) != 41) return 0
            return add(config(i16, 2), same.value)
        }
        """, b"42\n", None,
    ),
    "type_record_procedure_result_rejects_unknown_field": (
        """
        config proc() struct { child type } { return { child = i16, missing = 1 } }
        main proc() config().child { return 42 }
        """, b"", b"no such field",
    ),
    "type_record_procedure_result_rejects_duplicate_field": (
        """
        config proc() struct { child type } { return { child = i16, child = i8 } }
        main proc() config().child { return 42 }
        """, b"", b"more than once",
    ),
    "type_record_procedure_argument_rejects_missing_field": (
        """
        Selected proc(input struct { child type, count usize }) type { return input.child }
        main proc() Selected({ child = i16 }) { return 42 }
        """, b"", b"missing a required field",
    ),
    "type_record_procedure_result_rejects_non_type_leaf": (
        """
        config proc() struct { child type } { return { child = 42 } }
        main proc() config().child { return 42 }
        """, b"", b"incompatible value type",
    ),
    "type_record_procedure_argument_rejects_exact_value_leaf_mismatch": (
        """
        Selected proc(input struct { child type, count usize }) type { return input.child }
        Bad proc() type {
            wrong u8 :: 2
            return Selected({ child = i16, count = wrong })
        }
        main proc() Bad() { return 42 }
        """, b"", b"incompatible value type",
    ),
    "type_record_procedure_erased_argument_rejects_exact_type_leaf_mismatch": (
        """
        add proc($input struct { child type }, value input.child) input.child { return value }
        main proc() i16 {
            wrong u8 :: 42
            return add({ child = i16 }, wrong)
        }
        """, b"", b"incompatible value type",
    ),
    "type_record_procedure_result_preserves_value_leaf_overflow": (
        """
        config proc() struct { child type, count i8 } { return { child = i16, count = 128 } }
        main proc() config().child { return 42 }
        """, b"", b"outside its runtime type",
    ),
    "type_record_procedure_rejects_runtime_dependency": (
        """
        config proc(wide bool) struct { child type } {
            if (wide) return { child = i16 }
            return { child = i8 }
        }
        read proc(wide bool) i16 {
            value config(wide).child :: 42
            return i16(value)
        }
        main proc() i16 { return read(true) }
        """, b"", b"generic argument must be a compile-time value",
    ),
    "type_record_procedure_nested_execution_shares_step_limit": (
        """
        config proc(count usize) struct { child type } {
            for (0..count) |_| { }
            return { child = i16 }
        }
        Selected proc() type {
            for (0..usize(100)) |index| { input :: config(index + 100) }
            return i16
        }
        main proc() Selected() { return 42 }
        """, b"", b"compile-time execution exceeds its recursion or step limit",
    ),
    "type_record_procedure_results_do_not_enable_runtime_storage": (
        """
        config proc() struct { child type, count i16 } { return { child = i16, count = 42 } }
        main proc() i16 {
            input :: config()
            return input.count
        }
        """, b"", b"",
    ),
    "type_records_reject_unknown_initializer_field": (
        """
        Bad proc() type {
            config struct { child type } :: { child = i16, missing = 1 }
            return config.child
        }
        main proc() Bad() { return 42 }
        """, b"", b"no such field",
    ),
    "type_records_reject_duplicate_initializer_field": (
        """
        Bad proc() type {
            config struct { child type } :: { child = i16, child = i8 }
            return config.child
        }
        main proc() Bad() { return 42 }
        """, b"", b"more than once",
    ),
    "type_records_reject_missing_initializer_field": (
        """
        Bad proc() type {
            config struct { child type, count usize } :: { child = i16 }
            return config.child
        }
        main proc() Bad() { return 42 }
        """, b"", b"missing a required field",
    ),
    "type_records_reject_non_type_leaf": (
        """
        Bad proc() type {
            config struct { child type } :: { child = 42 }
            return config.child
        }
        main proc() Bad() { return 42 }
        """, b"", b"incompatible value type",
    ),
    "type_records_preserve_value_leaf_overflow": (
        """
        Bad proc() type {
            config struct { child type, count i8 } := { child = i16, count = 127 }
            config.count = config.count + 1
            return config.child
        }
        main proc() Bad() { return 42 }
        """, b"", b"integer arithmetic overflow",
    ),
    "type_records_reject_immutable_capture_updates": (
        """
        Bad proc() type {
            for [{ child = i16 }] |item| { item.child = i8 }
            return i16
        }
        main proc() Bad() { return 42 }
        """, b"", b"writable array element",
    ),
    "type_records_reject_readonly_nested_array_updates": (
        """
        Bad proc() type {
            config struct { children [1]type } := { children = [i16] }
            config.children[0] = i8
            return i16
        }
        main proc() Bad() { return 42 }
        """, b"", b"writable array element",
    ),
    "type_records_reject_unknown_selected_field": (
        """
        Bad proc() type {
            config :: { child = i16 }
            return field!(config, "missing")
        }
        main proc() Bad() { return 42 }
        """, b"", b"no such field",
    ),
    "type_records_reject_unused_invalid_value_field": (
        """
        Bad proc() type {
            Config :: struct { child type, invalid void }
            return i16
        }
        main proc() Bad() { return 42 }
        """, b"42\n", b"void cannot be stored",
    ),
    "type_records_reject_unused_invalid_default": (
        """
        Bad proc() type {
            Config :: struct { child type, count i8 = 128 }
            return i16
        }
        main proc() Bad() { return 42 }
        """, b"", b"outside its runtime type",
    ),
    "type_records_do_not_enable_runtime_storage": (
        """
        main proc() i16 {
            config :: { child = i16, count = 42 }
            return config.count
        }
        """, b"", b"type name cannot be used as a value",
    ),
    "field_reflection_runtime_places_callbacks_and_single_evaluation": (
        """
        Point :: struct { value i16, callback @proc(value i16) i16 }
        add proc(value i16) i16 { return value + 2 }
        place proc(point @mut Point, calls @mut i16) @mut Point {
            calls^ = calls^ + 1
            return point
        }
        name proc() string { return "val\\x75e" }
        main proc() [3]i16 {
            point Point := { value = 19, callback = add }
            calls i16 := 0
            field!(place(&point, &calls), name()) = 40
            pointer @mut i16 :: &field!(point, "value")
            pointer^ = pointer^ + 1
            return [field!(point, "callback")(field!(point, "value")), calls, point.value]
        }
        """, b"[43, 1, 41]\n", None,
    ),
    "field_reflection_tuple_enum_union_and_sequence_members": (
        """
        A :: enum { first, second }
        B :: enum { third }
        E :: alias A | B
        Choice :: union(enum) { payload i16, empty void }
        read proc($name string, value struct { count i16 }) i16 {
            return field!(value, name)
        }
        main proc() [4]i16 {
            tuple :: {i16(19), i16(21)}
            choice Choice :: { payload = 2 }
            values [2]i16 :: [19, 21]
            member E :: field!(E, "third")
            if (member != E.third) return [0, 0, 0, 0]
            return [
                field!(tuple, "0") + field!(tuple, "1") + field!(choice, "payload"),
                i16(field!(values, "len")),
                field!(&values, "ptr")[1],
                read("count", { count = 42 }),
            ]
        }
        """, b"[42, 2, 21, 42]\n", None,
    ),
    "field_reflection_type_control_and_constant_materialization": (
        """
        Config :: struct { count usize, value i16 }
        config proc() Config { return { count = 2, value = 21 } }
        Storage proc(name string) type {
            settings :: config()
            types :: [i16, bool]
            return struct {
                values [field!(settings, name)]types[0]
                bias i16 = i16(field!(types, "len"))
            }
        }
        main proc() i16 {
            value Storage("count") :: { values = [19, 21] }
            constant :: $(field!(config(), "value"))
            return value.values[0] + constant + value.bias
        }
        """, b"42\n", None,
    ),
    "field_reflection_type_control_updates_names_and_nested_places": (
        """
        Selected proc() type {
            config struct { left [1]mut usize, right [1]mut usize } := { left = [0], right = [0] }
            original :: config
            for ["left", "right"] |name| { field!(config, name)[0] = 2 }
            if (original.left[0] != 0 or original.right[0] != 0) return bool
            return [config.left[0] + config.right[0]]i16
        }
        main proc() i16 {
            values Selected() :: [10, 10, 10, 12]
            return values[0] + values[1] + values[2] + values[3]
        }
        """, b"42\n", None,
    ),
    "field_reflection_preserves_readonly_pointer_assignment": (
        """
        Point :: struct { value i16 }
        update proc(point @Point) void { field!(point, "value") = 0 }
        main proc() i16 {
            point Point :: { value = 42 }
            update(&point)
            return point.value
        }
        """, b"", b"mutable pointee",
    ),
    "field_reflection_rejects_nonstring_names": (
        """
        main proc() i16 { return field!({ value = i16(42) }, 0) }
        """, b"", b"compile-time value",
    ),
    "field_reflection_rejects_unknown_enum_members": (
        """
        E :: enum { first }
        main proc() E { return field!(E, "missing") }
        """, b"", b"no such field",
    ),
    "field_reflection_rejects_overflowing_tuple_ordinals": (
        """
        main proc() i16 { return field!({i16(42)}, "18446744073709551616") }
        """, b"", b"no such field",
    ),
    "field_reflection_rejects_runtime_names": (
        """
        read proc(name string, value struct { count i16 }) i16 {
            return field!(value, name)
        }
        main proc() i16 { return read("count", { count = 42 }) }
        """, b"", b"compile-time value",
    ),
    "field_reflection_rejects_unknown_fields": (
        """
        main proc() i16 { return field!({ count = i16(42) }, "missing") }
        """, b"", b"no such field",
    ),
    "field_reflection_rejects_noncanonical_tuple_names": (
        """
        main proc() i16 { return field!({i16(42)}, "00") }
        """, b"", b"no such field",
    ),
    "field_reflection_rejects_wrong_arity": (
        """
        main proc() i16 { return field!(42) }
        """, b"", b"argument count",
    ),
    "field_reflection_preserves_union_active_field_checks": (
        """
        Choice :: union(enum) { value i16, empty void }
        main proc() i16 {
            choice Choice :: .empty
            return field!(choice, "value")
        }
        """, b"", b"not active",
    ),
    "collection_iteration_borrows_array_and_reads_each_element": (
        """
        main proc() [2]i16 {
            values [3]mut i16 := [19, 1, 100]
            total i16 := 0
            for values |value, index| {
                if (index == 0) values[1] = 22
                if (index == 2) break
                total = total + value + i16(index)
                continue
            }
            return [total, values[1]]
        }
        """, b"[42, 22]\n", None,
    ),
    "collection_iteration_pointer_captures_slices_and_array_pointers": (
        """
        main proc() [3]i16 {
            values [2]mut i16 := [19, 21]
            for (&values) |@value, index| { value^ = value^ + i16(index) }
            view []mut i16 :: values[..]
            for view |@value| { value^ = value^ + 1 }
            total i16 := 0
            for view |value| { total = total + value }
            return [total - 1, values[0], values[1]]
        }
        """, b"[42, 20, 23]\n", None,
    ),
    "collection_iteration_evaluates_entry_once_and_retains_slice_bound": (
        """
        data proc(calls @mut i16) [2]i16 {
            calls^ = calls^ + 1
            return [19, 22]
        }
        main proc() [3]i16 {
            calls i16 := 0
            total i16 := 0
            for data(&calls) |value, index| { total = total + value + i16(index) }
            values [2]mut i16 := [20, 22]
            view []mut i16 := values[..]
            sum i16 := 0
            for view |value| {
                view = values[0..0]
                sum = sum + value
            }
            return [total, calls, sum]
        }
        """, b"[42, 1, 42]\n", None,
    ),
    "collection_iteration_aggregate_copies_nested_and_zero_cell_shapes": (
        """
        Empty :: struct {}
        Item :: struct { values [2]mut i16 }
        main proc() [3]i16 {
            items [2]mut Item := [{ values = [19, 21] }, { values = [0, 0] }]
            total i16 := 0
            for items |item, index| {
                copied := item
                copied.values[0] = copied.values[0] + 1
                for copied.values |value| { total = total + value }
                if (index == 0) items[1].values[1] = 0
            }
            empty [0]i16 :: []
            for empty |_| { return [-1, -1, -1] }
            cells [2]Empty :: [{}, {}]
            count i16 := 0
            for cells |cell, index| {
                saved Empty :: cell
                count = count + i16(index) + 1
            }
            return [total, items[0].values[0], count]
        }
        """, b"[42, 19, 3]\n", None,
    ),
    "collection_iteration_strings_and_cleanup_on_transfers": (
        """
        main proc() [2]i16 {
            values [3]i16 :: [20, 22, 99]
            total i16 := 0
            cleanups i16 := 0
            for values |value, index| {
                defer cleanups = cleanups + 1
                if (index == 2) break
                total = total + value
                continue
            }
            for "a\\0b" |byte, index| {
                if (index == 1 and byte != 0) return [-1, -1]
                if (index == 2 and byte != u8(98)) return [-2, -2]
            }
            return [total, cleanups]
        }
        """, b"[42, 3]\n", None,
    ),
    "collection_iteration_ordinary_constant_procedure": (
        """
        compute proc() i16 {
            values [2]mut i16 := [19, 21]
            view []mut i16 :: values[..]
            for view |@value| { value^ = value^ + 1 }
            total i16 := 0
            for values |value| { total = total + value }
            return total
        }
        main proc() i16 { return $(compute()) }
        """, b"42\n", None,
    ),
    "type_execution_collection_iteration_observes_updates_and_freezes_places": (
        """
        Storage proc() type {
            rows [2]mut [2]mut usize := [[1, 1], [9, 9]]
            selected usize := 0
            length usize := 0
            for rows[selected] |value, index| {
                selected = 1
                (rows[0])[1] = 2
                length = length + value
                if (index == 0) continue
                break
            }
            Result := i8
            for "a\\0b" |byte, index| {
                if (byte == 0 and index == 1) Result = i16
            }
            return struct { values [length]Result }
        }
        main proc() i16 {
            value Storage() :: { values = [19, 21, 2] }
            same Storage() :: value
            return same.values[0] + same.values[1] + same.values[2]
        }
        """, b"42\n", None,
    ),
    "type_execution_collection_nested_aggregate_captures_and_empty_body": (
        """
        Storage proc() type {
            items [2]struct { values [2]usize, text string } :: [
                { values = [1, 0], text = "a" }, { values = [0, 1], text = "b" },
            ]
            length usize := 0
            for items |item| {
                for item.values |value| { length = length + value }
            }
            empty [0]i16 :: []
            for empty |_| { return i8(128) }
            for items |@_, index| { if (index == 1) break }
            return [length]i16
        }
        main proc() i16 {
            values Storage() :: [20, 22]
            return values[0] + values[1]
        }
        """, b"42\n", None,
    ),
    "collection_iteration_rejects_scalar_iterable": (
        "main proc() i16 { for 1.5 |_| { return 0 }\nreturn 42 }",
        b"", b"for-loop iteration requires",
    ),
    "collection_iteration_rejects_capture_rebinding": (
        "main proc() i16 { values [1]i16 :: [1]\nfor values |value| { value = 42 }\nreturn 0 }",
        b"", b"assignment requires a mutable local binding",
    ),
    "collection_iteration_rejects_index_rebinding": (
        "main proc() usize { values [1]i16 :: [1]\nfor values |_, index| { index = 42 }\nreturn 0 }",
        b"", b"assignment requires a mutable local binding",
    ),
    "collection_iteration_rejects_readonly_array_pointer_mutation": (
        "main proc() i16 { values [1]mut i16 :: [1]\nfor values |@value| { value^ = 42 }\nreturn 0 }",
        b"", b"mutable pointee",
    ),
    "collection_iteration_rejects_readonly_slice_pointer_mutation": (
        "main proc() i16 { values [1]mut i16 := [1]\nview []i16 :: values[..]\nfor view |@value| { value^ = 42 }\nreturn 0 }",
        b"", b"mutable pointee",
    ),
    "type_execution_collection_iteration_preserves_element_type": (
        """
        Bad proc() type {
            values [1]i8 :: [127]
            for values |value| { next :: value + 1 }
            return i16
        }
        main proc() Bad() { return 0 }
        """, b"", b"integer arithmetic overflow",
    ),
    "type_execution_collection_iteration_preserves_step_bound": (
        """
        Bad proc() type {
            cells [1]i16 :: [0]
            for cells |_| { while (true) {} }
            return i16
        }
        main proc() Bad() { return 0 }
        """, b"", b"compile-time execution",
    ),
    "type_execution_aggregate_updates_preserve_copies_and_generate_defaults": (
        """
        Config :: struct { values [2]mut i16, count usize, text string }
        Storage proc() type {
            config Config := { values = [17, 19], count = 0, text = "old" }
            original :: config
            for (usize(0)..2) |index| {
                config.values[index] = config.values[index] + 1
                config.count = config.count + 1
            }
            config.text = "owned"
            config.values[config.count - 1] = match true {
                true: config.values[0] + 4,
                false: i8(128),
            }
            return struct {
                values [config.count]mut i16 = config.values
                bias i16 = original.values[0] - 17 + i16(config.text.len) - 3
            }
        }
        main proc() i16 {
            value Storage() :: {}
            same Storage() :: value
            return same.values[0] + same.values[1] + same.bias
        }
        """, b"42\n", None,
    ),
    "type_execution_nested_array_record_updates_and_loop_transfers": (
        """
        Item :: struct { length usize, value i16 }
        Storage proc() type {
            items [2]mut Item := [{ length = 0, value = 0 }, { length = 0, value = 0 }]
            index usize := 0
            while (index < 3) {
                if (index == 2) break
                items[index].length = index + 1
                items[index].value = i16(index) + 20
                index = index + 1
                continue
            }
            return struct {
                values [items[1].length]i16
                bias i16 = items[0].value + items[1].value + i16(items[0].length)
            }
        }
        main proc() i16 {
            value Storage() :: { values = [0, 0] }
            return value.bias
        }
        """, b"42\n", None,
    ),
    "type_execution_field_update_rejects_constant_root": (
        """
        Config :: struct { count usize }
        Bad proc() type {
            config Config :: { count = 1 }
            config.count = 2
            return [config.count]i16
        }
        main proc() Bad() { return [0, 0] }
        """, b"", b"assignment requires a mutable local binding",
    ),
    "type_execution_index_update_rejects_readonly_array": (
        """
        Bad proc() type {
            counts [1]usize := [1]
            counts[0] = 2
            return [counts[0]]i16
        }
        main proc() Bad() { return [0, 0] }
        """, b"", b"indexed assignment requires a writable array element",
    ),
    "type_execution_index_update_checks_bounds": (
        """
        Bad proc() type {
            counts [1]mut usize := [1]
            counts[1] = 2
            return [counts[0]]i16
        }
        main proc() Bad() { return [0] }
        """, b"", b"index out of bounds",
    ),
    "type_execution_index_update_checks_index_type": (
        """
        Bad proc() type {
            counts [1]mut usize := [1]
            index u8 :: 0
            counts[index] = 2
            return [counts[0]]i16
        }
        main proc() Bad() { return [0, 0] }
        """, b"", b"array index must have type usize",
    ),
    "type_execution_field_update_checks_exact_value_type": (
        """
        Config :: struct { count usize }
        Bad proc() type {
            config Config := { count = 1 }
            value u8 :: 2
            config.count = value
            return [config.count]i16
        }
        main proc() Bad() { return [0, 0] }
        """, b"", b"incompatible value type",
    ),
    "type_execution_index_update_preserves_narrow_overflow": (
        """
        Bad proc() type {
            values [1]mut i8 := [127]
            values[0] = values[0] + 1
            return [usize(values[0])]i16
        }
        main proc() Bad() { return [0] }
        """, b"", b"integer arithmetic overflow",
    ),
    "type_execution_field_update_rejects_payload_capture_mutation": (
        """
        Config :: struct { count usize }
        Bad proc() type {
            input ?Config :: { count = 1 }
            if input |config| {
                config.count = 2
                return [config.count]i16
            }
            return i16
        }
        main proc() Bad() { return [0, 0] }
        """, b"", b"assignment requires a mutable local binding",
    ),
    "type_execution_index_update_rejects_constant_array_root": (
        """
        Bad proc() type {
            counts [1]mut usize :: [1]
            counts[0] = 2
            return [counts[0]]i16
        }
        main proc() Bad() { return [0, 0] }
        """, b"", b"indexed assignment requires a writable array element",
    ),
    "type_execution_field_update_checks_member": (
        """
        Config :: struct { count usize }
        Bad proc() type {
            config Config := { count = 1 }
            config.missing = 2
            return [config.count]i16
        }
        main proc() Bad() { return [0] }
        """, b"", b"record has no such field",
    ),
    "type_execution_optional_if_selects_storage_and_cache_identity": (
        """
        Count :: alias ?usize
        Storage proc(count Count) type {
            if count |length| { return struct { values [length]i16 } }
            else { return struct { value i16 = 2 } }
        }
        main proc() i16 {
            value Storage(2) :: { values = [19, 21] }
            same Storage(usize(2)) :: value
            empty Storage(null) :: {}
            return same.values[0] + same.values[1] + empty.value
        }
        """, b"42\n", None,
    ),
    "type_execution_optional_payload_owned_aggregate_and_nested_capture": (
        """
        Detail :: struct { values [2]i16, text string, count ?usize }
        detail proc() ?Detail { return { values = [19, 21], text = "ok", count = 2 } }
        Storage proc() type {
            if detail() |payload| {
                if payload.count |count| {
                    return struct {
                        values [count]i16
                        bias i16 = payload.values[0] + payload.values[1] + i16(payload.text.len)
                    }
                }
            }
            return bool
        }
        main proc() i16 {
            value Storage() :: { values = [0, 0] }
            return value.bias
        }
        """, b"42\n", None,
    ),
    "type_execution_optional_while_refreshes_capture_and_preserves_transfers": (
        """
        next proc(count usize) ?usize {
            if (count < 4) return count
            return null
        }
        Storage proc() type {
            count usize := 0
            Result := i16
            while next(count) |length| {
                count = count + 1
                if (length == 0) continue
                if (length == 3) break
                Result = [length]Result
            }
            pending ?usize := 2
            while pending |length| {
                pending = null
                if (length != 2) return bool
                count = count + length
            }
            return struct { values Result, bias i16 = i16(count) }
        }
        main proc() i16 {
            value Storage() :: { values = [[17], [19]] }
            return value.values[0][0] + value.values[1][0] + value.bias
        }
        """, b"42\n", None,
    ),
    "type_execution_optional_absence_skips_invalid_bodies": (
        """
        absent proc() ?i16 { return null }
        null_pointer proc() ?@i16 { return null }
        null_callback proc() ?@proc() i16 { return null }
        Storage proc() type {
            pointer ?@i16 :: null
            while pointer |_| { return i8(128) }
            if absent() |_| { return i8(128) }
            else { return i16 }
        }
        main proc() i16 {
            absent_pointer :: $(null_pointer())
            absent_callback :: $(null_callback())
            if absent_pointer |_| { return -1 }
            if absent_callback |_| { return -2 }
            value Storage() :: 42
            return value
        }
        """, b"42\n", None,
    ),
    "type_execution_optional_capture_retains_exact_payload_type": (
        """
        narrow proc() ?i8 { return 127 }
        Bad proc() type {
            if narrow() |value| {
                copy :: value
                overflow :: copy + 1
                return [usize(overflow)]i16
            }
            return i16
        }
        main proc() i16 { value Bad() :: 42
            return value }
        """, b"", b"integer arithmetic overflow",
    ),
    "type_execution_optional_capture_is_immutable": (
        """
        Bad proc(value ?usize) type {
            if value |length| { length = 2 }
            return i16
        }
        main proc() i16 { value Bad(1) :: 42
            return value }
        """, b"", b"assignment requires a mutable local binding",
    ),
    "type_execution_optional_capture_requires_optional_guard": (
        """
        Bad proc() type {
            if true |_| { return i16 }
            return bool
        }
        main proc() i16 { value Bad() :: 42
            return value }
        """, b"", b"condition must have type bool",
    ),
    "type_execution_optional_capture_preserves_resolution_errors": (
        """
        Bad proc(value ?usize) type {
            if value |value| { return i16 }
            return bool
        }
        main proc() i16 { value Bad(null) :: 42
            return value }
        """, b"", b"duplicate name",
    ),
    "type_execution_optional_capture_loop_is_bounded": (
        """
        Bad proc(value ?usize) type {
            while value |_| {}
            return i16
        }
        main proc() i16 { value Bad(1) :: 42
            return value }
        """, b"", b"compile-time execution exceeds its recursion or step limit",
    ),
    "generic_match_scalar_enum_and_range_selection": (
        """
        Mode :: enum { pair, other }
        answer proc($mode Mode, $count u8, value i16) i16 {
            match true {
                true: {
                    selected :: count
                    match selected {
                        0..2: return value.missing,
                        2..=3: {
                            return match mode {
                                .pair: value + 2,
                                .other: value.missing,
                            }
                        }
                        else: return false,
                    }
                }
                false: return value.missing,
            }
        }
        main proc() i16 { return answer(.pair, 2, 40) }
        """, b"42\n", None,
    ),
    "generic_match_expression_selects_each_instance": (
        """
        read proc($take bool, value i16) i16 {
            return match take {
                true: match take { true: value, false: value.missing },
                false: value + 2,
            }
        }
        main proc() i16 { return read(true, 20) + read(false, 20) }
        """, b"42\n", None,
    ),
    "generic_match_tagged_capture_exact_scalar_type": (
        """
        Choice :: union(enum) { small i8, empty void }
        exact proc(value i8) i16 { return i16(value) }
        read proc($choice Choice, value i16) i16 {
            return match choice {
                .small |small|: exact(small) + value,
                .empty: value.missing,
            }
        }
        main proc() i16 { return read(.small{2}, 40) }
        """, b"42\n", None,
    ),
    "generic_match_tagged_capture_nested_owned_values": (
        """
        Inner :: union(enum) { small i8, empty void }
        Detail :: struct { inner Inner, values [2]i16, text string }
        Choice :: union(enum) { detail Detail, unused void }
        exact proc(value i8) i16 { return i16(value) }
        read proc($choice Choice, value i16) i16 {
            match choice {
                .detail |detail|: {
                    copied Detail :: detail
                    if (copied.text.len != 5 or copied.text[0] != 111) return -1
                    return match detail.inner {
                        .small |small|: exact(small) + detail.values[0] + detail.values[1] + value,
                        .empty: value.missing,
                    }
                }
                .unused: return value.missing,
            }
        }
        main proc() i16 {
            return read(.detail{ inner = .small{2}, values = [19, 20], text = "owned" }, 1)
        }
        """, b"42\n", None,
    ),
    "generic_match_selected_return_runs_cleanup": (
        """
        read proc($take bool, total @mut i16, value i16) i16 {
            defer total^ = total^ + 1
            match take {
                true: {
                    owned :: ["owned", "copy"]
                    defer total^ = total^ + i16(owned[1].len)
                    return value
                }
                false: return value.missing,
            }
        }
        main proc() i16 {
            total i16 := 0
            value :: read(true, &total, 37)
            return value + total
        }
        """, b"42\n", None,
    ),
    "generic_match_selected_loop_transfers_run_cleanup": (
        """
        read proc($take bool) i16 {
            total i16 := 0
            for (0..3) |index| {
                match take {
                    true: {
                        owned :: ["owned", "copy"]
                        defer total = total + i16(owned[1].len)
                        if (index == 0) continue
                        break
                    }
                    false: return total.missing,
                }
                total = -100
            }
            return total + 34
        }
        main proc() i16 { return read(true) }
        """, b"42\n", None,
    ),
    "explicit_comptime_match_ordinary_call_without_templates": (
        """
        selected proc() bool { return true }
        main proc() i16 {
            bias :: match $(selected()) { true: 2, false: false.missing }
            match $(selected()) {
                true: return 40 + bias,
                false: return false,
            }
        }
        """, b"42\n", None,
    ),
    "generic_match_ordinary_call_still_checks_both_arms": (
        """
        selected proc() bool { return true }
        read proc($unused bool, value i16) i16 {
            match selected() { true: return value, false: return false }
        }
        main proc() i16 { return read(true, 42) }
        """, b"42\n", b"return does not match",
    ),
    "ordinary_literal_match_still_checks_both_arms": (
        """
        main proc() i16 {
            match true { true: return 42, false: return false }
        }
        """, b"42\n", b"return does not match",
    ),
    "generic_match_record_default_call_stays_runtime": (
        """
        default_value proc() u8 { return 2 }
        Choice :: struct { count u8, bias u8 = default_value() }
        read proc($unused bool, value i16) i16 {
            choice Choice :: { count = 2 }
            match choice.bias { 2: return value, else: return false }
        }
        main proc() i16 { return read(true, 42) }
        """, b"42\n", b"return does not match",
    ),
    "generic_match_runtime_subject_still_checks_both_arms": (
        """
        read proc($unused bool, take bool, value i16) i16 {
            match take { true: return value, false: return false }
        }
        main proc() i16 { return read(true, true, 42) }
        """, b"42\n", b"return does not match",
    ),
    "generic_match_mutable_local_still_checks_both_arms": (
        """
        read proc($take bool, value i16) i16 {
            selected := take
            match selected { true: return value, false: return false }
        }
        main proc() i16 { return read(true, 42) }
        """, b"42\n", b"return does not match",
    ),
    "generic_match_runtime_subject_runs_once": (
        """
        selected proc(count @mut i16) bool {
            count^ = count^ + 1
            return true
        }
        read proc($unused bool, count @mut i16) i16 {
            return match selected(count) { true: 41, false: 0 }
        }
        main proc() i16 {
            count i16 := 0
            value :: read(true, &count)
            return value + count
        }
        """, b"42\n", None,
    ),
    "generic_match_checks_untaken_overlap": (
        """
        read proc($value u8) i16 {
            return match value { 0: 42, 1..=3: 0, 3..5: 0, else: 0 }
        }
        main proc() i16 { return read(0) }
        """, b"", b"duplicate or overlapping match pattern",
    ),
    "generic_match_checks_exhaustiveness": (
        """
        read proc($value bool) i16 { return match value { true: 42 } }
        main proc() i16 { return read(true) }
        """, b"", b"match is not exhaustive",
    ),
    "generic_match_checks_untaken_capture_types": (
        """
        Choice :: union(enum) { count usize, small i8, empty void }
        read proc($choice Choice) i16 {
            return match choice { .count, .small |value|: i16(value), .empty: 42 }
        }
        main proc() i16 { return read(.empty) }
        """, b"", b"payload capture requires compatible nonvoid tagged union variants",
    ),
    "generic_match_preserves_untaken_resolution_errors": (
        """
        read proc($value bool) i16 {
            return match value { true: 42, false: missing }
        }
        main proc() i16 { return read(true) }
        """, b"42\n", b"unknown name",
    ),
    "explicit_comptime_match_rejects_runtime_subject": (
        """
        read proc(take bool) i16 {
            return match $(take) { true: 42, false: 0 }
        }
        main proc() i16 { return read(true) }
        """, b"", b"generic argument must be a compile-time value",
    ),
    "type_match_tagged_capture_preserves_resolution_errors": (
        """
        data proc() [2]i16 { return [40, 2] }
        Choice :: union(enum) { payload struct { values [2]i16 }, empty void }
        Storage proc(input Choice) type {
            return match input {
                .payload |data|: struct { bias i16 = data.values[1] },
                .empty: bool,
            }
        }
        main proc() Storage(.payload{ values = [40, 2] }) { return {} }
        """, b"", b"duplicate name",
    ),
    "type_match_tagged_capture_statements_and_cache_identity": (
        """
        Tag :: enum(i16) { empty = -7, second = 3, count = 90 }
        Choice :: union(Tag) { count usize, second usize, empty void }
        choice proc() Choice { return .second{2} }
        Storage proc(input Choice) type {
            Result := i8
            match input {
                .count, .second |length|: {
                    Result = struct { values [length]i16 }
                }
                .empty: return struct { values [0]i16 },
            }
            return Result
        }
        main proc() i16 {
            value Storage(choice()) :: { values = [40, 2] }
            cached Storage(.second{2}) :: value
            empty Storage(.empty) :: { values = [] }
            return cached.values[0] + cached.values[1] + i16(empty.values.len)
        }
        """, b"42\n", None,
    ),
    "type_match_tagged_capture_nested_expressions_and_storage": (
        """
        Inner :: union(enum) { count usize, empty void }
        Detail :: struct { inner Inner, wide bool, bias i16, text string }
        Choice :: union(enum) { detail Detail, unused i8 }
        Box proc(T type) type { return struct { value T } }
        Storage proc(input Choice) type {
            Element :: match input {
                .detail |detail|: match detail.wide { true: i16, false: i8 },
                .unused |small|: [small]bool,
            }
            return struct {
                values [match input {
                    .detail |detail|: match detail.inner { .count |n|: n, .empty: usize(0) },
                    .unused |small|: usize(small),
                }]Element
                boxed Box(match input { .detail |detail|: Element, .unused: bool })
                bias i16 = match input { .detail |detail|: detail.bias, .unused: i8(128) }
                label string = match input { .detail |detail|: detail.text, .unused: "" }
            }
        }
        main proc() i16 {
            value Storage(.detail{ inner = .count{2}, wide = true, bias = 2, text = "owned" }) :: {
                values = [19, 21], boxed = { value = 0 },
            }
            if (value.label.len != 5 or value.label[0] != 111) return -1
            return value.values[0] + value.values[1] + value.boxed.value + value.bias
        }
        """, b"42\n", None,
    ),
    "type_match_tagged_capture_loop_snapshots_and_transfers": (
        """
        Choice :: union(enum) { count usize, empty void }
        Nest proc() type {
            Result := i16
            current Choice := .count{0}
            for (0..usize(4)) |index| {
                current = .count{index}
                match current {
                    .count |length|: {
                        if (length == 0) continue
                        if (length == 3) break
                        Result = [length]Result
                    }
                    .empty: return bool,
                }
            }
            return Result
        }
        main proc() i16 {
            value Nest() :: [[40], [2]]
            return value[0][0] + value[1][0]
        }
        """, b"42\n", None,
    ),
    "type_match_tagged_capture_checks_unselected_capture_types": (
        """
        Choice :: union(enum) { count usize, small i8, empty void }
        Bad proc(input Choice) type {
            return match input { .count, .small |value|: [value]i16, .empty: bool }
        }
        main proc() Bad(.empty) { return true }
        """, b"", b"payload capture requires compatible nonvoid tagged union variants",
    ),
    "type_match_tagged_capture_preserves_exact_payload_type": (
        """
        Choice :: union(enum) { small i8, empty void }
        Bad proc(input Choice) type {
            match input {
                .small |value|: {
                    next :: value + 1
                    return [usize(next)]i16
                }
                .empty: return bool,
            }
        }
        main proc() Bad(.small{127}) { return [] }
        """, b"", b"integer arithmetic overflow",
    ),
    "type_match_tagged_capture_is_immutable": (
        """
        Choice :: union(enum) { count usize }
        Bad proc(input Choice) type {
            match input { .count |length|: {
                length = 2
                return [length]i16
            } }
        }
        main proc() Bad(.count{1}) { return [0, 0] }
        """, b"", b"mutable local binding",
    ),
    "nested_type_match_failure_preserves_shared_return_types": (
        """
        Original :: alias [2]i16
        Broken proc() type {
            Unused :: struct { value match true { true: i16 } }
            return Original
        }
        main proc() i16 {
            if (false) {
                bad Broken() :: [0, 0]
                return bad[0]
            }
            good Original :: [40, 2]
            return good[0] + good[1]
        }
        """, b"42\n", b"match is not exhaustive",
    ),
    "nested_type_matches_in_storage_calls_and_defaults": (
        """
        Box proc(T type) type { return struct { value T } }
        increment proc(value i16) i16 { return value + 2 }
        Storage proc(wide bool) type {
            return struct {
                values [match wide { true: usize(2), false: usize(1) }]mut (match wide { true: i16, false: i8 })
                boxed Box(match wide { true: i16, false: i8 })
                callback @proc(value match wide { true: i16, false: i8 }) match wide { true: i16, false: i8 }
                bias i16 = match (match wide { true: true, false: false }) { true: 0, false: i8(128) }
                enabled bool = match wide { true: true, false: false }
                label []u8 = match wide { true: "ok", false: "bad" }
                fraction f64 = match wide { true: 0.5, false: 0.0 }
            }
        }
        main proc() i16 {
            value Storage(true) :: { values = [20, 20], boxed = { value = 0 }, callback = increment }
            cached Storage(!false) :: value
            if (!cached.enabled or cached.label[0] != 111 or cached.fraction != 0.5) return -1
            return cached.callback(cached.values[0] + cached.values[1]) + cached.boxed.value + cached.bias
        }
        """, b"42\n", None,
    ),
    "nested_type_matches_preserve_recursive_nominal_identity": (
        """
        Node proc(wide bool) type {
            return struct {
                value match wide { true: i16, false: i8 }
                next ?@match wide { true: Node(wide), false: bool } = match wide { true: null, false: null }
            }
        }
        main proc() i16 {
            leaf Node(true) :: { value = 40 }
            head Node(!false) :: { value = 2, next = &leaf }
            small Node(false) :: { value = 0 }
            return head.value + head.next?.value + i16(small.value)
        }
        """, b"42\n", None,
    ),
    "nested_type_matches_retain_loop_snapshots": (
        """
        Nest proc(depth usize) type {
            Result match true { true: type, false: i8 } := i16
            count match true { true: usize, false: i8 } := depth
            for (0..count) |index| {
                Result = [match index { 0: usize(2), else: usize(1) }](match index {
                    0: Result,
                    else: match true { true: Result, false: i8(128) },
                })
            }
            return Result
        }
        main proc() i16 {
            values Nest(2) :: [[40, 2]]
            return values[0][0] + values[0][1]
        }
        """, b"42\n", None,
    ),
    "nested_type_matches_check_patterns_before_selecting": (
        """
        Bad proc() type {
            return struct { value [1](match true { true: i16 }) }
        }
        main proc() Bad() { return {} }
        """, b"", b"match is not exhaustive",
    ),
    "nested_type_matches_check_selected_defaults": (
        """
        Bad proc() type {
            return struct { value i8 = match true { true: 128, false: 0 } }
        }
        main proc() i16 {
            value Bad() :: {}
            return i16(value.value)
        }
        """, b"", b"numeric literal is outside",
    ),
    "type_match_enum_storage_and_cache_identity": (
        """
        Mode :: enum { tiny, pair, wide }
        pair proc() Mode { return .pair }
        Storage proc(mode Mode) type {
            Result := i8
            match mode {
                .tiny: Result = i8,
                .pair, .wide: {
                    Element :: i16
                    Result = [2]Element
                }
            }
            return struct { values Result }
        }
        main proc() i16 {
            value Storage(.pair) :: { values = [40, 2] }
            cached Storage(pair()) :: value
            tiny Storage(.tiny) :: { values = 1 }
            return cached.values[0] + cached.values[1] + i16(tiny.values - 1)
        }
        """, b"42\n", None,
    ),
    "type_match_ranges_transfers_and_mutable_subject": (
        """
        Storage proc() type {
            Result := i8
            size usize := 0
            for (0..usize(5)) |index| {
                match index {
                    0: continue,
                    1..=2: {
                        Result = [1]Result
                        size = size + 1
                    }
                    else: break,
                }
            }
            match size {
                0..2: return bool,
                else: return struct { values Result, bias i16 = i16(size) },
            }
        }
        main proc() i16 {
            value Storage() :: { values = [[40]] }
            return i16(value.values[0][0]) + value.bias
        }
        """, b"42\n", None,
    ),
    "type_match_type_expressions_and_untaken_bodies": (
        """
        Select proc(wide bool) type {
            Result := match wide {
                true: match wide { true: i16, false: i8(128) },
                false: i8,
            }
            Result = match wide { true: [2]Result, false: bool }
            return match wide {
                true: struct { values Result },
                false: struct { value Result },
            }
        }
        main proc() i16 {
            wide Select(true) :: { values = [40, 2] }
            narrow Select(false) :: { value = true }
            if (narrow.value) return wide.values[0] + wide.values[1]
            return 0
        }
        """, b"42\n", None,
    ),
    "type_match_float_and_unsigned_boundaries": (
        """
        Select proc() type {
            Result := i8
            match u64(18446744073709551615) {
                0..18446744073709551615: Result = bool,
                18446744073709551615: Result = i16,
                else: Result = i8,
            }
            match -1.5 {
                (-2.0)..(-1.0): return Result,
                else: return bool,
            }
        }
        main proc() Select() { return 42 }
        """, b"42\n", None,
    ),
    "type_match_checks_nonexhaustive_patterns": (
        """
        Bad proc() type {
            match true { true: return i16 }
            return i8
        }
        main proc() Bad() { return 42 }
        """, b"", b"match is not exhaustive",
    ),
    "type_match_checks_overlapping_patterns": (
        """
        Bad proc() type {
            match u8(2) {
                0..=2: return i16,
                2..4: return i8,
                else: return bool,
            }
        }
        main proc() Bad() { return 42 }
        """, b"", b"duplicate or overlapping match pattern",
    ),
    "type_match_checks_unreachable_arms": (
        """
        Bad proc() type {
            return match true { else: i16, false: i8 }
        }
        main proc() Bad() { return 42 }
        """, b"", b"match arm is unreachable",
    ),
    "type_match_checks_pattern_types": (
        """
        Bad proc() type {
            match u8(2) { true: return i16, else: return i8 }
        }
        main proc() Bad() { return 42 }
        """, b"", b"incompatible value type",
    ),
    "type_match_checks_subject_types": (
        """
        Bad proc() type {
            return match "text" { else: i16 }
        }
        main proc() Bad() { return 42 }
        """, b"", b"match subject",
    ),
    "type_match_checks_selected_body": (
        """
        Bad proc() type {
            match false {
                true: return i16,
                false: {
                    count u8 := 255
                    count = count + 1
                    return [count]i16
                }
            }
        }
        main proc() Bad() { return 42 }
        """, b"", b"integer arithmetic overflow",
    ),
    "type_match_resolves_untaken_bodies": (
        """
        Bad proc() type {
            return struct { value match true { true: i16, false: missing } }
        }
        main proc() i16 {
            value Bad() :: { value = 42 }
            return value.value
        }
        """, b"42\n", b"unknown name",
    ),
    "type_match_remains_bounded": (
        """
        Bad proc() type {
            while (true) {
                match true { true: continue, false: return i16 }
            }
        }
        main proc() Bad() { return 42 }
        """, b"", b"compile-time execution exceeds its recursion or step limit",
    ),
    "type_execution_retains_existing_nominal_type_identity": (
        """
        Original :: struct { value i16 }
        Identity proc(T type) type {
            Result := T
            Same :: Result
            return Same
        }
        Box proc(T type) type { return struct { value T } }
        main proc() i16 {
            first Original :: { value = 42 }
            same Identity(Original) :: first
            boxed Box(Identity(Original)) :: { value = same }
            cached Box(Original) :: boxed
            return cached.value.value
        }
        """, b"42\n", None,
    ),
    "type_arrays_nested_copies_assignment_and_consumers": (
        """
        Box proc(T type) type { return struct { value T } }
        Selected proc() type {
            explicit [2]mut [_]mut type := [[i8, bool], [u8, i16]]
            inferred := [[i8, bool], [u8, i16]]
            original :: explicit
            copied [2]mut [_]mut type := explicit
            (copied[0])[0] = bool
            explicit[0] = [i16, u8]
            inferred = [[i16, u8], [bool, i8]]
            if (original[0][0] != i8 or copied[0][0] != bool) return bool
            if (explicit[0][0] != i16 or inferred[1][0] != bool) return bool
            return Box(explicit[0][0])
        }
        Storage proc() type {
            types :: [Selected(), [2]i16]
            return struct { item types[0], values types[1], bias i16 = 2 }
        }
        read proc(value Selected()) i16 { return value.value }
        main proc() i16 {
            value Storage() :: { item = { value = 19 }, values = [20, 1] }
            return read(value.item) + value.values[0] + value.values[1] + value.bias
        }
        """, b"42\n", None,
    ),
    "type_arrays_iteration_borrows_nested_places_and_copies_captures": (
        """
        Selected proc() type {
            types [2]mut type := [i8, bool]
            Result := bool
            for types |T, index| {
                at usize :: index
                if (at == 0) types[1] = i16
                if (at == 1) Result = T
            }
            rows [2]mut [1]mut type := [[i8], [bool]]
            for rows |row, index| {
                copied := row
                copied[0] = u8
                if (row[0] == u8) return bool
                if (index == 0) { (rows[1])[0] = Result }
                if (index == 1 and row[0] != i16) return bool
            }
            empty [0]type :: []
            for (empty) |_| return bool
            if (empty.len != 0 or types.len != 2 or rows[0].len != 1) return bool
            return rows[1][0]
        }
        main proc() i16 { value Selected() :: 42
            return value
        }
        """, b"42\n", None,
    ),
    "type_arrays_undefined_loop_initialization_generates_storage": (
        """
        Storage proc() type {
            types [2]mut type := undefined
            if (types.len != 2) return bool
            for (usize(0)..types.len) |index| { types[index] = i16 }
            return struct { values [types.len]types[0], bias types[1] = 2 }
        }
        main proc() i16 {
            value Storage() :: { values = [19, 21] }
            same Storage() :: value
            return same.values[0] + same.values[1] + same.bias
        }
        """, b"42\n", None,
    ),
    "type_arrays_undefined_nested_updates_partial_copies_and_replacement": (
        """
        Storage proc() type {
            rows [2]mut [2]mut type := undefined
            if (rows.len != 2 or rows[0].len != 2) return bool
            (rows[0])[0] = i8
            copied := rows
            (rows[0])[1] = u8
            rows[1] = [i8, bool]
            (copied[0])[1] = i16
            copied[1] = [bool, i16]
            if (rows[0][0] != i8 or copied[0][0] != i8) return bool
            if (rows[0][1] != u8 or rows[1][0] != i8 or rows[1][1] != bool) return bool
            replacement [2]mut type := undefined
            replacement = [i16, bool]
            return struct { first copied[0][1], second copied[1][1], bias replacement[0] = 2 }
        }
        main proc() i16 {
            value Storage() :: { first = 19, second = 21 }
            return value.first + value.second + value.bias
        }
        """, b"42\n", None,
    ),
    "type_arrays_undefined_empty_shapes_and_erased_generic_consumer": (
        """
        empty_rows proc() [2]mut [0]mut type {
            rows [2]mut [0]mut type := undefined
            return rows
        }
        empty_outer proc() [0]mut [2]mut type {
            rows [0]mut [2]mut type := undefined
            return rows
        }
        row proc() [2]mut type {
            types [2]mut type := undefined
            rows :: empty_rows()
            outer :: empty_outer()
            if (rows.len != 2 or rows[0].len != 0 or rows[1].len != 0 or outer.len != 0) {
                return [bool, bool]
            }
            for rows |empty| { for empty |_| { return [bool, bool] } }
            types[0] = i16
            types[1] = bool
            return types
        }
        add proc($types [2]mut type, left types[0], right types[0]) types[0] {
            enabled types[1] :: true
            if (enabled) return left + right
            return 0
        }
        forward proc(value i16) i16 { return add(row(), value, 2) }
        main proc() i16 { return forward(40) }
        """, b"42\n", None,
    ),
    "type_arrays_undefined_reject_premature_leaf_read": (
        """
        Bad proc() type {
            types [2]mut type := undefined
            types[0] = i16
            return types[1]
        }
        main proc() Bad() { return 42 }
        """, b"", b"local value was read before initialization",
    ),
    "type_arrays_undefined_reject_partial_return": (
        """
        rows proc() [2]mut [1]mut type {
            types [2]mut [1]mut type := undefined
            (types[0])[0] = i16
            return types
        }
        main proc() rows()[0][0] { return 42 }
        """, b"", b"local value was read before initialization",
    ),
    "type_arrays_undefined_reject_partial_argument": (
        """
        Selected proc(types [2]mut [1]mut type) type { return i16 }
        Bad proc() type {
            types [2]mut [1]mut type := undefined
            (types[0])[0] = i16
            return Selected(types)
        }
        main proc() Bad() { return 42 }
        """, b"", b"local value was read before initialization",
    ),
    "type_arrays_undefined_reject_partial_erased_argument": (
        """
        Selected proc($types [2]mut type) type { return i16 }
        Bad proc() type {
            types [2]mut type := undefined
            types[0] = i16
            return Selected(types)
        }
        main proc() Bad() { return 42 }
        """, b"", b"local value was read before initialization",
    ),
    "type_arrays_undefined_copy_does_not_observe_later_initialization": (
        """
        Bad proc() type {
            types [2]mut type := undefined
            types[0] = i16
            copied :: types
            types[1] = bool
            return copied[1]
        }
        main proc() Bad() { return 42 }
        """, b"", b"local value was read before initialization",
    ),
    "type_arrays_undefined_reject_readonly_element_write": (
        """
        Bad proc() type {
            types [1]type := undefined
            types[0] = i16
            return types[0]
        }
        main proc() Bad() { return 42 }
        """, b"", b"indexed assignment requires a writable array element",
    ),
    "type_arrays_undefined_reject_inferred_length": (
        """
        Bad proc() type {
            types [_]mut type := undefined
            return i16
        }
        main proc() Bad() { return 42 }
        """, b"", b"array length must be a nonnegative supported compile-time integer",
    ),
    "type_arrays_undefined_empty_outer_rejects_inferred_child_length": (
        """
        Bad proc() type {
            types [0]mut [_]mut type := undefined
            return i16
        }
        main proc() Bad() { return 42 }
        """, b"", b"array length must be a nonnegative supported compile-time integer",
    ),
    "type_arrays_undefined_construction_is_bounded": (
        """
        Bad proc() type {
            types [1000000000]mut type := undefined
            return i16
        }
        main proc() Bad() { return 42 }
        """, b"", b"compile-time execution exceeds its recursion or step limit",
    ),
    "type_arrays_undefined_nested_empty_construction_is_bounded": (
        """
        Bad proc() type {
            types [100]mut [100]mut [0]mut type := undefined
            return i16
        }
        main proc() Bad() { return 42 }
        """, b"", b"compile-time execution exceeds its recursion or step limit",
    ),
    "type_arrays_undefined_does_not_enable_runtime_storage": (
        """
        main proc() i16 {
            values [1]mut i16 := undefined
            values[0] = 42
            return values[0]
        }
        """, b"", b"generic argument must be a compile-time value",
    ),
    "value_arrays_undefined_loop_initialization_generates_storage": (
        """
        Storage proc() type {
            rows [2]mut [2]mut usize := undefined
            for (usize(0)..rows.len) |row| {
                for (usize(0)..rows[row].len) |column| { (rows[row])[column] = row + column + 1 }
            }
            return struct { values [rows[0][1]]i16, bias i16 = i16(rows[1][0]) }
        }
        main proc() i16 {
            value Storage() :: { values = [19, 21] }
            same Storage() :: value
            return same.values[0] + same.values[1] + same.bias
        }
        """, b"42\n", None,
    ),
    "value_arrays_undefined_partial_copies_and_whole_replacement": (
        """
        Storage proc() type {
            rows [2]mut [2]mut i16 := undefined
            (rows[0])[0] = 19
            copied := rows
            (rows[0])[1] = 1
            rows[1] = [2, 3]
            (copied[0])[1] = 21
            copied[1] = [1, 1]
            replacement [2]mut i16 := undefined
            replacement = copied[1]
            if (rows[0][1] != 1 or rows[1][0] != 2) return bool
            return struct { value i16 = copied[0][0] + copied[0][1] + replacement[0] + replacement[1] }
        }
        main proc() i16 { value Storage() :: {}
            return value.value
        }
        """, b"42\n", None,
    ),
    "value_arrays_undefined_strings_optionals_records_and_unions": (
        """
        Item :: struct { text string, amount ?i16, values [1]mut i16 }
        Choice :: union(enum) { value i16, empty void }
        Storage proc() type {
            words [2]mut string := undefined
            words[0] = "ok"
            words[1] = "yes"
            options [2]mut ?i16 := undefined
            options[0] = null
            options[1] = 37
            items [1]mut Item := undefined
            items[0] = { text = words[0], amount = options[0], values = [0] }
            items[0].amount = options[1]
            items[0].values[0] = 37
            choices [1]mut Choice := undefined
            choices[0] = .empty
            choices[0] = .value{items[0].values[0]}
            amount :: match choices[0] { .value |value|: value, .empty: i16(0) }
            return struct { value i16 = amount + items[0].amount? - 37 + i16(items[0].text.len + words[1].len) }
        }
        main proc() i16 { value Storage() :: {}
            return value.value
        }
        """, b"42\n", None,
    ),
    "value_arrays_undefined_empty_shapes_and_type_array_producer": (
        """
        Rows proc() [1]mut type {
            rows [2]mut [0]mut i16 := undefined
            outer [0]mut [2]mut usize := undefined
            copied :: rows
            if (copied.len != 2 or copied[0].len != 0 or outer.len != 0) return [bool]
            for rows |row| { for row |_| { return [bool] } }
            counts [1]mut usize := undefined
            counts[0] = rows.len
            return [struct { values [counts[0]]i16, bias i16 = 2 }]
        }
        main proc() i16 {
            value Rows()[0] :: { values = [19, 21] }
            return value.values[0] + value.values[1] + value.bias
        }
        """, b"42\n", None,
    ),
    "value_arrays_undefined_reject_premature_leaf_read": (
        """
        Bad proc() type {
            values [2]mut usize := undefined
            values[0] = 2
            return [values[1]]i16
        }
        main proc() Bad() { return [] }
        """, b"", b"local value was read before initialization",
    ),
    "value_arrays_undefined_reject_partial_argument": (
        """
        Selected proc(values [2]mut usize) type { return [values[0]]i16 }
        Bad proc() type {
            values [2]mut usize := undefined
            values[0] = 2
            return Selected(values)
        }
        main proc() Bad() { return [19, 23] }
        """, b"", b"local value was read before initialization",
    ),
    "value_arrays_undefined_copy_does_not_observe_later_initialization": (
        """
        Bad proc() type {
            values [2]mut usize := undefined
            values[0] = 2
            copied :: values
            values[1] = 1
            return [copied[1]]i16
        }
        main proc() Bad() { return [] }
        """, b"", b"local value was read before initialization",
    ),
    "value_arrays_undefined_reject_readonly_binding": (
        """
        Bad proc() type {
            values [1]mut usize :: undefined
            values = [2]
            return i16
        }
        main proc() Bad() { return 42 }
        """, b"", b"assignment requires a mutable local binding",
    ),
    "value_arrays_undefined_reject_readonly_elements": (
        """
        Bad proc() type {
            values [1]usize := undefined
            values[0] = 2
            return i16
        }
        main proc() Bad() { return 42 }
        """, b"", b"indexed assignment requires a writable array element",
    ),
    "value_arrays_undefined_reject_wrong_leaf_type": (
        """
        Bad proc() type {
            values [1]mut usize := undefined
            wrong u8 :: 2
            values[0] = wrong
            return i16
        }
        main proc() Bad() { return 42 }
        """, b"", b"",
    ),
    "value_arrays_undefined_reject_wrong_index_type": (
        """
        Bad proc() type {
            values [1]mut usize := undefined
            index u8 :: 0
            values[index] = 2
            return i16
        }
        main proc() Bad() { return 42 }
        """, b"", b"array index must have type usize",
    ),
    "value_arrays_undefined_reject_out_of_bounds_write": (
        """
        Bad proc() type {
            values [1]mut usize := undefined
            values[1] = 2
            return i16
        }
        main proc() Bad() { return 42 }
        """, b"", b"index out of bounds",
    ),
    "value_arrays_undefined_preserve_narrow_overflow": (
        """
        Bad proc() type {
            values [1]mut i8 := undefined
            values[0] = 127
            values[0] = values[0] + 1
            return i16
        }
        main proc() Bad() { return 42 }
        """, b"", b"integer arithmetic overflow",
    ),
    "value_arrays_undefined_construction_is_bounded": (
        """
        Bad proc() type {
            values [1000000000]mut usize := undefined
            return i16
        }
        main proc() Bad() { return 42 }
        """, b"", b"compile-time execution exceeds its recursion or step limit",
    ),
    "value_arrays_undefined_nested_empty_construction_is_bounded": (
        """
        Bad proc() type {
            values [100]mut [100]mut [0]mut usize := undefined
            return i16
        }
        main proc() Bad() { return 42 }
        """, b"", b"compile-time execution exceeds its recursion or step limit",
    ),
    "value_arrays_undefined_reject_invalid_empty_element_annotation": (
        """
        Storage proc() type {
            values [0]mut void := undefined
            return i16
        }
        main proc() Storage() { return 42 }
        """, b"42\n", b"array element annotations",
    ),
    "type_arrays_only_execute_selected_bodies": (
        """
        Selected proc(ok bool) type {
            types :: [i16]
            if (ok) return types[0]
            return types[1]
        }
        main proc() i16 { value Selected(true) :: 42
            return value
        }
        """, b"42\n", None,
    ),
    "type_arrays_selected_invalid_body": (
        """
        Selected proc(ok bool) type {
            types :: [i16]
            if (ok) return types[0]
            return types[1]
        }
        main proc() Selected(false) { return 42 }
        """, b"", b"index out of bounds",
    ),
    "type_arrays_read_requires_usize_index": (
        """
        Bad proc() type {
            types :: [i16]
            index u8 :: 0
            return types[index]
        }
        main proc() Bad() { return 42 }
        """, b"", b"array index must have type usize",
    ),
    "type_arrays_write_checks_bounds": (
        """
        Bad proc() type {
            types [1]mut type := [i16]
            types[1] = bool
            return types[0]
        }
        main proc() Bad() { return 42 }
        """, b"", b"index out of bounds",
    ),
    "type_arrays_write_requires_usize_index": (
        """
        Bad proc() type {
            types [1]mut type := [i16]
            index u8 :: 0
            types[index] = bool
            return types[0]
        }
        main proc() Bad() { return 42 }
        """, b"", b"array index must have type usize",
    ),
    "type_arrays_write_requires_mutable_elements": (
        """
        Bad proc() type {
            types [1]type := [i16]
            types[0] = bool
            return types[0]
        }
        main proc() Bad() { return 42 }
        """, b"", b"indexed assignment requires a writable array element",
    ),
    "type_arrays_nested_write_requires_mutable_inner_elements": (
        """
        Bad proc() type {
            types [1]mut [1]type := [[i16]]
            (types[0])[0] = bool
            return types[0][0]
        }
        main proc() Bad() { return 42 }
        """, b"", b"indexed assignment requires a writable array element",
    ),
    "type_arrays_whole_assignment_requires_mutable_binding": (
        """
        Bad proc() type {
            types :: [i16]
            types = [bool]
            return types[0]
        }
        main proc() Bad() { return 42 }
        """, b"", b"assignment requires a mutable local binding",
    ),
    "type_arrays_whole_assignment_checks_shape": (
        """
        Bad proc() type {
            types := [[i16]]
            types = [[i16, bool]]
            return types[0][0]
        }
        main proc() Bad() { return 42 }
        """, b"", b"array literal length does not match its type",
    ),
    "type_arrays_indexed_assignment_checks_shape": (
        """
        Bad proc() type {
            types [1]mut [1]mut type := [[i16]]
            types[0] = [i16, bool]
            return types[0][0]
        }
        main proc() Bad() { return 42 }
        """, b"", b"array literal length does not match its type",
    ),
    "type_arrays_indexed_assignment_checks_value": (
        """
        Bad proc() type {
            types [1]mut type := [i16]
            types[0] = 42
            return types[0]
        }
        main proc() Bad() { return 42 }
        """, b"", b"incompatible value type",
    ),
    "type_arrays_inferred_nested_counts_reject_ragged_initialization": (
        """
        Bad proc() type {
            types [2]mut [_]mut type := [[i8], [i16, bool]]
            return types[1][0]
        }
        main proc() Bad() { return 42 }
        """, b"", b"array literal length does not match its type",
    ),
    "type_arrays_preserve_anonymous_nominal_identity_across_reads_and_copies": (
        """
        Selected proc() type {
            types :: [struct { value i16 }, struct { value i16 }]
            copied :: types
            if (types[0] != copied[0] or types[0] == types[1]) return bool
            return struct { first types[0], second copied[0] }
        }
        main proc() i16 {
            value Selected() :: { first = { value = 19 }, second = { value = 23 } }
            copied := value.first
            copied = value.second
            return value.first.value + copied.value
        }
        """, b"42\n", None,
    ),
    "type_arrays_empty_shapes_validate_child_lengths": (
        """
        Bad proc() type {
            types [0][-1]type :: []
            return i16
        }
        main proc() Bad() { return 42 }
        """, b"", b"array length must be a nonnegative supported compile-time integer",
    ),
    "type_array_procedure_nested_results_in_signatures_storage_and_defaults": (
        """
        Row :: alias [2]type
        row proc(wide bool) Row {
            if (wide) return [i16, [2]i16]
            return [i8, [2]i8]
        }
        rows proc() [2]Row { return [row(true), row(false)] }
        Storage :: struct { values rows()[0][1], bias rows()[0][0] = 2 }
        read proc(value rows()[0][0]) rows()[0][0] { return value }
        main proc() i16 {
            value Storage :: { values = [19, 21] }
            small rows()[1][1] :: [0, 0]
            return read(value.values[0]) + value.values[1] + value.bias + i16(small[0])
        }
        """, b"42\n", None,
    ),
    "type_array_procedure_dependent_lengths_and_ordinary_array_inputs": (
        """
        identity proc($N usize, types [N]type) [N]type { return types }
        row proc() [2]type { return identity(2, identity(1 + 1, [i16, bool])) }
        Box proc($N usize, types [N]type) type {
            return struct { value types[0], enabled types[1] = true }
        }
        main proc() i16 {
            value Box(2, row()) :: { value = 42 }
            same Box(1 + 1, [i16, bool]) :: value
            if (same.enabled) return same.value
            return 0
        }
        """, b"42\n", None,
    ),
    "type_array_procedure_erased_runtime_generic_parameters": (
        """
        row proc() [2]type { return [i16, bool] }
        add proc($types [2]type, left types[0], right types[0]) types[0] {
            enabled types[1] :: true
            if (enabled) return left + right
            return 0
        }
        forward proc(value i16) i16 { return add(row(), value, 2) }
        main proc() i16 { return forward(40) }
        """, b"42\n", None,
    ),
    "type_array_procedure_inputs_results_and_cache_are_by_value": (
        """
        change proc(types [1]mut [2]mut type) [1]mut [2]mut type {
            copied := types
            (copied[0])[0] = bool
            return copied
        }
        seed proc() [1]mut [2]mut type { return [[i16, i8]] }
        Selected proc() type {
            original [1]mut [2]mut type := $(seed())
            changed := change(original)
            (changed[0])[1] = u8
            cached := change(seed())
            fresh := seed()
            if (original[0][0] != i16 or fresh[0][0] != i16) return bool
            if (cached[0][0] != bool or cached[0][1] != i8) return bool
            if (changed[0][1] != u8) return bool
            return original[0][0]
        }
        main proc() Selected() { return 42 }
        """, b"42\n", None,
    ),
    "type_array_procedure_cache_canonical_leaves_and_nominal_identity": (
        """
        Original :: struct { value i16 }
        Same :: alias Original
        leaf proc() type { return Original }
        identity proc(types [2]type) [2]type { return types }
        Box proc(types [2]type) type { return struct { first types[0], second types[1] } }
        Selected proc() type {
            leaves :: identity([struct { value i16 }, struct { value i16 }])
            copied :: identity(leaves)
            if (leaves[0] == leaves[1] or leaves[0] != copied[0]) return bool
            if (Box([leaves[0], leaves[0]]) == Box([leaves[1], leaves[1]])) return bool
            return Box([copied[0], leaves[0]])
        }
        main proc() i16 {
            canonical Box(identity([Same, leaf()])) :: {
                first = { value = 19 }, second = { value = 21 },
            }
            cached Box([Original, Original]) :: canonical
            nominal Selected() :: { first = { value = 1 }, second = { value = 1 } }
            copy := nominal.first
            copy = nominal.second
            return cached.first.value + cached.second.value + nominal.first.value + copy.value
        }
        """, b"42\n", None,
    ),
    "type_array_procedure_empty_nested_shapes": (
        """
        empty proc() [0]type { return [] }
        rows proc(types [0]type) [2][0]type { return [types, empty()] }
        outer proc() [0][2]type { return [] }
        Selected proc(types [2][0]type, absent [0][2]type) type {
            if (types.len != 2 or types[0].len != 0 or absent.len != 0) return bool
            for types |row| { for row |_| { return bool } }
            return i16
        }
        main proc() Selected(rows(empty()), outer()) { return 42 }
        """, b"42\n", None,
    ),
    "type_array_procedure_rejects_input_shape": (
        """
        first proc(types [2]type) type { return types[0] }
        main proc() first([i16]) { return 42 }
        """, b"", b"array literal length does not match its type",
    ),
    "type_array_procedure_rejects_input_leaf": (
        """
        first proc(types [1]type) type { return types[0] }
        main proc() first([42]) { return 42 }
        """, b"", b"incompatible value type",
    ),
    "type_array_procedure_rejects_parameter_mutability_mismatch": (
        """
        row proc() [1]mut type { return [i16] }
        first proc(types [1]type) type { return types[0] }
        main proc() first(row()) { return 42 }
        """, b"", b"incompatible value type",
    ),
    "type_array_procedure_rejects_result_shape": (
        """
        bad proc() [1][2]type { return [[i16]] }
        main proc() bad()[0][0] { return 42 }
        """, b"", b"array literal length does not match its type",
    ),
    "type_array_procedure_rejects_result_leaf": (
        """
        bad proc() [1]type { return [42] }
        main proc() bad()[0] { return 42 }
        """, b"", b"incompatible value type",
    ),
    "type_array_procedure_result_index_checks_bounds": (
        """
        row proc() [1]type { return [i16] }
        main proc() row()[1] { return 42 }
        """, b"", b"index out of bounds",
    ),
    "type_array_procedure_result_index_requires_usize": (
        """
        row proc() [1]type { return [i16] }
        main proc() row()[u8(0)] { return 42 }
        """, b"", b"array index must have type usize",
    ),
    "type_array_procedure_rejects_runtime_dependency": (
        """
        row proc(wide bool) [1]type {
            if (wide) return [i16]
            return [i8]
        }
        read proc(wide bool) i16 {
            value row(wide)[0] :: 42
            return i16(value)
        }
        main proc() i16 { return read(true) }
        """, b"", b"generic argument must be a compile-time value",
    ),
    "type_array_procedure_requires_all_ordinary_arguments": (
        """
        row proc(ignored usize) [1]type { return [i16] }
        main proc() row()[0] { return 42 }
        """, b"", b"argument count does not match",
    ),
    "type_array_procedure_nested_execution_shares_step_limit": (
        """
        row proc(count usize) [1]type {
            for (0..count) |_| { }
            return [i16]
        }
        Selected proc() type {
            for (0..usize(100)) |index| { types :: row(index + 100) }
            return i16
        }
        main proc() Selected() { return 42 }
        """, b"", b"compile-time execution exceeds its recursion or step limit",
    ),
    "type_execution_mutable_types_and_nested_producers": (
        """
        Nest proc(T type, depth usize) type {
            Result type := T
            count usize := 0
            while (count < depth) {
                Result = [2]Result
                count = count + 1
            }
            return Result
        }
        Box proc(T type) type { return struct { value T } }
        main proc() i16 {
            value Box(Nest(i16, 2)) :: { value = [[20, 20], [1, 1]] }
            same Box(Nest(i16, 1 + 1)) :: value
            return same.value[0][0] + same.value[0][1] + same.value[1][0] + same.value[1][1]
        }
        """, b"42\n", None,
    ),
    "type_execution_range_captures_scope_and_transfers": (
        """
        Storage proc() type {
            Result := i8
            size usize := 0
            for (0..usize(5)) |index| {
                if (index == 0) continue
                if (index == 3) break
                next :: [index]Result
                Result = next
                size = size + 1
            }
            while (size < 3) {
                size = size + 1
                if (size == 3) continue
                Result = bool
            }
            return struct { values Result, bias i16 = i16(size) }
        }
        main proc() i16 {
            value Storage() :: { values = [[19], [20]] }
            return i16(value.values[0][0]) + i16(value.values[1][0]) + value.bias
        }
        """, b"42\n", None,
    ),
    "type_execution_early_return_and_recursive_calls": (
        """
        Nest proc(T type, depth usize) type {
            if (depth == 0) return T
            return [2]Nest(T, depth - 1)
        }
        Select proc(wide bool) type {
            Result := i8
            if (wide) {
                Result = Nest(i16, 1)
                return Result
            } else { Result = [2]Result }
            return Result
        }
        main proc() i16 {
            wide Select(true) :: [40, 2]
            narrow Select(false) :: [1, 1]
            return wide[0] + wide[1] + i16(narrow[0] - narrow[1])
        }
        """, b"42\n", None,
    ),
    "type_execution_inclusive_narrow_range_does_not_overflow": (
        """
        Storage proc() type {
            count usize := 0
            for (u8(254)..=u8(255)) |_| count = count + 1
            for (i8(2)..i8(-2)) |_| count = 99
            return [count]i16
        }
        main proc() i16 {
            values Storage() :: [40, 2]
            return values[0] + values[1]
        }
        """, b"42\n", None,
    ),
    "type_execution_rejects_constant_assignment": (
        """
        Bad proc() type {
            T :: i16
            T = bool
            return T
        }
        main proc() i16 {
            value Bad() :: 42
            return value
        }
        """, b"", b"assignment requires a mutable local binding",
    ),
    "type_execution_rejects_value_in_type_local": (
        """
        Bad proc() type {
            T := i16
            T = 42
            return T
        }
        main proc() i16 {
            value Bad() :: 42
            return value
        }
        """, b"", b"incompatible value type",
    ),
    "type_execution_preserves_scalar_type_and_overflow": (
        """
        Bad proc() type {
            count u8 := 255
            count = count + 1
            return [count]i16
        }
        main proc() i16 {
            value Bad() :: 42
            return value
        }
        """, b"", b"integer arithmetic overflow",
    ),
    "type_execution_checks_guard_operands": (
        """
        Bad proc() type {
            T := i16
            while (false and 1) T = bool
            return T
        }
        main proc() i16 {
            value Bad() :: 42
            return value
        }
        """, b"", b"operator requires compatible operand types",
    ),
    "type_execution_is_bounded": (
        """
        Bad proc() type {
            counter usize := 0
            while (counter >= 0) counter = counter + 1
            return i16
        }
        main proc() i16 {
            value Bad() :: 42
            return value
        }
        """, b"", b"compile-time execution exceeds its recursion or step limit",
    ),
    "type_producer_ordinary_inputs_generate_storage_and_cache_identity": (
        """
        Fixed proc(T type, count usize) type { return struct { values [count]T } }
        main proc() i16 {
            value Fixed(i16, 2) :: { values = [40, 2] }
            same Fixed(i16, 1 + 1) :: value
            return same.values[0] + same.values[1]
        }
        """, b"42\n", None,
    ),
    "type_producer_mixes_explicit_and_ordinary_constant_inputs": (
        """
        Fixed proc($T type, count usize) type { return [count]T }
        Choose proc(wide bool) type {
            if (wide) return Fixed(i16, 2)
            return Fixed(i8, 2)
        }
        main proc() i16 {
            wide Choose(true) :: [40, 2]
            narrow Choose(false) :: [1, 1]
            return wide[0] + wide[1] + i16(narrow[0] - narrow[1])
        }
        """, b"42\n", None,
    ),
    "type_producer_computed_ordinary_inputs_reuse_typed_execution": (
        """
        count proc(seed usize) usize {
            result := seed
            while (result < 2) result = result + 1
            return result
        }
        Fixed proc(size usize) type { return struct { values [size]i16 } }
        main proc() i16 {
            first Fixed(count(0)) :: { values = [40, 2] }
            same Fixed(2) :: first
            return same.values[0] + same.values[1]
        }
        """, b"42\n", None,
    ),
    "type_producer_ordinary_inputs_preserve_nominal_separation": (
        """
        Tag proc(value usize) type { return struct { value i16 } }
        main proc() bool { return $(Tag(1) != Tag(2)) }
        """, b"true\n", None,
    ),
    "type_producer_dependent_contextual_ordinary_input": (
        """
        Fixed proc($T type, $N usize, data [N]T) type {
            return struct { values [N]T = data }
        }
        main proc() i16 {
            value Fixed(i16, 2, [40, 2]) :: {}
            same Fixed(i16, 2, [20 + 20, 2]) :: value
            return same.values[0] + same.values[1]
        }
        """, b"42\n", None,
    ),
    "type_producer_rejects_runtime_dependency": (
        """
        Fixed proc(size usize) type { return [size]i16 }
        read proc(size usize) i16 {
            data Fixed(size) :: [40, 2]
            return data[0] + data[1]
        }
        main proc() i16 { return read(2) }
        """, b"", b"generic argument must be a compile-time value",
    ),
    "type_producer_checks_unused_ordinary_input_range": (
        """
        Tag proc(ignored u8) type { return struct { value i16 } }
        main proc() i16 {
            value Tag(256) :: { value = 42 }
            return value.value
        }
        """, b"", b"numeric literal is outside",
    ),
    "type_producer_checks_erased_ordinary_input_exact_type": (
        """
        Tag proc(ignored i16) type { return struct { value i16 } }
        main proc() i16 {
            wrong u16 :: 1
            first Tag(1) :: { value = 42 }
            same Tag(wrong) :: first
            return same.value
        }
        """, b"42\n", b"incompatible value type",
    ),
    "type_producer_requires_all_ordinary_inputs": (
        """
        Tag proc(ignored i16) type { return struct { value i16 } }
        main proc() i16 {
            value Tag() :: { value = 42 }
            return value.value
        }
        """, b"", b"argument count does not match",
    ),
    "type_producer_ordinary_input_trap_keeps_original_diagnostic": (
        """
        count proc() usize {
            high usize :: 18446744073709551615
            return high + 1
        }
        Fixed proc(size usize) type { return [size]i16 }
        main proc() i16 {
            value Fixed(count()) :: [40, 2]
            return value[0] + value[1]
        }
        """, b"", b"integer arithmetic overflow",
    ),
    "comptime_composed_type_identity_is_order_independent_and_cached": (
        """
        Left :: enum { left }
        Right :: enum { right }
        Box proc($T type) type { return struct { value T } }
        identical proc($A, $B type) bool { return A == B }
        main proc() bool {
            first Box(Left | Right) :: { value = .left }
            same Box(Right | Left | Left) :: first
            return (
                identical(Left | Right, Right | Left | Left)
                and identical(Left, Left | Left) and same.value == .left
            )
        }
        """, b"true\n", None,
    ),
    "comptime_callback_branch_uses_optional_function_identity": (
        """
        Callback :: alias @proc(value i16) i16
        increment proc(value i16) i16 { return value + 1 }
        apply proc($callback ?Callback, value i16) i16 {
            if $(callback == null) { return value }
            else { return callback?(value) }
        }
        main proc() i16 { return apply(null, 40) + apply(increment, 1) }
        """, b"42\n", None,
    ),
    "comptime_branch_rejects_runtime_guard_dependencies": (
        """
        answer proc(take bool) i16 {
            if $(take) { return 42 } else { return 0 }
        }
        main proc() i16 { return answer(true) }
        """, b"", b"generic argument must be a compile-time value",
    ),
    "comptime_branch_checks_short_circuited_guard_operands": (
        """
        main proc() i16 {
            if $(true or 1) { return 42 } else { return 0 }
        }
        """, b"", b"operator requires compatible operand types",
    ),
    "specialized_type_branches_check_only_selected_storage": (
        """
        count proc($T type, value T) i16 {
            if (T == i16) return value else return value.count
        }
        Box :: struct { count i16 }
        main proc() i16 { return count(i16, 40) + count(Box, Box { count = 2 }) }
        """, b"42\n", None,
    ),
    "explicit_comptime_branch_without_templates": (
        """
        enabled proc() bool { return true }
        main proc() i16 {
            if $(enabled()) { return 42 } else { return false }
        }
        """, b"42\n", None,
    ),
    "specialized_branches_skip_recursive_instantiation_and_traps": (
        """
        answer proc($stop bool) i16 {
            if (stop) return 42 else return answer(false)
        }
        main proc() i16 {
            if $(false) { return $(i8(128)) }
            return answer(true)
        }
        """, b"42\n", None,
    ),
    "specialized_branches_preserve_runtime_conditions": (
        """
        answer proc($T type, value T, take bool) i16 {
            if (T != i16) return value.count
            if (take) return value else return 2
        }
        main proc() i16 { return answer(i16, 40, true) + answer(i16, 99, false) }
        """, b"42\n", None,
    ),
    "specialized_branch_keeps_scope_and_cleanup": (
        """
        change proc($take bool, value @mut i16) void {
            if (take) {
                defer value^ = value^ + 2
                value^ = 40
            }
            else value^ = 0
        }
        main proc() i16 {
            value i16 := 0
            change(true, &value)
            return value
        }
        """, b"42\n", None,
    ),
    "comptime_type_equality_preserves_aliases_and_nominal_identity": (
        """
        First :: struct { value i16 }
        Second :: struct { value i16 }
        Same :: alias First
        identical proc($A, $B type) bool { return A == B }
        main proc() bool {
            return (
                identical(First, Same) and !identical(First, Second)
                and $(@mut i16 == @mut i16) and $(@mut i16 != @i16)
                and $([2]i16 == [1 + 1]i16) and $([2]i16 != [2]u16)
            )
        }
        """, b"true\n", None,
    ),
    "specialized_branch_retains_guard_type_errors": (
        """
        answer proc($take bool) i16 {
            if (take or 1) return 42 else return false
        }
        main proc() i16 { return answer(true) }
        """, b"", b"operator requires compatible operand types",
    ),
    "specialized_branch_retains_selected_body_errors": (
        """
        answer proc($T type, value T) i16 {
            if (T == i16) return value.missing
            return 42
        }
        main proc() i16 { return answer(i16, 0) }
        """, b"", b"field selection requires a record or tuple",
    ),
    "ordinary_constant_branch_still_checks_both_arms": (
        """
        main proc() i16 {
            if (true) return 42 else return false
        }
        """, b"42\n", b"return does not match",
    ),
    "specialized_branch_runtime_call_remains_reachable": (
        """
        fail proc(value i8) bool { return value + 1 == 0 }
        answer proc($T type) i16 {
            if (fail(127)) return 0
            return 42
        }
        main proc() i16 { return answer(i16) }
        """, b"", b"integer arithmetic overflow",
    ),
    "comptime_contextual_aggregate_arguments_keep_dependent_types": (
        """
        sum proc(items [2]i16) i16 { return items[0] + items[1] }
        answer proc($T type, $N usize, $items [N]T) T { return items[0] + items[1] }
        main proc() i16 { return answer(i16, 2, [20, 20]) + $(sum([1, 1])) }
        """, b"42\n", None,
    ),
    "comptime_zero_cell_aggregates_keep_logical_lengths": (
        """
        Empty :: struct {}
        Storage :: struct { items [3]Empty, empty [0]i16 }
        make proc() Storage { return Storage { items = [{}, {}, {}], empty = [] } }
        main proc() usize {
            value :: $(make())
            return value.items.len + value.empty.len
        }
        """, b"3\n", None,
    ),
    "explicit_comptime_float_arithmetic_keeps_ieee_equality": (
        """
        main proc() bool { return $(1.5 + 2.5) == 4.0 and $(-0.0 == 0.0) and $(4 / 2) == 2.0 }
        """, b"true\n", None,
    ),
    "comptime_aggregate_specialization_uses_normalized_values": (
        """
        Config :: struct { count usize, bias i16 = 2 }
        config proc() Config {
            result Config := { count = 0 }
            while (result.count < 2) result.count = result.count + 1
            return result
        }
        Fixed proc($settings Config) type {
            return struct { values [settings.count]i16 }
        }
        main proc() i16 {
            first Fixed(config()) :: { values = [40, 2] }
            same Fixed({ bias = 2, count = 2 }) :: first
            return same.values[0] + same.values[1]
        }
        """, b"42\n", None,
    ),
    "comptime_array_arguments_and_explicit_copy_isolation": (
        """
        values proc() [2]mut i16 {
            result [2]mut i16 := [0, 2]
            for (0..usize(2)) |index| result[index] = result[index] + 20
            return result
        }
        sum proc($items [2]mut i16) i16 { return items[0] + items[1] }
        main proc() i16 {
            first := $(values())
            copy := first
            first[0] = 99
            return sum(values()) + copy[0] - 20
        }
        """, b"42\n", None,
    ),
    "explicit_comptime_without_templates_preserves_nested_values": (
        r"""
        Kind :: enum { ready, empty }
        Inner :: struct { text string, count ?i16 }
        Packet :: struct { rows [2]mut Inner, kind Kind, weight f64 }
        make proc() Packet {
            result Packet := {
                rows = [{ text = "a\"\\\x00é", count = 20 }, { text = "ok", count = null }],
                kind = .ready, weight = 2.5,
            }
            result.rows[0].count = 40
            return result
        }
        main proc() i16 {
            packet := $(make())
            copy :: packet
            packet.rows[0].text = "changed"
            if (copy.kind != .ready or copy.weight != 2.5 or copy.rows[1].count != null) return 0
            return copy.rows[0].count? + i16(copy.rows[1].text.len)
        }
        """, b"42\n", None,
    ),
    "explicit_comptime_composes_and_supplies_array_lengths": (
        """
        size proc() usize { return 2 }
        main proc() i16 {
            amount :: 40
            values [$(size())]i16 :: [$(amount + 1), $(1)]
            return values[$(usize(0))] + values[1]
        }
        """, b"42\n", None,
    ),
    "explicit_comptime_cannot_read_runtime_parameters": (
        """
        select proc(value i16) i16 { return $(value + 1) }
        main proc() i16 { return select(41) }
        """, b"", b"generic argument must be a compile-time value",
    ),
    "comptime_aggregate_cannot_read_mutable_globals": (
        """
        state i16 := 42
        make proc() [1]i16 { return [state] }
        main proc() i16 { return $(make())[0] }
        """, b"", b"generic argument must be a compile-time value",
    ),
    "comptime_aggregate_keeps_narrow_overflow": (
        """
        Counter :: struct { value i8 }
        make proc() Counter {
            result Counter := { value = 127 }
            result.value = result.value + 1
            return result
        }
        main proc() i8 { return $(make()).value }
        """, b"", b"integer arithmetic overflow",
    ),
    "comptime_aggregate_rejects_pointer_escape": (
        """
        Borrow :: struct { pointer @i16 }
        make proc() Borrow {
            value i16 :: 42
            return Borrow { pointer = &value }
        }
        main proc() i16 { return $(make()).pointer^ }
        """, b"", b"compile-time result cannot escape evaluation",
    ),
    "comptime_aggregate_rejects_slice_escape": (
        """
        make proc() [1][]u8 { return ["borrowed"] }
        main proc() usize { return $(make())[0].len }
        """, b"", b"compile-time result cannot escape evaluation",
    ),
    "comptime_tagged_union_preserves_tags_and_nested_raw_payloads": (
        """
        Tag :: enum(i16) { number = -7, empty = 12, zero = 41, payload = 90 }
        Empty :: struct {}
        Payload :: struct { rows [2]struct { values [2]i16 }, text string }
        Choice :: union(Tag) { payload Payload, empty void, zero Empty, number i16 }
        make proc() Choice {
            return .payload { rows = [{ values = [10, 20] }, { values = [4, 6] }], text = "ok" }
        }
        numeric proc() Choice { return .number{42} }
        empty proc() Choice { return .empty }
        zero proc() Choice { return .zero{} }
        score proc(value Choice) i16 {
            return match value {
                .number |number|: number,
                .payload |payload|: (
                    payload.rows[0].values[0] + payload.rows[0].values[1]
                    + payload.rows[1].values[0] + payload.rows[1].values[1] + i16(payload.text.len)
                ),
                .empty: 3,
                .zero: 5,
            }
        }
        main proc() [5]i16 {
            value :: $(make())
            return [score(value), value.payload.rows[1].values[1], score($(numeric())),
                score($(empty())), score($(zero()))]
        }
        """, b"[42, 6, 42, 3, 5]\n", None,
    ),
    "comptime_tagged_union_value_parameters_and_cache_identity": (
        """
        Choice :: union(enum) { left i16, right i16 }
        make proc() Choice { return .left{2} }
        Fixed proc($choice Choice) type {
            return struct { values [usize(choice.left)]i16 }
        }
        Token proc($choice Choice) type { return struct {} }
        score proc($choice Choice) i16 {
            return match choice { .left |value|: value, .right |value|: value + 1 }
        }
        main proc() i16 {
            first Fixed(make()) :: { values = [20, 20] }
            same Fixed(Choice { left = 2 }) :: first
            if (Token(make()) != Token(Choice { left = 2 })) return 0
            if (Token(make()) == Token(Choice { right = 2 })) return 0
            if (Token(make()) == Token(Choice { left = 3 })) return 0
            return same.values[0] + same.values[1] + score(make())
        }
        """, b"42\n", None,
    ),
    "comptime_fallible_nested_storage_preserves_both_channels": (
        """
        Error :: enum { failed }
        Storage :: struct { values [2](i16 ! Error), text string ! Error }
        make proc() Storage { return { values = [40, .failed], text = "ok" } }
        main proc() i16 {
            storage :: $(make())
            text :: storage.text catch ""
            return (storage.values[0] catch 0) + (storage.values[1] catch i16(text.len))
        }
        """, b"42\n", None,
    ),
    "comptime_fallible_materializes_composed_and_payload_errors": (
        """
        Missing :: enum { missing }
        Detail :: union(enum) { failed struct { rows [2]i16, text string }, empty void }
        Failure :: struct { code i16 }
        missing proc() i16 ! (Missing | Detail) { return .missing }
        detail proc() i16 ! (Missing | Detail) {
            return .failed { rows = [20, 20], text = "ok" }
        }
        record proc() i16 ! Failure { return Failure { code = 42 } }
        read proc(value i16 ! (Missing | Detail)) i16 {
            return value catch |err| match err {
                .missing: 7,
                .failed |payload|: payload.rows[0] + payload.rows[1] + i16(payload.text.len),
                .empty: 0,
            }
        }
        main proc() [3]i16 {
            return [read($(missing())), read($(detail())), $(record()) catch |err| err.code]
        }
        """, b"[7, 42, 42]\n", None,
    ),
    "comptime_fallible_value_parameters_type_inputs_and_cache_identity": (
        """
        Error :: enum { failed, other }
        good proc() i16 ! Error { return 2 }
        bad proc() i16 ! Error { return .failed }
        Fixed proc($result i16 ! Error) type {
            return struct { values [usize(result catch 1)]i16 }
        }
        Token proc($result i16 ! Error) type { return struct {} }
        score proc($result i16 ! Error) i16 { return result catch 40 }
        main proc() i16 {
            first Fixed(good()) :: { values = [20, 20] }
            same Fixed(2) :: first
            if (Token(good()) != Token(2) or Token(bad()) != Token(Error.failed)) return 0
            if (Token(good()) == Token(3) or Token(bad()) == Token(Error.other)) return 0
            if (Token(good()) == Token(bad())) return 0
            return same.values[0] + same.values[1] + score(good())
        }
        """, b"42\n", None,
    ),
    "comptime_fallible_void_success_and_error_channels": (
        """
        Error :: enum { failed }
        good proc() void ! Error {}
        bad proc() void ! Error { return .failed }
        score proc($result void ! Error) i16 {
            result catch { return 2 }
            return 40
        }
        main proc() i16 {
            value i16 := 0
            $(good()) catch { value = 100 }
            $(bad()) catch { value = 2 }
            return value + score(good()) + score(bad()) - 2
        }
        """, b"42\n", None,
    ),
    "comptime_fallible_preserves_overlapping_channel_types": (
        """
        Success :: enum { failed }
        Error :: enum { failed }
        Value :: struct { code i16 }
        Failure :: struct { code i16 }
        good proc() Success ! Error { return Success.failed }
        bad proc() Success ! Error { return Error.failed }
        good_record proc() Value ! Failure { return Value { code = 40 } }
        bad_record proc() Value ! Failure { return Failure { code = 2 } }
        enum_score proc() i16 {
            value :: $(good()) catch { return -1 }
            if (value != Success.failed) return -2
            $(bad()) catch { return 42 }
            return 0
        }
        main proc() [2]i16 {
            left :: $(good_record()) catch Value { code = 0 }
            right :: $(bad_record()) catch |err| Value { code = err.code }
            return [enum_score(), left.code + right.code]
        }
        """, b"[42, 42]\n", None,
    ),
    "comptime_tagged_union_projects_raw_nested_channels": (
        """
        Error :: enum { failed }
        Choice :: union(enum) {
            data struct { maybe ?i16, result i16 ! Error }
            pointer @i16
            empty void
        }
        make proc() Choice { return .data { maybe = 40, result = .failed } }
        absent proc() Choice { return .empty }
        main proc() i16 {
            value :: $(make())
            empty :: $(absent())
            match empty { .empty: {}, else: return 0 }
            return value.data.maybe? + (value.data.result catch 2)
        }
        """, b"42\n", None,
    ),
    "comptime_tagged_union_rejects_nonnull_pointer_payload_escape": (
        """
        Choice :: union(enum) { pointer @i16, empty void }
        make proc() Choice {
            value i16 :: 42
            return .pointer{&value}
        }
        main proc() i16 { return $(make()).pointer^ }
        """, b"", b"compile-time result cannot escape evaluation",
    ),
    "comptime_fallible_rejects_nonnull_success_pointer_escape": (
        """
        Error :: enum { failed }
        make proc() @i16 ! Error {
            value i16 :: 42
            return &value
        }
        main proc() i16 { return ($(make()) catch null)^ }
        """, b"", b"compile-time result cannot escape evaluation",
    ),
    "comptime_fallible_rejects_nonnull_error_pointer_escape": (
        """
        Failure :: struct { pointer @i16 }
        make proc() i16 ! Failure {
            value i16 :: 42
            return Failure { pointer = &value }
        }
        main proc() i16 { return $(make()) catch |err| err.pointer^ }
        """, b"", b"compile-time result cannot escape evaluation",
    ),
    "comptime_untagged_union_remains_an_escape_trap": (
        """
        Storage :: union { number i16, bits u16 }
        make proc() Storage { return Storage { number = 42 } }
        main proc() i16 { return $(make()).number }
        """, b"", b"compile-time result cannot escape evaluation",
    ),
    "comptime_float_bits_survive_aggregate_materialization": (
        """
        Floats :: struct { zero f64, nan f64 }
        make proc() Floats {
            bits u64 :: 9221120237041090626
            return Floats { zero = -0.0, nan = ptrcast!(@f64, &bits)^ }
        }
        main proc() bool {
            values :: $(make())
            return (
                ptrcast!(@u64, &values.zero)^ == 9223372036854775808
                and ptrcast!(@u64, &values.nan)^ == 9221120237041090626
            )
        }
        """, b"true\n", None,
    ),
    "comptime_calls_share_runtime_inference_loops_and_recursion": (
        """
        length proc() usize {
            result := 0
            while (result < 2) result = result + 1
            return result
        }
        factorial proc(count usize) usize {
            if (count == 0) return 1
            return count * factorial(count - 1)
        }
        sum proc(count usize) usize {
            result := 0
            for (0..count) |index| {
                if (index == 1) continue
                if (index == 4) break
                result = result + index
            }
            return result
        }
        identity proc($T type, value T) T { return value }
        Fixed proc($N usize) type { return struct { values [N]i16 } }
        main proc() i16 {
            first Fixed(identity(usize, length())) :: { values = [40, 2] }
            same Fixed(factorial(3) - sum(6) + 1) :: first
            return same.values[0] + same.values[1]
        }
        """, b"42\n", None,
    ),
    "comptime_call_string_result_owns_escaped_bytes": (
        r"""
        choose proc(take bool, text string) string {
            if (take) return text
            return "unused"
        }
        offset proc($text string, value i16) i16 { return value + i16(text.len) }
        main proc() i16 { return offset(choose(true, "a\"\\\x00é"), 36) }
        """, b"42\n", None,
    ),
    "comptime_calls_preserve_checked_narrow_intermediates": (
        """
        overflow proc(seed i8) i8 {
            result := seed
            result = result + 1
            return result - 1
        }
        answer proc($N i8) i16 { return 42 }
        main proc() i16 { return answer(overflow(127)) }
        """, b"", b"integer arithmetic overflow",
    ),
    "comptime_calls_preserve_unsigned_and_signed_boundaries": (
        """
        unsigned proc(value u64) u64 { return value }
        signed proc(value i64) i64 { return value }
        answer proc($high u64, $low i64) i16 {
            if (high == 18446744073709551615 and low == -9223372036854775808) return 42
            return 0
        }
        main proc() i16 {
            return answer(unsigned(18446744073709551615), signed(-9223372036854775808))
        }
        """, b"42\n", None,
    ),
    "comptime_call_cannot_promote_runtime_parameter": (
        """
        identity proc(value usize) usize { return value }
        answer proc($N usize) i16 { return 42 }
        runtime proc(value usize) i16 { return answer(identity(value)) }
        main proc() i16 { return runtime(2) }
        """, b"", b"generic argument must be a compile-time value",
    ),
    "comptime_call_cannot_read_mutable_global": (
        """
        state usize := 2
        read proc() usize { return state }
        answer proc($N usize) i16 { return 42 }
        main proc() i16 { return answer(read()) }
        """, b"", b"generic argument must be a compile-time value",
    ),
    "comptime_call_cannot_mutate_runtime_storage": (
        """
        state usize := 0
        write proc() usize {
            state = 2
            return state
        }
        answer proc($N usize) i16 { return 42 }
        main proc() i16 { return answer(write()) }
        """, b"", b"generic argument must be a compile-time value",
    ),
    "comptime_call_keeps_uninitialized_local_traps": (
        """
        premature proc() usize {
            before :: after
            after :: 2
            return before
        }
        answer proc($N usize) i16 { return 42 }
        main proc() i16 { return answer(premature()) }
        """, b"", b"local value was read before initialization",
    ),
    "comptime_call_bounds_empty_loops": (
        """
        forever proc() usize {
            while true {}
            return 2
        }
        answer proc($N usize) i16 { return 42 }
        main proc() i16 { return answer(forever()) }
        """, b"", b"compile-time execution exceeds",
    ),
    "comptime_call_bounds_recursive_frames": (
        """
        forever proc(value usize) usize { return forever(value) }
        answer proc($N usize) i16 { return 42 }
        main proc() i16 { return answer(forever(2)) }
        """, b"", b"compile-time execution exceeds",
    ),
    "comptime_call_foreign_execution_remains_a_source_trap": (
        """
        getpid c_func() c_int
        answer proc($N c_int) i16 { return 42 }
        main proc() i16 { return answer(getpid()) }
        """, b"", b"foreign function calls are unsupported",
    ),
    "specialization_parameterless_type_producer_is_cached": (
        """
        Token proc() type { return struct { value i16 } }
        main proc() i16 {
            first Token() :: { value = 42 }
            second Token() :: first
            return second.value
        }
        """, b"42\n", None,
    ),
    "specialization_explicit_identity_independent_widths": (
        """
        identity proc($T type, value T) T { return value }
        main proc() i16 {
            narrow u8 :: identity(u8, 40)
            wide i16 :: identity(i16, 2)
            return i16(narrow) + wide
        }
        """, b"42\n", None,
    ),
    "specialization_inferred_identity_independent_widths": (
        """
        identity proc($T type, value T) T { return value }
        main proc() i16 {
            narrow u8 :: 40
            wide i16 :: 2
            first u8 :: identity(narrow)
            second i16 :: identity(wide)
            return i16(first) + second
        }
        """, b"42\n", None,
    ),
    "specialization_cached_nominal_type_through_aliases": (
        """
        Box proc($T type) type { return struct { value T } }
        Element :: alias i16
        First :: Box(Element)
        Second :: alias Box(i16)
        read proc(value Box(i16)) i16 { return value.value }
        main proc() i16 {
            first First :: { value = 40 }
            second Second :: first
            repeated Box(i16) :: second
            return read(repeated) + 2
        }
        """, b"42\n", None,
    ),
    "specialization_different_type_arguments_remain_nominally_distinct": (
        """
        Box proc($T type) type { return struct { value T } }
        main proc() i16 {
            signed Box(i16) :: { value = 42 }
            unsigned Box(u16) :: signed
            return i16(unsigned.value)
        }
        """, b"", b"incompatible value type",
    ),
    "specialization_generated_record_and_mutable_array_storage": (
        """
        Fixed proc($T type, $N usize) type {
            return struct { values [N]mut T }
        }
        main proc() i16 {
            fixed Fixed(i16, 2) := { values = [40, 0] }
            fixed.values[1] = 2
            copy Fixed(i16, 2) :: fixed
            return copy.values[0] + copy.values[1]
        }
        """, b"42\n", None,
    ),
    "specialization_infers_generated_record_type_and_length": (
        """
        Fixed proc($T type, $N usize) type {
            return struct { values [N]mut T }
        }
        first proc($T type, $N usize, value @Fixed(T, N)) T {
            return value.values[0]
        }
        length proc($T type, $N usize, value @Fixed(T, N)) usize {
            return N
        }
        main proc() i16 {
            fixed Fixed(i16, 2) :: { values = [40, 1] }
            return first(&fixed) + i16(length(&fixed))
        }
        """, b"42\n", None,
    ),
    "specialization_infers_pointer_array_element_and_length": (
        """
        endpoints proc($T type, $N usize, values @[N]T) T {
            return values[0] + values[N - 1]
        }
        main proc() i16 {
            values [2]i16 :: [40, 2]
            return endpoints(&values)
        }
        """, b"42\n", None,
    ),
    "specialization_nested_runtime_calls": (
        """
        identity proc($T type, value T) T { return value }
        forward proc($T type, value T) T { return identity(value) }
        main proc() i16 { return forward(i16, 42) }
        """, b"42\n", None,
    ),
    "specialization_nested_generated_types": (
        """
        Box proc($T type) type { return struct { value T } }
        Wrapped proc($T type) type { return struct { box Box(T) } }
        main proc() i16 {
            wrapped Wrapped(i16) :: { box = Box(i16){ value = 42 } }
            return wrapped.box.value
        }
        """, b"42\n", None,
    ),
    "specialization_recursive_runtime_call_reuses_instance": (
        """
        repeat proc($T type, value T, remaining usize) T {
            if (remaining == 0) return value
            return repeat(value, remaining - 1)
        }
        main proc() i16 { return repeat(i16, 42, 3) }
        """, b"42\n", None,
    ),
    "specialization_immutable_scalar_arguments_and_expressions": (
        """
        base usize :: 1
        Fixed proc($T type, $N usize) type {
            return struct { values [N]mut T }
        }
        main proc() i16 {
            count usize :: base + 1
            fixed Fixed(i16, count) :: { values = [40, 2] }
            same Fixed(i16, 2) :: fixed
            return same.values[0] + same.values[1]
        }
        """, b"42\n", None,
    ),
    "specialization_rejects_mutable_compile_time_argument": (
        """
        value proc($N usize, result i16) i16 { return result }
        main proc() i16 {
            count usize := 2
            return value(count, 42)
        }
        """, b"", b"generic argument must be a compile-time value",
    ),
    "specialization_rejects_runtime_compile_time_argument": (
        """
        value proc($N usize, result i16) i16 { return result }
        runtime proc(count usize) i16 { return value(count, 42) }
        main proc() i16 { return runtime(2) }
        """, b"", b"generic argument must be a compile-time value",
    ),
    "specialization_boolean_value_parameters": (
        """
        choose proc($enabled bool, value i16) i16 {
            if (enabled) return value
            return value + 2
        }
        main proc() i16 {
            enabled :: true
            if (choose(enabled, 40) != 40) return 0
            return choose(false, 40)
        }
        """, b"42\n", None,
    ),
    "specialization_string_value_parameters": (
        """
        offset proc($text string, value i16) i16 {
            return value + i16(text.len)
        }
        main proc() i16 { return offset("ab", 40) }
        """, b"42\n", None,
    ),
    "specialization_callback_value_parameters": (
        """
        increment proc(value i16) i16 { return value + 2 }
        apply proc($T type, $callback proc(value T) T, value T) T {
            return callback(value)
        }
        main proc() i16 { return apply(i16, increment, 40) }
        """, b"42\n", None,
    ),
    "specialization_type_producer_conditional_returns": (
        """
        Choose proc($wide bool) type {
            if (wide) return struct { value i64 }
            return struct { value i16 }
        }
        main proc() i16 {
            narrow Choose(false) :: { value = 40 }
            wide Choose(true) :: { value = 2 }
            return narrow.value + i16(wide.value)
        }
        """, b"42\n", None,
    ),
    "specialization_type_producer_local_computed_storage_count": (
        """
        Fixed proc($T type, $N usize) type {
            count usize :: N + 1
            return struct { values [count]mut T }
        }
        main proc() i16 {
            fixed Fixed(i16, 1) := { values = [40, 0] }
            fixed.values[1] = 2
            return fixed.values[0] + fixed.values[1]
        }
        """, b"42\n", None,
    ),
    "specialization_rejects_out_of_range_unused_value_argument": (
        """
        ignore proc($N u8) i16 { return 42 }
        main proc() i16 { return ignore(256) }
        """, b"", b"numeric literal is outside",
    ),
    "specialization_rejects_inconsistent_inferred_type": (
        """
        first proc($T type, left T, right T) T { return left }
        main proc() i16 {
            left i16 :: 40
            right u8 :: 2
            return first(left, right)
        }
        """, b"", b"cannot infer consistent compile-time arguments",
    ),
    "specialization_recursive_generated_record_pointer": (
        """
        Node proc($T type) type {
            return struct { value T, next ?@Node(T) }
        }
        main proc() i16 {
            tail Node(i16) :: { value = 2, next = null }
            head Node(i16) :: { value = 40, next = &tail }
            if head.next |next| return head.value + next.value
            return 0
        }
        """, b"42\n", None,
    ),
    "specialization_phantom_type_arguments_remain_nominally_distinct": (
        """
        Phantom proc($T type) type { return struct { value i16 } }
        main proc() i16 {
            first Phantom(i16) :: { value = 42 }
            second Phantom(u8) :: first
            return second.value
        }
        """, b"", b"incompatible value type",
    ),
    "specialization_phantom_value_arguments_remain_nominally_distinct": (
        """
        Tagged proc($N usize) type { return struct { value i16 } }
        main proc() i16 {
            first Tagged(1) :: { value = 42 }
            second Tagged(2) :: first
            return second.value
        }
        """, b"", b"incompatible value type",
    ),
    "specialization_direct_array_type_return": (
        """
        Array proc($T type, $N usize) type { return [N]T }
        Row :: alias Array(i16, 2)
        main proc() i16 {
            row Row :: [40, 2]
            same Array(i16, 2) :: row
            return same[0] + same[1]
        }
        """, b"42\n", None,
    ),
    "specialization_value_parameter_preserves_narrow_arithmetic": (
        """
        add proc($N u8) i64 { return i64(N + 1) }
        main proc() i64 { return add(255) }
        """, b"", b"integer arithmetic overflow",
    ),
    "specialization_optional_value_null_capture_is_safe": (
        """
        read proc($value ?i16) i16 {
            if value |present| return present
            return 42
        }
        main proc() i16 { return read(null) }
        """, b"42\n", None,
    ),
    "specialization_optional_value_null_unwrap_traps": (
        """
        read proc($value ?i16) i16 { return value? }
        main proc() i16 { return read(null) }
        """, b"", b"null",
    ),
    "specialization_optional_value_present_literal_capture": (
        """
        read proc($value ?i16) i16 {
            if value |present| return present
            return 0
        }
        main proc() i16 { return read(42) }
        """, b"42\n", None,
    ),
    "specialization_optional_callback_unwrap_and_call": (
        """
        increment proc(value i16) i16 { return value + 2 }
        invoke proc($callback ?proc(value i16) i16, value i16) i16 {
            return callback?(value)
        }
        main proc() i16 { return invoke(increment, 40) }
        """, b"42\n", None,
    ),
    "specialization_optional_callback_capture_and_null_comparison": (
        """
        increment proc(value i16) i16 { return value + 2 }
        invoke proc($callback ?proc(value i16) i16, value i16) i16 {
            if callback |present| return present(value)
            if (callback == null) return value + 2
            return 0
        }
        main proc() i16 {
            if (invoke(increment, 40) != 42) return 0
            return invoke(null, 40)
        }
        """, b"42\n", None,
    ),
    "specialization_string_argument_preserves_slice_annotation": (
        """
        length proc($text []u8) usize { return text.len }
        main proc() i16 { return 40 + i16(length("ab")) }
        """, b"42\n", None,
    ),
    "specialization_cached_phantom_type_canonical_builtin_alias": (
        """
        Phantom proc($T type) type { return struct { value i16 } }
        Element :: alias i64
        main proc() i16 {
            first Phantom(int) :: { value = 42 }
            second Phantom(i64) :: first
            third Phantom(Element) :: second
            return third.value
        }
        """, b"42\n", None,
    ),
    "specialization_infers_type_through_type_producer_wrapper": (
        """
        Box proc($T type) type { return struct { value T } }
        Wrapped proc($T type) type { return Box(T) }
        read proc($T type, value @Wrapped(T)) T { return value.value }
        main proc() i16 {
            value Wrapped(i16) :: { value = 42 }
            return read(&value)
        }
        """, b"42\n", None,
    ),
    "specialization_string_cache_uses_decoded_value": (
        """
        Tag proc($text string) type { return struct { value i16 } }
        main proc() i16 {
            first Tag("a") :: { value = 42 }
            second Tag("\\x61") :: first
            return second.value
        }
        """, b"42\n", None,
    ),
    "specialization_rejects_invalid_compile_time_comparison": (
        """
        ignore proc($flag bool) i16 { return 42 }
        main proc() i16 { return ignore(1 == true) }
        """, b"", b"generic argument must be a compile-time value",
    ),
    "specialization_contextual_literal_after_typed_argument": (
        """
        first proc($T type, left T, right T) T { return left }
        main proc() i16 {
            value i16 :: 42
            result i16 :: first(value, 0)
            return result
        }
        """, b"42\n", None,
    ),
    "specialization_contextual_literal_before_typed_argument": (
        """
        first proc($T type, left T, right T) T { return left }
        main proc() i16 {
            value i16 :: 42
            result i16 :: first(0, value)
            return result + 42
        }
        """, b"42\n", None,
    ),
    "specialization_contextual_nested_integer_literal_expressions": (
        """
        first proc($T type, left T, right T) T { return left }
        main proc() i16 {
            value i16 :: 40
            left i16 :: first(-(1 + 1), value)
            right i16 :: first(value, (3 * 4) + 2)
            return left + right + 4
        }
        """, b"42\n", None,
    ),
    "specialization_contextual_literal_right_out_of_range": (
        """
        first proc($T type, left T, right T) T { return left }
        main proc() u8 {
            value u8 :: 42
            return first(value, 256)
        }
        """, b"", b"numeric literal is outside",
    ),
    "specialization_contextual_literal_left_out_of_range": (
        """
        first proc($T type, left T, right T) T { return left }
        main proc() u8 {
            value u8 :: 42
            return first(256, value)
        }
        """, b"", b"numeric literal is outside",
    ),
    "fallible_success_and_catch_short_circuit": (
        """
        Error :: enum { failed }
        good proc() i16 ! Error { return 42 }
        bad_fallback proc() i16 { value i16 :: 32767
        return value + 1 }
        main proc() i16 { return good() catch bad_fallback() }
        """, b"42\n", None,
    ),
    "fallible_try_composed_errors_and_capture": (
        """
        First :: enum { first }
        Second :: enum { second }
        leaf proc() i16 ! Second { return .second }
        middle proc() i16 ! (First | Second) { return try leaf() }
        main proc() i16 {
            return middle() catch |err| match err {
                .first: 0,
                .second: 42,
            }
        }
        """, b"42\n", None,
    ),
    "fallible_matched_error_residual_return": (
        """
        First :: enum { first }
        Second :: enum { second }
        broad proc() i16 ! (First | Second) { return .second }
        narrow proc() i16 ! Second {
            broad() catch |err| {
                match err {
                    .first: return 0,
                    else: return err,
                }
            }
            return 0
        }
        main proc() i16 { return narrow() catch 42 }
        """, b"42\n", None,
    ),
    "fallible_matched_tagged_error_residual_return": (
        """
        Missing :: enum { missing }
        Detail :: union(enum) { failed i16 }
        broad proc() i16 ! (Missing | Detail) { return .failed{42} }
        narrow proc() i16 ! Detail {
            broad() catch |err| {
                match err {
                    .missing: return 0,
                    else: return err,
                }
            }
            return 0
        }
        main proc() i16 {
            return narrow() catch |err| match err { .failed |code|: code }
        }
        """, b"42\n", None,
    ),
    "fallible_storage_arrays_and_callbacks": (
        """
        Error :: enum { failed }
        Callback :: alias @proc(value i16) i16 ! Error
        Box :: struct { callback Callback, result i16 ! Error }
        maybe proc(value i16) i16 ! Error {
            if (value == 0) return .failed
            return value
        }
        main proc() i16 {
            box Box :: { callback = maybe, result = 40 }
            values [2](i16 ! Error) :: [box.result, .failed]
            first :: values[0] catch 0
            second :: values[1] catch 2
            return box.callback(first + second) catch 0
        }
        """, b"42\n", None,
    ),
    "fallible_struct_error_payload": (
        """
        Failure :: struct { code i16 }
        fail proc() i16 ! Failure { return Failure { code = 42 } }
        main proc() i16 { return fail() catch |err| err.code }
        """, b"42\n", None,
    ),
    "fallible_tagged_error_payload": (
        """
        Error :: union(enum) { failed i16, empty void }
        fail proc() i16 ! Error { return .failed{42} }
        main proc() i16 {
            return fail() catch |err| match err {
                .failed |code|: code,
                .empty: 0,
            }
        }
        """, b"42\n", None,
    ),
    "fallible_composed_tagged_payload_propagation": (
        """
        Missing :: enum { missing }
        Detail :: union(enum) { failed i16, empty void }
        leaf proc() i16 ! Detail { return .failed{42} }
        forward proc() i16 ! (Missing | Detail) { return try leaf() }
        main proc() i16 {
            return forward() catch |err| match err {
                .missing: 0,
                .failed |code|: code,
                .empty: 0,
            }
        }
        """, b"42\n", None,
    ),
    "fallible_terminal_catch_block": (
        """
        Error :: enum { failed }
        fail proc() i16 ! Error { return .failed }
        main proc() i16 {
            return fail() catch |err| {
                if (err == Error.failed) return 42
                return 0
            }
        }
        """, b"42\n", None,
    ),
    "fallible_void_catch_and_fallthrough": (
        """
        Error :: enum { failed }
        good proc(value @mut i16) void ! Error { value^ = 40 }
        fail proc() void ! Error { return .failed }
        main proc() i16 {
            value i16 := 0
            good(&value) catch { value = 0 }
            fail() catch { value = value + 2 }
            return value
        }
        """, b"42\n", None,
    ),
    "cleanup_scope_lifo_and_live_values": (
        """
        main proc() int {
            value := 0
            {
                defer value = value * 10 + 2
                defer value = value * 10 + 4
            }
            return value
        }
        """, b"42\n", None,
    ),
    "cleanup_return_payload_before_mutation": (
        """
        Pair :: struct { a i16, b i16 }
        get proc() Pair {
            value Pair := { a = 40, b = 2 }
            defer value.a = 0
            return value
        }
        main proc() i16 { value :: get()
        return value.a + value.b }
        """, b"42\n", None,
    ),
    "cleanup_loop_exited_scopes": (
        """
        main proc() int {
            value := 0
            for 0..4 |i| {
                defer value = value + 1
                if (i == 0) continue
                {
                    defer value = value + 10
                    break
                }
            }
            return value
        }
        """, b"12\n", None,
    ),
    "cleanup_only_reached_registrations": (
        """
        write proc(value @mut i64) void {
            defer value^ = value^ + 40
            if (true) return
            defer value^ = 0
        }
        main proc() int { value := 2
        write(&value)
        return value }
        """, b"42\n", None,
    ),
    "cleanup_errdefer_capture_and_order": (
        """
        Error :: enum { failed }
        fail proc(value @mut i64) int ! Error {
            defer value^ = value^ * 10 + 2
            errdefer |err| if (err == Error.failed) value^ = value^ * 10 + 4
            return .failed
        }
        main proc() int { value := 0
        ignored :: fail(&value) catch 0
        return value + ignored }
        """, b"42\n", None,
    ),
    "cleanup_errdefer_skip_on_success_and_scope_exit": (
        """
        Error :: enum { failed }
        good proc(value @mut i64) int ! Error {
            errdefer value^ = 0
            { errdefer value^ = 0 }
            defer value^ = value^ + 2
            return 1
        }
        main proc() int { value := 40
        ignored :: good(&value) catch 0
        return value }
        """, b"42\n", None,
    ),
    "cleanup_try_propagation": (
        """
        Error :: enum { failed }
        leaf proc() void ! Error { return .failed }
        outer proc(value @mut i64) void ! Error {
            defer value^ = value^ + 2
            errdefer value^ = value^ + 40
            try leaf()
            value^ = 0
        }
        main proc() int { value := 0
        outer(&value) catch {}
        return value }
        """, b"42\n", None,
    ),
    "cleanup_direct_fallible_return_and_expired_scope": (
        """
        First :: enum { first }
        Second :: enum { second }
        leaf proc() i16 ! Second { return .second }
        forward proc(value @mut i64) i16 ! (First | Second) {
            { errdefer value^ = 0 }
            errdefer value^ = value^ + 40
            defer value^ = value^ + 2
            return leaf()
        }
        main proc() int { value := 0
        ignored :: forward(&value) catch 0
        return value }
        """, b"42\n", None,
    ),
    "fallible_aggregate_success_copy": (
        """
        Error :: enum { failed }
        Pair :: struct { left i16, right i16 }
        read proc() Pair ! Error {
            value Pair := { left = 40, right = 2 }
            defer value.left = 0
            return value
        }
        main proc() i16 {
            value :: read() catch Pair { left = 0, right = 0 }
            return value.left + value.right
        }
        """, b"42\n", None,
    ),
    "fallible_borrowed_slice_success": (
        """
        Error :: enum { failed }
        borrow proc(values []mut i16) []mut i16 ! Error { return values }
        main proc() i16 {
            values [2]mut i16 := [40, 2]
            view :: borrow(values[..]) catch &[]
            view[0] = view[0] + view[1]
            return values[0]
        }
        """, b"42\n", None,
    ),
    "fallible_optional_pointer_success": (
        """
        Error :: enum { failed }
        borrow proc(value @mut i16) ?@mut i16 ! Error { return value }
        main proc() i16 {
            value i16 := 40
            pointer :: borrow(&value) catch null
            if pointer |present| present^ = present^ + 2
            return value
        }
        """, b"42\n", None,
    ),
    "fallible_trap_is_not_caught": (
        """
        Error :: enum { failed }
        bad proc() int ! Error { value :: [1]
        return value[2] }
        main proc() int { return bad() catch 42 }
        """, b"", b"index out of bounds",
    ),
    "fallible_try_requires_channel": (
        "main proc() int { return try 42 }", b"", b"",
    ),
    "fallible_incompatible_propagation": (
        """
        First :: enum { first }
        Second :: enum { second }
        leaf proc() int ! First { return .first }
        bad proc() int ! Second { return try leaf() }
        main proc() int { return bad() catch 42 }
        """, b"", b"",
    ),
    "cleanup_return_rejected": (
        "main proc() int { defer return 0\nreturn 42 }", b"", b"",
    ),
    "cleanup_errdefer_requires_fallible_function": (
        "main proc() int { errdefer main()\nreturn 42 }", b"", b"",
    ),
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

# Keep malformed schemas grouped: each must fail before generated storage is used.
for name, expression, diagnostic in (
    (
        "argument_count",
        'struct_type!(.auto, ["value"], [i16])',
        b"argument count",
    ),
    (
        "invalid_layout",
        'struct_type!(.packed, ["value"], [i16], [null])',
        b"layout",
    ),
    (
        "invalid_field_name",
        'struct_type!(.auto, ["9value"], [i16], [null])',
        b"generated field name",
    ),
    (
        "duplicate_field_name",
        'struct_type!(.auto, ["value", "value"], [i16, i16], [null, null])',
        b"specified more than once",
    ),
    (
        "named_record_collection",
        'struct_type!(.auto, {name = "value"}, [i16], [null])',
        b"compile-time value",
    ),
    (
        "unequal_collection_lengths",
        'struct_type!(.auto, ["value", "bias"], [i16], [null, 2])',
        b"length does not match",
    ),
    (
        "non_type_field",
        'struct_type!(.auto, ["value"], [42], [null])',
        b"record fields require a concrete value type",
    ),
    (
        "compile_time_only_field",
        'struct_type!(.auto, ["value"], [type], [null])',
        b"record fields require a concrete value type",
    ),
    (
        "wrong_typed_default",
        'struct_type!(.auto, ["value"], [i16], [u8(2)])',
        b"incompatible value type",
    ),
    (
        "out_of_range_default",
        'struct_type!(.auto, ["value"], [i8], [128])',
        b"outside its runtime type",
    ),
    (
        "empty_c_layout",
        'struct_type!(.c, [], [], [])',
        b"C-layout",
    ),
    (
        "non_c_field",
        'struct_type!(.c, ["value"], [string], [null])',
        b"C-layout",
    ),
    (
        "non_c_nested_record",
        'struct_type!(.c, ["value"], [struct { value i16 }], [null])',
        b"C-layout",
    ),
):
    CASES[f"struct_type_rejects_{name}"] = (
        f"""
        Generated proc() type {{ return {expression} }}
        main proc() i16 {{
            value Generated() :: {{}}
            return 42
        }}
        """, b"", diagnostic,
    )

generated_field_count = 4097
CASES["struct_type_bounds_generated_field_construction"] = (
    "Record :: struct_type!(.auto, ["
    + ", ".join(f'"field_{index}"' for index in range(generated_field_count))
    + "], [" + ", ".join(["i16"] * generated_field_count)
    + "], [" + ", ".join(["null"] * generated_field_count)
    + "])\nmain proc() i16 { value Record :: {}\nreturn 42 }\n",
    b"", b"compile-time execution exceeds",
)

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
    types.write_text(textwrap.dedent("""
        Generated proc(bias i16) type {
            return struct_type!(.auto, ["value", "bias"], [i16, i16], [null, bias])
        }
    """).strip() + "\n")
    (library / "functions.hon").write_text(
        "identity proc(value Generated(2)) Generated(2) { return value }\n"
    )
    (app / "main.hon").write_text(textwrap.dedent("""
        lib :: import "../library"
        Selected :: alias lib.Generated(2)
        main proc() i16 {
            value Selected :: { value = 40 }
            same lib.Generated(2) :: lib.identity(value)
            return same.value + same.bias
        }
    """).strip() + "\n")
    check(app, executable, (b"42\n", None))
    types.write_text(textwrap.dedent("""
        Generated proc(bias i16) type {
            return struct_type!(.auto, ["bad-name"], [i16], [null])
        }
    """).strip() + "\n")
    check(app, executable, (b"", b"generated field name"))
    diagnosed = run(str(HONEY), "run", str(app))
    assert diagnosed.stderr.startswith(f"{types.resolve()}:2:".encode()), outcome(diagnosed)
    return 6


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


def check_specialization_packages(temporary, executable):
    project = temporary / "specialization"
    app, library = project / "app", project / "library"
    app.mkdir(parents=True)
    library.mkdir()
    (library / "types.hon").write_text(textwrap.dedent("""
        Box proc(T type) type {
            return struct { value match true { true: T, false: i8(128) } }
        }
        Selected proc(wide bool) type {
            return match wide { true: i16, false: u8 }
        }
        Fixed proc($T type, N usize) type {
            Element := i8
            length usize := 0
            while (length < N) {
                Element = T
                length = length + 1
            }
            return struct { values [match length { 0: usize(0), else: length }]mut Element }
        }
        Choice :: union(enum) { count usize, empty void }
        Captured proc(T type, choice Choice) type {
            return match choice {
                .count |length|: struct { values [length]T },
                .empty: bool,
            }
        }
        TypeRecord proc(T type) type {
            config struct { child type, count usize } := { child = i8, count = 1 }
            original :: config
            config.child = T
            config.count = 2
            if (original.child != i8 or original.count != 1) return bool
            return struct { values [config.count]config.child }
        }
    """).strip() + "\n")
    (library / "functions.hon").write_text(textwrap.dedent("""
        identity proc($T type, value T) T { return value }
        first proc($T type, $N usize, value @Fixed(T, N)) T {
            return value.values[0]
        }
        count proc() usize {
            result := 0
            while (result < 2) result = result + 1
            return result
        }
        selected proc($choice Choice, value i16) i16 {
            match choice {
                .count |length|: {
                    return match length { 2: value + i16(length), else: value.missing }
                }
                .empty: return value.missing,
            }
        }
    """).strip() + "\n")
    source = app / "main.hon"
    source.write_text(textwrap.dedent("""
        lib :: import "../library"
        Element :: alias lib.Selected(true)
        ImportedBox :: alias lib.Box(Element)
        main proc() i16 {
            imported ImportedBox :: { value = 40 }
            copy lib.Box(i16) :: imported
            captured lib.Captured(ImportedBox, .count{2}) :: { values = [imported, copy] }
            fixed lib.Fixed(i16, lib.count()) :: { values = [2, 0] }
            return captured.values[0].value + lib.identity(lib.first(&fixed))
        }
    """).strip() + "\n")
    check(app, executable, (b"42\n", None))
    source.write_text(textwrap.dedent("""
        lib :: import "../library"
        main proc() i16 {
            value lib.Captured(i16, .count{2}) :: { values = [20, 20] }
            return lib.selected(.count{2}, value.values[0] + value.values[1])
        }
    """).strip() + "\n")
    check(app, executable, (b"42\n", None))
    source.write_text(textwrap.dedent("""
        lib :: import "../library"
        main proc() i16 {
            value lib.TypeRecord(i16) :: { values = [19, 23] }
            same lib.TypeRecord(i16) :: value
            return same.values[0] + same.values[1]
        }
    """).strip() + "\n")
    check(app, executable, (b"42\n", None))
    source.write_text(textwrap.dedent("""
        lib :: import "../library"
        Box proc($T type) type { return struct { value T } }
        main proc() i16 {
            imported lib.Box(i16) :: { value = 42 }
            local Box(i16) :: imported
            return local.value
        }
    """).strip() + "\n")
    check(app, executable, (b"", b"incompatible value type"))
    sizing = library / "sizing.hon"
    sizing.write_text(textwrap.dedent("""
        size proc() usize {
            high usize :: 18446744073709551615
            return high + 1
        }
    """).strip() + "\n")
    source.write_text(textwrap.dedent("""
        lib :: import "../library"
        main proc() i16 {
            if (false) {
                fixed lib.Fixed(i16, lib.size()) :: { values = [40, 2] }
                return fixed.values[0]
            }
            return 42
        }
    """).strip() + "\n")
    check(app, executable, (b"42\n", b"integer arithmetic overflow"))
    interpreted = run(str(HONEY), "run", str(app))
    assert interpreted.stderr.startswith(f"{sizing.resolve()}:3:12: ".encode()), outcome(interpreted)
    assert len(interpreted.stderr.splitlines()) == 1, outcome(interpreted)
    sizing.write_text(textwrap.dedent("""
        Bad proc() type {
            return struct { value match true { true: i16, true: i8, else: bool } }
        }
    """).strip() + "\n")
    source.write_text(textwrap.dedent("""
        lib :: import "../library"
        main proc() lib.Bad() { return 42 }
    """).strip() + "\n")
    check(app, executable, (b"", b"duplicate or overlapping match pattern"))
    interpreted = run(str(HONEY), "run", str(app))
    assert interpreted.stderr.startswith(f"{sizing.resolve()}:2:".encode()), outcome(interpreted)
    assert len(interpreted.stderr.splitlines()) == 1, outcome(interpreted)
    sizing.write_text(textwrap.dedent("""
        SizingChoice :: union(enum) { small i8 }
        Bad proc() type {
            input SizingChoice :: .small{127}
            match input {
                .small |value|: {
                    next :: value + 1
                    return [usize(next)]i16
                }
            }
        }
    """).strip() + "\n")
    check(app, executable, (b"", b"integer arithmetic overflow"))
    interpreted = run(str(HONEY), "run", str(app))
    assert interpreted.stderr.startswith(f"{sizing.resolve()}:6:".encode()), outcome(interpreted)
    assert len(interpreted.stderr.splitlines()) == 1, outcome(interpreted)
    sizing.write_text(textwrap.dedent("""
        Optional proc(T type, size ?usize) type {
            if size |length| { return struct { values [length]T } }
            else { return bool }
        }
    """).strip() + "\n")
    source.write_text(textwrap.dedent("""
        lib :: import "../library"
        main proc() i16 {
            value lib.Optional(i16, 2) :: { values = [40, 2] }
            return value.values[0] + value.values[1]
        }
    """).strip() + "\n")
    check(app, executable, (b"42\n", None))
    sizing.write_text(textwrap.dedent("""
        narrow proc() ?i8 { return 127 }
        Optional proc(T type, size ?usize) type {
            if narrow() |value| {
                next :: value + 1
                return [usize(next)]T
            }
            return T
        }
    """).strip() + "\n")
    check(app, executable, (b"", b"integer arithmetic overflow"))
    interpreted = run(str(HONEY), "run", str(app))
    assert interpreted.stderr.startswith(f"{sizing.resolve()}:4:".encode()), outcome(interpreted)
    assert len(interpreted.stderr.splitlines()) == 1, outcome(interpreted)
    sizing.write_text(textwrap.dedent("""
        Updated proc() type {
            config struct { counts [2]mut usize, bias i16 } := { counts = [0, 0], bias = 40 }
            config.counts[1] = 2
            config.bias = config.bias + i16(config.counts[1])
            return struct { values [config.counts[1]]i16, bias i16 = config.bias }
        }
    """).strip() + "\n")
    source.write_text(textwrap.dedent("""
        lib :: import "../library"
        main proc() i16 {
            value lib.Updated() :: { values = [0, 0] }
            return value.bias
        }
    """).strip() + "\n")
    check(app, executable, (b"42\n", None))
    sizing.write_text(textwrap.dedent("""
        Updated proc() type {
            counts [1]mut usize := [1]
            counts[1] = 2
            return [counts[0]]i16
        }
    """).strip() + "\n")
    source.write_text('lib :: import "../library"\nmain proc() lib.Updated() { return [0] }\n')
    check(app, executable, (b"", b"index out of bounds"))
    interpreted = run(str(HONEY), "run", str(app))
    assert interpreted.stderr.startswith(f"{sizing.resolve()}:3:".encode()), outcome(interpreted)
    assert len(interpreted.stderr.splitlines()) == 1, outcome(interpreted)
    sizing.write_text(textwrap.dedent("""
        Collected proc() type {
            values [2]usize :: [1, 1]
            length usize := 0
            for values |value, index| { length = length + value + index }
            return [length]i16
        }
    """).strip() + "\n")
    source.write_text(textwrap.dedent("""
        lib :: import "../library"
        main proc() i16 {
            values lib.Collected() :: [19, 21, 2]
            same lib.Collected() :: values
            total i16 := 0
            for same |value| { total = total + value }
            return total
        }
    """).strip() + "\n")
    check(app, executable, (b"42\n", None))
    sizing.write_text(textwrap.dedent("""
        Collected proc() type {
            values [1]i8 :: [127]
            for values |value| { next :: value + 1 }
            return i16
        }
    """).strip() + "\n")
    source.write_text('lib :: import "../library"\nmain proc() lib.Collected() { return 0 }\n')
    check(app, executable, (b"", b"integer arithmetic overflow"))
    interpreted = run(str(HONEY), "run", str(app))
    assert interpreted.stderr.startswith(f"{sizing.resolve()}:3:".encode()), outcome(interpreted)
    assert len(interpreted.stderr.splitlines()) == 1, outcome(interpreted)
    sizing.write_text(textwrap.dedent("""
        TypeArray proc() type {
            types [2]mut type := [i8, bool]
            for types |T, index| {
                if (index == 0) types[1] = i16
                if (index == 1) return struct { values [2]T, bias T = 2 }
            }
            return bool
        }
    """).strip() + "\n")
    source.write_text(textwrap.dedent("""
        lib :: import "../library"
        main proc() i16 {
            value lib.TypeArray() :: { values = [19, 21] }
            same lib.TypeArray() :: value
            return same.values[0] + same.values[1] + same.bias
        }
    """).strip() + "\n")
    check(app, executable, (b"42\n", None))
    sizing.write_text(textwrap.dedent("""
        TypeArray proc() type {
            types [1]mut type := [i16]
            types[1] = bool
            return types[0]
        }
    """).strip() + "\n")
    source.write_text('lib :: import "../library"\nmain proc() lib.TypeArray() { return 42 }\n')
    check(app, executable, (b"", b"index out of bounds"))
    interpreted = run(str(HONEY), "run", str(app))
    assert interpreted.stderr.startswith(f"{sizing.resolve()}:3:".encode()), outcome(interpreted)
    assert len(interpreted.stderr.splitlines()) == 1, outcome(interpreted)
    sizing.write_text(textwrap.dedent("""
        row proc($N usize, types [N]type) [N]type { return types }
        rows proc() [1][2]type { return [row(2, [i16, bool])] }
        FromTypes proc(types [2]type) type {
            return struct { value types[0], enabled types[1] = true }
        }
        add proc($types [2]type, value types[0]) types[0] { return value + 2 }
    """).strip() + "\n")
    source.write_text(textwrap.dedent("""
        lib :: import "../library"
        Imported :: alias lib.FromTypes(lib.rows()[0])
        read proc(value lib.rows()[0][0]) i16 {
            return lib.add(lib.row(2, [i16, bool]), value)
        }
        main proc() i16 {
            value Imported :: { value = 40 }
            same lib.FromTypes([i16, bool]) :: value
            if (same.enabled) return read(same.value)
            return 0
        }
    """).strip() + "\n")
    check(app, executable, (b"42\n", None))
    sizing.write_text(textwrap.dedent("""
        row proc() [1]type { return [i16] }
        bad_rows proc() [1][1]type { return [[row()[1]]] }
    """).strip() + "\n")
    source.write_text('lib :: import "../library"\nmain proc() lib.bad_rows()[0][0] { return 42 }\n')
    check(app, executable, (b"", b"index out of bounds"))
    interpreted = run(str(HONEY), "run", str(app))
    assert interpreted.stderr.startswith(f"{sizing.resolve()}:2:".encode()), outcome(interpreted)
    assert len(interpreted.stderr.splitlines()) == 1, outcome(interpreted)
    sizing.write_text(textwrap.dedent("""
        BadRecord proc() type {
            config :: { child = i16, count = i8(127) }
            next :: config.count + 1
            return [usize(next)]config.child
        }
    """).strip() + "\n")
    source.write_text('lib :: import "../library"\nmain proc() lib.BadRecord() { return 42 }\n')
    check(app, executable, (b"", b"integer arithmetic overflow"))
    interpreted = run(str(HONEY), "run", str(app))
    assert interpreted.stderr.startswith(f"{sizing.resolve()}:3:".encode()), outcome(interpreted)
    assert len(interpreted.stderr.splitlines()) == 1, outcome(interpreted)
    sizing.write_text(textwrap.dedent("""
        Config :: struct { child type, counts [2]mut usize, bias i16 = 2 }
        record_config proc() Config { return { child = i16, counts = [1, 2] } }
        FromRecord proc(input Config) type {
            return struct { values [input.counts[1]]input.child, bias i16 = input.bias }
        }
        add proc($input Config, value input.child) input.child { return value + input.bias }
        NominalConfig :: struct { child type }
        nominal_config proc() NominalConfig {
            return { child = struct { value i16 } }
        }
        FromNominal proc(input NominalConfig) type { return struct { value input.child } }
    """).strip() + "\n")
    source.write_text(textwrap.dedent("""
        lib :: import "../library"
        Imported :: alias lib.FromRecord(lib.record_config())
        main proc() i16 {
            value Imported :: { values = [19, 21] }
            same lib.FromRecord({ child = i16, counts = [1, 2] }) :: value
            nominal lib.FromNominal(lib.nominal_config()) :: { value = { value = 40 } }
            same_nominal lib.FromNominal(lib.nominal_config()) :: nominal
            if (same_nominal.value.value != 40) return 0
            return lib.add(lib.record_config(), same.values[0] + same.values[1])
        }
    """).strip() + "\n")
    check(app, executable, (b"42\n", None))
    sizing.write_text(textwrap.dedent("""
        Config :: struct { child type, rows [1]mut [1]mut type }
        record_config proc() Config { return { child = i16, rows = [[i16]] } }
        bad_config proc() Config {
            copied := record_config()
            copied.child = copied.rows[0][1]
            return copied
        }
    """).strip() + "\n")
    source.write_text('lib :: import "../library"\nmain proc() lib.bad_config().child { return 42 }\n')
    check(app, executable, (b"", b"index out of bounds"))
    interpreted = run(str(HONEY), "run", str(app))
    assert interpreted.stderr.startswith(f"{sizing.resolve()}:5:".encode()), outcome(interpreted)
    assert len(interpreted.stderr.splitlines()) == 1, outcome(interpreted)
    return 20


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



def check_fallible_packages_and_entry(temporary, executable):
    project = temporary / "fallible_packages"
    app = project / "app"
    library = project / "library"
    app.mkdir(parents=True)
    library.mkdir()
    dependency = library / "error.hon"
    dependency.write_text(
        "Error :: enum { unavailable }\n"
        "read proc() i16 ! Error { return .unavailable }\n"
    )
    source = app / "main.hon"
    source.write_text(textwrap.dedent("""
        library :: import "../library"
        LocalError :: enum { local }
        read proc() i16 ! (LocalError | library.Error) {
            return try library.read()
        }
        main proc() i16 {
            return read() catch |err| match err {
                .local: 0,
                .unavailable: 42,
            }
        }
    """).strip() + "\n")
    check(app, executable, (b"42\n", None))
    count = 1
    for result, body, expected in (
        ("i16", "return 42", (0, b"42\n", b"")),
        ("void", "return", (0, b"", b"")),
        ("void", "", (0, b"", b"")),
        ("i16", "return .failed", (1, b"", b"")),
        ("void", "return .failed", (1, b"", b"")),
    ):
        source.write_text(
            f"Error :: enum {{ failed }}\n"
            f"main proc() {result} ! Error {{ {body} }}\n"
        )
        assert outcome(run(str(HONEY), "run", str(source))) == expected
        for optimization in ("-O0", "-O2"):
            built = run(str(HONEY), "build", str(source), "-o", str(executable), optimization)
            assert outcome(built) == (0, b"", b""), outcome(built)
            assert outcome(run(str(executable))) == expected, optimization
            executable.unlink()
        count += 1
    return count


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
        count += check_specialization_packages(temporary, executable)
        count += check_fallible_packages_and_entry(temporary, executable)
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
