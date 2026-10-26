"""Short robust fits in range space; timestamps and uncertainty stay explicit."""
import numpy as np


def range_window(history,epoch,window=.20):
    samples=[(t,d) for t,d in history if epoch-window<=t<=epoch+.025]
    if len(samples)<5:return None
    values=np.array(samples,float);t=values[:,0]-epoch;y=values[:,1]
    if np.ptp(t)<.06:return None
    design=np.c_[np.ones(len(t)),t]
    coefficients=np.array([np.median(y),0.])
    weights=np.ones(len(t))
    for _ in range(4):
        residual=y-design@coefficients
        scale=max(.015,1.4826*np.median(np.abs(residual-np.median(residual))))
        weights=np.minimum(1.,1.5*scale/np.maximum(np.abs(residual),1e-9))
        coefficients=np.linalg.lstsq(design*np.sqrt(weights[:,None]),y*np.sqrt(weights),rcond=None)[0]
        # At most 1 m/s radial speed in the current physical motion profile.
        coefficients[1]=np.clip(coefficients[1],-1.,1.)
        coefficients[0]=np.sum(weights*(y-coefficients[1]*t))/weights.sum()
    residual=y-design@coefficients
    scatter=max(.02,1.4826*np.median(np.abs(residual-np.median(residual))))
    # Overlapping DS-TWR exchanges are correlated: do not claim sqrt(N)
    # precision from a short window. Retain the observed range scatter.
    return dict(distance=float(coefficients[0]),sigma=float(scatter),
                radial_speed=float(coefficients[1]),samples=len(y),
                newest=float(values[-1,0]))
