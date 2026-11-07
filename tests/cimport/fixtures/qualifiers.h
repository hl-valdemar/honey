#ifndef HONEY_CIMPORT_QUALIFIERS
#define HONEY_CIMPORT_QUALIFIERS
#define SMALL_VALUE 1.0e-30f
#define LARGE_VALUE 1.0e30f
#define FRACTION_VALUE (1.0 / 3.0)
#define NEGATIVE_ZERO (-0.0)
typedef const char ConstChar;
typedef char *MutableText;
typedef char *const FrozenText;
extern int readonly_text(const char *text);
extern int pointer_levels(
    const char *readonly_value,
    char *mutable_value,
    const char **nested_readonly,
    char *const *frozen_pointer,
    const char *const *frozen_both,
    ConstChar *typedef_const,
    const MutableText *qualified_typedef,
    FrozenText *frozen_typedef
);
extern float get_small_value(void);
extern float get_large_value(void);
extern double get_fraction_value(void);
extern double get_negative_zero(void);
#endif
