#include "validation.h"
#include "radio.h"
#include <deca_device_api.h>
#include <string.h>
#include <math.h>
#include <sys/atomic.h>
#include "uwb_app.h"
#include "uwb3d_wire.h"
static atomic_t busy;
static bool network_rx_active;
bool radio_busy(void) { return atomic_get(&busy)!=0; }
static int enter(void) { atomic_inc(&busy); if (ota_connected()) { atomic_dec(&busy); return -EBUSY; } return 0; }


static radio_stats_t stats;
static uint16_t tx_ant,rx_ant,pgcount;
static uint32_t tx_power;
static uint8_t pgdelay;
static const char *cal_source="missing";
/* Preserve existing CH5/code9/64-symbol/6.8M PHY. Complete the vendor
 * configuration explicitly: standard SFD/PHR, PAC8, no STS/PDoA. */
static dwt_config_t phy={.chan=5,.txPreambLength=DWT_PLEN_64,.rxPAC=DWT_PAC8,
    .txCode=9,.rxCode=9,.sfdType=DWT_SFD_IEEE_4A,.dataRate=DWT_BR_6M8,
    .phrMode=DWT_PHRMODE_STD,.phrRate=DWT_PHRRATE_STD,.sfdTO=65,
    .stsMode=DWT_STS_MODE_OFF,.stsLength=DWT_STS_LEN_64,.pdoaMode=DWT_PDOA_M0};
static int checked(int rc)
{
    if (radio_port_error()) { stats.ready=false; stats.calibrated=false; rc=radio_port_error(); }
    stats.error=rc; return rc;
}
const radio_stats_t *radio_stats(void) { return &stats; }
static uint64_t stamp(bool is_tx)
{
    uint8_t b[5]={0};
    if (is_tx) dwt_readtxtimestamp(b); else dwt_readrxtimestamp(b,DWT_IP_M);
    uint64_t value=0; for (int i=4;i>=0;--i) value=(value<<8)|b[i]; return value;
}
static int apply_cal(void)
{
    dwt_txconfig_t txrf={.PGdly=pgdelay,.power=tx_power,.PGcount=pgcount};
    dwt_settxantennadelay(tx_ant); dwt_setrxantennadelay(rx_ant); dwt_configuretxrf(&txrf);
    int rc=checked(0); stats.calibrated=!rc; return rc;
}
static int impl_init(void)
{
    phy.dataRate=DWT_BR_6M8;phy.rxPAC=DWT_PAC8;phy.sfdType=DWT_SFD_IEEE_4A;
    phy.stsMode=DWT_STS_MODE_OFF;phy.stsLength=DWT_STS_LEN_64;phy.sfdTO=65;
    phy.txPreambLength=DWT_PLEN_64;phy.phrMode=DWT_PHRMODE_STD;phy.sfdTO=65;
    stats.ready=false; stats.calibrated=false; cal_source="missing";
    out("[UWB] reset, probe, vendor initialization");
    int rc=radio_port_init(); if (rc) return checked(rc);
    rc=dwt_probe(&radio_probe); if (checked(rc)) return stats.error;
    stats.device_id=dwt_readdevid();
    if (checked(0)) return stats.error;
    if (stats.device_id!=0xdeca0302U) return checked(-ENODEV);
    /* Probe installs the driver before checkidlerc; bounded idle wait. */
    int64_t deadline=k_uptime_get()+100;
    while (!dwt_checkidlerc()) {
        if (radio_port_error()) return checked(-EIO);
        if (k_uptime_get()>=deadline) return checked(-ETIMEDOUT);
        k_msleep(1);
    }
    rc=dwt_initialise(DWT_READ_OTP_PID|DWT_READ_OTP_LID|DWT_READ_OTP_BAT|DWT_READ_OTP_TMP);
    if (checked(rc)) return stats.error;
    rc=dwt_configure(&phy); if (checked(rc)) return stats.error;
    dwt_setinterrupt(DWT_INT_TXFRS_BIT_MASK|DWT_INT_RXFCG_BIT_MASK|SYS_STATUS_ALL_RX_ERR|SYS_STATUS_ALL_RX_TO,0,DWT_ENABLE_INT_ONLY);
    dwt_setrxaftertxdelay(0);
    dwt_setrxtimeout(60000); /* UUS: ~61.5ms; software adds its own deadline. */
    if (checked(0)) return stats.error;
    stats.ready=true;
    uint32_t antenna=0,pg=0,power=0;
    /* DWM3001C datasheet Rev F table 10 (module production calibration).
     * Only READ OTP. Channel 5 uses RX high16/TX low16, PGCOUNT high16. */
    dwt_otpread(0x1a,&antenna,1); dwt_otpread(0x18,&pg,1); dwt_otpread(0x11,&power,1);
    if (checked(0)) return stats.error;
    tx_ant=(uint16_t)antenna; rx_ant=(uint16_t)(antenna>>16);
    pgcount=(uint16_t)(pg>>16); tx_power=power; pgdelay=0;
    out("[UWB] OTP antenna=0x%08x PG=0x%08x power=0x%08x",antenna,pg,power);
    if (tx_ant && tx_ant!=0xffff && rx_ant && rx_ant!=0xffff && pgcount && pgcount<0x1000 && power && power!=UINT32_MAX) {
        cal_source="module OTP"; rc=apply_cal(); if (rc) return rc;
    } else out("[UWB] factory calibration absent/invalid; TX disabled until uwb calibration ...");
    radio_status(); return checked(0);
}
static int impl_calibrate(uint16_t tx_delay,uint16_t rx_delay,uint32_t power,uint8_t pg_delay)
{
    if (!stats.ready) return -ENODEV;
    if (!tx_delay || !rx_delay || tx_delay==0xffff || rx_delay==0xffff || !power || power==UINT32_MAX || !pg_delay) return -EINVAL;
    tx_ant=tx_delay; rx_ant=rx_delay; tx_power=power; pgdelay=pg_delay; pgcount=0;
    cal_source="explicit CLI (volatile)"; return apply_cal();
}
static int impl_stop(void)
{
    if (!stats.ready) return stats.error ? stats.error : -ENODEV;
    dwt_forcetrxoff(); return checked(0);
}
static int impl_tx(const uint8_t *data,size_t len,uint64_t *timestamp,bool response_expected,bool delayed,uint64_t raw_time)
{
    network_rx_active=false;
    if (!stats.ready) return -ENODEV;
    if (!stats.calibrated) return -EACCES;
    if (!data || !timestamp || !len || len>RADIO_FRAME_MAX) return -EINVAL;
    if (phy.phrMode==DWT_PHRMODE_STD && len+2>127) return -EMSGSIZE;
    dwt_forcetrxoff(); dwt_writesysstatuslo(DWT_INT_TXFRS_BIT_MASK|SYS_STATUS_ALL_RX_GOOD|SYS_STATUS_ALL_RX_ERR|SYS_STATUS_ALL_RX_TO);
    int rc=dwt_writetxdata((uint16_t)len,(uint8_t *)data,0);
    if (checked(rc)) { ++stats.tx_errors; return stats.error; }
    dwt_writetxfctrl((uint16_t)(len+2),0,1);
    uint32_t extra_ms=0;
    if (delayed) {
        uint64_t now=radio_system_time();
        uint64_t ticks=(raw_time-now)&UINT64_C(0xffffffffff);
        /* Guard an already-late command and bound blocking time. This is a
         * primitive for bench qualification, not a deterministic TDMA task. */
        if(ticks<UINT64_C(31948800) || ticks>UINT64_C(15974400000))return checked(-ERANGE);
        extra_ms=(uint32_t)(ticks/63897600U)+1;
        dwt_setdelayedtrxtime((uint32_t)(raw_time>>8)&0xfffffffeU);
    }
    rc=dwt_starttx((delayed?DWT_START_TX_DELAYED:DWT_START_TX_IMMEDIATE)|(response_expected?DWT_RESPONSE_EXPECTED:0));
    if (checked(rc)) { ++stats.tx_errors; return stats.error; }
    int64_t deadline=k_uptime_get()+30+extra_ms;
    do {
        uint32_t status=dwt_readsysstatuslo();
        if (checked(0)) { ++stats.tx_errors; return stats.error; }
        if (status&DWT_INT_TXFRS_BIT_MASK) {
            *timestamp=stamp(true); dwt_writesysstatuslo(DWT_INT_TXFRS_BIT_MASK);
            if(delayed && *timestamp!=u3d_predicted_tx(raw_time,tx_ant)) { ++stats.tx_errors;return checked(-ERANGE); }
            if (checked(0)) { ++stats.tx_errors; return stats.error; }
            ++stats.tx; stats.last_ms=k_uptime_get_32(); return 0;
        }
        k_usleep(100);
    } while (k_uptime_get()<deadline);
    ++stats.timeouts; ++stats.tx_errors; dwt_forcetrxoff(); return checked(-ETIMEDOUT);
}
static int impl_rx(uint8_t *data,size_t capacity,size_t *length,uint64_t *timestamp,uint32_t timeout_ms,bool already_enabled,bool keep_rx)
{
    if (!stats.ready) return -ENODEV;
    if (!data || !length || !timestamp || !timeout_ms || timeout_ms>1000) return -EINVAL;
    *length=0;
    if (!already_enabled) {
        dwt_forcetrxoff();
        dwt_writesysstatuslo(SYS_STATUS_ALL_RX_GOOD|SYS_STATUS_ALL_RX_ERR|SYS_STATUS_ALL_RX_TO);
        int rc=dwt_rxenable(DWT_START_RX_IMMEDIATE); if (checked(rc)) return stats.error;
        if(keep_rx)network_rx_active=true;
    }
    int64_t deadline=k_uptime_get()+timeout_ms;
    do {
        uint32_t status=dwt_readsysstatuslo();
        if (checked(0)) return stats.error;
        if (status&SYS_STATUS_ALL_RX_ERR) {
            if (status&DWT_INT_RXFCE_BIT_MASK) ++stats.crc_errors;
            ++stats.rx_errors; dwt_writesysstatuslo(SYS_STATUS_ALL_RX_ERR); dwt_forcetrxoff(); return checked(-EBADMSG);
        }
        if (status&DWT_INT_RXFCG_BIT_MASK) {
            uint8_t ranging;
            uint16_t total=dwt_getframelength(&ranging);
            if (checked(0)) return stats.error;
            if (total<2 || total-2>capacity || total-2>RADIO_FRAME_MAX) {
                ++stats.rx_errors; dwt_forcetrxoff(); return checked(-EMSGSIZE);
            }
            *length=total-2; *timestamp=stamp(false); dwt_readrxdata(data,(uint16_t)*length,0);
            dwt_writesysstatuslo(SYS_STATUS_ALL_RX_GOOD);
            if (checked(0)) return stats.error;
            ++stats.rx; stats.last_ms=k_uptime_get_32(); stats.last_length=(uint16_t)*length;
            return 0;
        }
        if (status&SYS_STATUS_ALL_RX_TO) { network_rx_active=false;break; }
        k_usleep(100);
    } while (k_uptime_get()<deadline);
    if(keep_rx && network_rx_active)return checked(-EAGAIN);
    ++stats.timeouts; dwt_forcetrxoff(); return checked(-ETIMEDOUT);
}
void radio_status(void)
{
    out("UWB ID=0x%08x ready=%u calibrated=%u error=%d IRQ edges=%u",stats.device_id,stats.ready,stats.calibrated,stats.error,radio_irq_count());
    if(phy.stsMode!=DWT_STS_MODE_OFF)
        out("UWB reference CH5 PRF64 code9 preamble1024 850Kbps PAC32 SFD4Z timeout%u PHR standard STS1024 mode1 AES-GCM PDoA off",phy.sfdTO);
    else out("UWB CH5 PRF64 code9 preamble%u 6.8Mbps PAC8 SFD0 timeout%u PHR %s STS off PDoA off",phy.txPreambLength==DWT_PLEN_128?128:64,phy.sfdTO,phy.phrMode==DWT_PHRMODE_EXT?"extended":"standard");
    out("UWB calibration=%s TXdelay=%u RXdelay=%u TXpower=0x%08x PGcount=%u PGdelay=0x%02x",cal_source,tx_ant,rx_ant,tx_power,pgcount,pgdelay);
    out("UWB TX=%u RX=%u TXerr=%u RXerr=%u CRC=%u timeout=%u last_len=%u last_event_ms=%u",stats.tx,stats.rx,stats.tx_errors,stats.rx_errors,stats.crc_errors,stats.timeouts,stats.last_length,stats.last_ms);
    out("UWB IRQ operation requires observed edges; RSSI/quality not reported; collisions cannot be distinguished from other RX errors");
}

/* Connection flag + busy counter closes the preemption window before OTA erase.
 * Each radio operation is bounded. Once BLE connects, later operations refuse. */
int radio_init(void) { if (enter()) return -EBUSY; int rc=impl_init(); atomic_dec(&busy); return rc; }
int radio_stop(void) { if (enter()) return -EBUSY; int rc=impl_stop(); atomic_dec(&busy); return rc; }
int radio_calibrate(uint16_t t,uint16_t r,uint32_t p,uint8_t g) { if (enter()) return -EBUSY; int rc=impl_calibrate(t,r,p,g); atomic_dec(&busy); return rc; }
int radio_tx(const uint8_t *d,size_t n,uint64_t *t,bool response) { if (enter()) return -EBUSY; int rc=impl_tx(d,n,t,response,false,0); atomic_dec(&busy); return rc; }
int radio_tx_delayed(const uint8_t *d,size_t n,uint64_t raw,uint64_t *t,bool response) { if (enter()) return -EBUSY; int rc=impl_tx(d,n,t,response,true,raw); atomic_dec(&busy); return rc; }
/* DW3110 SYS_TIME exposes bits 39:8 and latches until an SPI write.
 * A zero write to the W1C status register refreshes the latch without clearing
 * pending RX/TX events. Read-only polling otherwise freezes time until RX timeout.
 * DW3000 User Manual 8.2.2.7 (SYS_TIME). */
uint64_t radio_system_time(void)
{
    dwt_writesysstatuslo(0);
    return (uint64_t)dwt_readsystimestamphi32()<<8;
}
uint16_t radio_tx_antenna_delay(void) { return tx_ant; }
int radio_network_phy(bool enable)
{
    network_rx_active=false;
    if(enter())return -EBUSY;
    int rc=-ENODEV;
    if(stats.ready) {
        dwt_forcetrxoff();phy.txPreambLength=enable?DWT_PLEN_128:DWT_PLEN_64;
        phy.phrMode=enable?DWT_PHRMODE_EXT:DWT_PHRMODE_STD;phy.sfdTO=enable?129:65;
        rc=checked(dwt_configure(&phy));if(!rc)rc=apply_cal();
#ifdef SWARM_RANGING
        if(enable)dwt_configciadiag(DW_CIA_DIAG_LOG_ALL);
#endif
    }
    atomic_dec(&busy);return rc;
}
int radio_rx(uint8_t *d,size_t cap,size_t *n,uint64_t *t,uint32_t timeout,bool enabled) { if (enter()) return -EBUSY; int rc=impl_rx(d,cap,n,t,timeout,enabled,false); atomic_dec(&busy); return rc; }
int radio_network_rx(uint8_t *d,size_t cap,size_t *n,uint64_t *t)
{
    if(enter())return -EBUSY;
    int rc=impl_rx(d,cap,n,t,1,network_rx_active,true);
    if(rc!=-EAGAIN)network_rx_active=false;
    atomic_dec(&busy);return rc;
}
#ifdef REFERENCE_RANGING
int radio_reference_acquire(void) { return enter(); }
void radio_reference_release(void) { atomic_dec(&busy); }
int radio_reference_phy(void)
{
    if(enter())return -EBUSY;
    int rc=-EACCES;
    if(stats.ready && stats.calibrated) {
        dwt_forcetrxoff();network_rx_active=false;
        phy.txPreambLength=DWT_PLEN_1024;phy.rxPAC=DWT_PAC32;
        phy.dataRate=DWT_BR_850K;phy.sfdType=DWT_SFD_IEEE_4Z;
        phy.phrMode=DWT_PHRMODE_STD;phy.sfdTO=1001;
        phy.stsMode=DWT_STS_MODE_1;phy.stsLength=DWT_STS_LEN_1024;
        rc=checked(dwt_configure(&phy));
        if(!rc) {
            rc=apply_cal();
            /* Upstream predicts unadjusted TX timestamps. Keep total measured
             * OTP antenna correction, placing it on RX as upstream does. */
            if(!rc) {
                dwt_settxantennadelay(0);dwt_setrxantennadelay((uint16_t)(tx_ant+rx_ant));
                rc=checked(0);
            }
        }
    }
    atomic_dec(&busy);return rc;
}
#endif
#ifdef SWARM_RANGING
/* Nonblocking primitives: the sole application-thread owner drains RX and arms
 * delayed TX without sleeping through the adjacent 1.3 ms slots. */
static bool swarm_tx_pending;
static uint64_t swarm_expected;
static int64_t swarm_deadline;
static uint32_t swarm_prepare_us,swarm_rx_us;
uint32_t radio_swarm_rx_us(void) { return swarm_rx_us; }
void radio_swarm_setup(size_t length)
{
    dwt_writetxfctrl(length+2,0,1);
    dwt_setrxaftertxdelay(0);dwt_setrxtimeout(0);
}
uint32_t radio_swarm_prepare_us(void) { return swarm_prepare_us; }
int radio_swarm_arm(const uint8_t *data,size_t length,uint64_t raw)
{
    if(swarm_tx_pending || enter())return -EBUSY;
    uint32_t prepare_start=k_cycle_get_32();
    int rc=0;
    if(!stats.ready || !stats.calibrated || !length || length>RADIO_FRAME_MAX)rc=-EINVAL;
    if(!rc) {
        network_rx_active=false;dwt_forcetrxoff();
        dwt_writesysstatuslo(DWT_INT_TXFRS_BIT_MASK|SYS_STATUS_ALL_RX_GOOD|SYS_STATUS_ALL_RX_ERR|SYS_STATUS_ALL_RX_TO);
        rc=dwt_writetxdata(length,(uint8_t *)data,0);
        /* Length / RX timing are fixed once at mode entry. Hardware HPDWARN
         * and TXERR in dwt_starttx reject a missed deadline. Avoid redundant
         * register traffic in this sub-millisecond critical path. */
        if(!rc) {
            dwt_setdelayedtrxtime((uint32_t)(raw>>8)&0xfffffffeU);
            rc=dwt_starttx(DWT_START_TX_DELAYED|DWT_RESPONSE_EXPECTED);
        }
    }
    swarm_prepare_us=k_cyc_to_us_floor32(k_cycle_get_32()-prepare_start);
    rc=checked(rc);
    if(rc) { ++stats.tx_errors;atomic_dec(&busy);return rc; }
    swarm_expected=u3d_predicted_tx(raw,tx_ant);swarm_tx_pending=true;
    swarm_deadline=k_uptime_get()+5;return 0;
}
int radio_swarm_complete(uint64_t *actual)
{
    if(!swarm_tx_pending)return -EINVAL;
    uint32_t status=dwt_readsysstatuslo();
    if(!(status&DWT_INT_TXFRS_BIT_MASK) && k_uptime_get()<swarm_deadline && !radio_port_error())return -EAGAIN;
    int rc=-ETIMEDOUT;
    if(status&DWT_INT_TXFRS_BIT_MASK) {
        *actual=stamp(true);dwt_writesysstatuslo(DWT_INT_TXFRS_BIT_MASK);
        rc=*actual==swarm_expected?0:-ERANGE;
    }
    swarm_tx_pending=false;atomic_dec(&busy);
    if(!rc) { ++stats.tx;network_rx_active=true; }
    else { ++stats.tx_errors;dwt_forcetrxoff();network_rx_active=false; }
    return checked(rc);
}
void radio_swarm_cancel(void)
{
    if(swarm_tx_pending) { dwt_forcetrxoff();swarm_tx_pending=false;atomic_dec(&busy); }
    network_rx_active=false;
}
int radio_swarm_rx(uint8_t *data,size_t capacity,size_t *length,uint64_t *timestamp,uint8_t *quality)
{
    if(swarm_tx_pending || enter())return -EAGAIN;
    uint32_t rx_start=k_cycle_get_32();
    int rc=-EAGAIN;if(quality)*quality=255;
    if(!network_rx_active) {
        dwt_forcetrxoff();dwt_writesysstatuslo(SYS_STATUS_ALL_RX_GOOD|SYS_STATUS_ALL_RX_ERR|SYS_STATUS_ALL_RX_TO);
        rc=dwt_rxenable(DWT_START_RX_IMMEDIATE);
        network_rx_active=rc==0;rc=rc?rc:-EAGAIN;
    }
    uint32_t status=dwt_readsysstatuslo();
    if(status&DWT_INT_RXFCG_BIT_MASK) {
        uint8_t ranging;uint16_t size=dwt_getframelength(&ranging);
        if(size<2 || size-2>capacity)rc=-EMSGSIZE;
        else {
            *length=size-2;*timestamp=stamp(false);dwt_readrxdata(data,*length,0);
            if(quality) {
            dwt_rxdiag_t diagnostics;dwt_readdiagnostics(&diagnostics);
            /* DW3110: RX=10log10(C*2^21/N^2)-A+6DGC,
             * FP=10log10((F1^2+F2^2+F3^2)/N^2)-A+6DGC.
             * F amplitudes have two fractional bits. In RX-FP, N, A and
             * DGC cancel. Compute the ratio without four extra SPI reads
             * or intermediate q8.8 saturation; absent diagnostics stay 255. */
            float f1=diagnostics.ipatovF1*.25f,f2=diagnostics.ipatovF2*.25f,f3=diagnostics.ipatovF3*.25f;
            float first=f1*f1+f2*f2+f3*f3;
            if(diagnostics.ipatovAccumCount && diagnostics.ipatovPower && first>0.f) {
                float db=10.f*log10f(((float)diagnostics.ipatovPower*2097152.f)/first);
                if(isfinite(db)) { int gap=(int)(db+.5f);*quality=gap<0?0:gap>254?254:gap; }
            }
            }
            ++stats.rx;rc=0;
        }
        dwt_writesysstatuslo(SYS_STATUS_ALL_RX_GOOD);
        network_rx_active=dwt_rxenable(DWT_START_RX_IMMEDIATE)==DWT_SUCCESS;
    } else if(status&(SYS_STATUS_ALL_RX_ERR|SYS_STATUS_ALL_RX_TO)) {
        ++stats.rx_errors;dwt_writesysstatuslo(SYS_STATUS_ALL_RX_ERR|SYS_STATUS_ALL_RX_TO);network_rx_active=false;rc=-EBADMSG;
    }
    if(!network_rx_active) {
        dwt_forcetrxoff();network_rx_active=dwt_rxenable(DWT_START_RX_IMMEDIATE)==DWT_SUCCESS;
    }
    if(!rc)swarm_rx_us=k_cyc_to_us_floor32(k_cycle_get_32()-rx_start);
    atomic_dec(&busy);return checked(rc);
}
#endif
