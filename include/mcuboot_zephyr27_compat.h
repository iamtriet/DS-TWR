/* Zephyr 2.7.1 defines this void API in drivers/timer/sys_clock_init.c but
 * omits its public prototype. MCUboot 1.9 calls it during chain-loading.
 * Supply the matching declaration without patching either dependency. */
#ifndef PCB_MCUBOOT_ZEPHYR27_COMPAT_H
#define PCB_MCUBOOT_ZEPHYR27_COMPAT_H
#ifdef __cplusplus
extern "C" {
#endif
void sys_clock_disable(void);
#ifdef __cplusplus
}
#endif
#endif
