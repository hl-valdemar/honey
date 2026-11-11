#include "readable.h"

Rectangle paint_rectangle(Rectangle rectangle, Color color) {
    rectangle.position.x += color.red;
    rectangle.position.y += color.green;
    rectangle.color = color;
    return rectangle;
}

Color rectangle_color(Rectangle rectangle) {
    return rectangle.color;
}
