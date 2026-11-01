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
    "runtime_helper_guards_preserve_borrowed_effects": (
        """
        predicate proc(calls @mut i16, value i16) bool {
            calls^ += 1
            return value == 42
        }
        choose proc($T type, calls @mut i16, value T) i16 {
            if predicate(calls, 42) { return i16(value) }
            return -1
        }
        main proc() i16 {
            calls := i16(0)
            first :: choose(&calls, i16(19))
            second :: choose(&calls, i16(23))
            if (calls != 2) return -2
            return first + second
        }
        """, b"42\n", None,
    ),
    "demanded_constant_calls_execute_mutable_borrows_and_only_reached_errors": (
        """
        fill proc(values []mut usize, seed usize) void {
            for values |_, index| { values[index] = seed + index }
        }
        produce proc($N usize, $seed usize) [N]mut usize {
            values [N]mut usize := undefined
            fill(values[..], seed)
            if (values[0] != seed) compile_error!("unexpected initialization")
            return values
        }
        main proc() usize {
            total := usize(0)
            inline for produce(3, 13) |value| { total += value }
            return total
        }
        """, b"42\n", None,
    ),
    "type_helper_guards_infer_constant_execution_without_explicit_syntax": (
        """
        scalar proc($T type) bool {
            return T == i16 or T == Id
        }
        Id :: distinct i16
        identity proc($T type, value T) T {
            if scalar(T) { return value }
            else { compile_error!("expected scalar") }
        }
        main proc() Id { return identity(Id(42)) }
        """, b"42\n", None,
    ),
    "pointer_array_slice_coercions_borrow_storage_and_preserve_optional_presence": (
        """
        borrow proc(value @mut [2]mut i16) []mut i16 { return value }
        optional proc(value ?@[2]i16) ?[]i16 { return value }
        c_string proc(value ?string) ?*u8 { return value }
        main proc() i16 {
            values [2]mut i16 := [19, 23]
            view :: borrow(&values)
            view[0] = 20
            if (values[0] != 20 or view.len != 2) return -1
            immutable [2]i16 :: [19, 23]
            present :: optional(&immutable)
            if (present?.len != 2 or present?[0] != 19 or optional(null) != null) return -2
            text :: c_string("ok")?
            if (text[0] != 111 or text[2] != 0 or c_string(null) != null) return -3
            return present?[0] + present?[1]
        }
        """, b"42\n", None,
    ),
    "pointer_array_slice_coercion_rejects_readonly_reversal": (
        """
        borrow proc(value @[2]mut i16) []mut i16 { return value }
        main proc() i16 { values [2]mut i16 := [19, 23]\nreturn borrow(&values)[0] }
        """, b"", b"return does not match the function result type",
    ),
    "pointer_array_slice_coercion_rejects_nominal_element_mismatch": (
        """
        Id :: distinct i16
        borrow proc(value @[2]Id) []i16 { return value }
        main proc() i16 { values [2]Id :: [Id(19), Id(23)]\nreturn borrow(&values)[0] }
        """, b"", b"return does not match the function result type",
    ),
    "partial_comptime_prefix_infers_remaining_types_and_reuses_nominal_results": (
        """
        Box proc($N usize, $T type) type { return struct { values [N]T } }
        wrap proc($N usize, $T type, value T) Box(N, T) {
            if (N != 2) compile_error!("expected two elements")
            return { values = [value, value] }
        }
        main proc() i16 {
            partial :: wrap(usize(2), i16(21))
            explicit Box(2, i16) :: wrap(usize(2), i16, i16(21))
            if (partial.values[1] != explicit.values[1]) return -1
            return partial.values[0] + partial.values[1]
        }
        """, b"42\n", None,
    ),
    "partial_comptime_prefix_checks_explicit_value_types": (
        """
        add proc($bias i8, $T type, value T) T { return value + T(bias) }
        main proc() i16 { return add(i16(2), i16(40)) }
        """, b"42\n", b"incompatible value type",
    ),
    "partial_comptime_prefix_still_requires_unresolved_parameters": (
        """
        add proc($bias i8, $T type, value i16) i16 { return value + i16(bias) }
        main proc() i16 { return add(i8(2), i16(40)) }
        """, b"", b"cannot infer",
    ),
    "pure_type_builtins_select_generic_guards_before_compile_errors": (
        """
        checked proc($T type, value T) T {
            if (minval!(T) == 0 and maxval!(T) == 255 and sizeof!(T) == 1 and alignof!(T) == 1) {
                return value
            } else {
                compile_error!("expected an unsigned byte")
            }
        }
        main proc() u8 { return checked(u8(42)) }
        """, b"42\n", None,
    ),
    "remainder_preserves_exact_signed_unsigned_float_and_nominal_values": (
        """
        Id :: distinct i16
        remainder proc(left, right Id) Id { return rem!(left, right) }
        signs proc(left, right i64) i64 { return rem!(left, right) }
        real proc(left, right f64) f64 { return rem!(left, right) }
        constant proc() i16 { return rem!(i16(-42), 23) }
        main proc() i16 {
            if (remainder(Id(-42), Id(23)) != Id(-19)) return -1
            if (signs(minval!(i64), -1) != 0 or signs(minval!(i64), 10) != -8) return -2
            if (signs(5, -3) != 2 or signs(-5, 3) != -2) return -3
            if (rem!(maxval!(u64), 10) != 5) return -4
            if (real(-5.5, 2.0) != -1.5 or real(5.5, -2.0) != 1.5) return -5
            if ($(constant()) != -19) return -6
            return 42
        }
        """, b"42\n", None,
    ),
    "remainder_traps_on_runtime_zero": (
        "calculate proc(zero i16) i16 { return rem!(i16(42), zero) }\n"
        "main proc() i16 { return calculate(0) }", b"", b"division by zero",
    ),
    "float_remainder_traps_on_runtime_negative_zero": (
        "calculate proc(zero f64) f64 { return rem!(f64(42), zero) }\n"
        "main proc() f64 { return calculate(-0.0) }", b"", b"division by zero",
    ),
    "remainder_rejects_mismatched_nominal_types": (
        "Id :: distinct i16\nmain proc() Id { return rem!(Id(42), i16(23)) }",
        b"", b"operator requires compatible operand types",
    ),
    "character_literals_preserve_codepoints_escapes_and_constant_values": (
        r"""
        constant proc() u32 { return '🙂' }
        main proc() [8]u32 {
            return ['A', 'é', $(constant()), '\n', '\r', '\t', '\\', '\'']
        }
        """, b"[65, 233, 128578, 10, 13, 9, 92, 39]\n", None,
    ),
    "compiletime_only_main_cannot_select_another_runtime_function": (
        """
        fallback proc() i16 { return -99 }
        main proc() i16 {
            types := [i16, bool]
            return 42
        }
        """, b"", b"main must be a runtime function with no parameters",
    ),
    "inferred_metadata_locals_preserve_owned_copies_and_updates": (
        """
        Config :: struct { children [2]mut ?type, count usize }
        compute proc(count usize) i16 {
            config := Config { children = [i8, null], count = 0 }
            saved :: config
            while (config.count < count) {
                config.children[0] = i16
                config.children[1] = config.children[0]
                config.count += 1
            }
            types := [i8, bool]
            before :: types
            types = [i16, bool]
            tuple := { types[0], config.count }
            tuple.0 = i8
            if (saved.children[0]? != i8 or saved.count != 0 or before[0] != i8) return -1
            if (tuple.0 != i8 or tuple.1 != 2) return -2
            for config.children |child| { if child |T| { if (T != i16) return -3 } }
            Element :: types[0]
            values [count]Element :: [19, 23]
            return values[0] + values[1]
        }
        main proc() i16 { return compute(2) }
        """, b"42\n", None,
    ),
    "inferred_type_locals_recurse_and_preserve_cleanup": (
        """
        Error :: enum { failed }
        count proc(depth usize) i16 {
            T := i8
            T = i16
            defer if (T != i16) unreachable
            if (depth == 0) return 21
            return count(depth - 1) + 21
        }
        fail proc() i16 ! Error {
            T := ?i16
            T = ?i8
            errdefer if (T != ?i8) unreachable
            return .failed
        }
        main proc() i16 { return fail() catch { yield count(1) } }
        """, b"42\n", None,
    ),
    "inferred_metadata_call_results_and_checked_projections": (
        """
        Config :: struct { child type, count usize }
        Schema :: alias Config
        create proc() Schema { return { child = i8, count = 2 } }
        children proc() [2]type { return [i8, bool] }
        optional proc() ?type { return i8 }
        pair proc() struct { type, usize } { return {i8, 2} }
        field_type proc(value i16) i16 {
            T := create().child
            T = i16
            result T :: value
            return result
        }
        indexed_type proc(value i16) i16 {
            T := children()[0]
            T = i16
            result T :: value
            return result
        }
        unwrapped_type proc(value i16) i16 {
            T := optional()?
            T = i16
            result T :: value
            return result
        }
        tuple_type proc(value i16) i16 {
            T := pair().0
            T = i16
            result T :: value
            return result
        }
        compute proc() i16 {
            config := create()
            saved :: config
            config.child = i16
            if (saved.child != i8) return -1
            return field_type(19) + indexed_type(19) + unwrapped_type(2) + tuple_type(2)
        }
        main proc() i16 { return compute() }
        """, b"42\n", None,
    ),
    "inferred_metadata_value_blocks_matches_and_fallible_calls": (
        """
        Error :: enum { failed }
        Config :: struct { child type, count usize }
        create proc() Config ! Error { return { child = i8, count = 2 } }
        compute proc(flag bool) i16 ! Error {
            errdefer unreachable
            config := try create()
            saved :: config
            config.child = i16
            fields := { yield [i8, bool] }
            selected := if (flag) { yield { child = i16, count = config.count } }
                else { yield { child = i8, count = usize(1) } }
            if (saved.child != i8 or fields[1] != bool or selected.child != i16) return -1
            Element :: selected.child
            values [selected.count]Element :: [19, 23]
            return values[0] + values[1]
        }
        main proc() i16 { return compute(true) catch -2 }
        """, b"42\n", None,
    ),
    "inferred_metadata_locals_require_constant_inputs": (
        """
        Config :: struct { child type, count usize }
        count proc(value usize) usize {
            config := Config { child = i16, count = value }
            return config.count
        }
        runtime proc(value usize) usize { return count(value) }
        main proc() usize { return runtime(42) }
        """, b"", b"generic argument must be a compile-time value",
    ),
    "inferred_metadata_arrays_keep_readonly_elements": (
        """
        compute proc() usize {
            types := [i8, bool]
            types[0] = i16
            return types.len
        }
        main proc() usize { return compute() }
        """, b"", b"indexed assignment requires a writable array element",
    ),
    "immutable_type_aliases_and_scalar_metadata_projections_stay_runtime": (
        """
        Config :: struct { child type, count usize }
        create proc() Config { return { child = i16, count = 2 } }
        Child proc() type { return i16 }
        add proc(value i16) i16 {
            Local :: alias i16
            Other :: alias Child()
            amount := create().count
            argument Local :: value
            result Other :: argument + i16(amount)
            return result
        }
        forward proc(value i16) i16 { return add(value) }
        main proc() i16 { return forward(40) }
        """, b"42\n", None,
    ),
    "explicit_metadata_locals_compute_without_metadata_inputs": (
        """
        Config :: struct { children [2]mut ?type, count usize }
        compute proc(count usize) i16 {
            config Config := undefined
            config.children = [i8, null]
            config.count = 0
            saved :: config
            while (config.count < count) {
                config.children[0] = i16
                config.children[1] = config.children[0]
                config.count = config.count + 1
            }
            if (saved.children[0]? != i8 or saved.count != 0) return -1
            for config.children |child| {
                if child |T| { if (T != i16) return -2 }
            }
            Element :: config.children[0]?
            values [config.count]Element :: [19, 23]
            return values[0] + values[1]
        }
        main proc() i16 { return $(compute(2)) }
        """, b"42\n", None,
    ),
    "explicit_type_locals_recurse_and_preserve_cleanup": (
        """
        Error :: enum { failed }
        count proc(depth usize) i16 {
            T type := i8
            T = i16
            defer if (T != i16) unreachable
            if (depth == 0) return 21
            return count(depth - 1) + 21
        }
        fail proc() i16 ! Error {
            T ?type := i16
            errdefer if (T? != i16) unreachable
            return .failed
        }
        main proc() i16 {
            return fail() catch { yield count(1) }
        }
        """, b"42\n", None,
    ),
    "explicit_metadata_locals_require_constant_runtime_inputs": (
        """
        count proc(value usize) usize {
            T type :: i16
            return value
        }
        runtime proc(value usize) usize { return count(value) }
        main proc() usize { return runtime(42) }
        """, b"", b"generic argument must be a compile-time value",
    ),
    "explicit_metadata_locals_reject_incomplete_results": (
        """
        Config :: struct { child type, count usize }
        compute proc() usize {
            config Config := undefined
            config.child = i16
            return config.count
        }
        main proc() usize { return compute() }
        """, b"", b"read before initialization",
    ),
    "explicit_metadata_locals_run_scoped_error_cleanup": (
        """
        Error :: enum { failed }
        fail proc() i16 ! Error {
            T type :: i16
            errdefer unreachable
            return .failed
        }
        main proc() i16 { return fail() catch 0 }
        """, b"", b"reached unreachable statement",
    ),
    "ordinary_local_type_aliases_keep_runtime_parameters": (
        """
        add proc(value i16) i16 {
            Local :: alias i16
            result Local :: value + 2
            return result
        }
        forward proc(value i16) i16 { return add(value) }
        main proc() i16 { return forward(40) }
        """, b"42\n", None,
    ),
    "ordinary_metadata_inputs_compute_owned_runtime_results": (
        """
        Config :: struct { child type, count usize }
        Result :: struct { values [2]i16, name []u8 }
        amount proc(input Config, extra usize) usize {
            copy := input
            saved :: copy
            copy.count = copy.count + extra
            if (saved.child != i16 or saved.count != 40) return 0
            return copy.count
        }
        compute proc(input Config) Result {
            return { values = [i16(amount(input, 2)), 19], name = "owned" }
        }
        identity proc(T type, value i16) i16 {
            if (T != i16) return 0
            return value
        }
        main proc() i16 {
            result :: compute(Config { child = i16, count = 40 })
            if (result.name.len != 5 or result.name[4] != 100) return -1
            return identity(i16, result.values[0])
        }
        """, b"42\n", None,
    ),
    "ordinary_metadata_inputs_select_recursive_helpers_and_void": (
        """
        validate proc(T type, early bool) void {
            if (T != i16) compile_error!("wrong child")
            if (early) return
        }
        count proc(types []type, index usize) usize {
            validate(i16, index == 0)
            if (index == types.len) return 0
            bias :: if (types[index] == i16) usize(21) else usize(0)
            return bias + count(types, index + 1)
        }
        main proc() usize { return count([i16, bool, i16], 0) }
        """, b"42\n", None,
    ),
    "ordinary_metadata_inputs_preserve_fallible_cleanup": (
        """
        Error :: enum { failed }
        fail proc(T type) i16 ! Error {
            errdefer if (T != i16) unreachable
            defer if (T != i16) unreachable
            return .failed
        }
        forward proc(T type) i16 ! Error {
            errdefer if (T != i16) unreachable
            return try fail(T)
        }
        succeed proc(T type) void ! Error {
            errdefer unreachable
            if (T == i16) return
        }
        main proc() i16 {
            succeed(i16) catch { return -1 }
            succeed(bool) catch { return -2 }
            return forward(i16) catch |error| {
                yield if (error == .failed) i16(42) else i16(0)
            }
        }
        """, b"42\n", None,
    ),
    "ordinary_metadata_inputs_run_error_cleanup": (
        """
        Error :: enum { failed }
        fail proc(T type) i16 ! Error {
            errdefer unreachable
            return .failed
        }
        main proc() i16 { return fail(i16) catch 0 }
        """, b"", b"reached unreachable statement",
    ),
    "ordinary_metadata_inputs_reject_runtime_dependencies": (
        """
        count proc(T type, value usize) usize { return value }
        runtime proc(value usize) usize { return count(i16, value) }
        main proc() usize { return runtime(42) }
        """, b"", b"generic argument must be a compile-time value",
    ),
    "ordinary_metadata_inputs_reject_incomplete_payloads": (
        """
        Config :: struct { child type, count usize }
        count proc(input Config) usize { return input.count }
        Produce proc() type {
            input Config := undefined
            input.count = 42
            return [count(input)]i16
        }
        main proc() usize { value Produce() :: []; return value.len }
        """, b"", b"read before initialization",
    ),
    "unreachable_is_a_demanded_source_trap_without_cleanup": (
        """
        fail proc() i16 {
            defer unreachable
            unreachable
        }
        main proc() i16 { return fail() }
        """, b"", b":3:5: runtime trap: reached unreachable statement",
    ),
    "untaken_and_unused_unreachable_do_not_diagnose": (
        """
        unused proc() i16 { unreachable }
        choose proc(flag bool) i16 {
            if (flag) unreachable
            return match flag { true: unreachable, false: 42 }
        }
        main proc() i16 { return choose(false) }
        """, b"42\n", None,
    ),
    "unreachable_traps_only_selected_constant_execution": (
        """
        choose proc(flag bool) i16 {
            if (flag) unreachable
            return 42
        }
        main proc() i16 { return $(choose(false)) }
        """, b"42\n", None,
    ),
    "unreachable_constant_execution_preserves_source": (
        """
        fail proc() i16 {
            unreachable
        }
        main proc() i16 { return $(fail()) }
        """, b"", b":2:5: runtime trap: reached unreachable statement",
    ),
    "terminal_while_true_preserves_nested_breaks_and_cleanups": (
        """
        compute proc(trace @mut i16) i16 {
            while true {
                defer trace^ += 1
                while true { break }
                if (trace^ == 2) return 42
                continue
            }
        }
        main proc() [2]i16 {
            trace i16 := 0
            answer :: compute(&trace)
            return [answer, trace]
        }
        """, b"[42, 3]\n", None,
    ),
    "terminal_while_true_return": (
        "main proc() i16 { while true { return 42 } }",
        b"42\n", None,
    ),
    "terminal_while_true_constant_execution": (
        "compute proc() i16 { while true { return 42 } }\nmain proc() i16 { return $(compute()) }",
        b"42\n", None,
    ),
    "terminal_while_true_ignores_break_after_return": (
        "main proc() i16 { while true { return 42\nbreak } }",
        b"42\n", None,
    ),
    "while_true_break_requires_return": (
        "main proc() i16 { while true { break } }",
        b"", b"nonvoid function can finish without returning a value",
    ),
    "while_true_value_block_break_requires_return": (
        """
        main proc() i16 {
            while true {
                value i16 :: { break }
                _ = value
            }
        }
        """, b"", b"nonvoid function can finish without returning a value",
    ),
    "while_runtime_guard_still_requires_return": (
        "main proc() i16 { flag := true\nwhile flag { return 42 } }",
        b"42\n", b"nonvoid function can finish without returning a value",
    ),
    "unreachable_rejects_trailing_operand": (
        "main proc() i16 { unreachable 42 }",
        b"", b"unexpected token",
    ),
    "distinct_bitwise_results_preserve_nominal_type": (
        """
        Flags :: distinct u16
        Signed :: distinct i8
        exact proc(value Flags) Flags { return value }
        main proc() [4]i16 {
            flags Flags := 21
            flags <<= u8(1)
            flags xor= 3
            flags &= 63
            flags |= 2
            signed Signed := -3
            signed >>= u64(1)
            return [i16(exact(flags)), i16(~Flags(65500)),
                i16(signed), i16(Signed(-2) <<| 7)]
        }
        """, b"[43, 35, -2, -128]\n", None,
    ),
    "compound_updates_evaluate_places_once_before_rhs": (
        """
        Box :: struct { values [2]mut i16 }
        place proc(box @mut Box, trace @mut i16) @mut i16 {
            trace^ = trace^ * 10 + 1
            return &box.values[0]
        }
        rhs proc(box @mut Box, trace @mut i16) i16 {
            trace^ = trace^ * 10 + 2
            box.values[0] = 100
            return 2
        }
        index proc(trace @mut i16) usize {
            trace^ = trace^ * 10 + 3
            return 1
        }
        main proc() [4]i16 {
            box Box := { values = [40, 6] }
            trace i16 := 0
            place(&box, &trace)^ += rhs(&box, &trace)
            box.values[index(&trace)] *= 7
            count i16 := 0
            for (0..3) |_| { defer count += 1 }
            return [box.values[0], box.values[1], trace, count]
        }
        """, b"[42, 42, 123, 3]\n", None,
    ),
    "compound_bitwise_and_float_updates": (
        """
        main proc() [4]i16 {
            value u8 := 255
            value &= 15
            value xor= 3
            value |= 1
            value <<= 1
            value >>= 1
            value <<|= u64(1000)
            number i16 := 20
            number += 3
            number -= 2
            number *= 2
            real f64 := 84
            real /= 2
            return [i16(value), number, i16(real), i16(~u8(213))]
        }
        """, b"[255, 42, 42, 42]\n", None,
    ),
    "bitwise_precedence_and_type_composition": (
        """
        A :: enum { first }
        B :: enum { second }
        Error :: A | B
        fail proc() i16 ! Error { return .first }
        main proc() [3]i16 {
            return [i16(u8(1) | 2 xor 3 & 6), i16(u8(3) << (1 + 1)),
                fail() catch |error| match error { .first: 42, .second: 0 }]
        }
        """, b"[0, 12, 42]\n", None,
    ),
    "bitwise_constants_and_type_producer_updates": (
        """
        Fixed proc() type {
            counts [2]mut usize := [1, 2]
            counts[0] <<= 2
            counts[1] |= 1
            total usize := counts[0]
            total += counts[1]
            return struct { values [total]i16, answer i16 = i16(~u8(213)) }
        }
        compute proc() u8 {
            value u8 := 21
            value <<= 1
            return value
        }
        choose proc($value u8) i16 {
            if ((value & 3) == 2) return 42
            else return missing_member(value)
        }
        main proc() [4]i16 {
            generated Fixed() :: { values = [0, 0, 0, 0, 0, 0, 0] }
            array [(usize(1) << 2)]i16 :: [0, 0, 0, 0]
            return [generated.answer, i16($(compute())), choose($(u8(21) << 1)),
                i16(generated.values.len + array.len)]
        }
        missing_member proc(value u8) bool { return true }
        """, b"[42, 42, 42, 11]\n", None,
    ),
    "compound_signed_overflow": (
        "main proc() i8 { value i8 := 127\nvalue += 1\nreturn value }",
        b"", b"integer arithmetic overflow",
    ),
    "compound_readonly_place": (
        "main proc() i16 { values [1]i16 := [40]\nvalues[0] += 2\nreturn 0 }",
        b"", b"indexed assignment requires a writable array element",
    ),
    "bitwise_float_rejected": (
        "main proc() f64 { return f64(1) & 2 }",
        b"", b"operator requires compatible operand types",
    ),
    "shift_signed_count_rejected": (
        "main proc() i16 { return i16(1) << i16(2) }",
        b"", b"operator requires compatible operand types",
    ),
    "shift_known_count_rejected_even_untaken": (
        "main proc() i16 { if (false) { _ = u8(1) << 8 }\nreturn 42 }",
        b"42\n", b"shift count must be smaller than the integer bit width",
    ),
    "shift_runtime_count_traps": (
        "shift proc(count u64) u8 { return u8(1) << count }\nmain proc() u8 { return shift(256) }",
        b"", b"shift count must be smaller than the integer bit width",
    ),
    "yielding_blocks_nest_and_copy_before_scoped_cleanup": (
        """
        Item :: struct { values [1]mut i16 }
        main proc() [3]i16 {
            trace i16 := 0
            source Item := { values = [40] }
            saved Item :: {
                defer trace = trace * 10 + 1
                inner i16 :: {
                    defer trace = trace * 10 + 2
                    yield 2
                }
                {
                    defer source.values[0] = 0
                    source.values[0] = source.values[0] + inner
                    yield source
                }
                trace = -1
            }
            return [saved.values[0], source.values[0], trace]
        }
        """, b"[42, 0, 21]\n", None,
    ),
    "yielding_blocks_preserve_loop_and_function_transfers": (
        """
        choose proc(flag bool) i16 {
            value i16 :: {
                if (flag) return 7
                yield 42
            }
            return value
        }
        main proc() [3]i16 {
            total i16 := 0
            cleanups i16 := 0
            for (0..5) |index| {
                value i16 :: {
                    defer cleanups = cleanups + 1
                    if (index == 0) continue
                    if (index == 3) break
                    yield i16(index)
                }
                total = total + value
            }
            return [total, cleanups, choose(true) + choose(false)]
        }
        """, b"[3, 4, 49]\n", None,
    ),
    "yielding_blocks_leave_inner_loops_on_yield": (
        """
        main proc() [2]i16 {
            cleanups i16 := 0
            value i16 :: {
                for (0..3) |index| {
                    defer cleanups = cleanups + 1
                    if (index == 0) continue
                    yield 42
                }
                yield 0
            }
            return [value, cleanups]
        }
        """, b"[42, 2]\n", None,
    ),
    "yielding_match_catch_and_conditional_blocks_are_lazy": (
        """
        Error :: enum { failed }
        fail proc(flag bool) i16 ! Error {
            if (flag) return .failed
            return 40
        }
        choose proc(flag bool) i16 {
            return match flag {
                true: { value :: fail(false) catch { yield 0 }
                    yield value + 2 },
                false: { yield fail(true) catch { yield 7 } },
            }
        }
        main proc() [3]i16 {
            return [choose(true), choose(false),
                if (true) { yield 42 } else { yield divtrunc!(1, 0) }]
        }
        """, b"[42, 7, 42]\n", None,
    ),
    "yielding_blocks_contextual_optional_fallible_and_aggregate_values": (
        """
        Error :: enum { failed }
        Item :: struct { value i16 }
        optional proc(flag bool) ?i16 {
            return { if (flag) yield 42
                yield null }
        }
        fallible proc(flag bool) i16 ! Error {
            return { if (flag) yield 42
                yield .failed }
        }
        item proc(flag bool) Item {
            return if (flag) { yield { value = 42 } } else { value = 7 }
        }
        main proc() [4]i16 {
            if (optional(false) != null) return [0, 0, 0, 0]
            return [optional(true)?, fallible(true) catch 0,
                fallible(false) catch { yield 7 }, item(true).value]
        }
        """, b"[42, 42, 7, 42]\n", None,
    ),
    "yielding_blocks_propagate_errors_and_run_only_reached_cleanup": (
        """
        Error :: enum { failed }
        fail proc() i16 ! Error { return .failed }
        compute proc(trace @mut i16, flag bool) i16 ! Error {
            value i16 :: {
                defer trace^ = trace^ * 10 + 1
                errdefer trace^ = trace^ * 10 + 2
                if (flag) yield 42
                yield try fail()
            }
            return value
        }
        main proc() [4]i16 {
            success i16 := 0
            failure i16 := 0
            left :: compute(&success, true) catch 0
            right :: compute(&failure, false) catch 7
            return [left, right, success, failure]
        }
        """, b"[42, 7, 1, 21]\n", None,
    ),
    "yielding_blocks_preserve_narrow_types_and_compile_time_values": (
        """
        exact proc(value i8) i16 { return i16(value) }
        compute proc() i8 {
            return {
                total i8 := 0
                for (i8(0)..i8(3)) |index| total = total + index
                yield total + 39
            }
        }
        main proc() [3]i16 {
            return [exact({ yield i8(42) }), i16($(compute())),
                i16($({ value i8 :: 7
                    yield value }))]
        }
        """, b"[42, 42, 7]\n", None,
    ),
    "yielding_blocks_generate_types_defaults_and_specialized_bodies": (
        """
        Choose proc(wide bool) type {
            return {
                Selected := i8
                if (wide) Selected = i16
                yield Selected
            }
        }
        Box proc(wide bool) type {
            count usize :: { yield if (wide) 2 else 1 }
            return struct {
                values [count]Choose(wide)
                bias i16 = { yield 7 }
            }
        }
        select proc($flag bool, value i16) i16 {
            return { if (flag) yield value
                yield compile_error!("unselected") }
        }
        main proc() [3]i16 {
            value Box(true) :: { values = [40, 2] }
            return [select(true, value.values[0] + value.values[1]),
                value.bias, i16(value.values.len)]
        }
        """, b"[42, 7, 2]\n", None,
    ),
    "yielding_blocks_keep_match_and_catch_capture_scopes": (
        """
        Choice :: union(enum) { value i8, empty void }
        Error :: union(enum) { failed i8 }
        fail proc() i16 ! Error { return .failed{7} }
        choose proc(value Choice) i16 {
            return match value {
                .value |payload|: { yield i16(payload) },
                .empty: { yield fail() catch |error| {
                    yield match error {
                        .failed |code|: { yield i16(code) },
                    }
                } },
            }
        }
        main proc() [2]i16 { return [choose(.value{42}), choose(.empty)] }
        """, b"[42, 7]\n", None,
    ),
    "yielding_blocks_allow_local_yields_inside_cleanup": (
        """
        main proc() i16 {
            value i16 := 0
            {
                defer value = { yield 42 }
            }
            return value
        }
        """, b"42\n", None,
    ),
    "yielding_blocks_require_yield_operands": (
        "main proc() i16 { return { yield } }",
        b"", b"unexpected token",
    ),
    "yielding_blocks_preserve_yield_capture_immutability": (
        """
        main proc() i16 {
            value ?i16 :: 42
            return if value |payload| { payload = 7
                yield payload } else 0
        }
        """, b"", b"assignment requires a mutable local binding",
    ),
    "yielding_blocks_reject_yield_outside_value_sources": (
        "main proc() i16 { yield 42 }",
        b"", b"yield requires an enclosing value block",
    ),
    "yielding_blocks_check_fallthrough_without_trapping_yielded_paths": (
        """
        choose proc(flag bool) i16 {
            return { if (flag) yield 42 }
        }
        main proc() i16 { return choose(true) }
        """, b"42\n", b"nonvoid value block can finish without yielding a value",
    ),
    "yielding_blocks_trap_reached_missing_value_paths": (
        """
        choose proc(flag bool) i16 {
            return { if (flag) yield 42 }
        }
        main proc() i16 { return choose(false) }
        """, b"", b"nonvoid value block can finish without yielding a value",
    ),
    "yielding_blocks_reject_incompatible_runtime_yields": (
        """
        choose proc(flag bool) i16 {
            return { if (flag) yield i16(42)
                yield u8(7) }
        }
        main proc() i16 { return choose(true) }
        """, b"42\n", b"yield does not match the value block result type",
    ),
    "yielding_blocks_reject_outward_cleanup_yields": (
        """
        main proc() i16 {
            return { defer { yield 7 }
                yield 42 }
        }
        """, b"", b"cleanup cannot",
    ),
    "yielding_blocks_preserve_type_control_mutation_captures_and_cleanup": (
        """
        Choose proc(input ?bool) type {
            Selected := i8
            Element :: {
                Selected = i8
                defer Selected = i16
                for (0..3) |index| {
                    if (index == 1) {
                        yield if input |flag| { yield if (flag) bool else i8 }
                            else { yield i16 }
                    }
                }
                yield i8
            }
            return struct { flag Element, value Selected }
        }
        main proc() i16 {
            value Choose(true) :: { flag = true, value = 42 }
            return if (value.flag) value.value else 0
        }
        """, b"42\n", None,
    ),
    "yielding_blocks_preserve_runtime_aliases_callbacks_and_void_results": (
        """
        add proc(value i16) i16 { return value + 2 }
        noop proc() void {}
        nothing proc() void { return { yield noop() } }
        main proc() [2]i16 {
            values [1]mut i16 := [40]
            pointer @mut i16 :: { yield &values[0] }
            view []mut i16 :: { yield values[..] }
            callback @proc(value i16) i16 :: { yield add }
            pointer^ = callback(view[0])
            ({ yield nothing() })
            return [values[0], view[0]]
        }
        """, b"[42, 42]\n", None,
    ),
    "yielding_blocks_keep_compile_time_logical_guards_lazy": (
        """
        Choose proc() type {
            Selected := i8
            if (false and { Selected = i16
                yield true }) Selected = bool
            if (true or { Selected = bool
                yield false }) {}
            return Selected
        }
        exact proc(value i8) i16 { return i16(value) }
        main proc() i16 {
            value Choose() :: 42
            return exact(value)
        }
        """, b"42\n", None,
    ),
    "yielding_blocks_check_skipped_compile_time_guard_result_types": (
        """
        Bad proc() type {
            if (false and { yield 1 }) return bool
            return i16
        }
        main proc() Bad() { return 42 }
        """, b"", b"operator requires compatible operand types",
    ),
    "conditional_expressions_evaluate_guard_once_and_only_selected_arm": (
        """
        next proc(calls @mut i16, value bool) bool { calls^ = calls^ + 1
            return value }
        main proc() [3]i16 {
            calls i16 := 0
            first i16 :: if next(&calls, true) 42 else divtrunc!(1, 0)
            second i16 :: if next(&calls, false) divtrunc!(1, 0) else 7
            return [first, second, calls]
        }
        """, b"[42, 7, 2]\n", None,
    ),
    "conditional_expressions_nest_bind_else_and_preserve_precedence": (
        """
        choose proc(a, b bool) i16 { return if (a) if (b) 1 else 40 else 3 + 4 }
        main proc() [3]i16 {
            return [choose(true, false) + 2, choose(false, true),
                2 * (if (false) 10 else 21)]
        }
        """, b"[42, 7, 42]\n", None,
    ),
    "conditional_expressions_preserve_aggregate_copy_isolation": (
        """
        Item :: struct { values [1]mut i16 }
        choose proc(flag bool, left, right Item) Item { return if (flag) left else right }
        main proc() i16 {
            left Item := { values = [42] }
            right Item :: { values = [7] }
            saved :: choose(true, left, right)
            left.values[0] = 0
            return saved.values[0]
        }
        """, b"42\n", None,
    ),
    "conditional_expressions_select_generated_types_and_specialized_bodies": (
        """
        Choose proc(wide bool) type { return if (wide) i16 else i8 }
        length proc(flag bool) usize { return if (flag) 2 else 1 }
        Box proc($wide bool) type {
            return struct {
                value Choose(wide)
                items [length(wide)]i16
                other (if (wide) i16 else i8)
            }
        }
        select proc($flag bool, value i16) i16 {
            return if $(flag) value else compile_error!("unselected")
        }
        main proc() [3]i16 {
            value Box(true) :: { value = 40, items = [1, 2], other = 42 }
            return [value.value, i16(value.items.len), select(true, value.other)]
        }
        """, b"[40, 2, 42]\n", None,
    ),
    "conditional_expressions_keep_outer_match_default": (
        """
        choose proc(flag bool) i16 {
            return match flag {
                true: (if (flag) 42
                    else 1)
                else: 7
            }
        }
        main proc() i16 { return choose(false) }
        """, b"7\n", None,
    ),
    "conditional_expressions_check_unselected_runtime_result_types": (
        """
        choose proc(flag bool) i16 { return if (flag) i16(42) else u8(1) }
        main proc() i16 { return choose(true) }
        """, b"42\n", b"incompatible value type",
    ),
    "conditional_expressions_require_else": (
        "main proc() i16 { return if (true) 42 }",
        b"", b"unexpected token",
    ),
    "conditional_expressions_preserve_optional_and_fallible_result_context": (
        """
        Error :: enum { failed }
        optional proc(flag bool) ?i16 { return if (flag) 42 else null }
        fallible proc(flag bool) i16 ! Error { return if (flag) 42 else .failed }
        main proc() [3]i16 {
            if (optional(false) != null) return [0, 0, 0]
            return [optional(true)?, fallible(true) catch 0, fallible(false) catch 7]
        }
        """, b"[42, 42, 7]\n", None,
    ),
    "conditional_expressions_materialize_direct_constants": (
        """
        main proc() [2]i16 {
            return [i16($(if (true) 42 else divtrunc!(1, 0))),
                $(if (false) 0 else i16(7))]
        }
        """, b"[42, 7]\n", None,
    ),
    "conditional_expressions_reject_nonboolean_guards": (
        "main proc() i16 { return if (1) 42 else 0 }",
        b"", b"incompatible value type",
    ),
    "optional_conditional_expressions_evaluate_once_and_keep_branches_lazy": (
        """
        next proc(calls @mut i16, value ?i16) ?i16 {
            calls^ = calls^ + 1
            return value
        }
        main proc() [3]i16 {
            calls i16 := 0
            first i16 :: if next(&calls, 42) |payload| payload else divtrunc!(1, 0)
            second i16 :: if next(&calls, null) |payload| divtrunc!(payload, 0) else 7
            return [first, second, calls]
        }
        """, b"[42, 7, 2]\n", None,
    ),
    "optional_conditional_expressions_keep_nested_presence_and_else_binding": (
        """
        identity proc($T type, value T) T { return value }
        choose proc(value ??i16) i16 {
            return if value |inner| if identity(inner) |payload| payload else 7 else 3
        }
        main proc() [3]i16 {
            return [choose(some!(null)), choose(null), choose(some!(some!(42)))]
        }
        """, b"[7, 3, 42]\n", None,
    ),
    "optional_conditional_expressions_snapshot_aggregate_payloads": (
        """
        Item :: struct { values [1]mut i16 }
        replace proc(value @mut ?Item) i16 {
            value^ = Item { values = [0] }
            return 0
        }
        main proc() [2]i16 {
            original ?Item := Item { values = [42] }
            saved i16 :: if original |payload| replace(&original) + payload.values[0] else -1
            return [saved, (original?).values[0]]
        }
        """, b"[42, 0]\n", None,
    ),
    "optional_conditional_expressions_preserve_narrow_payload_types": (
        """
        identity proc($T type, value T) T { return value }
        exact proc(value i8) i16 { return i16(value) }
        choose proc(value ?i8) i16 { return if value |payload| exact(identity(payload)) else 7 }
        main proc() [2]i16 { return [choose(42), choose(null)] }
        """, b"[42, 7]\n", None,
    ),
    "optional_conditional_expressions_capture_pointer_callback_slice_and_zero_size": (
        """
        increment proc(value i16) i16 { return value + 2 }
        read_pointer proc(value ?@i16) i16 { return if value |pointer| pointer^ else 7 }
        invoke proc(value ?@proc(value i16) i16) i16 {
            return if value |callback| callback(40) else 8
        }
        sum proc(value ?[]i16) i16 {
            return if value |items| items[0] + items[1] else 9
        }
        empty proc(value ?[0]i16) i16 { return if value |items| i16(items.len) else 10 }
        main proc() [8]i16 {
            value i16 :: 42
            items [2]i16 :: [19, 23]
            return [read_pointer(&value), read_pointer(null), invoke(increment), invoke(null),
                sum(items[..]), sum(null), empty([]), empty(null)]
        }
        """, b"[42, 7, 42, 8, 42, 9, 0, 10]\n", None,
    ),
    "optional_conditional_expressions_preserve_optional_and_fallible_context": (
        """
        Error :: enum { failed }
        optional proc(value ?i16) ?i16 { return if value |payload| payload + 2 else null }
        fallible proc(value ?i16) i16 ! Error {
            return if value |payload| payload + 2 else .failed
        }
        main proc() [3]i16 {
            if (optional(null) != null) return [0, 0, 0]
            return [optional(40)?, fallible(40) catch 0, fallible(null) catch 7]
        }
        """, b"[42, 42, 7]\n", None,
    ),
    "optional_conditional_expressions_materialize_compile_time_scalars": (
        """
        choose proc(value ?i8) i16 { return if value |payload| i16(payload) + 2 else 7 }
        nested proc(value ??i16) i16 {
            return if value |inner| if inner |payload| payload else 11 else 13
        }
        main proc() [4]i16 {
            return [$(choose(40)), $(choose(null)), $(nested(some!(null))), $(nested(null))]
        }
        """, b"[42, 7, 11, 13]\n", None,
    ),
    "optional_conditional_expressions_generate_types_and_captured_defaults": (
        """
        Choose proc(value ?usize) type { return if value |_| i16 else i8 }
        Box proc(count ?usize) type {
            return if count |length| struct { values [length]i16, bias i16 = i16(length) }
                else struct { value i16 = 7 }
        }
        main proc() [3]i16 {
            first Box(2) :: { values = [19, 21] }
            same Box(usize(2)) :: first
            absent Box(null) :: {}
            narrow Choose(null) :: 5
            wide Choose(2) :: 40
            return [same.values[0] + same.values[1] + same.bias, absent.value,
                wide + i16(narrow)]
        }
        """, b"[42, 7, 45]\n", None,
    ),
    "optional_conditional_expressions_own_compile_time_aggregate_captures": (
        """
        Config :: struct { name string, counts [2]usize }
        Storage proc(config ?Config) type {
            return if config |payload| struct {
                items [payload.counts[0]]i16
                name string = payload.name
            } else struct { value i16 = 7 }
        }
        main proc() i16 {
            value Storage(Config { name = "abc", counts = [2, 1] }) :: { items = [19, 20] }
            return value.items[0] + value.items[1] + i16(value.name.len)
        }
        """, b"42\n", None,
    ),
    "optional_conditional_expressions_specialize_selected_capture_only": (
        """
        present proc($value ?i8) i16 {
            return if $(value) |payload| i16(payload) + 2 else compile_error!("absent")
        }
        absent proc($value ?i8) i16 {
            return if $(value) |_| compile_error!("present") else 7
        }
        select proc($value ?i8) i16 {
            return if $(value) |payload| i16(payload) + 2 else 7
        }
        main proc() [6]i16 {
            return [present(40), absent(null), select(40), select(null), select(1), select(40)]
        }
        """, b"[42, 7, 42, 7, 3, 42]\n", None,
    ),
    "optional_conditional_expressions_keep_compile_time_narrow_overflow": (
        """
        narrow proc(value ?i8) i8 { return if value |payload| payload + 1 else 0 }
        main proc() i8 { return $(narrow(127)) }
        """, b"", b"integer arithmetic overflow",
    ),
    "optional_conditional_expressions_reject_runtime_explicit_comptime_subject": (
        """
        choose proc(value ?i16) i16 { return if $(value) |payload| payload else 0 }
        main proc() i16 { return choose(42) }
        """, b"", b"generic argument must be a compile-time value",
    ),
    "optional_conditional_expressions_reject_nonoptional_subject": (
        "main proc() i16 { return if true |payload| 42 else 0 }",
        b"", b"condition",
    ),
    "optional_conditional_expressions_capture_does_not_reach_else": (
        "main proc() i16 { value ?i16 :: null\nreturn if value |payload| 42 else payload }",
        b"", b"unknown name",
    ),
    "optional_conditional_expressions_capture_does_not_escape": (
        """
        main proc() i16 {
            value ?i16 :: 42
            saved i16 :: if value |payload| payload else 0
            return saved + payload
        }
        """, b"", b"unknown name",
    ),
    "optional_conditional_expressions_reject_capture_shadowing": (
        "main proc() i16 { value ?i16 :: 42\nreturn if value |value| value else 0 }",
        b"", b"duplicate name or forbidden shadowing",
    ),
    "optional_conditional_expressions_check_unselected_result_types": (
        """
        choose proc(value ?i16) i16 { return if value |payload| payload else u8(7) }
        main proc() i16 { return choose(42) }
        """, b"42\n", b"incompatible value type",
    ),
    "optional_conditional_expressions_require_else": (
        "main proc() i16 { value ?i16 :: 42\nreturn if value |payload| payload }",
        b"", b"unexpected token",
    ),
    "optional_conditional_expressions_reject_pointer_capture_syntax": (
        "main proc() i16 { value ?i16 :: 42\nreturn if value |@payload| payload^ else 0 }",
        b"", b"unexpected token",
    ),
    "integer_divtrunc_preserves_sign_and_single_evaluation": (
        """
        next proc(calls @mut i16) i16 { calls^ = calls^ + 1
            return calls^ + 5 }
        quotient proc(value i16) i16 { return divtrunc!(value, 3) }
        main proc() [6]i16 {
            calls i16 := 0
            result :: divtrunc!(next(&calls), next(&calls))
            return [result, calls, quotient(-7), quotient(7),
                $(quotient(-7)), divtrunc!(-7, i16(-3))]
        }
        """, b"[0, 2, -2, 2, -2, 2]\n", None,
    ),
    "integer_divtrunc_rejects_zero": (
        "main proc() u64 { return divtrunc!(maxval!(u64), 0) }",
        b"", b"integer division by zero",
    ),
    "integer_divtrunc_checks_narrow_signed_overflow": (
        "main proc() i8 { return divtrunc!(minval!(i8), -1) }",
        b"", b"integer arithmetic overflow",
    ),
    "integer_divtrunc_checks_full_width_signed_overflow": (
        "main proc() i64 { return divtrunc!(minval!(i64), -1) }",
        b"", b"integer arithmetic overflow",
    ),
    "integer_divtrunc_rejects_float_operands": (
        "main proc() f64 { return divtrunc!(7.0, 3.0) }",
        b"", b"operator requires compatible operand types",
    ),
    "integer_limits_reject_noninteger_types": (
        "main proc() bool { return maxval!(bool) }",
        b"", b"operator requires compatible operand types",
    ),
    "integer_divtrunc_rejects_rigid_width_conflicts": (
        "main proc() i16 { return divtrunc!(i8(7), i16(3)) }",
        b"", b"operator requires compatible operand types",
    ),
    "c_layout_records_and_nominal_opaque_pointers": (
        """
        Handle :: opaque
        Other :: opaque
        Header :: c_struct { tag c_uchar, value c_ulong }
        main proc() c_ulong {
            header Header := { tag = 2, value = 40 }
            if (sizeof!(Header) != 16 or alignof!(Header) != 8) return 0
            pointer ?*mut Handle :: ptrcast!(&header)
            restored *mut Header :: ptrcast!(pointer?)
            return restored.value + c_ulong(restored.tag)
        }
        """, b"42\n", None,
    ),
    "c_opaque_pointer_types_remain_nominal": (
        """
        Left :: opaque
        Right :: opaque
        consume proc(value ?*Left) bool { return value == null }
        main proc() bool { value ?*Right :: null
            return consume(value) }
        """, b"", b"incompatible value type",
    ),
    "runtime_undefined_nested_storage_and_copy_isolation": (
        """
        Item :: struct { values [2]mut i16, label string }
        fill proc() Item {
            item Item := undefined
            for (usize(0)..item.values.len) |i| { item.values[i] = i16(i) + 19 }
            item.label = "yes"
            return item
        }
        main proc() i16 {
            item := fill()
            saved :: item
            pointer :: &item
            pointer.values[0] = 0
            return saved.values[0] + saved.values[1] + i16(saved.label.len)
        }
        """, b"42\n", None,
    ),
    "ordinary_constant_procedures_fill_undefined_arrays": (
        """
        values proc() [2]mut i16 {
            result [2]mut i16 := undefined
            for (usize(0)..result.len) |i| { result[i] = i16(i) + 20 }
            return result
        }
        main proc() i16 { result :: $(values())
            return result[0] + result[1] + 1 }
        """, b"42\n", None,
    ),
    "ordinary_constants_reject_partial_undefined_snapshots": (
        """
        values proc() [2]mut i16 {
            result [2]mut i16 := undefined
            result[0] = 42
            saved :: result
            result[1] = 0
            return saved
        }
        main proc() [2]mut i16 { return $(values()) }
        """, b"", b"read before initialization",
    ),
    "ordinary_constants_reject_undefined_optional_presence": (
        """
        missing proc() ?i16 { value ?i16 := undefined
            return value }
        main proc() ?i16 { return $(missing()) }
        """, b"", b"read before initialization",
    ),
    "ordinary_constants_reject_undefined_union_tags": (
        """
        Choice :: union(enum) { value i16, empty void }
        missing proc() Choice { value Choice := undefined
            return value }
        main proc() Choice { return $(missing()) }
        """, b"", b"read before initialization",
    ),
    "undefined_zero_leaf_shapes_cross_runtime_and_constant_calls": (
        """
        empty proc() [2]mut [0]i16 {
            rows [2]mut [0]i16 := undefined
            return rows
        }
        main proc() usize {
            runtime :: empty()
            constant :: $(empty())
            return runtime.len + constant.len
        }
        """, b"4\n", None,
    ),
    "some_nested_present_null_survives_constant_roundtrip": (
        """
        nested proc() ??i16 { return some!(null) }
        present proc() ??i16 { return some!(some!(42)) }
        main proc() i16 {
            first ??i16 :: $(nested())
            second ??i16 :: $(present())
            absent ??i16 :: null
            if (first == null or first? != null or absent != null) return -1
            return second??
        }
        """, b"42\n", None,
    ),
    "some_optional_specialization_keys_preserve_every_presence_layer": (
        """
        Box proc($value ??i16) type { return struct { value ??i16 = value } }
        main proc() i16 {
            empty Box(null) :: {}
            nested Box(some!(null)) :: {}
            present Box(some!(some!(42))) :: {}
            same Box(some!(some!(42))) :: present
            if (empty.value != null or nested.value == null or nested.value? != null) return -1
            if (Box(null) == Box(some!(null))) return -2
            if (Box(some!(some!(42))) == Box(some!(some!(41)))) return -3
            return same.value??
        }
        """, b"42\n", None,
    ),
    "generic_contextual_literals_use_rigid_arguments_in_either_order": (
        """
        Key :: enum { first, last }
        choose proc($T type, value T, anchor @T) T { return value }
        payload proc($T type, anchor @T, value struct { item T }) T { return value.item }
        main proc() i16 {
            key Key :: .first
            selected :: choose(.last, &key)
            other :: payload(&key, { item = .first })
            return match selected { .last: match other { .first: 42, else: 0 }, else: 0 }
        }
        """, b"42\n", None,
    ),
    "expected_result_inference_direct_return_without_runtime_arguments": (
        """
        answer proc($T type) T { return 42 }
        main proc() i16 { return answer() }
        """, b"42\n", None,
    ),
    "expected_result_inference_assignment_return_and_nested_parameters": (
        """
        answer proc($T type) T { return 42 }
        identity proc($T type, value T) T { return value }
        read proc() i16 { return identity(42) }
        consume proc(value i16) i16 { return value }
        main proc() [4]i16 {
            value i16 := identity(0)
            value = answer()
            return [value, read(), consume(identity(42)), consume(identity(answer()))]
        }
        """, b"[42, 42, 42, 42]\n", None,
    ),
    "expected_result_inference_nested_aggregate_fields_and_elements": (
        """
        identity proc($T type, value T) T { return value }
        answer proc($T type) T { return 42 }
        Inner :: struct { value i16 }
        Outer :: struct { rows [2]Inner }
        main proc() [2]i16 {
            result Outer :: { rows = [{ value = identity(42) }, { value = answer() }] }
            return [result.rows[0].value, result.rows[1].value]
        }
        """, b"[42, 42]\n", None,
    ),
    "expected_result_inference_precedes_contextual_literal_defaults": (
        """
        Key :: enum { first, last }
        Record :: struct { value i16 }
        identity proc($T type, value T) T { return value }
        main proc() i16 {
            number u8 :: identity(42)
            key Key :: identity(.last)
            record Record :: identity({ value = 42 })
            absent ?i16 :: identity(null)
            text []u8 :: identity("ab")
            if (number != 42 or key != .last or absent != null or text.len != 2) return -1
            return record.value
        }
        """, b"42\n", None,
    ),
    "expected_result_inference_generated_nominal_type_and_value_arguments": (
        """
        Box proc($T type, $count usize) type {
            return struct { value T, length usize = count }
        }
        create proc($T type, $count usize, value T) Box(T, count) {
            return { value = value }
        }
        main proc() i16 {
            box Box(i16, 2) :: create(40)
            explicit Box(i16, 2) :: create(i16, 2, 40)
            if (box.value != explicit.value or box.length != 2) return -1
            return box.value + i16(box.length)
        }
        """, b"42\n", None,
    ),
    "expected_result_inference_optional_and_fallible_success_contexts": (
        """
        Error :: enum { failed }
        identity proc($T type, value T) T { return value }
        optional proc() ?i16 { return identity(42) }
        fallible proc() i16 ! Error { return identity(42) }
        main proc() [2]i16 { return [optional()?, fallible() catch 0] }
        """, b"[42, 42]\n", None,
    ),
    "expected_result_inference_match_arms_and_value_block_yields": (
        """
        answer proc($T type) T { return 42 }
        choose proc(flag bool) i16 {
            return match flag {
                true: answer(),
                false: { yield answer() },
            }
        }
        main proc() [2]i16 { return [choose(true), choose(false)] }
        """, b"[42, 42]\n", None,
    ),
    "expected_result_inference_inside_compile_time_type_producers": (
        """
        answer proc($T type) T { return 2 }
        Fixed proc() type {
            count usize :: answer()
            return [count]i16
        }
        main proc() Fixed() { return [19, 23] }
        """, b"[19, 23]\n", None,
    ),
    "expected_result_inference_cleanup_comptime_and_expression_contexts": (
        """
        Error :: enum { failed }
        answer proc($T type) T { return 21 }
        fail proc($T type) T ! Error { return .failed }
        recover proc() i16 { return fail() catch |error| { yield answer() } }
        propagate proc() i16 ! Error { return try fail() }
        Default :: struct { value i16 = answer() }
        main proc() [7]i16 {
            value ?i16 :: some!(answer())
            constant i16 :: $(answer())
            defaults Default :: {}
            return [answer() + answer(), -answer(), ~answer(), fail() catch answer(),
                recover(), (value?), constant + defaults.value]
        }
        """, b"[42, -21, -22, 21, 21, 21, 42]\n", None,
    ),
    "expected_result_inference_structural_returns_and_nested_optional_presence": (
        """
        Error :: enum { failed }
        exact proc(value i16) i16 { return value }
        callback proc($T type) @proc(value T) T { return exact }
        optional proc($T type) ?T { return 42 }
        fallible proc($T, $E type) T ! E { return 42 }
        identity proc($T type, value T) T { return value }
        main proc() [4]i16 {
            call @proc(value i16) i16 :: callback()
            maybe ?i16 :: optional()
            result i16 ! Error :: fallible()
            nested ??i16 :: identity(some!(null))
            if (nested == null or nested? != null) return [0, 0, 0, 0]
            return [call(42), (maybe?), result catch 0, 42]
        }
        """, b"[42, 42, 42, 42]\n", None,
    ),
    "expected_result_inference_rejects_conflicting_rigid_argument": (
        """
        identity proc($T type, value T) T { return value }
        main proc() u16 {
            value i16 :: 42
            return identity(value)
        }
        """, b"", b"cannot infer consistent compile-time arguments",
    ),
    "expected_result_inference_requires_context_for_result_only_arguments": (
        """
        answer proc($T type) T { return 42 }
        main proc() i16 {
            value :: answer()
            return 42
        }
        """, b"", b"cannot infer consistent compile-time arguments",
    ),
    "generic_contextual_enum_rejects_unknown_member": (
        """
        Key :: enum { first }
        choose proc($T type, anchor @T, value T) T { return value }
        main proc() i16 { key Key :: .first
            choose(&key, .missing)
            return 42 }
        """, b"", b"record has no such field",
    ),
    "generic_contextual_only_call_cannot_infer_nominal_type": (
        """
        choose proc($T type, value T) T { return value }
        main proc() i16 { choose(.first)
            return 42 }
        """, b"", b"cannot infer consistent compile-time arguments",
    ),
    "generic_contextual_arguments_do_not_hide_rigid_type_conflicts": (
        """
        First :: enum { first }
        Second :: enum { first }
        choose proc($T type, left @T, right @T, value T) T { return value }
        main proc() i16 {
            left First :: .first
            right Second :: .first
            choose(&left, &right, .first)
            return 42
        }
        """, b"", b"cannot infer consistent compile-time arguments",
    ),
    "undefined_rejects_immutable_local": (
        "main proc() i16 { value i16 :: undefined\nreturn value }",
        b"", b"undefined requires a concrete mutable local declaration",
    ),
    "undefined_rejects_untyped_local": (
        "main proc() i16 { value := undefined\nreturn 42 }",
        b"", b"undefined requires a concrete mutable local declaration",
    ),
    "undefined_rejects_noninitializer_expression": (
        "main proc() i16 { return undefined }",
        b"", b"undefined requires a concrete mutable local declaration",
    ),
    "undefined_rejects_inferred_array_length": (
        "main proc() i16 { values [_]mut i16 := undefined\nreturn 42 }",
        b"", b"undefined requires a concrete mutable local declaration",
    ),
    "undefined_rejects_global_storage": (
        "value i16 := undefined\nmain proc() i16 { return value }",
        b"", b"undefined requires a concrete mutable local declaration",
    ),
    "inline_match_composed_enums_keep_static_sum_tags_and_runtime_dispatch": (
        """
        First :: enum { skipped, first }
        Second :: enum { second, last }
        Errors :: alias (First | (Second | First))
        selected proc(input Errors, calls @mut i16) Errors {
            calls^ = calls^ + 1
            return input
        }
        known proc($tag Errors) i16 {
            return match tag { .first: 19, .second: 23, else: 0 }
        }
        read proc(input Errors, calls @mut i16) i16 {
            return match selected(input, calls) {
                .skipped: 0,
                inline |tag|: known(tag),
            }
        }
        main proc() [2]i16 {
            calls i16 := 0
            total :: read(.skipped, &calls) + read(.first, &calls) + read(.second, &calls) + read(.last, &calls)
            return [total, calls]
        }
        """, b"[42, 4]\n", None,
    ),
    "inline_match_composed_payloads_catch_static_tags_and_owned_copies": (
        """
        Missing :: enum { missing }
        Detail :: union(enum) { number i8, text string, empty void }
        Failure :: struct { values [2]mut i16 }
        Errors :: alias ((Missing | Detail) | (Failure | Missing))
        fail proc(input Errors) i16 ! Errors { return input }
        exact proc(value i8) i16 { return i16(value) }
        read proc(input Errors) i16 {
            fail(input) catch |err| {
                match err {
                    .missing: return 0,
                    inline |payload, tag|: {
                        match tag {
                            inline |selected|: {
                                if (selected == .number) { return exact(payload) }
                                else if (selected == .text) { return i16(payload.len) }
                                else if (selected == .Failure) {
                                    copy := payload
                                    copy.values[0] = 0
                                    return payload.values[0] + payload.values[1]
                                } else {
                                    payload
                                    return 1
                                }
                            },
                        }
                    },
                }
            }
            return -1
        }
        main proc() i16 {
            return read(.missing) + read(.number{19}) + read(.text{"ok"}) + read(.Failure{values = [9, 11]}) + read(.empty)
        }
        """, b"42\n", None,
    ),
    "inline_match_composed_type_producers_and_constant_inputs": (
        """
        Missing :: enum { missing }
        Detail :: union(enum) { count usize, empty void }
        Failure :: struct { count usize }
        Errors :: alias (Missing | Detail | (Failure | Detail))
        Storage proc(input Errors) type {
            length usize := 0
            match input {
                inline |payload, tag|: {
                    if (tag == .count) { length = payload }
                    else if (tag == .Failure) { length = payload.count }
                    else if (tag == .missing) { length = 1 }
                    else { length = 0 }
                },
            }
            return struct { values [length]i16 }
        }
        score proc(input Errors) i16 {
            return match input {
                inline |payload, tag|: match tag { .count: i16(payload), else: 0 },
            }
        }
        known proc($input Errors) i16 { return score(input) }
        main proc() i16 {
            first Storage(.count{2}) :: { values = [19, 20] }
            same Storage(.count{2}) :: first
            record Storage(.Failure{count = 2}) :: { values = [0, 0] }
            other Storage(.missing) :: { values = [0] }
            empty Storage(.empty) :: { values = [] }
            return same.values[0] + same.values[1] + $(score(.count{2})) + known(.count{1}) + other.values[0] + i16(empty.values.len) + record.values[0]
        }
        """, b"42\n", None,
    ),
    "inline_match_composed_payload_cleanup_on_error_return": (
        """
        Missing :: enum { missing }
        Detail :: union(enum) { failed i16 }
        Errors :: alias (Missing | Detail)
        forward proc(input Errors, cleanups @mut i16) i16 ! Errors {
            match input {
                inline |payload, tag|: {
                    defer cleanups^ = cleanups^ + 1
                    if (tag == .failed) { return .failed{payload} }
                    else { return .missing }
                },
            }
        }
        main proc() i16 {
            cleanups i16 := 0
            value :: forward(.failed{41}, &cleanups) catch |err| match err {
                inline |payload, tag|: match tag { .failed: payload, else: 0 },
            }
            return value + cleanups
        }
        """, b"42\n", None,
    ),
    "inline_match_composed_enums_reject_second_capture": (
        "A :: enum { a }\nB :: enum { b }\nmain proc() i16 { input (A | B) :: .a\nreturn match input { inline |value, tag|: 42 } }",
        b"", b"inline match arm requires one enum capture",
    ),
    "inline_match_composed_rejects_conflicting_variant_payloads": (
        "A :: union(enum) { failed i8 }\nB :: union(enum) { failed i16 }\nmain proc() i16 { input (A | B) :: .failed{42}\nreturn match input { inline |payload, tag|: i16(payload) } }",
        b"", b"sum composition requires compatible",
    ),
    "inline_match_composed_preserves_narrow_payload_type": (
        "A :: enum { a }\nB :: union(enum) { failed i8 }\nread proc(input (A | B)) i16 { return match input { inline |payload, tag|: match tag { .failed: payload, else: i16(0) } } }\nmain proc() i16 { return read(.failed{42}) }",
        b"", b"incompatible value type",
    ),
    "inline_match_composed_rejects_fully_covered_arm": (
        "A :: enum { a }\nB :: enum { b }\nmain proc() i16 { input (A | B) :: .a\nreturn match input { .a, .b: 42, inline |_|: 0 } }",
        b"", b"match arm is unreachable",
    ),
    "inline_match_runtime_enum_static_capture_expression_and_single_subject": (
        """
        Mode :: enum(i16) { handled = -7, first = 3, second = 8, answer = 42, zero = 90 }
        selected proc(value Mode, calls @mut i16) Mode {
            calls^ = calls^ + 1
            return value
        }
        remaining proc($tag Mode) i16 {
            if (tag == .answer) { return 42 }
            else if (tag == .zero) { return 0 }
            else { return false }
        }
        read proc(value Mode, calls @mut i16) i16 {
            return match selected(value, calls) {
                .handled: 0,
                .first, .second: 0,
                inline |tag|: remaining(tag),
            }
        }
        main proc() [2]i16 {
            calls i16 := 0
            total :: (
                read(.handled, &calls) + read(.first, &calls) + read(.second, &calls)
                + read(.answer, &calls) + read(.zero, &calls)
            )
            return [total, calls]
        }
        """, b"[42, 5]\n", None,
    ),
    "inline_match_generic_heterogeneous_payloads_static_tags_and_nested_selection": (
        """
        Tag :: enum(i16) { handled = -7, first = 3, second = 8, number = 42, record = 90, text = 100, empty = 120 }
        Pair :: struct { values [2]i16 }
        Choice :: union(Tag) { text string, empty void, record Pair, number i8, handled bool, second bool, first bool }
        narrow proc($tag Tag, value i8) i16 {
            if (tag == .number) return i16(value) else return false
        }
        pair proc(value Pair) i16 { return value.values[0] + value.values[1] }
        read proc($T type, input T) i16 {
            return match input {
                .handled: 0,
                .first, .second: 0,
                inline |payload, tag|: match tag {
                    .number: narrow(tag, payload),
                    .record: pair(payload),
                    .text: i16(payload.len),
                    .empty: 1,
                    else: false,
                },
            }
        }
        nested proc(input Choice) i16 {
            match input {
                .handled, .first, .second: return 0,
                inline |payload, tag|: {
                    match tag {
                        inline |selected|: {
                            if (selected == .number) { return narrow(tag, payload) }
                            else if (selected == .record) { return pair(payload) }
                            else if (selected == .text) { return i16(payload.len) }
                            else {
                                payload
                                return 1
                            }
                        },
                    }
                },
            }
        }
        main proc() i16 {
            if (read(Choice, .handled{false}) + read(Choice, .first{true}) + read(Choice, .second{false}) != 0) return -1
            if (nested(.number{19}) + nested(.record{values = [9, 11]}) + nested(.text{"ok"}) + nested(.empty) != 42) return -2
            return (
                read(Choice, .number{19}) + read(Choice, .record{values = [9, 11]})
                + read(Choice, .text{"ok"}) + read(Choice, .empty)
            )
        }
        """, b"42\n", None,
    ),
    "inline_match_union_subject_once_and_payload_copy_isolation_without_tag": (
        """
        Pair :: struct { values [2]mut i16 }
        Choice :: union(enum) { record Pair, other Pair }
        exact proc(value Pair) i16 { return value.values[0] + value.values[1] }
        selected proc(input @Choice, calls @mut i16) Choice {
            calls^ = calls^ + 1
            return input^
        }
        main proc() i16 {
            input Choice := .record{values = [19, 23]}
            calls i16 := 0
            match selected(&input, &calls) {
                inline |payload|: {
                    input.record.values[0] = 0
                    copied := payload
                    copied.values[1] = 0
                    if (calls != 1 or input.record.values[0] != 0 or copied.values[1] != 0) return -1
                    return exact(payload)
                },
            }
        }
        """, b"42\n", None,
    ),
    "inline_match_return_break_continue_run_arm_cleanup": (
        """
        Mode :: enum { skip, add, stop }
        finish proc(input Mode, cleanups @mut i16) i16 {
            match input {
                .skip, .stop: return 0,
                inline |tag|: {
                    defer cleanups^ = cleanups^ + 1
                    if (tag == .add) return 19 else return false
                },
            }
        }
        main proc() i16 {
            total i16 := 0
            cleanups i16 := 0
            for [Mode.skip, .add, .stop, .add] |input| {
                match input {
                    inline |tag|: {
                        defer cleanups = cleanups + 1
                        if (tag == .skip) { continue }
                        else if (tag == .stop) { break }
                        else { total = total + 19 }
                    },
                }
            }
            result :: finish(.add, &cleanups)
            return total + result + cleanups
        }
        """, b"42\n", None,
    ),
    "inline_match_rejects_scalar_subject": (
        "main proc() i16 { return match i16(42) { inline |value|: value } }",
        b"", b"inline match arm requires an enum or tagged union",
    ),
    "inline_match_rejects_untagged_union_subject": (
        "Choice :: union { number i16 }\nmain proc() i16 { input Choice :: { number = 42 }\nreturn match input { inline |value|: value } }",
        b"", b"inline match arm requires an enum or tagged union",
    ),
    "inline_match_rejects_second_enum_capture": (
        "Mode :: enum { answer }\nmain proc() i16 { return match Mode.answer { inline |value, tag|: 42 } }",
        b"", b"inline match arm requires one enum capture",
    ),
    "inline_match_rejects_redundant_remaining_arm": (
        "Mode :: enum { first, second }\nmain proc() i16 { return match Mode.first { .first, .second: 42, inline |_|: 0 } }",
        b"", b"match arm is unreachable",
    ),
    "inline_match_rejects_following_arm": (
        "Mode :: enum { first, second }\nmain proc() i16 { return match Mode.first { inline |_|: 42, .second: 0 } }",
        b"", b"match arm is unreachable",
    ),
    "inline_match_rejects_arm_after_else": (
        "Mode :: enum { first, second }\nmain proc() i16 { return match Mode.first { else: 42, inline |_|: 0 } }",
        b"", b"match arm is unreachable",
    ),
    "inline_match_checks_preceding_duplicate_patterns": (
        "Mode :: enum { first, second }\nmain proc() i16 { return match Mode.first { .first: 42, .first: 0, inline |_|: 0 } }",
        b"", b"duplicate or overlapping match pattern",
    ),
    "inline_match_rejects_duplicate_capture_names": (
        "Choice :: union(enum) { number i8 }\nmain proc() i16 { input Choice :: .number{42}\nreturn match input { inline |value, value|: 42 } }",
        b"", b"duplicate name or forbidden shadowing",
    ),
    "inline_match_rejects_shadowed_enum_capture": (
        "Mode :: enum { answer }\nmain proc() i16 { value :: 42\nreturn match Mode.answer { inline |value|: 42 } }",
        b"", b"duplicate name or forbidden shadowing",
    ),
    "inline_match_rejects_shadowed_tag_capture": (
        "Choice :: union(enum) { number i8 }\nmain proc() i16 { tag :: 42\ninput Choice :: .number{42}\nreturn match input { inline |value, tag|: 42 } }",
        b"", b"duplicate name or forbidden shadowing",
    ),
    "inline_match_enum_capture_is_immutable": (
        "Mode :: enum { first, second }\nmain proc() i16 { match Mode.first { inline |value|: value = .second }\nreturn 42 }",
        b"", b"assignment requires a mutable local binding",
    ),
    "inline_match_payload_capture_is_immutable": (
        "Choice :: union(enum) { number i8 }\nmain proc() i16 { input Choice :: .number{42}\nmatch input { inline |value|: value = 0 }\nreturn 42 }",
        b"", b"assignment requires a mutable local binding",
    ),
    "inline_match_tag_capture_is_immutable": (
        "Choice :: union(enum) { number i8, empty void }\nmain proc() i16 { input Choice :: .number{42}\nmatch input { inline |value, tag|: tag = .empty }\nreturn 42 }",
        b"", b"assignment requires a mutable local binding",
    ),
    "inline_match_payload_preserves_narrow_type": (
        "Choice :: union(enum) { number i8 }\nread proc(input Choice) i16 { match input { inline |value|: return value } }\nmain proc() i16 { return read(.number{42}) }",
        b"", b"return does not match",
    ),
    "inline_match_payload_is_not_a_comptime_binding": (
        "Choice :: union(enum) { number i8 }\nknown proc($value i8) i16 { return i16(value) }\nread proc(input Choice) i16 { return match input { inline |value|: known(value) } }\nmain proc() i16 { return read(.number{42}) }",
        b"", b"compile-time",
    ),
    "inline_match_resolves_unselected_bodies": (
        "Mode :: enum { first, second }\nmain proc() i16 { return match Mode.first { inline |tag|: match tag { .first: 42, .second: missing } } }",
        b"42\n", b"unknown name",
    ),
    "inline_match_rejects_pointer_capture_syntax": (
        "Choice :: union(enum) { number i8 }\nmain proc() i16 { input Choice :: .number{42}\nreturn match input { inline |*value|: 42 } }",
        b"", b"unexpected token",
    ),
    "inline_match_nested_optional_loop_and_ordinary_match_captures": (
        """
        Mode :: enum { skip, answer }
        Choice :: union(enum) { mode Mode }
        score proc(value Mode) i16 {
            return match value { inline |tag|: match tag { .skip: 0, .answer: 14 } }
        }
        read proc(input ?Mode, choice Choice) i16 {
            total i16 := 0
            if input |value| {
                total = match value { inline |tag|: score(tag) }
            }
            pending ?Mode := input
            while pending |value| {
                total = total + match value { inline |tag|: score(tag) }
                pending = null
            }
            match choice {
                .mode |value|: {
                    total = total + match value { inline |tag|: score(tag) }
                }
            }
            return total
        }
        main proc() i16 { return read(.answer, .mode{.answer}) + read(null, .mode{.skip}) }
        """, b"42\n", None,
    ),
    "inline_match_type_producer_selects_generated_storage": (
        """
        Mode :: enum { small, wide }
        Storage proc(input Mode) type {
            match input { inline |tag|: {
                if (tag == .small) { return struct { value i8 = 19 } }
                else { return struct { value i16 = 23 } }
            } }
        }
        main proc() i16 {
            small Storage(.small) :: {}
            wide Storage(.wide) :: {}
            return i16(small.value) + wide.value
        }
        """, b"42\n", None,
    ),
    "inline_match_auto_tag_nested_expansion_infers_exact_payload_generics": (
        """
        Choice :: union(enum) { number i8, empty void }
        identity proc($T type, value T) T { return value }
        read proc(input Choice) i16 {
            match input { inline |payload, tag|: match tag {
                inline |value|: {
                    if (value == .number) { return i16(identity(payload)) }
                    else {
                        payload
                        return 2
                    }
                }
            } }
        }
        main proc() i16 { return read(.number{40}) + read(.empty) }
        """, b"42\n", None,
    ),
    "inline_match_type_producer_updates_outer_type_and_value_locals": (
        """
        Choice :: union(enum) { number u8, empty void }
        Storage proc(input Choice) type {
            length usize := 1
            Element := i8
            for [input] |item| {
                match item { inline |payload, tag|: {
                    if (tag == .number) {
                        length = usize(payload)
                        Element = i16
                    } else { length = 2 }
                } }
            }
            return struct { values [length]Element }
        }
        main proc() i16 {
            value Storage(.number{2}) :: { values = [19, 23] }
            return value.values[0] + value.values[1]
        }
        """, b"42\n", None,
    ),
    "inline_for_heterogeneous_tuple_exact_captures_and_nested_values": (
        """
        Choice :: union(enum) { number i8, empty void }
        values proc() struct { i8, bool, string, [2]i16, Choice, ?i16 } {
            return {19, true, "ok", [10, 11], .number{2}, 0}
        }
        narrow proc(value i8) i16 { return i16(value) }
        main proc() i16 {
            total i16 := 0
            inline for values() |value, index| {
                at usize :: index
                if (at == 0) {
                    total = total + narrow(value)
                } else if (at == 1) {
                    if (!value) return -1
                } else if (at == 2) {
                    text string :: value
                    if (text.len != 2) return -2
                } else if (at == 3) {
                    total = total + value[0] + value[1]
                } else if (at == 4) {
                    match value { .number |payload|: { total = total + narrow(payload) }, .empty: {} }
                } else total = total + value?
            }
            return total
        }
        """, b"42\n", None,
    ),
    "inline_for_mixed_type_value_tuple_generates_runtime_storage": (
        """
        Child :: struct { value i16 = 40 }
        Metadata :: alias struct { type, usize, type, string }
        metadata proc() Metadata { return {Child, 2, i16, "ok"} }
        answer proc($input Metadata, bias i16) i16 {
            total i16 := 0
            inline for input |item, index| {
                if (index == 0) {
                    value item :: {}
                    total = total + value.value
                } else if (index == 1) {
                    values [item]i16 :: [0, bias]
                    total = total + values[1]
                } else if (index == 2) {
                    value item :: total
                    total = value
                } else if (item.len != 2) return -1
            }
            return total
        }
        main proc() i16 { return answer(metadata(), 2) }
        """, b"42\n", None,
    ),
    "inline_for_tuple_literals_empty_shapes_and_static_transfers": (
        """
        main proc() i16 {
            total i16 := 0
            cleanups i16 := 0
            inline for ({}) |_| { return false }
            inline for ({i8(19), true, "stop", i16(99)}) |item, index| {
                defer cleanups = cleanups + 1
                if (index == 0) {
                    total = total + i16(item)
                } else if (index == 1) {
                    continue
                } else if (index == 2) {
                    break
                } else total = false
                total = total + 20
            }
            return total + cleanups
        }
        """, b"42\n", None,
    ),
    "inline_for_type_producer_tuple_places_observe_updates_and_isolate_copies": (
        """
        Storage proc() type {
            config := {i8, usize(1), "before"}
            saved :: config
            length usize := 0
            inline for config |item, index| {
                if (index == 0) {
                    if (item != i8) return bool
                    config.0 = i16
                    config.1 = 2
                    config.2 = "after"
                } else if (index == 1) {
                    length = item
                } else if (item.len != 5) return bool
            }
            if (saved.0 != i8 or saved.1 != 1 or (saved.2).len != 6) return bool
            Element :: config.0
            return struct { values [length]Element }
        }
        main proc() i16 {
            value Storage() :: { values = [19, 23] }
            return value.values[0] + value.values[1]
        }
        """, b"42\n", None,
    ),
    "inline_for_nested_tuple_projections_and_aggregate_copy_isolation": (
        """
        Item :: struct { values [2]mut i16 }
        tuples proc() struct { struct { Item, bool }, string } {
            return {{Item { values = [19, 23] }, true}, "ok"}
        }
        main proc() i16 {
            input :: tuples()
            total i16 := 0
            inline for input.0 |item, index| {
                if (index == 0) {
                    copy := item
                    copy.values[0] = 0
                    total = item.values[0] + item.values[1]
                } else if (!item) return -1
            }
            return total
        }
        """, b"42\n", None,
    ),
    "inline_for_tuple_rejects_runtime_dependency_even_with_discard_capture": (
        """
        walk proc(value i16) i16 {
            inline for ({i8(1), value}) |_| { break }
            return 42
        }
        main proc() i16 { return walk(42) }
        """, b"", b"compile-time",
    ),
    "inline_for_tuple_rejects_named_records": (
        """
        main proc() i16 {
            inline for ({number = i16(42)}) |value| { return value }
            return 0
        }
        """, b"", b"for-loop iteration requires",
    ),
    "inline_for_tuple_capture_is_immutable": (
        """
        main proc() i16 {
            inline for ({i8(1), true}) |value, index| {
                if (index == 0) value = 42
            }
            return 0
        }
        """, b"", b"assignment requires a mutable local binding",
    ),
    "inline_for_tuple_preserves_narrow_overflow": (
        """
        main proc() i16 {
            inline for ({i8(127), true}) |value, index| {
                if (index == 0) next :: value + 1
            }
            return 42
        }
        """, b"", b"integer arithmetic overflow",
    ),
    "inline_for_tuple_rejects_storage_escape_before_break": (
        """
        values proc() struct { i16, @i16 } {
            local i16 :: 2
            return {42, &local}
        }
        main proc() i16 {
            inline for values() |_| { break }
            return 42
        }
        """, b"", b"escape",
    ),
    "ordinary_for_still_rejects_heterogeneous_tuple": (
        """
        main proc() i16 {
            for ({i16(42), true}) |_| {}
            return 42
        }
        """, b"", b"for-loop iteration requires",
    ),
    "inline_for_range_runtime_calls_and_constant_branches": (
        """
        add proc(value i16, bias i16) i16 { return value + bias }
        answer proc(bias i16) i16 {
            total i16 := 0
            inline for (i8(0)..i8(3)) |index| {
                if (index == 0) {
                    total = add(total, bias)
                } else {
                    total = add(total, i16(index))
                }
                if (index > 9) return false
            }
            return total
        }
        main proc() i16 { return answer(39) }
        """, b"42\n", None,
    ),
    "inline_for_inferred_arrays_concretize_scalar_shorthand": (
        """
        signed proc(value i64) i64 { return value }
        real proc(value f64) f64 { return value }
        integer proc() int { return 23 }
        floating proc() float { return 22.5 }
        main proc() i64 {
            total i64 := 0
            inline for [int(19), integer()] |value| { total = total + signed(value) }
            fraction f64 := 0.0
            inline for [float(19.5), floating()] |value| { fraction = fraction + real(value) }
            return total + i64(fraction) - 42
        }
        """, b"42\n", None,
    ),
    "inline_for_array_constants_and_usize_indices": (
        """
        data proc() [2]i16 { return [19, 22] }
        at proc(index usize, value i16) i16 { return value + i16(index) }
        main proc() i16 {
            total i16 := 0
            inline for data() |value, index| {
                if (value == 19) {
                    total = total + at(index, value)
                } else if (value == 22) {
                    total = total + at(index, value)
                } else return false
            }
            return total
        }
        """, b"42\n", None,
    ),
    "inline_for_string_bytes_embedded_nul_and_usize_indices": (
        r"""
        text proc() string { return "a\0b" }
        at proc(index usize, byte u8) i16 {
            if (byte == 0) return i16(index) + 39
            return 1
        }
        main proc() i16 {
            total i16 := 0
            inline for text() |byte, index| {
                if (index == 1) {
                    if (byte != 0) return false
                } else if (byte == 0) return false
                total = total + at(index, byte)
            }
            return total
        }
        """, b"42\n", None,
    ),
    "inline_for_type_array_specializes_each_runtime_body": (
        """
        types proc() [2]type { return [i16, bool] }
        add proc(value i16) i16 { return value + 1 }
        main proc() i16 {
            total i16 := 0
            inline for types() |T, index| {
                if (T == i16) {
                    value T :: 39
                    total = total + add(value)
                } else {
                    value T :: true
                    if (value) total = total + i16(index) + 1
                }
            }
            return total
        }
        """, b"42\n", None,
    ),
    "inline_for_narrow_signed_and_unsigned_inclusive_maxima": (
        """
        signed proc(value i8) i16 { return i16(value) }
        unsigned proc(value u8) i16 { return i16(value) }
        main proc() [3]i16 {
            negative i16 := 0
            inline for (i8(-2)..i8(0)) |value| { negative = negative + signed(value) }
            high i16 := 0
            inline for (i8(126)..=i8(127)) |value| { high = high + signed(value) }
            top i16 := 0
            inline for (u8(254)..=u8(255)) |value| { top = top + unsigned(value) }
            return [negative, high, top]
        }
        """, b"[-3, 253, 509]\n", None,
    ),
    "inline_for_nested_inline_and_ordinary_loops": (
        """
        answer proc(length usize) i16 {
            total i16 := 0
            inline for [i16(10), i16(11)] |base| {
                inline for (i8(0)..i8(2)) |offset| {
                    if (offset == 0) continue
                    for (0..length) |index| {
                        if (index == 0) continue
                        total = total + base
                        break
                    }
                }
            }
            return total
        }
        main proc() i16 { return answer(3) + answer(2) }
        """, b"42\n", None,
    ),
    "inline_for_static_break_continue_run_iteration_defers": (
        """
        walk proc(cleanups @mut i16) i16 {
            total i16 := 0
            inline for (i8(0)..i8(5)) |index| {
                defer cleanups^ = cleanups^ + 1
                if (index < 1) continue
                if (index == 3) break
                total = total + i16(index)
            }
            return total
        }
        main proc() i16 {
            cleanups i16 := 0
            total :: walk(&cleanups)
            return total + cleanups + 35
        }
        """, b"42\n", None,
    ),
    "inline_for_static_transfers_skip_invalid_tail_and_later_iterations": (
        """
        main proc() i16 {
            inline for (i8(0)..i8(3)) |index| {
                if (index == 0) continue
                if (index == 1) break
                return false
            }
            return 42
        }
        """, b"42\n", None,
    ),
    "inline_for_tagged_match_transfers_prune_tail_and_payloads": (
        """
        Choice :: union(enum) { number i16, stop void }
        choices proc() [4]Choice { return [.number{0}, .number{39}, .stop, .number{99}] }
        main proc() i16 {
            total i16 := 0
            inline for choices() |choice| {
                defer total = total + 1
                match choice {
                    .number |value|: {
                        if (value == 0) continue
                        total = total + value
                        continue
                    },
                    .stop: break,
                }
                return false
            }
            return total
        }
        """, b"42\n", None,
    ),
    "inline_for_rejects_runtime_break": (
        """
        walk proc(stop bool) i16 {
            inline for (i8(0)..i8(2)) |_| {
                if (stop) break
            }
            return 42
        }
        main proc() i16 { return walk(true) }
        """, b"", b"compile-time-resolvable",
    ),
    "inline_for_rejects_runtime_continue": (
        """
        walk proc(skip bool) i16 {
            inline for [i16(1), i16(2)] |_| {
                if (skip) continue
            }
            return 42
        }
        main proc() i16 { return walk(true) }
        """, b"", b"compile-time-resolvable",
    ),
    "inline_for_runtime_return_runs_iteration_defer": (
        """
        find proc(take bool, cleanups @mut i16) i16 {
            inline for [i16(1), i16(2), i16(3)] |value| {
                defer cleanups^ = cleanups^ + 1
                if (take and value == 2) return 40
            }
            return 0
        }
        main proc() i16 {
            cleanups i16 := 0
            value :: find(true, &cleanups)
            return value + cleanups
        }
        """, b"42\n", None,
    ),
    "inline_for_runtime_error_return_runs_errdefer_only_on_error": (
        """
        Error :: enum { failed }
        walk proc(fail bool, cleanups @mut i16) i16 ! Error {
            inline for (i8(0)..i8(3)) |index| {
                defer cleanups^ = cleanups^ + 1
                errdefer cleanups^ = cleanups^ + 10
                if (fail and index == 1) return .failed
            }
            return 39
        }
        main proc() [2]i16 {
            failed i16 := 0
            success i16 := 0
            left :: walk(true, &failed) catch 30
            right :: walk(false, &success) catch 0
            return [left + failed, right + success]
        }
        """, b"[42, 42]\n", None,
    ),
    "inline_for_empty_ranges_arrays_strings_skip_invalid_typing": (
        """
        empty proc() [0]type { return [] }
        main proc() i16 {
            inline for (i8(2)..i8(-2)) |_| { return false }
            inline for (i8(1)..i8(1)) |_| { return i8(128) }
            inline for empty() |_| { return false }
            inline for "" |_| { return i8(128) }
            return 42
        }
        """, b"42\n", None,
    ),
    "inline_for_capture_keeps_narrow_arithmetic_overflow": (
        """
        main proc() i16 {
            inline for (i8(127)..=i8(127)) |value| { next :: value + 1 }
            return 42
        }
        """, b"", b"integer arithmetic overflow",
    ),
    "inline_for_capture_is_immutable": (
        """
        main proc() i16 {
            inline for [i16(1)] |value| { value = 42 }
            return 0
        }
        """, b"", b"assignment requires a mutable local binding",
    ),
    "inline_for_index_is_immutable": (
        """
        main proc() i16 {
            inline for "a" |_, index| { index = 42 }
            return 0
        }
        """, b"", b"assignment requires a mutable local binding",
    ),
    "inline_for_rejects_runtime_range_bound": (
        """
        walk proc(length usize) i16 {
            inline for (0..length) |_| {}
            return 42
        }
        main proc() i16 { return walk(2) }
        """, b"", b"compile-time value",
    ),
    "inline_for_rejects_runtime_array": (
        """
        walk proc(values [2]i16) i16 {
            inline for values |_| {}
            return 42
        }
        main proc() i16 { return walk([19, 23]) }
        """, b"", b"compile-time value",
    ),
    "inline_for_rejects_runtime_string": (
        """
        walk proc(text string) i16 {
            inline for text |_| {}
            return 42
        }
        main proc() i16 { return walk("ok") }
        """, b"", b"compile-time value",
    ),
    "inline_for_rejects_pointer_capture": (
        """
        main proc() i16 {
            inline for [i16(42)] |@value| { return value^ }
            return 0
        }
        """, b"", b"escape",
    ),
    "inline_for_materializes_readonly_byte_slice_elements": (
        """
        data proc() [1][]u8 { return ["borrowed"] }
        main proc() usize {
            inline for data() |value| { return value.len }
            return 0
        }
        """, b"8\n", None,
    ),
    "inline_for_rejects_mutable_byte_slice_element_escape": (
        """
        data proc() [1][]mut u8 {
            values [1]mut u8 := [42]
            return [values[..]]
        }
        main proc() usize {
            inline for data() |value| { return value.len }
            return 0
        }
        """, b"", b"escape",
    ),
    "inline_for_expansion_is_bounded": (
        """
        main proc() i16 {
            inline for (0..usize(100000)) |_| {}
            return 42
        }
        """, b"", b"step limit",
    ),
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
    "tagged_metadata_active_payload_updates_own_saved_copies": (
        """
        Detail :: struct { child type, rows [2]mut struct { child type, count usize } }
        Choice :: union(enum) { child type, detail Detail, empty void }
        Storage proc() type {
            direct Choice := .child{bool}
            direct_saved :: direct
            direct.child = i16
            if (direct_saved.child != bool or direct.child != i16) return bool
            value Choice := .detail{
                child = i8,
                rows = [{ child = bool, count = 1 }, { child = i16, count = 2 }],
            }
            saved :: value
            value.detail.child = bool
            value.detail.rows[1].child = u8
            value.detail.rows[1].count = 3
            if (saved.detail.child != i8 or saved.detail.rows[1].child != i16) return bool
            if (value.detail.rows[1].child != u8 or value.detail.rows[1].count != 3) return bool
            value = .child{i8}
            if (value.child != i8) return bool
            value = .empty
            return match value {
                .empty: [saved.detail.rows[1].count]saved.detail.rows[1].child,
                .child, .detail: bool,
            }
        }
        main proc() Storage() { return [40, 2] }
        """, b"[40, 2]\n", None,
    ),
    "tagged_metadata_undefined_storage_whole_replacement": (
        """
        Choice :: union(enum) { child type, empty void }
        Container :: struct { choice Choice }
        selected proc() type {
            local Choice := undefined
            record Container := undefined
            values [2]mut Choice := undefined
            local = .child{bool}
            record.choice = .child{i16}
            values[0] = local
            values[1] = record.choice
            saved :: values
            local = .empty
            record.choice = .empty
            values[1] = .child{u8}
            if (saved[0].child != bool or values[1].child != u8) return bool
            return saved[1].child
        }
        main proc() selected() { return 42 }
        """, b"42\n", None,
    ),
    "tagged_metadata_procedure_capture_erasure_generated_defaults_and_cache": (
        """
        Id :: distinct i16
        Other :: distinct i16
        Detail :: struct { child type, count usize, bias i16 }
        Choice :: union(enum) { detail Detail, child type, empty void }
        make proc(T type) Choice { return .detail{ child = T, count = 2, bias = 2 } }
        pass proc(input Choice) Choice {
            match input {
                .detail |payload|: {
                    copied := payload
                    copied.child = bool
                    copied.count = 3
                    return .detail{payload}
                },
                .child |T|: return .child{T},
                .empty: return .empty,
            }
        }
        Storage proc(input Choice) type {
            return match input {
                .detail |payload|: struct {
                    values [payload.count]payload.child
                    bias i16 = payload.bias
                },
                .child |T|: struct { value T },
                .empty: struct {},
            }
        }
        score proc($input Choice, value Storage(input)) i16 {
            return i16(value.values[0]) + i16(value.values[1]) + value.bias
        }
        main proc() i16 {
            Selected :: alias Storage(pass(make(Id)))
            if (Selected != Storage(make(Id))) return -1
            if (Selected == Storage(make(Other))) return -2
            if (Selected == Storage(.child{Id})) return -3
            value Selected :: { values = [Id(19), Id(21)] }
            same Storage(make(Id)) :: value
            return score(pass(make(Id)), same)
        }
        """, b"42\n", None,
    ),
    "tagged_metadata_sparse_explicit_tags_match_variants_not_unused_tags": (
        """
        Tag :: enum(i16) { unused = -90, empty = -7, child = 42 }
        Choice :: union(Tag) { child type, empty void }
        choose proc(present bool) Choice {
            if (present) return .child{i16}
            return .empty
        }
        Selected proc(input Choice) type {
            return match input { .child |T|: T, .empty: bool }
        }
        main proc() i16 {
            empty Selected(choose(false)) :: true
            value Selected(choose(true)) :: 42
            if (!empty) return 0
            return value
        }
        """, b"42\n", None,
    ),
    "tagged_metadata_reject_inactive_payload_write": (
        """
        Choice :: union(enum) { child type, empty void }
        Bad proc() type {
            value Choice := .empty
            value.child = i16
            return value.child
        }
        main proc() Bad() { return 42 }
        """, b"", b"tagged union field is not active",
    ),
    "tagged_metadata_reject_inactive_payload_read": (
        """
        Choice :: union(enum) { child type, empty void }
        Bad proc() type { value Choice :: .empty
            return value.child }
        main proc() Bad() { return 42 }
        """, b"", b"tagged union field is not active",
    ),
    "tagged_metadata_reject_wrong_nominal_union_literal": (
        """
        Choice :: union(enum) { child type }
        Other :: union(enum) { child type }
        make proc() Choice { return Other { child = i16 } }
        main proc() make().child { return 42 }
        """, b"", b"incompatible value type",
    ),
    "tagged_metadata_reject_unknown_member": (
        """
        Choice :: union(enum) { child type }
        make proc() Choice { return Choice { missing = i16 } }
        main proc() make().child { return 42 }
        """, b"", b"record has no such field",
    ),
    "tagged_metadata_reject_zero_initializers": (
        """
        Choice :: union(enum) { child type, empty void }
        make proc() Choice { return Choice {} }
        main proc() make().child { return 42 }
        """, b"", b"union construction requires exactly one member",
    ),
    "tagged_metadata_reject_multiple_initializers": (
        """
        Choice :: union(enum) { child type, other type }
        make proc() Choice { return Choice { child = i16, other = bool } }
        main proc() make().child { return 42 }
        """, b"", b"union construction requires exactly one member",
    ),
    "tagged_metadata_reject_explicit_tag_missing_variant": (
        """
        Tag :: enum { other }
        Choice :: union(Tag) { child type }
        make proc() Choice { return .child{i16} }
        main proc() make().child { return 42 }
        """, b"", b"union tag must be an enum containing every union variant",
    ),
    "tagged_metadata_reject_non_enum_tag": (
        """
        Choice :: union(i16) { child type }
        make proc() Choice { return .child{i16} }
        main proc() make().child { return 42 }
        """, b"", b"union tag must be an enum containing every union variant",
    ),
    "tagged_metadata_reject_untagged_overlapping_layout": (
        """
        Choice :: union { detail struct { children [1]type }, count usize }
        make proc() Choice { return Choice { detail = { children = [i16] } } }
        main proc() make().detail.children[0] { return 42 }
        """, b"", b"record fields require a concrete value type",
    ),
    "tagged_metadata_reject_undefined_result": (
        """
        Choice :: union(enum) { child type, empty void }
        make proc() Choice { value Choice := undefined
            return value }
        main proc() make().child { return 42 }
        """, b"", b"read before initialization",
    ),
    "tagged_metadata_reject_undefined_payload_read": (
        """
        Choice :: union(enum) { child type, empty void }
        Bad proc() type { value Choice := undefined
            return value.child }
        main proc() Bad() { return 42 }
        """, b"", b"read before initialization",
    ),
    "tagged_metadata_preserve_narrow_ordinary_payload_overflow": (
        """
        Choice :: union(enum) { child type, small i8 }
        Bad proc() type {
            value Choice := .small{127}
            value.small = value.small + 1
            return i16
        }
        main proc() Bad() { return 42 }
        """, b"", b"integer arithmetic overflow",
    ),
    "tagged_metadata_reject_mutating_match_capture": (
        """
        Choice :: union(enum) { detail struct { child type }, empty void }
        Bad proc() type {
            value Choice :: .detail{ child = i16 }
            match value {
                .detail |payload|: { payload.child = bool
                    return payload.child },
                .empty: return bool,
            }
        }
        main proc() Bad() { return 42 }
        """, b"", b"indexed assignment requires a writable array element",
    ),
    "metadata_slices_direct_literal_views_start_bounded_evaluation": (
        """
        Selected proc($items []type) type { return items[0] }
        main proc() [2]usize {
            value Selected(([i16, bool])[..1]) :: 42
            return [usize(value), $(([i16, bool])[1..].len)]
        }
        """, b"[42, 1]\n", None,
    ),
    "metadata_slices_contextual_literals_inputs_results_and_erased_inputs": (
        """
        Id :: distinct i16
        types proc() []type { return [Id, i16] }
        pass proc(values []type) []type { return values }
        window proc(values []type, start usize, end usize) []type { return values[start..end] }
        Selected proc(values []type) type { return values[0] }
        exact proc($values []type, value Selected(values)) Selected(values) { return value }
        Storage proc() type {
            empty []type :: []
            if (pass(empty).len != 0 or types().len != 2) return bool
            return Selected(window(pass([bool, Id]), 1, 2))
        }
        main proc() i16 {
            value Storage() :: 40
            return i16(exact(types(), value)) + 2
        }
        """, b"42\n", None,
    ),
    "metadata_slices_cache_keys_preserve_nominal_leaves_order_and_length": (
        """
        Id :: distinct i16
        Other :: distinct i16
        Token proc($values []type) type { return struct {} }
        types proc() []type {
            backing [4]type :: [bool, Id, i16, u8]
            return backing[1..3]
        }
        Storage proc() type {
            if (Token(types()) != Token([Id, i16])) return bool
            if (Token(types()) == Token([Other, i16])) return bool
            if (Token(types()) == Token([i16, Id])) return bool
            if (Token(types()) == Token([Id])) return bool
            if (Token([]) != Token([])) return bool
            return types()[0]
        }
        main proc() i16 {
            value Storage() :: 42
            return i16(value)
        }
        """, b"42\n", None,
    ),
    "metadata_record_slices_nested_leaves_and_exact_cache_identity": (
        """
        Id :: distinct i16
        Other :: distinct i16
        Info :: struct { child type, count usize, name string }
        Row :: struct { types []type, fields []Info }
        Token proc($values []Info) type { return struct {} }
        fields proc() []Info { return [{ child = Id, count = 2, name = "value" }] }
        rows proc() []Row {
            return [{ types = [Id, i16], fields = fields() }]
        }
        Storage proc() type {
            empty []Info :: []
            if (empty.len != 0 or rows()[0].types[1] != i16) return bool
            source :: rows()[0].fields
            copied := source[0]
            copied.child = bool
            copied.count = 3
            if (source[0].child != Id or source[0].count != 2) return bool
            if (Token(source) != Token(fields())) return bool
            if (Token(source) == Token([{ child = Other, count = 2, name = "value" }])) return bool
            if (Token(source) == Token([{ child = Id, count = 3, name = "value" }])) return bool
            if (Token(source) == Token([{ child = Id, count = 2, name = "other" }])) return bool
            return [source[0].count]source[0].child
        }
        main proc() i16 {
            values Storage() :: [40, 2]
            return i16(values[0]) + i16(values[1])
        }
        """, b"42\n", None,
    ),
    "metadata_slices_nested_slices_by_value_and_inline_iteration": (
        """
        Info :: struct { child type, count usize }
        rows proc() [][]type { return [[i8, i16], [], [bool]] }
        fields proc() []Info { return [{ child = i16, count = 2 }] }
        Storage proc() type {
            total usize := 0
            for rows() |row, index| {
                total += row.len + index
                for row |T| {
                    if (T == i16) total += 1
                }
            }
            inline for rows() |row| {
                inline for row |T| {
                    if (T == bool) total += 1
                }
            }
            for fields() |field| {
                copied := field
                copied.child = bool
                copied.count = 99
            }
            inline for fields() |field| {
                if (field.child != i16 or field.count != 2) return bool
            }
            if (total != 8) return bool
            return [fields()[0].count]fields()[0].child
        }
        main proc() Storage() { return [40, 2] }
        """, b"[40, 2]\n", None,
    ),
    "metadata_slices_subviews_empty_boundaries_and_readonly_backing_replacement": (
        """
        types proc() []type {
            backing [3]type := [bool, i16, u8]
            frozen :: backing
            saved :: frozen[1..2]
            backing = [u8, bool, bool]
            if (saved[0] != i16 or backing[1] != bool) return []
            return saved
        }
        Storage proc() type {
            source :: types()
            full :: source[..]
            nested :: full[0..full.len][..1]
            if (nested.len != 1 or nested[0] != i16) return bool
            if (source[0..0].len != 0 or source[1..1].len != 0) return bool
            empty []type :: []
            if (empty[..].len != 0 or empty[0..0].len != 0) return bool
            return nested[0]
        }
        main proc() Storage() { return 42 }
        """, b"42\n", None,
    ),
    "metadata_slices_optional_fallible_parameters_results_and_captures": (
        """
        Failure :: struct { types []type }
        Info :: struct { child type, count usize }
        choose proc(present bool, failed bool) ?[]Info ! Failure {
            if (failed) return Failure { types = [i16] }
            if (present) return some!([{ child = i16, count = 2 }])
            return null
        }
        pass proc(value ?[]Info ! Failure) ?[]Info ! Failure { return value }
        Token proc($value ?[]Info ! Failure) type { return struct {} }
        Storage proc() type {
            original :: pass(choose(true, false))
            if (Token(original) != Token(choose(true, false))) return bool
            if (Token(original) == Token(choose(false, false))) return bool
            if (Token(original) == Token(choose(false, true))) return bool
            missing :: pass(choose(false, false)) catch null
            if (missing != null) return bool
            pass(choose(false, true)) catch |err| {
                if (err.types[0] != i16) return bool
                yield null
            }
            selected :: original catch null
            if selected |fields| {
                return [fields[0].count]fields[0].child
            }
            return bool
        }
        main proc() Storage() { return [40, 2] }
        """, b"[40, 2]\n", None,
    ),
    "metadata_slices_reject_mutable_slice_annotation": (
        """
        types proc() []mut type { return [i16] }
        main proc() types()[0] { return 42 }
        """, b"", b"compile-time result cannot escape evaluation as a source value",
    ),
    "metadata_slices_reject_mutable_array_backing": (
        """
        types proc() []type {
            backing [1]mut type := [i16]
            return backing[..]
        }
        main proc() types()[0] { return 42 }
        """, b"", b"compile-time result cannot escape evaluation as a source value",
    ),
    "metadata_slices_reject_mutable_binding_with_readonly_array_elements": (
        """
        types proc() []type {
            backing [1]type := [i16]
            return backing[..]
        }
        main proc() types()[0] { return 42 }
        """, b"", b"compile-time result cannot escape evaluation as a source value",
    ),
    "metadata_record_slices_reject_mutable_array_backing": (
        """
        Info :: struct { child type, count usize }
        fields proc() []Info {
            backing [1]mut Info := [{ child = i16, count = 2 }]
            return backing[..]
        }
        main proc() fields()[0].child { return 42 }
        """, b"", b"compile-time result cannot escape evaluation as a source value",
    ),
    "metadata_slices_reject_sentinel_annotation": (
        """
        types proc() [;0]type { return [i16] }
        main proc() types()[0] { return 42 }
        """, b"", b"operator requires compatible operand types",
    ),
    "metadata_slices_reject_sentinel_subview": (
        """
        types proc() []type {
            backing [2]type :: [i16, bool]
            return backing[..1;0]
        }
        main proc() types()[0] { return 42 }
        """, b"", b"operator requires compatible operand types",
    ),
    "metadata_slices_reject_reversed_bounds": (
        """
        types proc() []type {
            backing [2]type :: [i16, bool]
            return backing[2..1]
        }
        main proc() types()[0] { return 42 }
        """, b"", b"slice bounds are invalid",
    ),
    "metadata_slices_reject_excessive_array_bounds": (
        """
        types proc() []type {
            backing [1]type :: [i16]
            return backing[..2]
        }
        main proc() types()[0] { return 42 }
        """, b"", b"slice bounds are invalid",
    ),
    "metadata_slices_reject_excessive_subview_bounds": (
        """
        types proc() []type { return [i16, bool] }
        bad proc() []type { return types()[1..2][..2] }
        main proc() bad()[0] { return 42 }
        """, b"", b"slice bounds are invalid",
    ),
    "metadata_slices_reject_start_beyond_empty_boundary": (
        """
        types proc() []type { return [] }
        bad proc() []type { return types()[1..1] }
        main proc() bad()[0] { return 42 }
        """, b"", b"slice bounds are invalid",
    ),
    "metadata_slices_reject_non_usize_start": (
        """
        types proc() []type { return [i16] }
        bad proc() []type { return types()[u8(0)..] }
        main proc() bad()[0] { return 42 }
        """, b"", b"array index must have type usize",
    ),
    "metadata_slices_reject_non_usize_end": (
        """
        types proc() []type { return [i16] }
        bad proc() []type { return types()[..i16(1)] }
        main proc() bad()[0] { return 42 }
        """, b"", b"array index must have type usize",
    ),
    "metadata_slices_reject_non_usize_element_index": (
        """
        types proc() []type { return [i16] }
        main proc() types()[u8(0)] { return 42 }
        """, b"", b"array index must have type usize",
    ),
    "metadata_slices_reject_element_index_at_length": (
        """
        types proc() []type { return [i16] }
        main proc() types()[types().len] { return 42 }
        """, b"", b"index out of bounds",
    ),
    "metadata_slices_reject_empty_element_index": (
        """
        types proc() []type { return [] }
        main proc() types()[0] { return 42 }
        """, b"", b"index out of bounds",
    ),
    "metadata_slices_reject_non_type_literal_leaf": (
        """
        types proc() []type { return [42] }
        main proc() types()[0] { return 42 }
        """, b"", b"incompatible value type",
    ),
    "metadata_record_slices_reject_incompatible_literal_field": (
        """
        Info :: struct { child type, count usize }
        fields proc() []Info { return [{ child = i16, count = false }] }
        main proc() fields()[0].child { return 42 }
        """, b"", b"incompatible value type",
    ),
    "metadata_slices_reject_readonly_element_assignment": (
        """
        types proc() []type {
            values []type := [i16]
            values[0] = bool
            return values
        }
        main proc() types()[0] { return 42 }
        """, b"", b"writable",
    ),
    "metadata_record_slices_reject_readonly_field_assignment": (
        """
        Info :: struct { child type, count usize }
        fields proc() []Info {
            values []Info := [{ child = i16, count = 2 }]
            values[0].count = 3
            return values
        }
        main proc() fields()[0].child { return 42 }
        """, b"", b"writable",
    ),
    "metadata_slices_reject_uninitialized_type_leaf": (
        """
        types proc() []type {
            backing [1]type := undefined
            frozen :: backing
            return frozen[..]
        }
        main proc() types()[0] { return 42 }
        """, b"", b"read before initialization",
    ),
    "metadata_record_slices_reject_uninitialized_value_leaf": (
        """
        Info :: struct { child type, count usize }
        fields proc() []Info {
            partial Info := undefined
            partial.child = i16
            return [partial]
        }
        main proc() fields()[0].child { return 42 }
        """, b"", b"read before initialization",
    ),
    "metadata_slices_reject_runtime_producer_dependencies": (
        """
        types proc(wide bool) []type {
            if (wide) return [i16]
            return [i8]
        }
        read proc(wide bool) i16 {
            value types(wide)[0] :: 42
            return i16(value)
        }
        main proc() i16 { return read(true) }
        """, b"", b"compile-time value",
    ),
    "metadata_slices_inline_captures_specialize_runtime_bodies": (
        """
        types proc() []type { return [i16, u8] }
        add proc(value i16) i16 { return value + 1 }
        main proc() i16 {
            total i16 := 0
            inline for types() |T, index| {
                value T :: 19
                total += add(i16(value)) + i16(index + 1)
            }
            return total - 1
        }
        """, b"42\n", None,
    ),
    "optional_types_procedure_boundaries_copies_and_static_captures": (
        """
        Token :: distinct i16
        choose proc(present bool) ?type {
            if (present) return Token
            return null
        }
        pass proc(value ?type) ?type { return value }
        Box proc(value ?type) type {
            T :: if value |Child| Child else bool
            return struct { item T }
        }
        Selected proc(value ?type) type { return value? }
        exact proc($value ?type, input Selected(value)) Selected(value) { return input }
        Storage proc() type {
            selected ?type := pass(choose(true))
            saved :: selected
            selected = null
            if (selected != null or null == saved) return bool
            if (Box(saved) != Box(choose(true))) return bool
            if (Box(null) == Box(saved)) return bool
            while saved |T| {
                return [2]T
            }
            return bool
        }
        main proc() i16 {
            values Storage() :: [40, 2]
            return i16(exact(choose(true), values[0])) + i16(values[1])
        }
        """, b"42\n", None,
    ),
    "optional_metadata_records_nested_presence_and_owned_updates": (
        """
        Config :: struct { child type, count usize }
        config proc() ?Config { return { child = i16, count = 2 } }
        nested proc(present bool) ??type {
            if (present) return some!(null)
            return null
        }
        Storage proc() type {
            outer :: nested(true)
            if outer |inner| {
                if (inner != null or nested(false) != null) return bool
            } else return bool
            values [2]mut ?Config := [config(), null]
            saved :: values
            values[0] = null
            values[1] = { child = bool, count = 3 }
            if (values[0] != null or saved[1] != null) return bool
            info :: saved[0]?
            return [info.count]info.child
        }
        main proc() Storage() { return [40, 2] }
        """, b"[40, 2]\n", None,
    ),
    "optional_types_fill_undefined_fields_and_yield_before_cleanup": (
        """
        Config :: struct { child ?type = bool, count usize }
        make proc() ?Config {
            config Config := undefined
            config.child = i16
            config.count = 2
            return some!(config)
        }
        Storage proc() type {
            value ?type := undefined
            value = null
            selected ?type :: {
                defer value = bool
                yield make()?.child
            }
            if (value? != bool) return bool
            T :: selected?
            return [make()?.count]T
        }
        main proc() Storage() { return [40, 2] }
        """, b"[40, 2]\n", None,
    ),
    "optional_types_reject_null_unwrap": (
        """
        empty proc() ?type { return null }
        Bad proc() type { return empty()? }
        main proc() Bad() { return 42 }
        """, b"", b"cannot unwrap a null optional",
    ),
    "optional_types_reject_non_type_payload": (
        """
        Bad proc() ?type { return some!(42) }
        Selected proc() type { return Bad()? }
        main proc() Selected() { return 42 }
        """, b"", b"incompatible value type",
    ),
    "optional_types_reject_incomplete_payload_result": (
        """
        Config :: struct { child type, count usize }
        bad proc() ?Config {
            value Config := undefined
            value.child = i16
            return some!(value)
        }
        Selected proc() type { return bad()?.child }
        main proc() Selected() { return 42 }
        """, b"", b"read before initialization",
    ),
    "optional_types_reject_uninitialized_presence": (
        """
        Bad proc() type {
            value ?type := undefined
            if value |_| return i16
            return bool
        }
        main proc() Bad() { return 42 }
        """, b"", b"read before initialization",
    ),
    "optional_types_reject_capture_mutation": (
        """
        Bad proc() type {
            value ?type :: i16
            if value |T| { T = bool }
            return i16
        }
        main proc() Bad() { return 42 }
        """, b"", b"mutable local binding",
    ),
    "optional_types_reject_runtime_dependency": (
        """
        choose proc(present bool) ?type {
            if (present) return i16
            return null
        }
        main proc() i16 {
            present := true
            return if choose(present) |_| 42 else 0
        }
        """, b"", b"compile-time value",
    ),
    "fallible_types_record_channels_select_runtime_storage": (
        """
        Config :: struct { child ?type, count usize }
        Failure :: struct { child type, count usize }
        make proc(failed bool) Config ! Failure {
            if (failed) return Failure { child = bool, count = 2 }
            return Config { child = i16, count = 2 }
        }
        Selected proc(failed bool) type {
            config :: make(failed) catch |err| Config { child = err.child, count = err.count }
            T :: config.child?
            return [config.count]T
        }
        main proc() [2]i16 {
            values Selected(false) :: [40, 2]
            flags Selected(true) :: [true, false]
            if (!flags[0] or flags[1]) return [0, 0]
            return [values[0] + values[1], i16(flags.len)]
        }
        """, b"[42, 2]\n", None,
    ),
    "fallible_types_optional_success_try_and_composed_errors": (
        """
        First :: enum { first }
        Second :: enum { second }
        choose proc(present bool, failed bool) ?type ! First {
            if (failed) return .first
            if (present) return i16
            return null
        }
        forward proc(present bool, failed bool) ?type ! (First | Second) {
            selected :: try choose(present, failed)
            return selected
        }
        Selected proc(present bool, failed bool) type {
            selected :: forward(present, failed) catch |err| match err {
                .first: some!(bool),
                .second: some!(u16),
            }
            return if selected |T| T else u8
        }
        main proc() [2]i16 {
            value Selected(true, false) :: 40
            missing Selected(false, false) :: 2
            recovered Selected(true, true) :: true
            if (!recovered) return [0, 0]
            return [value + i16(missing), i16(sizeof!(Selected(false, true)))]
        }
        """, b"[42, 1]\n", None,
    ),
    "fallible_types_copies_procedure_inputs_and_erased_cache_identity": (
        """
        Config :: struct { child type, count usize }
        Failure :: struct { child type, count usize }
        good proc() Config ! Failure { return Config { child = i16, count = 2 } }
        bad proc() Config ! Failure { return Failure { child = bool, count = 2 } }
        pass proc(value Config ! Failure) Config ! Failure { return value }
        Token proc($value Config ! Failure) type { return struct {} }
        Selected proc(value Config ! Failure) type {
            config :: value catch |err| Config { child = err.child, count = err.count }
            return [config.count]config.child
        }
        exact proc($value Config ! Failure, input Selected(value)) Selected(value) { return input }
        Storage proc() type {
            value Config ! Failure := pass(good())
            saved :: value
            copied := pass(value)
            value = bad()
            copied = Config { child = u8, count = 3 }
            if (Token(saved) != Token(good())) return bool
            if (Token(value) != Token(bad())) return bool
            if (Token(saved) == Token(value) or Token(saved) == Token(copied)) return bool
            if (Token(saved) != Token(Config { child = i16, count = 2 })) return bool
            if (Token(value) == Token(Failure { child = u8, count = 2 })) return bool
            if (Token(value) == Token(Failure { child = bool, count = 3 })) return bool
            if (Token(saved) == Token(Failure { child = i16, count = 2 })) return bool
            return Selected(saved)
        }
        main proc() i16 {
            values Storage() :: exact(good(), [40, 2])
            flags :: exact(bad(), [true, false])
            if (!flags[0] or flags[1]) return 0
            return values[0] + values[1]
        }
        """, b"42\n", None,
    ),
    "fallible_types_nested_optional_arrays_preserve_owned_replacements": (
        """
        Config :: struct { child type, count usize }
        Failure :: struct { child ?type, count usize }
        Packet :: struct { rows [2]mut (?Config ! Failure) }
        make proc() Packet {
            return { rows = [
                some!(Config { child = i16, count = 2 }),
                Failure { child = bool, count = 3 },
            ] }
        }
        pass proc(value Packet) Packet { return value }
        Storage proc() type {
            value := make()
            saved :: pass(value)
            copied := saved
            value.rows[0] = null
            value.rows[1] = some!(Config { child = u8, count = 4 })
            copied.rows[0] = Failure { child = null, count = 5 }
            if ((value.rows[0] catch null) != null) return bool
            changed :: value.rows[1] catch null
            if (changed?.child != u8 or changed?.count != 4) return bool
            copied.rows[0] catch |err| {
                if (err.child != null or err.count != 5) return bool
                yield null
            }
            config :: (saved.rows[0] catch null)?
            saved.rows[1] catch |err| {
                if (err.child? != bool or err.count != 3) return bool
                return [config.count]config.child
            }
            return bool
        }
        main proc() Storage() { return [40, 2] }
        """, b"[40, 2]\n", None,
    ),
    "fallible_types_return_snapshots_and_cleanup_order": (
        """
        Config :: struct { child type, count usize, trace usize }
        Failure :: struct { child type, count usize, trace usize }
        make proc(failed bool) Config ! Failure {
            trace usize := 0
            value Config ! Failure := undefined
            defer {
                if (trace != (if (failed) 321 else 31)) unreachable
                value catch |err| {
                    if (err.child != u8 or err.count != 9 or err.trace != 9) unreachable
                    yield Config { child = u8, count = 9, trace = 9 }
                }
            }
            defer value = Failure { child = u8, count = 9, trace = 9 }
            defer trace = trace * 10 + 1
            errdefer |err| {
                if (err.child != bool or err.count != 2 or err.trace != 3) unreachable
                trace = trace * 10 + 2
            }
            { defer trace = trace * 10 + 3 }
            value = Config { child = i16, count = 2, trace = trace }
            if (failed) value = Failure { child = bool, count = 2, trace = trace }
            return value
        }
        Storage proc() type {
            config :: make(false) catch { return bool }
            make(true) catch |err| {
                if (err.child != bool or err.count != 2) return bool
                if (config.child != i16 or config.count != 2) return bool
                return struct {
                    values [config.count]config.child
                    success usize = config.trace
                    failure usize = err.trace
                }
            }
            return bool
        }
        main proc() [3]i16 {
            storage Storage() :: { values = [40, 2] }
            return [storage.values[0] + storage.values[1], i16(storage.success), i16(storage.failure)]
        }
        """, b"[42, 3, 3]\n", None,
    ),
    "fallible_types_reject_incomplete_success_payload": (
        """
        Config :: struct { child type, count usize }
        Error :: enum { failed }
        bad proc() Config ! Error {
            value Config := undefined
            value.child = i16
            return value
        }
        Selected proc() type { return (bad() catch { return bool }).child }
        main proc() Selected() { return 42 }
        """, b"", b"read before initialization",
    ),
    "fallible_types_reject_incomplete_error_payload": (
        """
        Failure :: struct { child type, count usize }
        bad proc() type ! Failure {
            value Failure := undefined
            value.child = i16
            return value
        }
        Selected proc() type { return bad() catch |err| err.child }
        main proc() Selected() { return 42 }
        """, b"", b"read before initialization",
    ),
    "fallible_types_reject_uninitialized_channel": (
        """
        Error :: enum { failed }
        Selected proc() type {
            value type ! Error := undefined
            return value catch bool
        }
        main proc() Selected() { return 42 }
        """, b"", b"read before initialization",
    ),
    "fallible_types_reject_non_type_payload": (
        """
        Error :: enum { failed }
        bad proc() type ! Error { return 42 }
        Selected proc() type { return bad() catch bool }
        main proc() Selected() { return 42 }
        """, b"", b"incompatible value type",
    ),
    "fallible_types_reject_incompatible_error_propagation": (
        """
        First :: enum { first }
        Second :: enum { second }
        leaf proc() type ! First { return .first }
        bad proc() type ! Second { return try leaf() }
        Selected proc() type { return bad() catch i16 }
        main proc() Selected() { return 42 }
        """, b"", b"propagated errors are not a subset",
    ),
    "fallible_types_reject_catch_capture_mutation": (
        """
        Failure :: struct { child type }
        bad proc() type ! Failure { return Failure { child = i16 } }
        Selected proc() type {
            return bad() catch |err| {
                err.child = bool
                yield err.child
            }
        }
        main proc() Selected() { return 42 }
        """, b"", b"writable array element",
    ),
    "fallible_types_reject_errdefer_capture_mutation": (
        """
        Failure :: struct { child type }
        bad proc() type ! Failure {
            errdefer |err| { err.child = bool }
            return Failure { child = i16 }
        }
        Selected proc() type { return bad() catch |err| err.child }
        main proc() Selected() { return 42 }
        """, b"", b"writable array element",
    ),
    "fallible_types_reject_runtime_dependency": (
        """
        Error :: enum { failed }
        choose proc(failed bool) type ! Error {
            if (failed) return .failed
            return i16
        }
        main proc() i16 {
            failed := false
            T :: choose(failed) catch bool
            value T :: 42
            return value
        }
        """, b"", b"compile-time value",
    ),
    "fallible_types_direct_catches_select_types_and_runtime_handlers": (
        """
        Error :: enum { failed }
        Failure :: struct { child type }
        choose proc(failed bool) type ! Error {
            if (failed) return .failed
            return i16
        }
        compute proc() i16 ! Failure { return Failure { child = i16 } }
        recover proc(input i16) i16 {
            return compute() catch |err| {
                value err.child :: input
                yield value + 2
            }
        }
        main proc() [2]i16 {
            Selected :: choose(false) catch bool
            Recovered :: choose(true) catch i16
            first Selected :: 40
            second Recovered :: 2
            return [first + second, recover(40)]
        }
        """, b"[42, 42]\n", None,
    ),
    "fallible_types_composed_metadata_errors_preserve_payloads_and_nominals": (
        """
        Child :: distinct i16
        Failure :: struct { child type, count usize }
        Other :: enum { missing }
        leaf proc(failed bool) type ! Failure {
            if (failed) return Failure { child = Child, count = 2 }
            return bool
        }
        forward proc(failed bool) type ! (Failure | Other) { return try leaf(failed) }
        Storage proc() type {
            return forward(true) catch |err| match err {
                .Failure |info|: [info.count]info.child,
                .missing: bool,
            }
        }
        main proc() i16 {
            values Storage() :: [40, 2]
            return i16(values[0]) + i16(values[1])
        }
        """, b"42\n", None,
    ),
    "fallible_types_reject_invalid_direct_catch_result": (
        """
        Error :: enum { failed }
        choose proc() type ! Error { return .failed }
        main proc() i16 {
            Selected :: choose() catch 42
            return 42
        }
        """, b"", b"incompatible value type",
    ),
    "fallible_types_void_completion_and_success_precedence": (
        """
        Success :: enum { same, good }
        Failure :: union(enum) { same type, bad type }
        work proc(mode usize) void ! Failure {
            errdefer |err| {
                if (mode != 2) unreachable
                match err { .same |T|: if (T != i16) unreachable, .bad: unreachable }
            }
            if (mode == 0) return
            if (mode == 2) return Failure { same = i16 }
        }
        choose proc() Success ! Failure { return .same }
        Storage proc() type {
            work(0) catch { return bool }
            work(1) catch { return bool }
            tag :: choose() catch { return bool }
            if (tag != .same) return bool
            work(2) catch |err| {
                return match err { .same |T|: T, .bad: bool }
            }
            return bool
        }
        main proc() Storage() { return 42 }
        """, b"42\n", None,
    ),
    "fallible_types_reject_backed_enum_metadata_composition": (
        """
        Failure :: struct { child type }
        Other :: enum(i16) { failed = 1 }
        bad proc() type ! (Failure | Other) { return i16 }
        Selected proc() type { return bad() catch bool }
        main proc() Selected() { return 42 }
        """, b"", b"sum composition requires compatible",
    ),
    "type_records_undefined_nested_updates_preserve_partial_copies": (
        """
        Config :: struct {
            child type = bool
            detail struct { count usize, bias i16 }
            rows [2]mut struct { child type, count usize }
        }
        config proc() Config {
            value Config := undefined
            value.child = i16
            value.detail.count = 2
            copied := value
            value.detail.bias = 99
            copied.detail.bias = 2
            for (usize(0)..copied.rows.len) |index| {
                copied.rows[index].child = copied.child
                copied.rows[index].count = index + 1
            }
            return copied
        }
        Storage proc(input Config) type {
            return struct { values [input.detail.count]input.rows[1].child, bias i16 = input.detail.bias }
        }
        main proc() i16 {
            value Storage(config()) :: { values = [19, 21] }
            same Storage(config()) :: value
            return same.values[0] + same.values[1] + same.bias
        }
        """, b"42\n", None,
    ),
    "type_records_undefined_tuples_replacement_and_empty_fields": (
        """
        Pair :: struct { type, struct { child type, values [0]mut type } }
        pair proc() Pair {
            value Pair := undefined
            value.0 = bool
            value.1.child = i8
            copied := value
            value = {i16, { child = i16, values = [] }}
            copied.0 = value.0
            copied.1 = value.1
            return copied
        }
        Element proc(input Pair) type { return field!(input, "0") }
        add proc($schema Pair, value Element(schema)) Element(schema) {
            if (schema.1.child != i16) return 0
            return value + 2
        }
        main proc() i16 { return add(pair(), 40) }
        """, b"42\n", None,
    ),
    "type_records_undefined_reject_type_leaf_read": (
        """
        Bad proc() type {
            value struct { child type } := undefined
            return value.child
        }
        main proc() Bad() { return 42 }
        """, b"", b"local value was read before initialization",
    ),
    "type_records_undefined_reject_value_leaf_read": (
        """
        Bad proc() type {
            value struct { child type, count usize } := undefined
            value.child = i16
            return [value.count]value.child
        }
        main proc() Bad() { return 42 }
        """, b"", b"local value was read before initialization",
    ),
    "type_records_undefined_defaults_do_not_initialize_fields": (
        """
        config proc() struct { child type = i16 } {
            value struct { child type = i16 } := undefined
            return value
        }
        main proc() config().child { return 42 }
        """, b"", b"local value was read before initialization",
    ),
    "type_records_undefined_copy_retains_uninitialized_leaves": (
        """
        Bad proc() type {
            value struct { child type, count usize } := undefined
            value.child = i16
            copied :: value
            value.count = 2
            return [copied.count]copied.child
        }
        main proc() Bad() { return 42 }
        """, b"", b"local value was read before initialization",
    ),
    "type_records_undefined_reject_partial_argument": (
        """
        Config :: struct { child type, count usize }
        Selected proc(value Config) type { return value.child }
        Bad proc() type {
            value Config := undefined
            value.child = i16
            return Selected(value)
        }
        main proc() Bad() { return 42 }
        """, b"", b"local value was read before initialization",
    ),
    "type_records_undefined_reject_partial_erased_argument": (
        """
        Config :: struct { child type, count usize }
        selected proc($value Config) i16 { return 42 }
        Bad proc() type {
            value Config := undefined
            value.child = i16
            return [usize(selected(value))]i16
        }
        main proc() Bad() { return [] }
        """, b"", b"local value was read before initialization",
    ),
    "type_records_undefined_reject_readonly_field_update": (
        """
        Bad proc() type {
            value struct { child type } :: undefined
            value.child = i16
            return i16
        }
        main proc() Bad() { return 42 }
        """, b"", b"indexed assignment requires a writable array element",
    ),
    "type_records_undefined_reject_readonly_nested_array_update": (
        """
        Bad proc() type {
            value struct { children [1]type } := undefined
            value.children[0] = i16
            return i16
        }
        main proc() Bad() { return 42 }
        """, b"", b"indexed assignment requires a writable array element",
    ),
    "type_records_undefined_reject_invalid_schema": (
        """
        Bad proc() type {
            value struct { child type, count i8 = 128 } := undefined
            return i16
        }
        main proc() Bad() { return 42 }
        """, b"", b"numeric literal is outside its runtime type's range",
    ),
    "type_records_undefined_construction_is_bounded": (
        """
        Bad proc() type {
            value struct { child type, rows [1000000000]mut struct { child type } } := undefined
            return i16
        }
        main proc() Bad() { return 42 }
        """, b"", b"compile-time execution exceeds its recursion or step limit",
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
    "comptime_computed_callbacks_preserve_optional_presence_and_calls": (
        """
        Callback :: alias @proc(value i16) i16
        increment proc(value i16) i16 { return value + 1 }
        decrement proc(value i16) i16 { return value - 1 }
        choose proc(up bool) Callback {
            callback Callback := decrement
            if (up) callback = increment
            return callback
        }
        maybe proc(present bool) ?Callback {
            if (present) return choose(true)
            return null
        }
        nested proc(present bool) ??Callback {
            if (present) return some!(maybe(true))
            return some!(null)
        }
        main proc() [6]i16 {
            up :: $(choose(true))
            down :: $(choose(false))
            optional :: $(maybe(true))
            absent :: $(maybe(false))
            outer :: $(nested(false))
            inner :: $(nested(true))
            return [up(41), down(43), (optional?)(41), ((inner?)?)(41),
                (if (absent == null) i16(42) else i16(0)),
                (if (outer != null and outer? == null) i16(42) else i16(0))]
        }
        """, b"[42, 42, 42, 42, 42, 42]\n", None,
    ),
    "comptime_callback_aggregates_cache_source_identity_and_generate_defaults": (
        """
        Callback :: alias @proc(value i16) i16
        increment proc(value i16) i16 { return value + 1 }
        decrement proc(value i16) i16 { return value - 1 }
        Callbacks :: struct { items [2]Callback, optional ?Callback }
        Choice :: union(enum) { callback Callback, empty void }
        Failure :: union(enum) { callback Callback }
        make proc(reverse bool) Callbacks {
            if (reverse) return { items = [decrement, increment], optional = null }
            return { items = [increment, decrement], optional = increment }
        }
        choice proc() Choice { return .callback{increment} }
        result proc(fail bool) Callback ! Failure {
            if (fail) return .callback{decrement}
            return increment
        }
        Box proc($callbacks Callbacks) type {
            return struct { callbacks Callbacks = callbacks }
        }
        main proc() [6]i16 {
            first Box(make(false)) :: {}
            same Box(Callbacks { items = [increment, decrement], optional = increment }) :: first
            other Box(make(true)) :: {}
            selected :: $(choice())
            success :: $(result(false)) catch increment
            failure :: $(result(true)) catch |err| err.callback
            return [same.callbacks.items[0](41), same.callbacks.optional?(41),
                other.callbacks.items[0](43), selected.callback(41),
                success(41), failure(43)]
        }
        """, b"[42, 42, 42, 42, 42, 42]\n", None,
    ),
    "comptime_computed_callback_signatures_remain_checked": (
        """
        Callback :: alias @proc(value i16) i16
        wrong proc(value u8) u8 { return value }
        choose proc() Callback { return wrong }
        main proc() i16 { return $(choose())(42) }
        """, b"", b"return does not match the function result type",
    ),
    "comptime_computed_callback_traps_keep_declaration_provenance": (
        """
        Callback :: alias @proc(value i8) i8
        increment proc(value i8) i8 { return value + 1 }
        choose proc() Callback { return increment }
        main proc() i8 { return $(choose())(127) }
        """, b"", b":2:38: runtime trap: integer arithmetic overflow",
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
    "comptime_aggregate_materializes_readonly_byte_slices": (
        """
        make proc() [1][]u8 { return ["borrowed"] }
        main proc() usize { return $(make())[0].len }
        """, b"8\n", None,
    ),
    "comptime_byte_slices_preserve_subviews_and_zero_sentinel": (
        """
        subview proc() []u8 { return "unused42ignored"[6..8] }
        terminated proc() [;0]u8 { return "unused42"[6..;0] }
        main proc() [5]u8 {
            plain :: $(subview())
            zero [;0]u8 :: $(terminated())
            return [plain[0], plain[1], zero[0], zero[1], zero.ptr[zero.len]]
        }
        """, b"[52, 50, 52, 50, 0]\n", None,
    ),
    "comptime_byte_slices_own_filled_local_arrays": (
        """
        make proc(seed u8) []u8 {
            values [3]mut u8 := undefined
            for (0..usize(3)) |index| values[index] = seed + u8(index)
            copy := values
            values[0] = 99
            return copy[1..]
        }
        main proc() [4]u8 {
            first :: $(make(40))
            second :: $(make(10))
            return [first[0], first[1], second[0], second[1]]
        }
        """, b"[41, 42, 11, 12]\n", None,
    ),
    "comptime_byte_slices_materialize_immutable_globals_and_raw_fields": (
        """
        bytes [4]u8 :: [9, 40, 2, 8]
        Views :: struct { rows [2][]u8 }
        make proc() Views { return { rows = [bytes[1..3], bytes[2..3]] } }
        main proc() [3]u8 {
            views :: $(make())
            return [views.rows[0][0], views.rows[0][1], views.rows[1][0]]
        }
        """, b"[40, 2, 2]\n", None,
    ),
    "comptime_byte_slices_preserve_nested_optional_tagged_and_fallible_channels": (
        """
        Failure :: struct { text []u8 }
        Choice :: union(enum) { data struct { rows [2]?[]u8 }, empty void }
        Packet :: struct { choice ?Choice, result []u8 ! Failure }
        make proc(fail bool) Packet {
            values [2]mut u8 := [40, 2]
            result []u8 ! Failure := values[..]
            if (fail) result = Failure { text = values[1..] }
            return {
                choice = Choice { data = { rows = [values[..1], null] } },
                result = result,
            }
        }
        main proc() [4]u8 {
            good :: $(make(false))
            bad :: $(make(true))
            choice :: good.choice?
            if (choice.data.rows[1] != null) return [0, 0, 0, 0]
            success :: good.result catch ""
            failure :: bad.result catch |err| err.text
            return [choice.data.rows[0]?[0], success[0], success[1], failure[0]]
        }
        """, b"[40, 40, 2, 2]\n", None,
    ),
    "comptime_byte_slices_normalize_generic_cache_content": (
        """
        make proc() []u8 {
            values [2]mut u8 := [40, 2]
            return values[..]
        }
        same proc() []u8 { return "(\\x28\\x02)"[1..3] }
        other proc() []u8 { return "\\x28\\x03" }
        Token proc($bytes []u8) type { return struct {} }
        score proc($bytes []u8) u8 { return bytes[0] + bytes[1] }
        main proc() u8 {
            if (Token(make()) != Token(same())) return 0
            if (Token(make()) != Token("\\x28\\x02")) return 0
            if (Token(make()) == Token(other())) return 0
            return score(make())
        }
        """, b"42\n", None,
    ),
    "comptime_byte_slices_supply_generated_defaults": (
        """
        make proc() []u8 {
            values [2]mut u8 := [40, 2]
            return values[..]
        }
        Generated proc() type {
            return struct_type!(.auto, ["bytes"], [[]u8], [make()])
        }
        main proc() u8 {
            value Generated() :: {}
            return value.bytes[0] + value.bytes[1]
        }
        """, b"42\n", None,
    ),
    "comptime_byte_slices_preserve_nul_high_bytes_and_empty_views": (
        r"""
        make proc() []u8 { return "!\x00\x80\xff?"[1..4] }
        empty proc() []u8 { return "ignored"[3..3] }
        zero proc() [;0]u8 { return ""[..;0] }
        main proc() [6]usize {
            bytes :: $(make())
            plain :: $(empty())
            terminated :: $(zero())
            return [usize(bytes[0]), usize(bytes[1]), usize(bytes[2]),
                plain.len, terminated.len, usize(terminated.ptr[0])]
        }
        """, b"[0, 128, 255, 0, 0, 0]\n", None,
    ),
    "comptime_byte_slices_reject_uninitialized_source_bytes": (
        """
        make proc() []u8 {
            values [2]mut u8 := undefined
            values[0] = 42
            return values[..]
        }
        main proc() usize { return $(make()).len }
        """, b"", b"local value was read before initialization",
    ),
    "comptime_byte_slices_ignore_unobserved_uninitialized_bytes": (
        """
        make proc() []u8 {
            values [3]mut u8 := undefined
            values[1] = 42
            return values[1..2]
        }
        main proc() u8 { return $(make())[0] }
        """, b"42\n", None,
    ),
    "comptime_byte_slices_reject_invalid_ranges": (
        """
        make proc() []u8 {
            values [1]u8 :: [42]
            return values[..2]
        }
        main proc() usize { return $(make()).len }
        """, b"", b"slice",
    ),
    "comptime_byte_slices_reject_oversized_pointer_views_before_allocation": (
        """
        make proc() []u8 {
            values [1]u8 :: [42]
            return values.ptr[..maxval!(usize)]
        }
        main proc() usize { return $(make()).len }
        """, b"", b"compile-time result cannot escape evaluation",
    ),
    "comptime_byte_slices_recheck_zero_sentinel_after_mutation": (
        """
        make proc() [;0]u8 {
            values [2]mut u8 := [42, 0]
            view [;0]u8 :: values[..1;0]
            values[1] = 7
            return view
        }
        main proc() usize { return $(make()).len }
        """, b"", b"sentinel",
    ),
    "comptime_aggregate_rejects_mutable_byte_slice_escape": (
        """
        make proc() [1][]mut u8 {
            values [1]mut u8 := [42]
            return [values[..]]
        }
        main proc() usize { return $(make())[0].len }
        """, b"", b"compile-time result cannot escape evaluation",
    ),
    "comptime_nonbyte_slices_materialize_callback_elements": (
        """
        Callback :: alias @proc(value i16) i16
        increment proc(value i16) i16 { return value + 1 }
        decrement proc(value i16) i16 { return value - 1 }
        make proc() []Callback {
            callbacks [2]Callback :: [increment, decrement]
            return callbacks[..]
        }
        main proc() [3]i16 {
            callbacks :: $(make())
            return [callbacks[0](41), callbacks[1](43), i16(callbacks.len)]
        }
        """, b"[42, 42, 2]\n", None,
    ),
    "comptime_nonbyte_slices_preserve_full_width_sentinels": (
        """
        make proc() [;18446744073709551615]u64 {
            values [3]u64 :: [9, 4294967298, 18446744073709551615]
            return values[1..2;18446744073709551615]
        }
        main proc() [3]u64 {
            values :: $(make())
            return [u64(values.len), values[0], values.ptr[1]]
        }
        """, b"[1, 4294967298, 18446744073709551615]\n", None,
    ),
    "comptime_aggregate_materializes_readonly_nonbyte_slices": (
        """
        make proc() []i16 {
            values [2]mut i16 := [-300, 1024]
            return values[..]
        }
        main proc() [3]i16 {
            values :: $(make())
            return [values[0], values[1], i16(values.len)]
        }
        """, b"[-300, 1024, 2]\n", None,
    ),
    "comptime_aggregate_materializes_nominal_byte_slices": (
        """
        Byte :: distinct u8
        exact proc(value Byte) usize { return usize(value) }
        make proc() []Byte {
            values [3]Byte :: [0, 128, 255]
            return values[..]
        }
        main proc() [4]usize {
            values :: $(make())
            return [exact(values[0]), exact(values[1]), exact(values[2]), values.len]
        }
        """, b"[0, 128, 255, 3]\n", None,
    ),
    "comptime_nonbyte_slices_preserve_subviews_and_full_element_stride": (
        """
        make proc() []i64 {
            values [4]i64 :: [9, -4294967297, 4294967298, 8]
            return values[1..3]
        }
        main proc() [3]i64 {
            values :: $(make())
            return [i64(values.len), values[0], values[1]]
        }
        """, b"[2, -4294967297, 4294967298]\n", None,
    ),
    "comptime_nonbyte_slices_own_array_elements": (
        """
        make proc() [][2]i16 {
            rows [3][2]i16 :: [[9, 8], [-300, 1024], [40, 2]]
            return rows[1..]
        }
        main proc() [5]i16 {
            rows :: $(make())
            return [i16(rows.len), rows[0][0], rows[0][1], rows[1][0], rows[1][1]]
        }
        """, b"[2, -300, 1024, 40, 2]\n", None,
    ),
    "comptime_nonbyte_slices_own_records_callbacks_and_nested_slices": (
        """
        Callback :: alias @proc(value i16) i16
        Entry :: struct { children [][]i16, run Callback, values [2]i16 }
        increment proc(value i16) i16 { return value + 1 }
        decrement proc(value i16) i16 { return value - 1 }
        make proc() []Entry {
            first [2]i16 :: [11, 12]
            second [2]i16 :: [21, 22]
            rows [2][]i16 :: [first[..], second[..]]
            entries [2]Entry :: [
                { children = rows[..], run = increment, values = [40, 2] },
                { children = rows[1..], run = decrement, values = [44, 3] },
            ]
            return entries[..]
        }
        main proc() [8]i16 {
            entries :: $(make())
            if (
                entries.len != 2 or entries[0].children.len != 2
                or entries[1].children.len != 1 or entries[0].children[0].len != 2
            ) {
                return [0, 0, 0, 0, 0, 0, 0, 0]
            }
            return [entries[0].children[0][0], entries[0].children[0][1],
                entries[0].children[1][0], entries[1].children[0][1],
                entries[0].run(41), entries[1].run(43),
                entries[0].values[0], entries[1].values[1]]
        }
        """, b"[11, 12, 21, 22, 42, 42, 40, 3]\n", None,
    ),
    "comptime_nonbyte_slices_own_raw_record_views": (
        """
        Entry :: struct { children []i16, run @proc(value i16) i16, tail i16 }
        Backing :: union { entry Entry }
        increment proc(value i16) i16 { return value + 1 }
        make proc() []Entry {
            children [2]i16 :: [40, 2]
            backing Backing :: { entry = { children = children[..], run = increment, tail = 7 } }
            pointer *Entry :: &backing.entry
            return pointer[..1]
        }
        main proc() [4]i16 {
            entries :: $(make())
            return [entries[0].children[0], entries[0].children[1],
                entries[0].run(41), entries[0].tail]
        }
        """, b"[40, 2, 42, 7]\n", None,
    ),
    "comptime_nonbyte_slices_preserve_distinct_slice_identity": (
        """
        Slice :: alias []i16
        View :: distinct Slice
        make proc() View {
            values [2]i16 :: [40, 2]
            return View(values[..])
        }
        exact proc(value View) i16 {
            values :: Slice(value)
            return values[0] + values[1]
        }
        main proc() i16 { return exact($(make())) }
        """, b"42\n", None,
    ),
    "comptime_nonbyte_slices_preserve_empty_and_zero_sized_shapes": (
        """
        Empty :: struct {}
        empty proc() []i16 {
            values [0]i16 :: []
            return values[..]
        }
        cells proc() []Empty {
            values [3]Empty :: [{}, {}, {}]
            return values[..]
        }
        rows proc() [][0]i16 {
            values [2][0]i16 :: [[], []]
            return values[..]
        }
        main proc() [5]usize {
            none :: $(empty())
            items :: $(cells())
            arrays :: $(rows())
            saved Empty :: items[2]
            return [none.len, items.len, arrays.len, arrays[0].len, arrays[1].len]
        }
        """, b"[0, 3, 2, 0, 0]\n", None,
    ),
    "comptime_nonbyte_slices_cross_nested_optional_tagged_and_fallible_channels": (
        """
        Failure :: struct { values []i16 }
        Choice :: union(enum) { data struct { rows [2]?[]i16 }, empty void }
        Packet :: struct { choice ??Choice, result []i16 ! Failure }
        make proc(fail bool) Packet {
            values [2]i16 :: [-300, 1024]
            result []i16 ! Failure := values[..]
            if (fail) result = Failure { values = values[1..] }
            return {
                choice = some!(some!(Choice { data = { rows = [values[..1], null] } })),
                result = result,
            }
        }
        main proc() [5]i16 {
            good :: $(make(false))
            bad :: $(make(true))
            choice :: good.choice??
            if (choice.data.rows[1] != null) return [0, 0, 0, 0, 0]
            success :: good.result catch { return [1, 1, 1, 1, 1] }
            failure :: bad.result catch |err| err.values
            return [choice.data.rows[0]?[0], success[0], success[1],
                failure[0], i16(failure.len)]
        }
        """, b"[-300, -300, 1024, 1024, 1]\n", None,
    ),
    "comptime_nonbyte_slices_normalize_cache_identity_and_generated_defaults": (
        """
        direct [2]i16 :: [40, 2]
        make proc(padded bool) []i16 {
            if (padded) {
                prefixed [4]i16 :: [9, 40, 2, 8]
                return prefixed[1..3]
            }
            values [2]mut i16 := [40, 2]
            return values[..]
        }
        other proc() []i16 {
            values [2]i16 :: [40, 3]
            return values[..]
        }
        Box proc($values []i16) type { return struct { values []i16 = values } }
        Generated proc() type {
            return struct_type!(.auto, ["values"], [[]i16], [make(false)])
        }
        main proc() [4]i16 {
            first Box(make(false)) :: {}
            same Box(make(true)) :: first
            literal Box(direct[..]) :: same
            generated Generated() :: {}
            if (Box(make(false)) == Box(other())) return [0, 0, 0, 0]
            return [literal.values[0], literal.values[1],
                i16(literal.values.len), generated.values[0] + generated.values[1]]
        }
        """, b"[40, 2, 2, 42]\n", None,
    ),
    "comptime_nonbyte_slice_backing_outlives_runtime_helper_returns": (
        """
        make proc(seed i16) []i16 {
            values [2]i16 :: [seed, seed + 1]
            return values[..]
        }
        first proc() []i16 { return $(make(40)) }
        second proc() []i16 { return $(make(100)) }
        disturb proc(seed i16) i16 {
            values [8]i16 :: [seed, 2, 3, 4, 5, 6, 7, 8]
            return values[0] + values[7]
        }
        main proc() [5]i16 {
            left :: first()
            right :: second()
            noise :: disturb(200)
            return [left[0], left[1], right[0], right[1], noise]
        }
        """, b"[40, 41, 100, 101, 208]\n", None,
    ),
    "comptime_nonbyte_slices_reject_uninitialized_elements": (
        """
        make proc() []i16 {
            values [2]mut i16 := undefined
            values[0] = 42
            return values[..]
        }
        main proc() usize { return $(make()).len }
        """, b"", b"local value was read before initialization",
    ),
    "comptime_nonbyte_slices_reject_oversized_pointer_lengths": (
        """
        make proc() []i16 {
            values [1]i16 :: [42]
            return values.ptr[..maxval!(usize)]
        }
        main proc() usize { return $(make()).len }
        """, b"", b"compile-time execution exceeds its recursion or step limit",
    ),
    "comptime_zero_sized_slices_reject_oversized_pointer_lengths": (
        """
        Empty :: struct {}
        make proc() []Empty {
            values [1]Empty :: [{}]
            return values.ptr[..maxval!(usize)]
        }
        main proc() usize { return $(make()).len }
        """, b"", b"compile-time execution exceeds its recursion or step limit",
    ),
    "comptime_nonbyte_slices_bound_cyclic_retained_storage": (
        """
        Node :: struct { children []Node }
        make proc() []Node {
            values [1]mut Node := undefined
            values[0] = { children = values[..] }
            return values[..]
        }
        main proc() usize { return $(make()).len }
        """, b"", b"compile-time execution exceeds its recursion or step limit",
    ),
    "comptime_nonbyte_slices_reject_pointer_lengths_beyond_backing": (
        """
        make proc() []i16 {
            values [1]i16 :: [42]
            return values.ptr[..2]
        }
        main proc() usize { return $(make()).len }
        """, b"", b"compile-time result cannot escape evaluation",
    ),
    "comptime_nonbyte_slices_reject_mutable_escape": (
        """
        make proc() []mut i16 {
            values [1]mut i16 := [42]
            return values[..]
        }
        main proc() usize { return $(make()).len }
        """, b"", b"compile-time result cannot escape evaluation",
    ),
    "comptime_nonbyte_slices_reject_nonnull_pointer_element_escape": (
        """
        make proc() []@i16 {
            value i16 :: 42
            pointers [1]@i16 :: [&value]
            return pointers[..]
        }
        main proc() usize { return $(make()).len }
        """, b"", b"compile-time result cannot escape evaluation",
    ),
    "comptime_nonbyte_sentinel_slices_own_subviews_and_empty_backing": (
        """
        make proc() [;-7]i16 {
            values [4]i16 :: [9, -300, 1024, -7]
            return values[1..3;-7]
        }
        empty proc() [;1024]i16 {
            values [1]i16 :: [1024]
            return values[..0;1024]
        }
        main proc() [6]i16 {
            values :: $(make())
            none :: $(empty())
            return [i16(values.len), values[0], values[1], values.ptr[2],
                i16(none.len), none.ptr[0]]
        }
        """, b"[2, -300, 1024, -7, 0, 1024]\n", None,
    ),
    "comptime_nonbyte_sentinel_slices_recheck_mutated_sentinel": (
        """
        make proc() [;1024]i16 {
            values [2]mut i16 := [42, 1024]
            view [;1024]i16 :: values[..1;1024]
            values[1] = 7
            return view
        }
        main proc() usize { return $(make()).len }
        """, b"", b"sentinel",
    ),
    "comptime_nonzero_sentinel_byte_slices_own_subviews": (
        """
        make proc() [;255]u8 {
            values [5]mut u8 := [9, 40, 0, 2, 255]
            return values[1..4;255]
        }
        empty proc() [;128]u8 {
            values [1]u8 :: [128]
            return values[..0;128]
        }
        main proc() usize {
            value :: $(make())
            zero :: $(empty())
            if (value.len != 3 or value[1] != 0 or value.ptr[3] != 255) return 1
            if (zero.len != 0 or zero.ptr[0] != 128) return 2
            return usize(value[0]) + usize(value[2])
        }
        """, b"42\n", None,
    ),
    "comptime_nonzero_sentinel_byte_slices_normalize_cache_and_defaults": (
        """
        make proc(padded bool) [;7]u8 {
            if (padded) {
                prefixed [4]u8 :: [9, 40, 2, 7]
                return prefixed[1..3;7]
            }
            values [3]u8 :: [40, 2, 7]
            return values[..2;7]
        }
        Box proc($bytes [;7]u8) type { return struct { bytes [;7]u8 = bytes } }
        main proc() u8 {
            a Box(make(false)) :: {}
            b Box(make(true)) :: a
            direct Box("!(\\x02\\x07"[1..3;7]) :: b
            if (direct.bytes.len != 2 or direct.bytes.ptr[2] != 7) return 0
            return direct.bytes[0] + direct.bytes[1]
        }
        """, b"42\n", None,
    ),
    "comptime_nonzero_sentinel_byte_slices_cross_nested_channels": (
        """
        View :: struct { rows [1]??[;9]u8 }
        Choice :: union(enum) { view View, empty void }
        Failure :: struct { bytes [;9]u8 }
        make proc() [;9]u8 {
            values [3]u8 :: [40, 2, 9]
            return values[..2;9]
        }
        choice proc() Choice { return .view { rows = [some!(some!(make()))] } }
        success proc() View ! Failure { return { rows = [some!(some!(make()))] } }
        failure proc() View ! Failure { return Failure { bytes = make() } }
        main proc() usize {
            value :: $(choice())
            good :: $(success()) catch { return 1 }
            bad :: $(failure()) catch |err| {
                match value {
                    .view |view|: {
                        bytes :: view.rows[0]??
                        if (bytes.ptr[2] != 9 or good.rows[0]??.ptr[2] != 9) return 2
                        return usize(bytes[0]) + usize(err.bytes[1])
                    }
                    .empty: return 3
                }
            }
            return bad.rows.len
        }
        """, b"42\n", None,
    ),
    "inline_for_materializes_nonzero_sentinel_byte_slices": (
        """
        make proc() [1][;1]u8 {
            bytes [2]u8 :: [42, 1]
            return [bytes[..1;1]]
        }
        main proc() u8 {
            inline for make() |bytes| {
                if (bytes.ptr[1] != 1) return 0
                return bytes[0]
            }
            return 0
        }
        """, b"42\n", None,
    ),
    "comptime_nonzero_sentinel_byte_slices_recheck_mutated_sentinel": (
        """
        make proc() [;255]u8 {
            values [2]mut u8 := [42, 255]
            view [;255]u8 :: values[..1;255]
            values[1] = 0
            return view
        }
        main proc() usize { return $(make()).len }
        """, b"", b"sentinel",
    ),
    "comptime_nonzero_sentinel_byte_slices_reject_uninitialized_sentinel": (
        """
        make proc() [;7]u8 {
            values [2]mut u8 := [42, 7]
            view [;7]u8 :: values[..1;7]
            other [2]mut u8 := undefined
            other[0] = 42
            values = other
            return view
        }
        main proc() usize { return $(make()).len }
        """, b"", b"before initialization",
    ),
    "comptime_nonzero_sentinel_byte_slices_reject_out_of_bounds_sentinel": (
        """
        make proc() [;7]u8 {
            values [1]u8 :: [42]
            return values.ptr[..1;7]
        }
        main proc() usize { return $(make()).len }
        """, b"", b"bounds",
    ),
    "comptime_nonzero_sentinel_byte_slices_reject_mutable_escape": (
        """
        make proc() [;7]mut u8 {
            values [2]mut u8 := [42, 7]
            return values[..1;7]
        }
        main proc() usize { return $(make()).len }
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
    "comptime_untagged_union_preserves_scalar_and_ieee_bits": (
        """
        Storage :: union { real f64, bits u64 }
        make proc(bits u64) Storage { return Storage { bits = bits } }
        main proc() bool {
            one :: $(make(4607182418800017408))
            zero :: $(make(9223372036854775808))
            nan :: $(make(9221120237041090626))
            return (
                one.real == 1.0 and zero.bits == 9223372036854775808
                and nan.bits == 9221120237041090626
            )
        }
        """, b"true\n", None,
    ),
    "comptime_untagged_union_owns_padding_tail_bytes_and_copies": (
        """
        Pair :: struct { small u8, large u64 }
        Storage :: union { small u8, rows [2]mut Pair, words [4]mut u64, bytes [32]u8 }
        make proc(prefix u64) Storage {
            value := Storage { words = [prefix, 40, maxval!(u64), 2] }
            value.small = 9
            return value
        }
        Token proc($value Storage) type { return struct {} }
        main proc() [8]u64 {
            if (Token(make(maxval!(u64))) == Token(make(9))) return [0, 0, 0, 0, 0, 0, 0, 0]
            value := $(make(maxval!(u64)))
            saved :: value
            value.small = 0
            value.rows[0].large = 99
            other :: $(make(maxval!(u64)))
            return [u64(saved.small), u64(saved.bytes[1]), u64(saved.bytes[7]),
                saved.rows[0].large, u64(saved.bytes[17]), u64(saved.bytes[23]),
                other.rows[1].large, value.rows[0].large]
        }
        """, b"[9, 255, 255, 40, 255, 255, 2, 99]\n", None,
    ),
    "comptime_untagged_union_cache_identity_and_generated_defaults": (
        """
        Produced proc() type { return union { small u16, words [2]mut u16, bits u32 } }
        Storage :: alias Produced()
        make proc(tail u16) Storage {
            value := Storage { words = [0, tail] }
            value.small = 40
            return value
        }
        Token proc($value Storage) type { return struct {} }
        Fixed proc($value Storage) type { return struct { values [usize(value.words[1])]i16 } }
        score proc($value Storage) u16 { return value.small + value.words[1] }
        Generated proc() type {
            return struct_type!(.auto, ["value"], [Storage], [make(2)])
        }
        main proc() u16 {
            if (Token(make(2)) != Token(Storage { bits = 131112 })) return 0
            if (Token(make(2)) != Token(Storage { words = [40, 2] })) return 0
            if (Token(make(2)) == Token(make(3))) return 0
            first Fixed(make(2)) :: { values = [20, 20] }
            same Fixed(Storage { words = [40, 2] }) :: first
            generated Generated() :: {}
            if (generated.value.words[1] != 2 or same.values[1] != 20) return 0
            return score(make(2))
        }
        """, b"42\n", None,
    ),
    "comptime_untagged_union_preserves_zero_size_and_nominal_members": (
        """
        Number :: distinct u16
        Kind :: enum(u16) { answer = 42 }
        Storage :: union { number Number, kind Kind }
        Empty :: union { bytes [0]u8, record struct {} }
        make proc() Storage { return Storage { kind = .answer } }
        empty proc() Empty { return Empty { bytes = [] } }
        exact proc(value Storage) Number { return value.number }
        main proc() u16 {
            zero :: $(empty())
            if (sizeof!(Empty) != 0 or zero.bytes.len != 0) return 0
            return u16(exact($(make())))
        }
        """, b"42\n", None,
    ),
    "comptime_untagged_union_nested_optional_tagged_and_fallible_channels": (
        """
        Storage :: union { small u16, words [2]u16 }
        Choice :: union(enum) { data Storage, empty void }
        Failure :: struct { value Storage }
        Packet :: struct {
            maybe [2]?Storage
            choice Choice
            success Storage ! Failure
            failure Storage ! Failure
        }
        make proc() Packet {
            value Storage :: { words = [40, 2] }
            return {
                maybe = [value, null], choice = .data{value},
                success = value, failure = Failure { value = value },
            }
        }
        score proc(value Storage) u16 { return value.words[0] + value.words[1] }
        main proc() [4]u16 {
            packet :: $(make())
            if (packet.maybe[1] != null) return [0, 0, 0, 0]
            good :: packet.success catch Storage { small = 0 }
            bad :: packet.failure catch |err| err.value
            return [score(packet.maybe[0]?), score(packet.choice.data), score(good), score(bad)]
        }
        """, b"[42, 42, 42, 42]\n", None,
    ),
    "comptime_untagged_union_rejects_source_pointer": (
        """
        Storage :: union { bits u64, pointer @i16 }
        make proc() Storage {
            value i16 :: 42
            return Storage { pointer = &value }
        }
        main proc() u64 { return $(make()).bits }
        """, b"", b"compile-time result cannot escape evaluation",
    ),
    "comptime_untagged_union_rejects_inactive_pointer_layout": (
        """
        Storage :: union { bits u64, pointer ?@i16 }
        make proc() Storage { return Storage { bits = 0 } }
        main proc() u64 { return $(make()).bits }
        """, b"", b"compile-time result cannot escape evaluation",
    ),
    "comptime_untagged_union_rejects_inactive_callback_layout": (
        """
        Storage :: union { bits u64, callback @proc(value i16) i16 }
        make proc() Storage { return Storage { bits = 42 } }
        main proc() u64 { return $(make()).bits }
        """, b"", b"compile-time result cannot escape evaluation",
    ),
    "comptime_untagged_union_rejects_inactive_string_layout": (
        """
        Storage :: union { bits [2]u64, text string }
        make proc() Storage { return Storage { bits = [0, 0] } }
        main proc() u64 { return $(make()).bits[0] }
        """, b"", b"compile-time result cannot escape evaluation",
    ),
    "comptime_untagged_union_rejects_nested_inactive_slice_layout": (
        """
        Storage :: union { bits [2]u64, rows [1]struct { bytes []u8 } }
        make proc() Storage { return Storage { bits = [0, 0] } }
        main proc() u64 { return $(make()).bits[0] }
        """, b"", b"compile-time result cannot escape evaluation",
    ),
    "comptime_untagged_union_rejects_uninitialized_tail_bytes": (
        """
        Storage :: union { small u8, words [2]mut u64 }
        make proc() Storage {
            value Storage := undefined
            value.small = 42
            return value
        }
        main proc() u8 { return $(make()).small }
        """, b"", b"read before initialization",
    ),
    "comptime_untagged_union_rejects_uninitialized_padding_bytes": (
        """
        Pair :: struct { small u8, large u64 }
        Storage :: union { pair Pair, words [2]u64 }
        make proc() Storage {
            value Storage := undefined
            value.pair.small = 1
            value.pair.large = 42
            return value
        }
        main proc() u64 { return $(make()).pair.large }
        """, b"", b"read before initialization",
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
    "optional_compile_time_callbacks_preserve_source_identity": (
        """
        first proc(value i16) i16 { return value }
        second proc(value i16) i16 { return value + 2 }
        invoke proc($callback proc(value i16) i16, value i16) i16 { return callback(value) }
        choose proc($callback ?proc(value i16) i16, value i16) i16 {
            if $(callback == null) return value + 3
            else return invoke(callback?, value)
        }
        main proc() [3]i16 { return [choose(first, 42), choose(second, 40), choose(null, 39)] }
        """, b"[42, 42, 42]\n", None,
    ),
    "null_compile_time_callback_unwrap_traps": (
        """
        invoke proc($callback proc() i16) i16 { return callback() }
        choose proc($callback ?proc() i16) i16 { return invoke(callback?) }
        main proc() i16 { return choose(null) }
        """, b"", b"null optional",
    ),
    "compile_error_reports_owned_computed_message": (
        """
        explain proc() string { return "invalid \\"key\\"" }
        fail proc($bad bool) i16 {
            if (bad) compile_error!(explain())
            return 42
        }
        main proc() i16 { return fail(true) }
        """, b"", b'invalid "key"',
    ),
    "compile_error_skips_unselected_generic_and_type_bodies": (
        """
        choose proc($bad bool) i16 {
            if (bad) compile_error!("wrong branch")
            return 42
        }
        Storage proc(bad bool) type {
            if (bad) compile_error!("wrong type branch")
            return i16
        }
        main proc() Storage(false) { return choose(false) }
        """, b"42\n", None,
    ),
    "compile_error_type_execution_preserves_message": (
        """
        Storage proc(bad bool) type {
            if (bad) compile_error!("invalid generated storage")
            return i16
        }
        main proc() Storage(true) { return 42 }
        """, b"", b"invalid generated storage",
    ),
    "compile_error_custom_diagnostics_survive_later_evaluation": (
        """
        unused proc() void { compile_error!("retained message") }
        compute proc() i16 { return 42 }
        main proc() i16 { return $(compute()) }
        """, b"42\n", b"error: retained message",
    ),
    "compile_error_rejects_runtime_messages": (
        """
        fail proc(message string) void { compile_error!(message) }
        main proc() i16 { fail("not constant")\nreturn 42 }
        """, b"", b"compile_error! requires one compile-time string argument",
    ),
    "compile_error_rejects_wrong_arity": (
        "main proc() i16 { compile_error!()\nreturn 42 }",
        b"", b"compile_error! requires one compile-time string argument",
    ),
    "type_execution_unreachable_is_selected_only": (
        """
        Storage proc(bad bool) type {
            if (bad) unreachable
            return i16
        }
        main proc() Storage(false) { return 42 }
        """, b"42\n", None,
    ),
    "type_execution_unreachable_retains_source": (
        """
        Storage proc() type {
            unreachable
        }
        main proc() Storage() { return 42 }
        """, b"", b":2:5:",
    ),
    "rigid_slice_inference_preserves_borrowing_and_string_context": (
        """
        Box proc($T type) type { return struct { value T } }
        length proc($T type, box @Box(T), value T) usize { return box.value.len + value.len }
        main proc() [3]usize {
            box Box([]u8) :: { value = "ab" }
            bytes [3]mut u8 := [1, 2, 3]
            return [length(&box, bytes[..]), length(&box, "abc"), length(&box, bytes)]
        }
        """, b"[5, 5, 5]\n", None,
    ),
    "rigid_slice_inference_rejects_readonly_weakening_reversal": (
        """
        Box proc($T type) type { return struct { value T } }
        write proc($T type, box @Box(T), value T) void { value[0] = 0 }
        main proc() i16 {
            bytes [1]mut u8 := [42]
            box Box([]mut u8) :: { value = bytes[..] }
            readonly []u8 :: bytes[..]
            write(&box, readonly)
            return 42
        }
        """, b"", b"",
    ),
    "rigid_slice_inference_rejects_incompatible_elements": (
        """
        Box proc($T type) type { return struct { value T } }
        length proc($T type, box @Box(T), value T) usize { return value.len }
        main proc() usize {
            box Box([]u8) :: { value = "" }
            words [1]i16 :: [42]
            return length(&box, words[..])
        }
        """, b"", b"",
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
    CASES[f"{integer_type}_bitwise_boundaries"] = (
        f"""
        shift proc(value {integer_type}, count u64) [4]{integer_type} {{
            return [value << count, value >> count, value <<| count, ~value]
        }}
        main proc() [8]{integer_type} {{
            high :: shift(maxval!({integer_type}), {bits - 1})
            huge :: shift_zero({integer_type}(0), maxval!(u64))
            return [high[0], high[1], high[2], high[3],
                {integer_type}(1) <<| maxval!(u64), huge,
                minval!({integer_type}) <<| maxval!(u64),
                minval!({integer_type}) >> u8({bits - 1})]
        }}
        shift_zero proc(value {integer_type}, count u64) {integer_type} {{ return value <<| count }}
        """,
        (f"[{- (1 << (bits - 1)) if signed else 1 << (bits - 1)}, "
         f"{0 if signed else 1}, {maximum}, {minimum}, {maximum}, 0, {minimum}, "
         f"{-1 if signed else 0}]\n").encode(),
        None,
    )
    CASES[f"{integer_type}_limits_and_truncating_division"] = (
        f"""
        identity proc(value {integer_type}) {integer_type} {{ return value }}
        main proc() [4]{integer_type} {{
            return [identity(minval!({integer_type})), identity(maxval!({integer_type})),
                divtrunc!(maxval!({integer_type}), 2), divtrunc!(minval!({integer_type}), 2)]
        }}
        """,
        f"[{minimum}, {maximum}, {maximum // 2}, {-((-minimum) // 2) if minimum < 0 else 0}]\n".encode(),
        None,
    )
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
    put("hidden/a.hon", "@hide:file\nhelper proc() int { return 20 }\n@hide\npackage_value :: 19\nfirst proc() int { return helper() }\n")
    put("hidden/b.hon", "@hide:file\nhelper proc() int { return 22 }\nsecond proc() int { return helper() }\nanswer proc() int { return package_value + 23 }\n")
    visibility_cases = (
        ('import "../hidden"\nmain proc() int { return hidden.first() + hidden.second() }\n', b"42\n", None),
        ('import "../hidden"\nmain proc() int { return hidden.helper() }\n', b"", b"unknown name"),
        ('import "../hidden"\nmain proc() int { return hidden.answer() }\n', b"42\n", None),
        ('import "../hidden"\nmain proc() int { return hidden.package_value }\n', b"", b"unknown name"),
        ('import "../hidden"\nspace :: alias hidden\nmain proc() int { return space.package_value }\n', b"", b"unknown name"),
        ('@hide:file\nprivate proc() int { return 42 }\nmain proc() int { return private() }\n', b"42\n", None),
    )
    sibling.write_text("sibling proc(n int) int { return n }\n")
    for text, stdout, error in visibility_cases:
        main.write_text(text)
        check(app, executable, (stdout, error))
    sibling.write_text("@hide:file\nprivate proc() int { return 42 }\n")
    main.write_text("main proc() int { return private() }\n")
    check(app, executable, (b"", b"unknown name"))
    return len(cases) + len(visibility_cases) + 7


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
    sizing.write_text(textwrap.dedent("""
        values proc() [2]i16 { return [19, 22] }
        types proc() [2]type { return [i16, bool] }
    """).strip() + "\n")
    source.write_text(textwrap.dedent("""
        lib :: import "../library"
        add proc(value i16, index usize) i16 { return value + i16(index) }
        main proc() i16 {
            total i16 := 0
            inline for lib.values() |value, index| { total = total + add(value, index) }
            inline for lib.types() |T| {
                if (T == i16) {
                    value T :: total
                    total = value
                } else {
                    value T :: true
                    if (!value) return 0
                }
            }
            return total
        }
    """).strip() + "\n")
    check(app, executable, (b"42\n", None))
    sizing.write_text(textwrap.dedent("""
        values proc() [1]i16 {
            value i8 :: 127
            overflow :: value + 1
            return [i16(overflow)]
        }
    """).strip() + "\n")
    source.write_text(textwrap.dedent("""
        lib :: import "../library"
        main proc() i16 {
            inline for lib.values() |value| { return value }
            return 0
        }
    """).strip() + "\n")
    check(app, executable, (b"", b"integer arithmetic overflow"))
    interpreted = run(str(HONEY), "run", str(app))
    assert interpreted.stderr.startswith(f"{sizing.resolve()}:3:".encode()), outcome(interpreted)
    assert len(interpreted.stderr.splitlines()) == 1, outcome(interpreted)
    sizing.write_text(textwrap.dedent("""
        Callback :: alias @proc(value i16) i16
        bias i16 :: 1
        @hide:file
        increment proc(value i16) i16 { return value + bias }
        choose proc() Callback { return increment }
    """).strip() + "\n")
    source.write_text(textwrap.dedent("""
        lib :: import "../library"
        increment proc(value i16) i16 { return value + 100 }
        main proc() i16 {
            callback :: $(lib.choose())
            return callback(41)
        }
    """).strip() + "\n")
    check(app, executable, (b"42\n", None))
    sizing.write_text(textwrap.dedent("""
        bytes [3]u8 :: [9, 40, 2]
        view proc() []u8 { return bytes[1..] }
        filled proc() []u8 {
            local [2]mut u8 := undefined
            local[0] = 40
            local[1] = 2
            return local[..]
        }
        Token proc($value []u8) type { return struct {} }
        Generated proc() type {
            return struct_type!(.auto, ["value"], [[]u8], [filled()])
        }
    """).strip() + "\n")
    source.write_text(textwrap.dedent("""
        lib :: import "../library"
        main proc() [3]u8 {
            value lib.Generated() :: {}
            direct :: $(lib.view())
            if (lib.Token(lib.view()) != lib.Token(lib.filled())) return [0, 0, 0]
            return [direct[0], value.value[1], $(lib.filled())[0]]
        }
    """).strip() + "\n")
    check(app, executable, (b"[40, 2, 40]\n", None))
    sizing.write_text(textwrap.dedent("""
        Storage :: union { small u16, words [2]mut u16 }
        make proc() Storage {
            value := Storage { words = [0, 2] }
            value.small = 40
            return value
        }
        Token proc($value Storage) type { return struct {} }
        Generated proc() type {
            return struct_type!(.auto, ["value"], [Storage], [make()])
        }
    """).strip() + "\n")
    source.write_text(textwrap.dedent("""
        lib :: import "../library"
        main proc() u16 {
            direct :: $(lib.make())
            value lib.Generated() :: {}
            if (lib.Token(lib.make()) != lib.Token(lib.Storage { words = [40, 2] })) return 0
            return direct.small + value.value.words[1]
        }
    """).strip() + "\n")
    check(app, executable, (b"42\n", None))
    sizing.write_text(textwrap.dedent("""
        Storage :: union { small u8, words [2]u64 }
        make proc() Storage {
            value u8 :: 255
            overflow :: value + 1
            return Storage { words = [u64(overflow), 2] }
        }
    """).strip() + "\n")
    source.write_text(textwrap.dedent("""
        lib :: import "../library"
        main proc() u8 { return $(lib.make()).small }
    """).strip() + "\n")
    check(app, executable, (b"", b"integer arithmetic overflow"))
    interpreted = run(str(HONEY), "run", str(app))
    assert interpreted.stderr.startswith(f"{sizing.resolve()}:4:".encode()), outcome(interpreted)
    assert len(interpreted.stderr.splitlines()) == 1, outcome(interpreted)
    return 26


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
                return {
                    yield 9223372036854775807 + 1
                }
            }
        """, 5, 15, False),
        ("""
            fail proc() int {
                return {
                    yield missing
                }
            }
        """, 5, 15, True),
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
                inline for [int(9223372036854775807)] |value| {
                    return value + 1
                }
                return 42
            }
        """, 5, 16, False),
        ("""
            Choice :: union(enum) { number int, empty void }
            fail proc() int {
                input Choice :: .number{9223372036854775807}
                return match input {
                    inline |payload, tag|: match tag {
                        .number: payload + 1,
                        .empty: 0,
                    },
                }
            }
        """, 8, 22, False),
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

def check_arraylist_packages(temporary, executable):
    project = temporary / "arraylist"
    project.mkdir()
    (project / "build.hon").write_text("# project root\n")
    for package in ("std", "ffi"):
        (project / package).symlink_to(ROOT / package, target_is_directory=True)
    source = project / "main.hon"
    # A fixed-capacity test allocator lets the interpreter exercise the real
    # library's growth/failure paths without executing foreign calls.
    source.write_text(textwrap.dedent("""
        import "@std/arraylist"
        import "@std/mem"
        State :: struct { storage *mut i16, fail bool = false, allocations usize = 0, releases usize = 0 }
        allocate proc(context ?@mut anyopaque, size usize, alignment usize) ?*mut u8 {
            state @mut State :: ptrcast!(context?)
            state.allocations = state.allocations + 1
            if (state.fail or size > sizeof!([64]i16) or alignment > alignof!(i16)) return null
            return ptrcast!(state.storage)
        }
        resize proc(
            context ?@mut anyopaque, _ ?*mut u8, _ usize, size usize, alignment usize,
        ) ?*mut u8 { return allocate(context, size, alignment) }
        release proc(context ?@mut anyopaque, _ ?*mut u8, _ usize, _ usize) void {
            state @mut State :: ptrcast!(context?)
            state.releases = state.releases + 1
        }
        table mem.AllocatorVTable :: { alloc = allocate, realloc = resize, free = release }
        fail proc(allocator mem.Allocator) void ! mem.AllocError {
            list := arraylist.init(i16, allocator)
            defer arraylist.deinit(&list)
            try arraylist.append(&list, 42)
            return .out_of_memory
        }
        main proc() i16 ! mem.AllocError {
            storage [64]mut i16 := undefined
            state State := { storage = (&storage).ptr }
            allocator mem.Allocator :: { context = &state, vtable = &table }
            list := arraylist.init(i16, allocator)
            if (list.items.len != 0 or list.capacity != 0 or state.allocations != 0) return -1
            if (arraylist.pop(&list) != null) return -2
            for (i16(0)..20) |value| { try arraylist.append(&list, value) }
            if (list.items.len != 20 or list.capacity < 20) return -3
            for list.items |value, index| { if (value != i16(index)) return -4 }
            previous_capacity :: list.capacity
            previous_calls :: state.allocations
            try arraylist.reserve(&list, previous_capacity)
            if (state.allocations != previous_calls) return -5
            state.fail = true
            arraylist.reserve(&list, previous_capacity + 1) catch |err| {
                if (err != .out_of_memory) return -6
            }
            if (list.capacity != previous_capacity or list.items.len != 20 or list.items[19] != 19) return -7
            if (state.allocations != previous_calls + 1) return -8
            if (arraylist.pop(&list)? != 19 or list.items.len != 19) return -9
            arraylist.clear(&list)
            if (list.items.len != 0 or list.capacity != previous_capacity) return -10
            state.fail = false
            try arraylist.append(&list, 42)
            if (list.items[0] != 42 or state.allocations != previous_calls + 1) return -11
            arraylist.deinit(&list)
            if (list.items.len != 0 or list.capacity != 0 or state.releases != 1) return -12
            arraylist.deinit(&list)
            if (state.releases != 1) return -13
            fail(allocator) catch {}
            if (state.releases != 2) return -14
            state.fail = true
            arraylist.append(&list, 1) catch {}
            if (list.items.len != 0 or list.capacity != 0) return -15
            return 42
        }
    """).strip() + "\n")
    check(source, executable, (b"42\n", None))
    source.write_text(textwrap.dedent("""
        import "@std/arraylist"
        import "@std/mem"
        Empty :: struct {}
        main proc() i16 ! mem.AllocError {
            list := arraylist.init(Empty, mem.c_allocator)
            defer arraylist.deinit(&list)
            try arraylist.reserve(&list, 20)
            for (0..21) |_| { try arraylist.append(&list, Empty{}) }
            if (list.items.len != 21 or list.capacity < 21) return -1
            if (arraylist.pop(&list) == null or list.items.len != 20) return -2
            arraylist.clear(&list)
            if (list.items.len != 0 or arraylist.pop(&list) != null) return -3
            return 42
        }
    """).strip() + "\n")
    check(source, executable, (b"42\n", None))
    native_cases = (
        """
        import "@std/arraylist"
        import "@std/mem"
        Item :: struct { number i16, values [2]mut i16 }
        main proc() i16 ! mem.AllocError {
            list := arraylist.init(Item, mem.c_allocator)
            defer arraylist.deinit(&list)
            for (i16(0)..32) |value| {
                try arraylist.append(&list, Item{ number = value, values = [value, 2] })
            }
            if (list.items.len != 32 or list.capacity < 32) return -1
            saved :: list.items[20]
            list.items[20].values[0] = 0
            if (saved.values[0] != 20) return -2
            for list.items |item, index| { if (item.number != i16(index)) return -3 }
            if (arraylist.pop(&list)?.number != 31) return -4
            arraylist.clear(&list)
            try arraylist.append(&list, saved)
            return list.items[0].number + list.items[0].values[0] + list.items[0].values[1]
        }
        """,
        """
        import "@std/mem"
        main proc() i16 {
            allocator :: mem.c_allocator
            if (mem.raw_alloc(allocator, 16, 3) != null) return -1
            initial :: mem.raw_alloc(allocator, 16, 64)
            if initial |bytes| {
                values *mut i16 :: ptrcast!(bytes)
                values[0] = 40
                values[1] = 2
                resized :: mem.raw_realloc(allocator, bytes, 16, 128, 64)
                if resized |grown| {
                    defer mem.raw_free(allocator, grown, 128, 64)
                    result *i16 :: ptrcast!(grown)
                    return result[0] + result[1]
                }
                mem.raw_free(allocator, bytes, 16, 64)
            }
            return -2
        }
        """,
    )
    for text in native_cases:
        source.write_text(textwrap.dedent(text).strip() + "\n")
        interpreted = run(str(HONEY), "run", str(source))
        assert interpreted.returncode == 1 and interpreted.stdout == b"", outcome(interpreted)
        assert b"foreign function calls are unsupported by the interpreter" in interpreted.stderr, outcome(interpreted)
        assert len(interpreted.stderr.splitlines()) == 1, outcome(interpreted)
        for optimization in ("-O0", "-O2"):
            built = run(str(HONEY), "build", str(source), "-o", str(executable), optimization)
            assert outcome(built) == (0, b"", b""), outcome(built)
            assert outcome(run(str(executable))) == (0, b"42\n", b""), optimization
            executable.unlink()
    return 2, len(native_cases)


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


def check_typeinfo_packages(temporary, executable):
    project = temporary / "typeinfo"
    project.mkdir()
    (project / "build.hon").write_text("# project root\n")
    (project / "std").mkdir()
    for package in ("meta", "enummap"):
        (project / "std" / package).symlink_to(ROOT / "std" / package, target_is_directory=True)

    cases = {
        "ordinary_metadata_helpers_return_values_and_names": (
            """
            import "@std/meta"
            Record :: struct { first i16, second bool }
            field_name proc(info meta.TypeInfo, index usize) []u8 {
                return match info {
                    .record |record|: record.fields[index].name,
                    else: compile_error!("expected record"),
                }
            }
            count proc(fields []meta.FieldInfo) usize {
                total usize := 0
                for fields |field| {
                    if (field.type == i16) total += 19
                    else total += 23
                }
                return total
            }
            main proc() usize {
                name :: field_name(typeinfo!(Record), 1)
                if (name.len != 6 or name[0] != 115) return 0
                return count(typeinfo!(Record).record.fields)
            }
            """, b"42\n", None,
        ),
        "categories_specialize_generic_calls": (
            """
            reflection :: import "@std/meta"
            Error :: enum { failed }
            Choice :: union(enum) { number i16, empty void }
            Raw :: union { number i16, flag bool }
            category proc($T type) i16 {
                return match typeinfo!(T) {
                    .integer: 1,
                    .bool: 2,
                    .float: 3,
                    .void: 4,
                    .anyopaque: 5,
                    .pointer: 6,
                    .slice: 7,
                    .optional: 8,
                    .function: 9,
                    .union: 10,
                    .fallible: 11,
                    else: -1,
                }
            }
            widen proc($T type, value T) i16 {
                return match typeinfo!(T) {
                    .integer: i16(value),
                    .bool: match value { true: 23, false: 0 },
                    else: value.missing,
                }
            }
            main proc() [14]i16 {
                return [
                    category(i8), category(bool), category(f64), category(void),
                    category(anyopaque), category(@mut i16), category([]u8),
                    category(?i16), category(proc(value i16) i16),
                    category(Choice), category(i16 ! Error), category(Raw),
                    category(string), widen(i8(19)) + widen(true),
                ]
            }
            """, b"[1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 10, 6, 42]\n", None,
        ),
        "arrays_and_immediate_distinct_backing": (
            """
            import "@std/meta"
            Inner :: distinct u16
            Outer :: distinct Inner
            Other :: distinct u16
            OuterAlias :: alias Outer
            ArrayAlias :: alias [3]Inner
            array_matches proc($T, $Child type, $length usize) bool {
                return match typeinfo!(T) {
                    .array |info|: info.child == Child and info.len == length,
                    else: false,
                }
            }
            backing_matches proc($T, $Backing type) bool {
                return match typeinfo!(T) {
                    .distinct |backing|: backing == Backing,
                    else: false,
                }
            }
            main proc() bool {
                return $(
                    array_matches(ArrayAlias, Inner, 3)
                    and array_matches([0]bool, bool, 0)
                    and array_matches([2]mut i16, i16, 2)
                    and !array_matches(ArrayAlias, Other, 3)
                    and backing_matches(Inner, u16)
                    and backing_matches(OuterAlias, Inner)
                    and !backing_matches(Outer, u16)
                    and OuterAlias == Outer and Inner != Other
                )
            }
            """, b"true\n", None,
        ),
        "record_tuple_names_order_exact_types_and_layout": (
            """
            import "@std/meta"
            Child :: struct { value i16 }
            Other :: struct { value i16 }
            Record :: struct { child Child, maybe ?i8, pointer @u8 }
            RecordAlias :: alias Record
            Tuple :: alias struct { i8, Child, bool }
            Packet :: struct_type!(.c, ["value"], [i16], [null])
            same_name proc(left, right []u8) bool {
                if (left.len != right.len) return false
                for left |byte, index| {
                    if (byte != right[index]) return false
                }
                return true
            }
            valid proc() bool {
                match typeinfo!(RecordAlias) {
                    .record |info|: {
                        if (info.is_tuple or info.layout != .auto) return false
                        if (!same_name(info.name?, "Record") or info.fields.len != 3) return false
                        if (!same_name(info.fields[0].name, "child") or info.fields[0].index != 0) return false
                        if (!same_name(info.fields[1].name, "maybe") or info.fields[1].index != 1) return false
                        if (!same_name(info.fields[2].name, "pointer") or info.fields[2].index != 2) return false
                        if (info.fields[0].type != Child or info.fields[0].type == Other) return false
                        if (info.fields[1].type != ?i8 or info.fields[2].type != @u8) return false
                    }
                    else: return false,
                }
                match typeinfo!(Tuple) {
                    .record |info|: {
                        if (!info.is_tuple or info.name != null or info.layout != .auto) return false
                        if (info.fields.len != 3) return false
                        inline for info.fields |field, index| {
                            if (field.index != index or field.name.len != 1) return false
                            if (field.name[0] != u8(48 + index)) return false
                            if (index == 0 and field.type != i8) return false
                            if (index == 1 and field.type != Child) return false
                            if (index == 2 and field.type != bool) return false
                        }
                    }
                    else: return false,
                }
                match typeinfo!(struct { value i16 }) {
                    .record |info|: if (info.name != null or info.is_tuple) return false,
                    else: return false,
                }
                return match typeinfo!(Packet) {
                    .record |info|: info.layout == .c and !info.is_tuple,
                    else: false,
                }
            }
            main proc() bool { return $(valid()) }
            """, b"true\n", None,
        ),
        "enum_metadata_uses_declaration_order_not_discriminants": (
            """
            import "@std/meta"
            Sparse :: enum(i16) { last = -7, first = 3, middle = 90 }
            SparseAlias :: alias Sparse
            Other :: enum(i16) { last = -7, first = 3, middle = 90 }
            valid proc() bool {
                return match typeinfo!(SparseAlias) {
                    .enum |info|: (
                        info.fields.len == 3
                        and info.fields[0].index == 0
                        and info.fields[1].index == 1
                        and info.fields[2].index == 2
                        and info.fields[0].name.len == 4 and info.fields[0].name[0] == 108
                        and info.fields[1].name.len == 5 and info.fields[1].name[0] == 102
                        and info.fields[2].name.len == 6 and info.fields[2].name[0] == 109
                        and info.fields[0].type == Sparse
                        and info.fields[1].type == SparseAlias
                        and info.fields[2].type != Other
                    ),
                    else: false,
                }
            }
            main proc() [3]mut i16 {
                if (!$(valid())) return [0, 0, 0]
                values [3]mut i16 := [0, 0, 0]
                match typeinfo!(SparseAlias) {
                    .enum |info|: inline for info.fields |field, index| {
                        values[index] = i16(field!(SparseAlias, field.name))
                    }
                    else: compile_error!("expected enum"),
                }
                return values
            }
            """, b"[-7, 3, 90]\n", None,
        ),
        "record_fields_drive_field_access_and_generic_inference": (
            """
            import "@std/meta"
            Record :: struct { small i8, wide i16, enabled bool }
            convert proc($T type, value T) i16 {
                return match typeinfo!(T) {
                    .integer: i16(value),
                    .bool: match value { true: 2, false: 0 },
                    else: compile_error!("unsupported field"),
                }
            }
            sum proc($T type, value T) i16 {
                total i16 := 0
                match typeinfo!(T) {
                    .record |info|: inline for info.fields |field| {
                        total = total + convert(field!(value, field.name))
                    }
                    else: compile_error!("expected record"),
                }
                return total
            }
            main proc() i16 {
                value Record :: { small = 19, wide = 21, enabled = true }
                return sum(value)
            }
            """, b"42\n", None,
        ),
        "reflected_fields_generate_owned_schema_and_defaults": (
            """
            import "@std/meta"
            Source :: struct { left i16, right i16 }
            Defaults proc($T type, $default i16) type {
                match typeinfo!(T) {
                    .record |info|: {
                        names [info.fields.len]mut []u8 := undefined
                        types [info.fields.len]mut type := undefined
                        defaults [info.fields.len]mut ?i16 := undefined
                        inline for info.fields |field, index| {
                            names[index] = field.name
                            types[index] = field.type
                            defaults[index] = default
                        }
                        return struct_type!(info.layout, names, types, defaults)
                    }
                    else: compile_error!("expected record"),
                }
            }
            main proc() i16 {
                first Defaults(Source, 21) := {}
                saved :: first
                field!(first, "left") = 0
                same Defaults(Source, 21) :: saved
                return same.left + same.right
            }
            """, b"42\n", None,
        ),
        "real_meta_enum_field_struct_defaults": (
            """
            import "@std/meta"
            Key :: enum(i16) { last = -7, first = 3, middle = 90 }
            Values :: alias meta.EnumFieldStruct(Key, i16, 21)
            main proc() i16 {
                values Values :: { last = 19, first = 23 }
                empty Values :: {}
                if (values.middle != 21 or empty.last != 21 or empty.first != 21) return -1
                return values.last + field!(values, "first")
            }
            """, b"42\n", None,
        ),
        "real_enummap_generates_ordinal_storage": (
            """
            import "@std/meta"
            import "@std/enummap"
            Key :: enum(i16) { last = -7, first = 3, missing = 90 }
            KeyAlias :: alias Key
            main proc() i16 {
                values enummap.EnumMap(KeyAlias, i16) :: {
                    present = [true, true, false], values = [19, 23, 0],
                }
                if (values.present[2]) return -1
                return values.values[0] + values.values[1]
            }
            """, b"42\n", None,
        ),
        "real_enummap_init_get_sparse_alias_and_absence": (
            """
            import "@std/meta"
            import "@std/enummap"
            Key :: enum(i16) { last = -7, first = 3, missing = 90 }
            KeyAlias :: alias Key
            main proc() i16 {
                values :: enummap.init(KeyAlias, i16, { last = 19, first = 23 })
                empty :: enummap.init(Key, i16, {})
                if (enummap.get(&values, .missing) != null) return -1
                if (enummap.get(&empty, .first) != null) return -2
                return enummap.get(&values, .last)? + enummap.get(Key, i16, &values, .first)?
            }
            """, b"42\n", None,
        ),
        "real_enummap_composed_keys_and_owned_aggregate_values": (
            """
            import "@std/meta"
            import "@std/enummap"
            First :: enum { first, absent }
            Second :: enum { last }
            Keys :: alias (First | (Second | First))
            Item :: struct { values [2]mut i16 }
            main proc() i16 {
                source Item := { values = [19, 23] }
                values :: enummap.init(Keys, Item, { first = source })
                source.values[0] = 0
                copy := enummap.get(&values, .first)?
                copy.values[1] = 0
                retained :: enummap.get(&values, .first)?
                if (enummap.get(&values, .last) != null or enummap.get(&values, .absent) != null) return -1
                return retained.values[0] + retained.values[1]
            }
            """, b"42\n", None,
        ),
        "real_enummap_all_entries_materialize_at_compile_time": (
            """
            import "@std/meta"
            import "@std/enummap"
            Key :: enum { first, last }
            known proc() enummap.EnumMap(Key, i16) {
                return enummap.init(Key, i16, { first = 19, last = 23 })
            }
            main proc() i16 {
                values :: $(known())
                return enummap.get(&values, .first)? + enummap.get(&values, .last)?
            }
            """, b"42\n", None,
        ),
        "real_enummap_filled_sparse_constants_preserve_optional_presence": (
            """
            import "@std/meta"
            import "@std/enummap"
            Key :: enum(i16) { absent = -3, present_null = 8, present_value = 21 }
            main proc() i16 {
                values :: $(enummap.init_default(Key, ?i16, null, {
                    present_null = some!(null),
                    present_value = some!(some!(i16(42))),
                }))
                empty :: $(enummap.init_default(Key, i16, i16(99), {}))
                if (enummap.get(&values, .absent) != null or enummap.get(&empty, .absent) != null) return -1
                payload :: enummap.get(&values, .present_null)
                if (payload == null or payload? != null) return -2
                return enummap.get(&values, .present_value)??
            }
            """, b"42\n", None,
        ),
        "metadata_slices_cross_producers_without_losing_type_identity": (
            """
            import "@std/meta"
            Id :: distinct i16
            Record :: struct { value Id }
            retain proc(fields []meta.FieldInfo) []meta.FieldInfo { return fields }
            describe_fields proc($T type) []meta.FieldInfo {
                return match typeinfo!(T) {
                    .record |info|: retain(info.fields),
                    .enum |info|: retain(info.fields),
                    else: compile_error!("expected fields"),
                }
            }
            Storage proc() type {
                source :: describe_fields(Record)
                copy := source[0]
                copy.type = bool
                if (copy.type != bool or source[0].type != Id) return bool
                if (describe_fields(struct {}).len != 0) return bool
                return struct_type!(.auto, [source[0].name], [source[0].type], [Id(42)])
            }
            main proc() i16 {
                value Storage() :: {}
                return i16(value.value)
            }
            """, b"42\n", None,
        ),
        "composed_enum_metadata_preserves_members_and_sum_identity": (
            """
            import "@std/meta"
            First :: enum { one, two }
            Second :: enum { three }
            Keys :: alias (First | (Second | First))
            valid proc() bool {
                match typeinfo!(Keys) {
                    .enum |info|: {
                        if (info.fields.len != 3) return false
                        inline for info.fields |field, index| {
                            if (field.index != index or field.type != Keys) return false
                            selected Keys :: field!(Keys, field.name)
                            if (index == 0 and selected != .one) return false
                            if (index == 1 and selected != .two) return false
                            if (index == 2 and selected != .three) return false
                        }
                        return true
                    }
                    else: return false,
                }
            }
            main proc() bool { return $(valid()) }
            """, b"true\n", None,
        ),
        "inline_metadata_match_keeps_static_tags_and_type_payloads": (
            """
            import "@std/meta"
            Id :: distinct i16
            score proc($T type) i16 {
                match typeinfo!(T) {
                    inline |payload, tag|: {
                        if (tag == .array) {
                            if (payload.child == i16) return i16(payload.len)
                            return 0
                        } else if (tag == .distinct) {
                            if (payload == i16) return 40
                            return 0
                        } else {
                            return 0
                        }
                    },
                }
            }
            main proc() i16 { return score([2]i16) + score(Id) + score(bool) }
            """, b"42\n", None,
        ),
        "active_metadata_payloads_support_direct_checked_projection": (
            """
            import "@std/meta"
            Id :: distinct i16
            Storage proc() type {
                if (typeinfo!(Id).distinct != i16) return bool
                child :: typeinfo!([2]Id).array.child
                count :: typeinfo!([2]Id).array.len
                return struct { values [count]child }
            }
            main proc() i16 {
                value Storage() :: { values = [Id(19), Id(23)] }
                if (typeinfo!(Storage()).record.fields[0].index != 0) return 0
                return i16(value.values[0]) + i16(value.values[1])
            }
            """, b"42\n", None,
        ),
    }
    for name, text, error in (
        ("missing_argument", "main proc() i16 { typeinfo!()\nreturn 42 }", b"argument count"),
        ("extra_argument", "main proc() i16 { typeinfo!(i16, bool)\nreturn 42 }", b"argument count"),
        ("non_type_argument", "main proc() i16 { typeinfo!(42)\nreturn 42 }", b"must name a type"),
        (
            "overlapping_patterns",
            "main proc() i16 { return match typeinfo!(i16) { .integer: 42, .integer: 0, else: 0 } }",
            b"duplicate or overlapping match pattern",
        ),
        (
            "non_exhaustive_match",
            "main proc() i16 { return match typeinfo!(i16) { .integer: 42 } }",
            b"match is not exhaustive",
        ),
        (
            "void_payload_capture",
            "main proc() i16 { return match typeinfo!(i16) { .integer |value|: 42, else: 0 } }",
            b"payload capture requires compatible nonvoid tagged union variants",
        ),
        (
            "incompatible_payload_capture",
            "main proc() i16 { return match typeinfo!(i16) { .array, .record |info|: 42, else: 0 } }",
            b"payload capture requires compatible nonvoid tagged union variants",
        ),
        (
            "field_bounds",
            """
            Bad proc() type {
                match typeinfo!(struct { value i16 }) {
                    .record |info|: return info.fields[1].type,
                    else: return bool,
                }
            }
            main proc() i16 { value Bad() :: 42
                return value }
            """,
            b"index out of bounds",
        ),
        (
            "inactive_metadata_payload_projection",
            "main proc() usize { return typeinfo!(i16).array.len }",
            b"tagged union field is not active",
        ),
        (
            "metadata_slice_elements_remain_readonly",
            """
            Bad proc() type {
                match typeinfo!(struct { value i16 }) {
                    .record |info|: {
                        fields := info.fields
                        fields[0].type = bool
                        return fields[0].type
                    }
                    else: return bool,
                }
            }
            main proc() Bad() { return 42 }
            """,
            b"indexed assignment requires a writable array element",
        ),
    ):
        cases[name] = ('import "@std/meta"\n' + text, b"", error)
    cases["inferred_reflection_locals_compute_without_metadata_parameters"] = (
        """
        import "@std/meta"
        Record :: struct { child i16, enabled bool }
        compute proc(bias i16) i16 {
            info :: typeinfo!(Record)
            match info {
                .record |record|: {
                    saved :: record.fields[0]
                    field := saved
                    field.type = i8
                    if (saved.type != i16 or field.type != i8) return -1
                    return bias + i16(record.fields.len)
                }
                else: return -2,
            }
        }
        main proc() i16 { return compute(40) }
        """, b"42\n", None,
    )
    cases["missing_meta_import"] = (
        "main proc() i16 { typeinfo!(i16)\nreturn 42 }",
        b"", b"typeinfo! requires importing @std/meta",
    )
    cases["relative_meta_import_is_not_reflection_opt_in"] = (
        'import "../std/meta"\nmain proc() i16 { typeinfo!(i16)\nreturn 42 }',
        b"", b"typeinfo! requires importing @std/meta",
    )

    for name, (text, stdout, error) in cases.items():
        app = project / name
        app.mkdir()
        (app / "main.hon").write_text(textwrap.dedent(text).strip() + "\n")
        check(app, executable, (stdout, error))

    library = project / "library"
    library.mkdir()
    definition = library / "types.hon"
    definition.write_text(textwrap.dedent("""
        import "@std/meta"
        Record :: struct { value i16 }
        Alias :: alias Record
        Other :: struct { value i16 }
        Child proc($T type) type {
            return match typeinfo!(T) {
                .record |info|: info.fields[0].type,
                else: bool,
            }
        }
    """).strip() + "\n")
    app = project / "import_identity"
    app.mkdir()
    source = app / "main.hon"
    source.write_text(textwrap.dedent("""
        import "@std/meta"
        lib :: import "../library"
        Record :: struct { value i16 }
        same_record proc($T type) bool {
            return match typeinfo!(T) {
                .array |info|: info.child == lib.Record and info.child != Record and info.child != lib.Other,
                else: false,
            }
        }
        main proc() i16 {
            if (!$(same_record([1]lib.Alias))) return -1
            value lib.Child(lib.Alias) :: 42
            return value
        }
    """).strip() + "\n")
    check(app, executable, (b"42\n", None))
    definition.write_text(textwrap.dedent("""
        import "@std/meta"
        Bad proc() type {
            match typeinfo!(struct { value i16 }) {
                .record |info|: return info.fields[1].type,
                else: return bool,
            }
        }
    """).strip() + "\n")
    source.write_text('lib :: import "../library"\nmain proc() lib.Bad() { return 42 }\n')
    check(app, executable, (b"", b"index out of bounds"))
    diagnosed = run(str(HONEY), "run", str(app))
    assert diagnosed.stderr.startswith(f"{definition.resolve()}:4:".encode()), outcome(diagnosed)
    assert len(diagnosed.stderr.splitlines()) == 1, outcome(diagnosed)
    sibling = project / "sibling_import"
    sibling.mkdir()
    (sibling / "metadata.hon").write_text('reflection :: import "@std/meta"\n')
    (sibling / "main.hon").write_text(
        "main proc() i16 { return match typeinfo!(i16) { .integer: 42, else: 0 } }\n"
    )
    check(sibling, executable, (b"42\n", None))
    malformed = project / "malformed"
    (malformed / "std" / "meta").mkdir(parents=True)
    (malformed / "build.hon").write_text("# project root\n")
    metadata = (ROOT / "std/meta/meta.hon").read_text()
    (malformed / "std/meta/meta.hon").write_text(metadata.replace("    type type\n", "    type usize\n"))
    (malformed / "main.hon").write_text(
        'import "@std/meta"\n'
        "main proc() i16 { return match typeinfo!(i16) { .integer: 42, else: 0 } }\n"
    )
    check(malformed, executable, (b"", b"@std/meta declarations do not match the compiler reflection ABI"))
    return len(cases) + 4


def check_hashmap_packages(temporary, executable):
    project = temporary / "hashmap"
    project.mkdir()
    (project / "build.hon").write_text("# project root\n")
    for package in ("std", "ffi"):
        (project / package).symlink_to(ROOT / package, target_is_directory=True)
    source = project / "main.hon"
    # Each growth allocation has distinct typed storage: clearing new entries
    # must not overwrite the old entries before the library rehashes them.
    source.write_text(textwrap.dedent("""
        import "@std/hashmap"
        import "@std/mem"
        Value :: struct { number i16, items [2]mut i16 }
        Slot :: alias hashmap.Entry(i16, Value)
        StringSlot :: alias hashmap.Entry([]u8, Value)
        State :: struct {
            small *mut Slot
            medium *mut Slot
            large *mut Slot
            strings *mut StringSlot
            fail bool = false
            allocations usize = 0
            releases usize = 0
        }
        allocate proc(context ?@mut anyopaque, size usize, alignment usize) ?*mut u8 {
            state @mut State :: ptrcast!(context?)
            state.allocations += 1
            if (state.fail or alignment != alignof!(Slot)) return null
            if (size == sizeof!([8]Slot)) return ptrcast!(state.small)
            if (size == sizeof!([16]Slot)) return ptrcast!(state.medium)
            if (size == sizeof!([32]Slot)) return ptrcast!(state.large)
            if (size == sizeof!([8]StringSlot)) return ptrcast!(state.strings)
            return null
        }
        resize proc(
            _ ?@mut anyopaque, _ ?*mut u8, _ usize, _ usize, _ usize,
        ) ?*mut u8 { return null }
        release proc(context ?@mut anyopaque, _ ?*mut u8, _ usize, _ usize) void {
            state @mut State :: ptrcast!(context?)
            state.releases += 1
        }
        table mem.AllocatorVTable :: { alloc = allocate, realloc = resize, free = release }
        collision_hash proc(key i16) usize { return if (key == 0) 0 else 1 }
        equivalent proc(left, right i16) bool {
            return left - divtrunc!(left, 100) * 100 == right - divtrunc!(right, 100) * 100
        }
        exercise proc(allocator mem.Allocator, state @mut State) i16 ! (hashmap.PutError | mem.AllocError) {
            map hashmap.HashMap(i16, Value, collision_hash, equivalent) := hashmap.init(allocator)
            defer hashmap.deinit(&map)
            if (map.count != 0 or map.entries.len != 0 or state.allocations != 0) return -1
            if (hashmap.get(&map, 0) != null) return -2
            original Value := { number = 0, items = [0, 1] }
            try hashmap.put(&map, 0, original)
            original.items[0] = 99
            if (hashmap.get(&map, 0)?.items[0] != 0) return -3
            copy := hashmap.get(&map, 0)?
            copy.items[1] = 99
            if (hashmap.get(&map, 100)?.items[1] != 1) return -4
            duplicate := false
            hashmap.put(&map, 100, Value{ number = 999, items = [9, 9] }) catch |err| {
                duplicate = err == .key_exists
            }
            if (!duplicate or map.count != 1 or hashmap.get(&map, 0)?.number != 0) return -7
            for (i16(1)..i16(12)) |key| {
                try hashmap.put(&map, key, Value{ number = key * 2, items = [key, key + 1] })
            }
            if (map.count != 12 or map.entries.len != 16) return -5
            if (state.allocations != 2 or state.releases != 1) return -6
            state.fail = true
            previous :: map.entries.ptr
            calls :: state.allocations
            released :: state.releases
            failed := false
            hashmap.put(&map, 12, original) catch |err| { failed = err == .out_of_memory }
            if (!failed or map.count != 12 or map.entries.len != 16 or map.entries.ptr != previous) return -8
            if (state.allocations != calls + 1 or state.releases != released) return -9
            if (hashmap.get(&map, 12) != null or hashmap.get(&map, 111)?.number != 22) return -10
            state.fail = false
            for (i16(12)..i16(24)) |key| {
                try hashmap.put(&map, key, Value{ number = key * 2, items = [key, key + 1] })
            }
            if (map.count != 24 or map.entries.len != 32) return -11
            for (i16(0)..i16(24)) |key| {
                value :: hashmap.get(&map, key + 100)?
                if (value.number != key * 2 or value.items[0] != key or value.items[1] != key + 1) return -12
            }
            for map.entries |entry| {
                if (entry.hash != 0 and entry.hash != 1) return -13
            }
            if (hashmap.get(&map, 99) != null) return -14
            failed = false
            hashmap.put(&map, 24, original) catch |err| { failed = err == .out_of_memory }
            if (!failed or map.count != 24 or map.entries.len != 32) return -15
            if (hashmap.get(&map, 23)?.number != 46 or hashmap.get(&map, 24) != null) return -16
            return 42
        }
        exercise_automatic_and_string_keys proc(allocator mem.Allocator) i16 ! (hashmap.PutError | mem.AllocError) {
            automatic hashmap.AutoHashMap(i16, Value) := hashmap.init(allocator)
            defer hashmap.deinit(&automatic)
            for (i16(0)..i16(12)) |key| {
                try hashmap.put(&automatic, key, Value{ number = key, items = [key, key + 1] })
            }
            edge Value :: { number = 42, items = [19, 23] }
            try hashmap.put(&automatic, minval!(i16), edge)
            try hashmap.put(&automatic, maxval!(i16), edge)
            for (i16(0)..i16(12)) |key| {
                value :: hashmap.get(&automatic, key)?
                if (value.number != key or value.items[1] != key + 1) return -20
            }
            if (hashmap.get(&automatic, minval!(i16))?.number != 42
                or hashmap.get(&automatic, maxval!(i16))?.items[0] != 19) return -21
            if (hashmap.get(&automatic, -1) != null) return -22
            strings hashmap.StringHashMap(Value) := hashmap.init(allocator)
            defer hashmap.deinit(&strings)
            left [3]mut u8 := [97, 0, 98]
            right [3]mut u8 := [97, 0, 98]
            try hashmap.put(&strings, left[..], edge)
            if (hashmap.get(&strings, right[..])?.items[1] != 23) return -23
            duplicate := false
            hashmap.put(&strings, right[..], Value{ number = 0, items = [0, 0] }) catch |err| {
                duplicate = err == .key_exists
            }
            if (!duplicate or strings.count != 1 or hashmap.get(&strings, left[..])?.number != 42) return -24
            try hashmap.put(&strings, "", edge)
            if (hashmap.get(&strings, right[..2]) != null or hashmap.get(&strings, "")?.number != 42) return -25
            return 42
        }
        main proc() i16 ! (hashmap.PutError | mem.AllocError) {
            small [8]mut Slot := undefined
            medium [16]mut Slot := undefined
            large [32]mut Slot := undefined
            string_storage [8]mut StringSlot := undefined
            state State := {
                small = (&small).ptr, medium = (&medium).ptr, large = (&large).ptr,
                strings = (&string_storage).ptr,
            }
            allocator mem.Allocator :: { context = &state, vtable = &table }
            result :: try exercise(allocator, &state)
            if (result != 42) return result
            if (state.allocations != 5 or state.releases != 3) return -17
            state.fail = true
            empty := hashmap.init(i16, Value, collision_hash, equivalent, allocator)
            defer hashmap.deinit(&empty)
            failed := false
            hashmap.put(&empty, 0, Value{ number = 42, items = [4, 2] }) catch |err| {
                failed = err == .out_of_memory
            }
            if (!failed or empty.count != 0 or empty.entries.len != 0) return -18
            if (hashmap.get(&empty, 0) != null or state.allocations != 6 or state.releases != 3) return -19
            state.fail = false
            automatic_result :: try exercise_automatic_and_string_keys(allocator)
            if (automatic_result != 42) return automatic_result
            if (state.allocations != 10 or state.releases != 7) return -26
            return 42
        }
    """).strip() + "\n")
    check(source, executable, (b"42\n", None))
    native_cases = (
        """
        import "@std/hashmap"
        import "@std/mem"
        Sparse :: enum(i16) { negative = -7, first = 3, far = 90 }
        SparseAlias :: alias Sparse
        Inner :: distinct u64
        Id :: distinct Inner
        integer_keys proc($K type) bool ! (hashmap.PutError | mem.AllocError) {
            map hashmap.AutoHashMap(K, usize) := hashmap.init(mem.c_allocator)
            defer hashmap.deinit(&map)
            keys [4]K :: [minval!(K), maxval!(K), K(1), K(2)]
            for keys |key, index| { try hashmap.put(&map, key, index) }
            for (3..40) |number| { try hashmap.put(&map, K(number), usize(number)) }
            if (map.count != 41 or map.entries.len != 64) return false
            for keys |key, index| { if (hashmap.get(&map, key)? != index) return false }
            for (3..40) |number| {
                if (hashmap.get(&map, K(number))? != usize(number)) return false
            }
            if (hashmap.get(&map, K(40)) != null) return false
            if $(minval!(K) < 0) {
                try hashmap.put(&map, K(-1), 99)
                if (hashmap.get(&map, K(-1))? != 99) return false
            }
            duplicate := false
            hashmap.put(&map, maxval!(K), 999) catch |err| { duplicate = err == .key_exists }
            return duplicate and hashmap.get(&map, maxval!(K))? == 1
        }
        main proc() i16 ! (hashmap.PutError | mem.AllocError) {
            if (!try integer_keys(i8) or !try integer_keys(u8)) return -1
            if (!try integer_keys(i16) or !try integer_keys(u16)) return -2
            if (!try integer_keys(i32) or !try integer_keys(u32)) return -3
            if (!try integer_keys(i64) or !try integer_keys(u64)) return -4
            if (!try integer_keys(isize) or !try integer_keys(usize)) return -5
            flags hashmap.AutoHashMap(bool, i16) := hashmap.init(mem.c_allocator)
            defer hashmap.deinit(&flags)
            try hashmap.put(&flags, false, 19)
            try hashmap.put(&flags, true, 23)
            if (flags.count != 2 or hashmap.get(&flags, false)? != 19 or hashmap.get(&flags, true)? != 23) return -6
            duplicate := false
            hashmap.put(&flags, false, 99) catch |err| { duplicate = err == .key_exists }
            if (!duplicate or flags.count != 2 or hashmap.get(&flags, false)? != 19) return -7
            sparse hashmap.AutoHashMap(SparseAlias, i16) := hashmap.init(mem.c_allocator)
            defer hashmap.deinit(&sparse)
            try hashmap.put(&sparse, .negative, 7)
            try hashmap.put(&sparse, .first, 3)
            try hashmap.put(&sparse, .far, 90)
            if (sparse.count != 3 or hashmap.get(&sparse, Sparse.negative)? != 7) return -8
            if (hashmap.get(&sparse, .first)? != 3 or hashmap.get(&sparse, .far)? != 90) return -9
            ids hashmap.AutoHashMap(Id, i16) := hashmap.init(mem.c_allocator)
            defer hashmap.deinit(&ids)
            try hashmap.put(&ids, Id(Inner(0)), 19)
            try hashmap.put(&ids, Id(Inner(maxval!(u64))), 23)
            if (hashmap.get(&ids, Id(Inner(0)))? != 19) return -10
            if (hashmap.get(&ids, Id(Inner(maxval!(u64))))? != 23) return -11
            if (hashmap.get(&ids, Id(Inner(1))) != null) return -12
            return 42
        }
        """,
        """
        import "@std/hashmap"
        import "@std/mem"
        Padding :: struct_type!(.c, ["tag", "number"], [u8, u64], [null, null])
        Row :: struct { values [2]u16 }
        Key :: struct { padded Padding, rows [2]Row }
        custom_hash proc(_ u16) usize { return 0 }
        custom_eql proc(left, right u16) bool {
            return left - divtrunc!(left, 100) * 100 == right - divtrunc!(right, 100) * 100
        }
        main proc() i16 ! (hashmap.PutError | mem.AllocError) {
            strings hashmap.StringHashMap(i16) := hashmap.init(mem.c_allocator)
            defer hashmap.deinit(&strings)
            left [3]mut u8 := [97, 0, 98]
            right [3]mut u8 := [97, 0, 98]
            other [3]mut u8 := [97, 0, 99]
            if ((&left).ptr == (&right).ptr) return -7
            try hashmap.put(&strings, left[..], 42)
            records hashmap.AutoHashMap(Key, i16) := hashmap.init(mem.c_allocator)
            defer hashmap.deinit(&records)
            first Key := { padded = { tag = 7, number = maxval!(u64) }, rows = [{ values = [2, 9] }, { values = [4, 8] }] }
            same Key := { padded = { tag = 7, number = maxval!(u64) }, rows = [{ values = [2, 9] }, { values = [4, 8] }] }
            first_bytes *mut u8 :: ptrcast!(&first.padded)
            same_bytes *mut u8 :: ptrcast!(&same.padded)
            if (sizeof!(Padding) != 16 or alignof!(Padding) != 8) return -1
            for (usize(1)..usize(8)) |index| {
                first_bytes[index] = 17
                same_bytes[index] = 238
            }
            if (first_bytes[1] == same_bytes[1]) return -2
            try hashmap.put(&records, first, 19)
            if (hashmap.get(&records, same)? != 19) return -3
            duplicate := false
            hashmap.put(&records, same, 99) catch |err| { duplicate = err == .key_exists }
            if (!duplicate or records.count != 1 or hashmap.get(&records, first)? != 19) return -4
            reversed :: Key{ padded = { tag = 7, number = maxval!(u64) }, rows = [{ values = [9, 2] }, { values = [4, 8] }] }
            swapped :: Key{ padded = { tag = 7, number = maxval!(u64) }, rows = [{ values = [4, 8] }, { values = [2, 9] }] }
            try hashmap.put(&records, reversed, 23)
            try hashmap.put(&records, swapped, 31)
            if (hashmap.get(&records, reversed)? != 23 or hashmap.get(&records, swapped)? != 31) return -5
            same.padded.number = 0
            if (hashmap.get(&records, same) != null or records.count != 3) return -6
            if (hashmap.get(&strings, right[..])? != 42) return -8
            if (hashmap.get(&strings, right[..2]) != null or hashmap.get(&strings, other[..]) != null) return -9
            duplicate = false
            hashmap.put(&strings, right[..], 99) catch |err| { duplicate = err == .key_exists }
            if (!duplicate or strings.count != 1 or hashmap.get(&strings, left[..])? != 42) return -10
            try hashmap.put(&strings, "", 7)
            try hashmap.put(&strings, other[..], 23)
            if (strings.count != 3 or hashmap.get(&strings, "")? != 7 or hashmap.get(&strings, other[..])? != 23) return -11
            custom hashmap.HashMap(u16, i16, custom_hash, custom_eql) := hashmap.init(mem.c_allocator)
            defer hashmap.deinit(&custom)
            for (u16(1)..u16(20)) |key| { try hashmap.put(&custom, key, i16(key * 2)) }
            for (u16(1)..u16(20)) |key| {
                if (hashmap.get(&custom, key + 100)? != i16(key * 2)) return -12
            }
            duplicate = false
            hashmap.put(&custom, 101, 999) catch |err| { duplicate = err == .key_exists }
            if (!duplicate or custom.count != 19 or hashmap.get(&custom, 1)? != 2) return -13
            if (hashmap.get(&custom, 99) != null) return -14
            return 42
        }
        """,
    )
    for text in native_cases:
        source.write_text(textwrap.dedent(text).strip() + "\n")
        interpreted = run(str(HONEY), "run", str(source))
        assert interpreted.returncode == 1 and interpreted.stdout == b"", outcome(interpreted)
        assert b"foreign function calls are unsupported by the interpreter" in interpreted.stderr, outcome(interpreted)
        assert len(interpreted.stderr.splitlines()) == 1, outcome(interpreted)
        for optimization in ("-O0", "-O2"):
            built = run(str(HONEY), "build", str(source), "-o", str(executable), optimization)
            assert outcome(built) == (0, b"", b""), outcome(built)
            assert outcome(run(str(executable))) == (0, b"42\n", b""), optimization
            executable.unlink()
    invalid_keys = (("f64", "f64(1)"), ("[]u8", '"abc"'), ("?i16", "some!(i16(1))"))
    for key_type, key in invalid_keys:
        source.write_text(textwrap.dedent(f"""
            import "@std/hashmap"
            import "@std/mem"
            main proc() bool {{
                map := hashmap.init({key_type}, i16, null, null, mem.c_allocator)
                return hashmap.get(&map, {key}) == null
            }}
        """).strip() + "\n")
        check(source, executable, (
            b"true\n",
            b"AutoHashMap key requires integers, booleans, enums, distinct types, arrays or records",
        ))
    return 1 + len(invalid_keys), len(native_cases)


def check_io_packages(temporary, executable):
    project = temporary / "io"
    project.mkdir()
    (project / "build.hon").write_text("# project root\n")
    for package in ("std", "ffi"):
        (project / package).symlink_to(ROOT / package, target_is_directory=True)
    source = project / "main.hon"
    writer = """
        import "@std/io"
        State :: struct {
            bytes []mut u8
            len usize = 0
            chunk usize = 3
            fail_at usize = maxval!(usize)
            mode u8 = 0
        }
        write proc(context ?@mut anyopaque, _ io.Handle, bytes []u8) usize ! io.WriteError {
            state @mut State :: ptrcast!(context?)
            if (state.len >= state.fail_at) return .write_failed
            if (state.mode == 1) return 0
            if (state.mode == 2) return bytes.len + 1
            count := bytes.len
            if (count > state.chunk) count = state.chunk
            if (count > state.bytes.len - state.len) return .write_failed
            memcopy!(state.bytes[state.len..state.len + count], bytes[..count])
            state.len += count
            return count
        }
        output proc(state @mut State) io.Writer {
            return { context = state, handle = { file_desc = -1 }, write = write }
        }
        equal proc(state @State, expected []u8) bool {
            if (state.len != expected.len) return false
            for expected |byte, index| { if (state.bytes[index] != byte) return false }
            return true
        }
    """
    cases = (
        """
        Kind :: enum(i16) { negative = -7, first = 3, far = 90 }
        KindAlias :: alias Kind
        Inner :: distinct i16
        Id :: distinct Inner
        main proc() i16 ! io.WriteError {
            buffer [512]mut u8 := undefined
            state State := { bytes = buffer[..] }
            try io.print("{{{} {d} {b} {o} {x} {X} {c} {s} {}}}",
                output(&state), {true, i16(-42), u8(5), u16(8), u32(254),
                    u64(254), u8('!'), "ok", Kind.far})
            if (!equal(&state, "{true -42 101 10 fe FE ! ok .far}")) return -1
            state.len = 0
            try io.print("{} {} {} {}", output(&state),
                {Id(Inner(-42)), false, KindAlias.first, ""})
            if (!equal(&state, "-42 false .first ")) return -2
            state.len = 0
            try io.print("", output(&state), {})
            if (!equal(&state, "")) return -3
            try io.print("{{plain}}", output(&state), {})
            if (!equal(&state, "{plain}")) return -4
            return 42
        }
        """,
        """
        main proc() i16 ! io.WriteError {
            buffer [512]mut u8 := undefined
            state State := { bytes = buffer[..], chunk = 1 }
            try io.print("{} {} {} {} {}", output(&state),
                {minval!(i64), maxval!(u64), i8(-128), u8(0), isize(-1)})
            if (!equal(&state, "-9223372036854775808 18446744073709551615 -128 0 -1")) return -1
            state.len = 0
            try io.print("{x} {b} {o}", output(&state), {i16(-255), i8(-5), u8(255)})
            if (!equal(&state, "-ff -101 377")) return -2
            return 42
        }
        """,
        """
        main proc() i16 {
            buffer [64]mut u8 := undefined
            state State := { bytes = buffer[..], chunk = 2, fail_at = 4 }
            failed := false
            io.print("abcdef{}", output(&state), {42}) catch |err| { failed = err == .write_failed }
            if (!failed or !equal(&state, "abcd")) return -1
            state.len = 0
            state.mode = 1
            failed = false
            io.print("{}", output(&state), {42}) catch |err| { failed = err == .no_progress }
            if (!failed or state.len != 0) return -2
            state.mode = 2
            failed = false
            io.print("{}", output(&state), {42}) catch |err| { failed = err == .write_failed }
            if (!failed or state.len != 0) return -3
            return 42
        }
        """,
        """
        format proc() []u8 { return "{} {s} {}" }
        render proc(writer io.Writer, number i16) void ! io.WriteError {
            bytes [2]u8 :: ['o', 'k']
            try io.print(format(), writer, {number, bytes, &bytes})
        }
        main proc() i16 ! io.WriteError {
            buffer [64]mut u8 := undefined
            state State := { bytes = buffer[..] }
            for (i16(40)..i16(43)) |value| {
                state.len = 0
                try render(output(&state), value)
                if (value == 42 and !equal(&state, "42 ok ok")) return -1
            }
            return 42
        }
        """,
    )
    for text in cases:
        source.write_text(textwrap.dedent(writer + text).strip() + "\n")
        check(source, executable, (b"42\n", None))
    errors = (
        ('"{"', '{}', b"unmatched '{'"),
        ('"}"', '{}', b"unmatched '}'"),
        ('"{q}"', '{42}', b"unknown specifier"),
        ('"{dd}"', '{42}', b"one-character specifier"),
        ('"{}"', '{}', b"argument count does not match the tuple"),
        ('"plain"', '{42}', b"argument count does not match the tuple"),
        ('"{}"', '{value = 42}', b"arguments must be a tuple"),
        ('"{}"', '42', b"arguments must be a tuple"),
        ('"{d}"', '{true}', b"requires an integer or float argument"),
        ('"{b}"', '{true}', b"requires an integer argument"),
        ('"{c}"', '{u16(65)}', b"requires an unsigned integer that fits in u8"),
        ('"{}"', '{{ value = 42 }}', b"does not support this argument type"),
    )
    for format_value, args, diagnostic in errors:
        source.write_text(textwrap.dedent(writer + f"""
            main proc() i16 ! io.WriteError {{
                buffer [64]mut u8 := undefined
                state State := {{ bytes = buffer[..] }}
                try io.print({format_value}, output(&state), {args})
                return 42
            }}
        """).strip() + "\n")
        check(source, executable, (b"", diagnostic))
    source.write_text(textwrap.dedent(writer + """
        Distance :: distinct f64
        main proc() i16 ! io.WriteError {
            buffer [512]mut u8 := undefined
            state State := { bytes = buffer[..] }
            try io.print("{} {d} {e}", output(&state), {f64(1.25), Distance(-2.5), f64(0.5)})
            if (!equal(&state, "1.25 -2.5 5.0000000000000000e-01")) return -1
            return 42
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
    return len(cases) + len(errors), 1


def check_variadic_libc(temporary, executable):
    source = temporary / "variadic.hon"
    source.write_text(textwrap.dedent("""
        snprintf c_func(buffer ?*mut c_char, size c_ulong, format ?*c_char, ...) c_int
        main proc() i16 {
            buffer [256]mut u8 := undefined
            count :: snprintf(ptrcast!((&buffer).ptr), c_ulong(buffer.len),
                "%d %d %d %d %d %d %u %lld %llu %.2f %s",
                true, i8(-128), u8(255), i16(-32768), u16(65535),
                i32(-2147483648), maxval!(u32), minval!(i64), maxval!(u64),
                f64(1.25), "ok".ptr)
            expected :: "1 -128 255 -32768 65535 -2147483648 4294967295 -9223372036854775808 18446744073709551615 1.25 ok"
            if (count < 0 or usize(count) != expected.len) return -1
            for expected |byte, index| { if (buffer[index] != byte) return -2 }
            if (buffer[expected.len] != 0) return -3
            return 42
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
        variadic_count = check_variadic_libc(temporary, executable)
        arraylist_count, arraylist_native_count = check_arraylist_packages(temporary, executable)
        count += arraylist_count
        hashmap_count, hashmap_native_count = check_hashmap_packages(temporary, executable)
        count += hashmap_count
        io_count, io_native_count = check_io_packages(temporary, executable)
        count += io_count
        count += check_packages(temporary, executable)
        count += check_namespace_packages(temporary, executable)
        count += check_record_packages(temporary, executable)
        count += check_data_model_packages(temporary, executable)
        count += check_specialization_packages(temporary, executable)
        count += check_typeinfo_packages(temporary, executable)
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
        print(f"{count} programs matched interpreter at -O0 and -O2; {allocator_count + arraylist_native_count + hashmap_native_count + io_native_count + variadic_count} native allocator/IO programs, LLVM output and CLI errors passed")


if __name__ == "__main__":
    main()
