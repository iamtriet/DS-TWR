#include <stdint.h>
typedef struct { uint32_t DEVICEID[2]; } mock_ficr_t;
extern mock_ficr_t mock_ficr;
#define NRF_FICR (&mock_ficr)
