#ifndef HONEY_ALL_DEPENDENCY_H
#define HONEY_ALL_DEPENDENCY_H

typedef struct AllDependency {
    char tag;
    double value;
    int count;
} AllDependency;

extern int unrelated_included_function(void);
#define UNRELATED_INCLUDED_VALUE 99

#endif
