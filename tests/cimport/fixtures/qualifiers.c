#include "qualifiers.h"
#include <string.h>

int readonly_text(const char *text) { return (int)strlen(text); }

int pointer_levels(
    const char *readonly_value, char *mutable_value,
    const char **nested_readonly, char *const *frozen_pointer,
    const char *const *frozen_both, ConstChar *typedef_const,
    const MutableText *qualified_typedef, FrozenText *frozen_typedef
) {
    if (readonly_value[0] != 'i' || mutable_value[0] != 'm' ||
        (*nested_readonly)[0] != 'i' || (*frozen_pointer)[0] != 'm' ||
        (*frozen_both)[0] != 'i' || typedef_const[0] != 'i' ||
        (*qualified_typedef)[0] != 'm' || (*frozen_typedef)[0] != 'm') return -1;
    mutable_value[0] = 'x';
    return 42;
}

float get_small_value(void) { return SMALL_VALUE; }
float get_large_value(void) { return LARGE_VALUE; }
double get_fraction_value(void) { return FRACTION_VALUE; }
double get_negative_zero(void) { return NEGATIVE_ZERO; }
