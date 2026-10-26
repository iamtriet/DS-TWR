#include "uwb_swarm.h"
#ifdef SWARM_RANGING
#include <zephyr.h>
#include <irq.h>
#include <hal/nrf_timer.h>
/* TIMER2 is reserved for this application; TIMER0/RTC0 belong to BLE,
 * RTC1 remains Zephyr's system clock. No SPI in the interrupt handler. */
K_SEM_DEFINE(swarm_event,0,1);
static bool timer_ready;
static void timer_isr(const void *unused)
{
    ARG_UNUSED(unused);NRF_TIMER2->EVENTS_COMPARE[0]=0;
    NRF_TIMER2->INTENCLR=TIMER_INTENCLR_COMPARE0_Msk;k_sem_give(&swarm_event);
}
void swarm_wake(void) { k_sem_give(&swarm_event); }
void swarm_timer_init(void)
{
    if(timer_ready)return;
    NRF_TIMER2->TASKS_STOP=1;NRF_TIMER2->MODE=TIMER_MODE_MODE_Timer;
    NRF_TIMER2->BITMODE=TIMER_BITMODE_BITMODE_32Bit;NRF_TIMER2->PRESCALER=4;
    NRF_TIMER2->TASKS_CLEAR=1;NRF_TIMER2->EVENTS_COMPARE[0]=0;
    IRQ_CONNECT(TIMER2_IRQn,3,timer_isr,NULL,0);irq_enable(TIMER2_IRQn);
    NRF_TIMER2->TASKS_START=1;timer_ready=true;
}
void swarm_timer_arm(uint32_t us)
{
    if(!timer_ready)return;
    NRF_TIMER2->TASKS_CAPTURE[1]=1;
    NRF_TIMER2->INTENCLR=TIMER_INTENCLR_COMPARE0_Msk;
    NRF_TIMER2->CC[0]=NRF_TIMER2->CC[1]+MAX(us,10U);
    NRF_TIMER2->EVENTS_COMPARE[0]=0;NRF_TIMER2->INTENSET=TIMER_INTENSET_COMPARE0_Msk;
}
void swarm_wait(void) { k_sem_take(&swarm_event,K_USEC(100)); }
#else
void swarm_wake(void) {}
void swarm_timer_init(void) {}
void swarm_timer_arm(unsigned int us) { (void)us; }
void swarm_wait(void) {}
#endif
