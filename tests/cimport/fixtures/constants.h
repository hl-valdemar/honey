#ifndef HONEY_CONSTANTS_H
#define HONEY_CONSTANTS_H

typedef unsigned char uint8_t;
typedef uint8_t pw_status;
static const pw_status PW_SUCCESS = 0;
static const pw_status PW_ERR_NOT_MAIN_THREAD = 1;
static const pw_status PW_ERR_APPKIT_EXCEPTION = 2;
static const pw_status PW_ERR_WINDOW_CREATION_ARGS = 3;
static const pw_status PW_ERR_WINDOW_CREATION_FAILURE = 4;
static const pw_status PW_ERR_WINDOW_HANDLE_NULL = 5;
static const pw_status PW_ERR_NO_GRAPHICAL_SESSION = 6;
static const pw_status PW_MAX = 255;

typedef const unsigned short Readonly;
static Readonly WIDTH = 65535;
static const signed char MINIMUM = -128;
static const unsigned long long MAXIMUM = 18446744073709551615ULL;
static const _Bool ENABLED = 1;
static const float FRACTION = 0.125f;
extern const int DEFINED;
const int DEFINED = 42;

typedef enum Mode { MODE_IDLE = 2, MODE_READY = 6 } Mode;
static const Mode CURRENT_MODE = MODE_READY;

static int MUTABLE = 8;
extern const int EXTERNAL_ONLY;
static const volatile int DEVICE_STATUS = 7;
typedef const volatile int DeviceRegister;
static DeviceRegister DEVICE_ALIAS = 9;
static const char *MUTABLE_POINTER = "text";
static const char *const ADDRESS = "text";

#endif
