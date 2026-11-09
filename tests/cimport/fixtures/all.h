#ifndef HONEY_ALL_H
#define HONEY_ALL_H

#include "all_dependency.h"

typedef struct AllUnsupported {
    AllDependency dependency;
    long double value;
} AllUnsupported;
extern AllUnsupported all_unsupported_record(void);

typedef int AllCount;
typedef enum AllMode {
    ALL_MODE_FIRST = 6,
    ALL_MODE_NEXT
} AllMode;

extern AllDependency all_transform(AllDependency input);
extern AllCount all_unused_function(void);
#define ALL_NUMBER (20 + 22)
#define c_types 1

extern int all_global;
static inline int all_inline(void) { return 1; }
#define ALL_APPLY(value) ((value) + 1)
extern long double all_long_double(long double value);

#endif
