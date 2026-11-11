#ifndef HONEY_CIMPORT_READABLE
#define HONEY_CIMPORT_READABLE

typedef struct { int red; int green; } Color;
typedef struct Rectangle {
    struct { int x; int y; } position;
    union { int integer; float scalar; } payload;
    Color color;
} Rectangle;

extern Rectangle paint_rectangle(Rectangle rectangle, Color color);
extern Color rectangle_color(Rectangle rectangle);

#endif
