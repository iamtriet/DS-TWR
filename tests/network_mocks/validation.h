#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <errno.h>
#define FW_VERSION_PATCH 14
int64_t k_uptime_get(void);
bool ota_connected(void);
void out(const char *format,...);
int console_binary(const uint8_t *data,size_t length);
