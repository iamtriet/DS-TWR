"""Presentation-only region overlays; never changes inference cells."""
import math
from PIL import Image, ImageDraw, ImageFilter, ImageChops

STYLES = {
    'FRONTIER': ('#8296aa', 24, 'Unexplored frontier'),
    'CAUTION': ('#ed9d7a', 30, 'Caution buffer'),
    'OBSERVED PASSAGE': ('#45c6a0', 66, 'Observed passage'),
    'QUIET / REST ZONE': ('#69aaf7', 68, 'Stationary'),
    'STATIONARY ZONE': ('#69aaf7', 68, 'Stationary'),
    'LOW PROGRESS ZONE': ('#ba8df2', 74, 'Low progress'),
    'TURNAROUND EVIDENCE': ('#f4be68', 65, 'Turn evidence'),
    'SUSPECTED OBSTRUCTION': ('#ff7588', 85, 'Suspected obstacle'),
}


def components(cells):
    remaining=set(cells)
    while remaining:
        seed=min(remaining);remaining.remove(seed);group={seed};todo=[seed]
        while todo:
            x,y=todo.pop()
            for q in ((x-1,y),(x+1,y),(x,y-1),(x,y+1)):
                if q in remaining:
                    remaining.remove(q);group.add(q);todo.append(q)
        yield group


def rounded_mask(size, rectangles, radius):
    mask=Image.new('L',size);draw=ImageDraw.Draw(mask)
    for box in rectangles:draw.rectangle(box,fill=255)
    # Round inward only. Clipping preserves unknown holes and separate islands.
    soft=mask.filter(ImageFilter.GaussianBlur(radius))
    soft=soft.point(lambda p:max(0,min(255,(p-110)*8)))
    return ImageChops.multiply(mask,soft)


def render_regions(width,height,scale,cell,layers):
    """Scan-style display of observed footprints, not a physical lidar scan."""
    result=Image.new('RGBA',(width,height))
    known=set()
    for state in ('OBSERVED PASSAGE','STATIONARY ZONE','QUIET / REST ZONE','LOW PROGRESS ZONE'):
        known.update(layers.get(state,()))
    points=layers.get('_OBSERVED_POINTS',())
    mask=Image.new('L',result.size);draw=ImageDraw.Draw(mask)
    # Overlapping round sample footprints form the observed swath. Unknown space
    # and predicted frontiers are never filled as discovered space.
    covered=set()
    radius=max(2.,cell*scale*.42)
    for px,py in points:
        k=(math.floor(px/cell),math.floor(py/cell))
        if k not in known:continue
        x=width/2+px*scale;y=height/2-py*scale
        draw.ellipse((x-radius,y-radius,x+radius,y+radius),fill=255);covered.add(k)
    for ix,iy in known-covered:
        x=width/2+(ix+.5)*cell*scale;y=height/2-(iy+.5)*cell*scale
        draw.ellipse((x-radius,y-radius,x+radius,y+radius),fill=255)
    # A small blur antialiases the footprint edge; it does not fill unknown gaps.
    mask=mask.filter(ImageFilter.GaussianBlur(.6))
    white=Image.new('RGBA',result.size,'white');white.putalpha(mask);result.alpha_composite(white)
    border=ImageChops.subtract(mask,mask.filter(ImageFilter.MinFilter(5)))
    black=Image.new('RGBA',result.size,'#101318');black.putalpha(border);result.alpha_composite(black)
    for state,color in (('TURNAROUND EVIDENCE','#ffbf45'),('SUSPECTED OBSTRUCTION','#ff334f')):
        obstacles=Image.new('L',result.size);draw=ImageDraw.Draw(obstacles)
        for ix,iy in layers.get(state,()):
            x=width/2+(ix+.5)*cell*scale;y=height/2-(iy+.5)*cell*scale
            draw.ellipse((x-radius,y-radius,x+radius,y+radius),fill=255)
        outline=ImageChops.subtract(obstacles,obstacles.filter(ImageFilter.MinFilter(7)))
        stroke=Image.new('RGBA',result.size,color);stroke.putalpha(outline);result.alpha_composite(stroke)
    return result,[]
