#include <stddef.h>
#include <stdint.h>
#include <stdarg.h>
#include <string.h>
#include <math.h>

typedef struct { float x, y; } F2;
typedef struct { float x, y, z, w; } F4;
typedef struct { double x, y; } D2;
typedef struct { double x, y, z, w; } D4;
typedef struct { F2 rows[2]; } NestedF;
typedef struct { D2 rows[2]; } NestedD;
typedef struct { float rows[2][2]; } MatrixF;
typedef struct { double rows[2][2]; } MatrixD;
typedef union { F4 record; float values[4]; float scalar; } FloatUnion;
typedef union { D2 record; double values[2]; double scalar; } DoubleUnion;
typedef struct { uint8_t tag; float number; uint16_t tail; } Mixed;
typedef struct { uint8_t bytes[3]; } Odd3;
typedef struct { uint8_t bytes[9]; } Odd9;
typedef union { uint16_t word; uint8_t bytes[3]; } OddUnion;
typedef union { uint64_t bits; float number; uint8_t bytes[5]; } MixedUnion;
typedef struct { uint64_t values[3]; } Large;
typedef union { uint64_t values[3]; uint8_t bytes[19]; } LargeUnion;
typedef struct { uint8_t tag; double number; uint16_t tail; } Padded;
typedef struct { uint8_t r, g, b, a; } Color;
Color color_identity(Color value) { return value; }
int abi_callback_color(Color (*callback)(Color)) {
    Color value = callback((Color){17, 63, 129, 255});
    return value.r == 17 && value.g == 63 && value.b == 129 && value.a == 255;
}
int f0(void) { return 1; }
int g0(void) { return 2; }
int s0(void) { return 3; }
int p0(void) { return 4; }
int paths(void) { return 5; }
int out(void) { return 6; }
int trap(void) { return 7; }
int report(void) { return 8; }
int print_float(void) { return 9; }
int fmt_int(void) { return 10; }
typedef struct { int proc; } KeywordRecord;
int proc(int value) { return value + 1; }
KeywordRecord abi_keyword_record(KeywordRecord value) { value.proc += 1; return value; }

#define LAYOUT(T, A, B) { sizeof(T), _Alignof(T), offsetof(T, A), offsetof(T, B) }
static const size_t layouts[][4] = {
    LAYOUT(F2, x, y), LAYOUT(F4, z, w), LAYOUT(D2, x, y), LAYOUT(D4, z, w),
    LAYOUT(NestedF, rows, rows[1].y), LAYOUT(NestedD, rows, rows[1].y),
    LAYOUT(FloatUnion, record, values), LAYOUT(DoubleUnion, record, values),
    LAYOUT(Mixed, number, tail), LAYOUT(Odd3, bytes, bytes[2]),
    LAYOUT(Odd9, bytes, bytes[8]), LAYOUT(OddUnion, word, bytes),
    LAYOUT(MixedUnion, bits, number), LAYOUT(Large, values, values[2]),
    LAYOUT(LargeUnion, values, bytes),
    LAYOUT(Padded, number, tail),
    LAYOUT(MatrixF, rows, rows[1][1]), LAYOUT(MatrixD, rows, rows[1][1]),
};
size_t abi_layout(unsigned type, unsigned property) { return layouts[type][property]; }

float abi_float(float value) { return value + 0.5f; }
uint32_t abi_float_bits(float value) {
    uint32_t bits;
    memcpy(&bits, &value, sizeof(bits));
    return bits;
}
float abi_float_from_bits(uint32_t bits) {
    float value;
    memcpy(&value, &bits, sizeof(value));
    return value;
}
int abi_variadic(int count, ...) {
    static const float expected[] = { 1.25f, -2.5f, 16777217.0f, 0.1f,
        5.5f, 6.25f, 7.75f, 8.5f, 9.25f, 10.75f, 0.0f, -0.0f };
    if (count != 12) return 0;
    va_list args;
    va_start(args, count);
    int valid = 1;
    for (int i = 0; i < count; ++i) {
        double value = va_arg(args, double);
        if (value != (double)expected[i]) valid = 0;
        if (i >= 10 && !!signbit(value) != !!signbit(expected[i])) valid = 0;
    }
    va_end(args);
    return valid;
}

F2 abi_f2(F2 value) { value.x += 1; value.y += 2; return value; }
F4 abi_f4(F4 value) { value.x += 1; value.y += 2; value.z += 3; value.w += 4; return value; }
D2 abi_d2(D2 value) { value.x += 1; value.y += 2; return value; }
D4 abi_d4(D4 value) { value.x += 1; value.y += 2; value.z += 3; value.w += 4; return value; }
NestedF abi_nested_f(NestedF value) {
    value.rows[0] = abi_f2(value.rows[0]); value.rows[1] = abi_f2(value.rows[1]); return value;
}
NestedD abi_nested_d(NestedD value) {
    value.rows[0] = abi_d2(value.rows[0]); value.rows[1] = abi_d2(value.rows[1]); return value;
}
FloatUnion abi_float_union(FloatUnion value) { value.record = abi_f4(value.record); return value; }
DoubleUnion abi_double_union(DoubleUnion value) { value.record = abi_d2(value.record); return value; }
Mixed abi_mixed(Mixed value) { value.tag += 1; value.number += 2; value.tail += 3; return value; }
Odd3 abi_odd3(Odd3 value) { for (int i = 0; i < 3; ++i) value.bytes[i] += i + 1; return value; }
Odd9 abi_odd9(Odd9 value) { for (int i = 0; i < 9; ++i) value.bytes[i] += i + 1; return value; }
OddUnion abi_odd_union(OddUnion value) { value.word += 7; return value; }
MixedUnion abi_mixed_union(MixedUnion value) { value.bits += 9; return value; }
Large abi_large(Large value) { for (int i = 0; i < 3; ++i) value.values[i] += i + 1; return value; }
LargeUnion abi_large_union(LargeUnion value) { for (int i = 0; i < 3; ++i) value.values[i] += i + 1; return value; }

/* force independent integer/SIMD register exhaustion and stack arguments. */
Mixed abi_pressure(uint64_t a, uint64_t b, uint64_t c, uint64_t d,
    uint64_t e, uint64_t f, uint64_t g, uint64_t h, uint64_t i,
    double j, double k, double l, double m, double n, double o, double p, double q,
    F4 floats, Odd9 odd, Mixed mixed, Large large, double tail) {
    mixed.tag += (uint8_t)(a + b + c + d + e + f + g + h + i);
    mixed.number += j + k + l + m + n + o + p + q + floats.x + floats.w + tail;
    mixed.tail += odd.bytes[8] + (uint16_t)large.values[2];
    return mixed;
}

#define CALLBACK(NAME, T, INPUT, CHECK) \
int abi_callback_##NAME(T (*callback)(T)) { \
    T input = INPUT; T value = callback(input); return (CHECK); \
}
CALLBACK(f2, F2, ((F2){1, 3}), value.x == 2 && value.y == 5)
CALLBACK(f4, F4, ((F4){1, 3, 5, 7}), value.x == 2 && value.y == 5 && value.z == 8 && value.w == 11)
CALLBACK(d2, D2, ((D2){1, 3}), value.x == 2 && value.y == 5)
CALLBACK(d4, D4, ((D4){1, 3, 5, 7}), value.x == 2 && value.y == 5 && value.z == 8 && value.w == 11)
CALLBACK(nested_f, NestedF, ((NestedF){{{1, 3}, {5, 7}}}), value.rows[0].x == 2 && value.rows[0].y == 5 && value.rows[1].x == 6 && value.rows[1].y == 9)
CALLBACK(nested_d, NestedD, ((NestedD){{{1, 3}, {5, 7}}}), value.rows[0].x == 2 && value.rows[0].y == 5 && value.rows[1].x == 6 && value.rows[1].y == 9)
CALLBACK(float_union, FloatUnion, ((FloatUnion){.values = {1, 3, 5, 7}}), value.record.x == 2 && value.record.y == 5 && value.record.z == 8 && value.record.w == 11)
CALLBACK(double_union, DoubleUnion, ((DoubleUnion){.values = {1, 3}}), value.record.x == 2 && value.record.y == 5)
CALLBACK(mixed, Mixed, ((Mixed){1, 3, 5}), value.tag == 2 && value.number == 5 && value.tail == 8)
CALLBACK(odd3, Odd3, ((Odd3){{1, 3, 5}}), value.bytes[0] == 2 && value.bytes[1] == 5 && value.bytes[2] == 8)
CALLBACK(odd9, Odd9, ((Odd9){{1, 3, 5, 7, 9, 11, 13, 15, 17}}), value.bytes[0] == 2 && value.bytes[4] == 14 && value.bytes[8] == 26)
CALLBACK(odd_union, OddUnion, ((OddUnion){.word = 35}), value.word == 42)
CALLBACK(mixed_union, MixedUnion, ((MixedUnion){.bits = 33}), value.bits == 42)
CALLBACK(large, Large, ((Large){{1, 3, 5}}), value.values[0] == 2 && value.values[1] == 5 && value.values[2] == 8 && input.values[0] == 1 && input.values[2] == 5)
CALLBACK(large_union, LargeUnion, ((LargeUnion){.values = {1, 3, 5}}), value.values[0] == 2 && value.values[1] == 5 && value.values[2] == 8 && input.values[0] == 1 && input.values[2] == 5)

int abi_callback_float(float (*callback)(float)) { return callback(1.25f) == 1.75f; }
int abi_callback_pressure(Mixed (*callback)(uint64_t, uint64_t, uint64_t, uint64_t,
    uint64_t, uint64_t, uint64_t, uint64_t, uint64_t,
    double, double, double, double, double, double, double, double,
    F4, Odd9, Mixed, Large, double)) {
    Large large = {{1, 3, 5}};
    Mixed value = callback(1, 2, 3, 4, 5, 6, 7, 8, 9,
        1, 2, 3, 4, 5, 6, 7, 8, (F4){1, 3, 5, 7},
        (Odd9){{1, 3, 5, 7, 9, 11, 13, 15, 17}}, (Mixed){1, 3, 5}, large, 9);
    return value.tag == 46 && value.number == 56 && value.tail == 27 && large.values[2] == 5;
}

MatrixF abi_matrix_f(MatrixF value) {
    for (int i = 0; i < 2; ++i) for (int j = 0; j < 2; ++j) value.rows[i][j] += i * 2 + j + 1;
    return value;
}
MatrixD abi_matrix_d(MatrixD value) {
    for (int i = 0; i < 2; ++i) for (int j = 0; j < 2; ++j) value.rows[i][j] += i * 2 + j + 1;
    return value;
}
CALLBACK(matrix_f, MatrixF, ((MatrixF){{{1, 3}, {5, 7}}}), value.rows[0][0] == 2 && value.rows[0][1] == 5 && value.rows[1][0] == 8 && value.rows[1][1] == 11)
CALLBACK(matrix_d, MatrixD, ((MatrixD){{{1, 3}, {5, 7}}}), value.rows[0][0] == 2 && value.rows[0][1] == 5 && value.rows[1][0] == 8 && value.rows[1][1] == 11)

Large abi_partial(uint64_t a, uint64_t b, uint64_t c, uint64_t d,
    uint64_t e, uint64_t f, uint64_t g, double h, double i, double j,
    double k, double l, double m, double n, Odd9 odd, F4 floats,
    uint64_t tail, double end) {
    return (Large){{a + b + c + d + e + f + g + tail,
        (uint64_t)(h + i + j + k + l + m + n + end),
        odd.bytes[8] + (uint64_t)floats.w}};
}
int abi_callback_partial(Large (*callback)(uint64_t, uint64_t, uint64_t,
    uint64_t, uint64_t, uint64_t, uint64_t, double, double, double,
    double, double, double, double, Odd9, F4, uint64_t, double)) {
    Large value = callback(1, 2, 3, 4, 5, 6, 7, 1, 2, 3, 4, 5, 6, 7,
        (Odd9){{1, 3, 5, 7, 9, 11, 13, 15, 17}}, (F4){1, 3, 5, 7}, 8, 8);
    return value.values[0] == 36 && value.values[1] == 36 && value.values[2] == 24;
}
