"""Regenerate the original SOG test fixture (optional development tool: Pillow).

No customer/third-party scene. Format specification:
https://developer.playcanvas.com/user-manual/gaussian-splatting/formats/sog/
The committed fixture keeps application/native tests independent of Pillow.
"""
import io
import json
import math
import zipfile
from pathlib import Path

from PIL import Image


def synthetic_sog():
    points = []
    for row in range(16):
        for col in range(16):
            latitude = (row+.5)/16*math.pi
            longitude = col/16*math.pi*2
            points.append((math.sin(latitude)*math.cos(longitude), math.cos(latitude),
                           math.sin(latitude)*math.sin(longitude)*.65))
    limit = math.log(2)
    quantized = [tuple(round((math.copysign(math.log1p(abs(v)),v)+limit)/(2*limit)*65535)
                       for v in point) for point in points]
    textures = {
        'means_l.webp': [tuple(v & 255 for v in point)+(255,) for point in quantized],
        'means_u.webp': [tuple(v >> 8 for v in point)+(255,) for point in quantized],
        'scales.webp': [(0,0,0,255)]*256,
        'quats.webp': [(128,128,128,252)]*256,
        'sh0.webp': [(round((x+1)*127.5),round((y+1)*127.5),210,245) for x,y,z in points],
    }
    metadata = {'version':2,'asset':{'generator':'Vorntek synthetic test fixture'},'count':256,
        'means':{'mins':[-limit]*3,'maxs':[limit]*3,'files':['means_l.webp','means_u.webp']},
        'scales':{'codebook':[-2.7]*256,'files':['scales.webp']},
        'quats':{'files':['quats.webp']},
        'sh0':{'codebook':[(i/255-.5)/.28209479177387814 for i in range(256)],'files':['sh0.webp']}}
    result = io.BytesIO()
    with zipfile.ZipFile(result,'w',compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr(zipfile.ZipInfo('meta.json'),json.dumps(metadata))
        for name,pixels in textures.items():
            image = Image.new('RGBA',(16,16)); image.putdata(pixels)
            data = io.BytesIO(); image.save(data,format='WEBP',lossless=True,exact=True)
            bundle.writestr(zipfile.ZipInfo(name),data.getvalue())
    return result.getvalue()


if __name__ == '__main__':
    destination = Path(__file__).resolve().parents[1]/'apps/crm/console/data/test-fixtures/synthetic-scene.sog'
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(synthetic_sog())
    print(destination)
