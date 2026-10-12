#ifndef VALIDATION_H
#define VALIDATION_H
#include <zephyr.h>
#include <stdint.h>
#include <stdbool.h>
#define FW_VERSION_MAJOR 1
#ifndef FW_VERSION_MINOR
#define FW_VERSION_MINOR 0
#endif
#ifndef FW_VERSION_PATCH
#define FW_VERSION_PATCH 0
#endif
#define STR_(x) #x
#define STR(x) STR_(x)
#define FW_VERSION STR(FW_VERSION_MAJOR) "." STR(FW_VERSION_MINOR) "." STR(FW_VERSION_PATCH)
/* Physical GPIO mappings from native Altium/reference, never Arduino indexes. */
#define IMU_INT 4
#define IMU_BOOT 5
#define IMU_RST 22
#define BOARD_LED 6
#define IMU_ADDR 0x4a
int console_init(void);
int console_binary(const uint8_t *data,size_t length);
void uwb3d_publish(uint16_t source,uint16_t target,double meters,uint32_t sequence);
void out(const char *fmt, ...) __attribute__((format(printf,1,2)));
void cli_service(void);
int console_remote(const void *data, size_t len);
void console_remote_clear(void);
void control_notify(const char *line);
bool control_enabled(void);
void control_disconnect(void);
bool ota_upload_locked(void);
uint32_t console_drops(void);
void board_info(void);
int board_init(void);
int board_led(bool on);
void board_led_role_mode(bool enable);
bool board_led_role_enabled(void);
void diagnostic(void);
int imu_init(void);
void imu_service(void);
int imu_read(void);
void imu_status(void);
void imu_stream(bool enable);
void imu_motion(void);
bool imu_fresh(void);
int uwb_init(void);
void uwb_status(void);
int uwb_command(int argc, char **argv);
void uwb_service(void);
int multitag_command(int argc, char **argv);
int ota_init(void);
int ota_command(int argc, char **argv);
void ota_status(void);
const char *status_name(int rc);
#endif
