"""Deterministic opaque-pixel extrusion; source colours and holes are retained."""
import io
import math


def extrude_rgba(payload, *, depth, axis='z', size=None, alpha_cutoff=1):
    from PIL import Image
    if axis not in ('x','y','z') or not isinstance(depth,(int,float)) or not math.isfinite(depth) or depth <= 0:
        raise ValueError('Extrusion requires a positive finite depth and x/y/z axis')
    if type(alpha_cutoff) is not int or not 1 <= alpha_cutoff <= 255:
        raise ValueError('Extrusion alpha cutoff must be 1..255')
    with Image.open(io.BytesIO(payload)) as source:
        if source.width * source.height > 16384:
            raise ValueError('Extrusion exceeds 16384 pixels; supply an explicit cropped/resized source asset')
        image=source.convert('RGBA')
    w,h=image.size
    size=size or [w/h,1]
    if len(size)!=2 or any(not isinstance(v,(int,float)) or not math.isfinite(v) or v<=0 for v in size):
        raise ValueError('Extrusion size requires two positive finite values')
    pixels=image.load()
    opaque={(x,y) for y in range(h) for x in range(w) if pixels[x,y][3]>=alpha_cutoff}
    vertices, triangles, colors=[],[],[]
    def point(x,y,z):
        return (x,y,z) if axis=='z' else ((z,y,x) if axis=='x' else (x,z,y))
    def quad(points, rgba):
        offset=len(vertices)
        vertices.extend(point(*p) for p in points)
        triangles.extend([(offset,offset+1,offset+2),(offset,offset+2,offset+3)])
        colors.extend([tuple(v/255 for v in rgba)]*4)
    for x,y in sorted(opaque):
        left,right=(x/w-.5)*size[0],((x+1)/w-.5)*size[0]
        top,bottom=(.5-y/h)*size[1],(.5-(y+1)/h)*size[1]
        front,back=-depth/2,depth/2
        rgba=pixels[x,y]
        quad([(left,bottom,front),(right,bottom,front),(right,top,front),(left,top,front)],rgba)
        quad([(right,bottom,back),(left,bottom,back),(left,top,back),(right,top,back)],rgba)
        if (x-1,y) not in opaque: quad([(left,bottom,back),(left,bottom,front),(left,top,front),(left,top,back)],rgba)
        if (x+1,y) not in opaque: quad([(right,bottom,front),(right,bottom,back),(right,top,back),(right,top,front)],rgba)
        if (x,y-1) not in opaque: quad([(left,top,front),(right,top,front),(right,top,back),(left,top,back)],rgba)
        if (x,y+1) not in opaque: quad([(left,bottom,back),(right,bottom,back),(right,bottom,front),(left,bottom,front)],rgba)
    if not vertices:
        raise ValueError('Extrusion source contains no visible pixels')
    return {'vertices':vertices,'triangles':triangles,'colors':colors,'opaque_pixels':len(opaque)}
