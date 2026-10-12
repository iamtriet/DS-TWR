#include "validation_math.h"
#include <math.h>
bool quaternion_rpy(float w,float x,float y,float z,float rpy[3])
{
    float n=sqrtf(w*w+x*x+y*y+z*z);
    if (!isfinite(n) || n<0.5f || n>1.5f || !rpy) return false;
    w/=n; x/=n; y/=n; z/=n;
    float p=2.0f*(w*y-z*x);
    if (p>1) p=1;
    if (p< -1) p=-1;
    const float degrees=57.295779513f;
    rpy[0]=atan2f(2*(w*x+y*z),1-2*(x*x+y*y))*degrees;
    rpy[1]=asinf(p)*degrees;
    rpy[2]=atan2f(2*(w*z+x*y),1-2*(y*y+z*z))*degrees;
    return true;
}
static double delta(uint64_t a,uint64_t b) { return (double)((a-b)&UINT64_C(0xffffffffff)); }
bool ds_distance(uint64_t pt,uint64_t pr,uint64_t rt,uint64_t rr,uint64_t ft,uint64_t fr,double *meters)
{
    double ra=delta(rr,pt), rb=delta(fr,rt), da=delta(ft,rr), db=delta(rt,pr);
    /* Each interval < 0.5s; rejects stale frames and reverse timestamps. */
    const double limit=31948800000.0;
    if (!meters || ra<=0 || rb<=0 || da<=0 || db<=0 || ra>limit || rb>limit || da>limit || db>limit) return false;
    double tof=(ra*rb-da*db)/(ra+rb+da+db);
    double d=tof*(299702547.0/(499.2e6*128.0));
    if (!isfinite(d) || d<0 || d>1000.0) return false;
    *meters=d; return true;
}
