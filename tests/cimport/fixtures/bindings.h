#ifndef HONEY_CIMPORT_FIXTURE
#define HONEY_CIMPORT_FIXTURE
#define ANSWER (6 * 7)
#define RATIO 0.5f
#define LABEL "honey"
#define APPLY(x) ((x) + 1)
typedef struct Point { float x; float y; } Point;
typedef union Value { int integer; float scalar; } Value;
typedef struct Handle Handle;
typedef void (*Callback)(Point point, void *context);
typedef enum Mode { MODE_IDLE, MODE_ACTIVE = 7, MODE_DONE } Mode;
extern Point move_point(Point point, float scale);
extern void set_callback(Callback callback, void *context);
extern Handle *open_handle(void);
extern int return_value(int value);
extern int global_value;
static inline int inline_only(int value) { return value + 1; }
#endif
