#include "uwb3d_wire.h"
#include <assert.h>
#include <stdio.h>
int main(void)
{
    uint8_t out[80];size_t n=u3d_range(1,2,1.234,7,100,5,out,sizeof(out));
    assert(n>20 && out[0]==0 && out[n-1]==0);
    assert(u3d_range(0,2,1,0,0,0,out,sizeof(out))==0);
    assert(u3d_range(1,2,1,0,0,0,out,10)==0);
    assert(u3d_predicted_tx(0x123456789aULL,100)==((0x1234567800ULL+100)&0xffffffffffULL));
    double distance;
    assert(!u3d_pipeline_distance(1,2,2,63,1,2,3,4,5,6,&distance));
    assert(!u3d_pipeline_distance(1,1,2,31,1,2,3,4,5,6,&distance));
    for(size_t i=0;i<n;++i)printf("%02x",out[i]);
    puts("");return 0;
}
