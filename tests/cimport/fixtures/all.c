#include "all.h"

AllDependency all_transform(AllDependency input) {
    input.tag += 1;
    input.value += 0.5;
    input.count += 2;
    return input;
}

AllCount all_unused_function(void) {
    return ALL_NUMBER + ALL_MODE_NEXT;
}
