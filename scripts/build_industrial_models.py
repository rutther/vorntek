"""Build original, metre-scale industrial GLBs from the content dimensions.

No borrowed models or network inputs. Rectangular manufactured parts, glazing,
fixings and equipment are modelled explicitly; catalogue views use these files.
"""
import json
import math
from pathlib import Path
import struct

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'docs/vorntekDemo/assets/models'


class Model:
    def __init__(self):
        self.parts=[]
        self.materials=[
            {'name':'Midnight powder coat','pbrMetallicRoughness':{'baseColorFactor':[.035,.105,.16,1],'metallicFactor':.25,'roughnessFactor':.42}},
            {'name':'Brushed aluminium','pbrMetallicRoughness':{'baseColorFactor':[.65,.68,.71,1],'metallicFactor':.75,'roughnessFactor':.36}},
            {'name':'Safety orange detail','pbrMetallicRoughness':{'baseColorFactor':[.91,.30,.055,1],'metallicFactor':.05,'roughnessFactor':.5}},
            {'name':'Clear glazing','pbrMetallicRoughness':{'baseColorFactor':[.72,.86,.89,.17],'metallicFactor':.1,'roughnessFactor':.12},'alphaMode':'BLEND','doubleSided':True},
            {'name':'Blue equipment panel','pbrMetallicRoughness':{'baseColorFactor':[.045,.23,.57,1],'metallicFactor':.2,'roughnessFactor':.44}},
            {'name':'Brushed metal','pbrMetallicRoughness':{'baseColorFactor':[.6,.62,.64,1],'metallicFactor':.85,'roughnessFactor':.3}},
            {'name':'Warm lighting','pbrMetallicRoughness':{'baseColorFactor':[1,.94,.78,1],'roughnessFactor':.4},'emissiveFactor':[.7,.62,.44]}
        ]

    def box(self,name,center,size,material=0,angle=0):
        # Independent face vertices keep manufactured edges crisp.
        faces=[((1,0,0),[(1,-1,-1),(1,1,-1),(1,1,1),(1,-1,1)]),
               ((-1,0,0),[(-1,-1,1),(-1,1,1),(-1,1,-1),(-1,-1,-1)]),
               ((0,1,0),[(-1,1,-1),(-1,1,1),(1,1,1),(1,1,-1)]),
               ((0,-1,0),[(-1,-1,1),(-1,-1,-1),(1,-1,-1),(1,-1,1)]),
               ((0,0,1),[(-1,-1,1),(1,-1,1),(1,1,1),(-1,1,1)]),
               ((0,0,-1),[(1,-1,-1),(-1,-1,-1),(-1,1,-1),(1,1,-1)])]
        positions=[];normals=[];indices=[]
        for n,vs in faces:
            offset=len(positions)//3
            for v in vs:positions.extend(v);normals.extend(n)
            indices.extend(offset+i for i in (0,1,2,0,2,3))
        self.parts.append((name,center,[s/2 for s in size],material,angle,positions,normals,indices))

    def export(self,path):
        data=bytearray();views=[];accessors=[];meshes=[];nodes=[]
        def accessor(values,kind,component,fmt):
            while len(data)%4:data.append(0)
            start=len(data);data.extend(struct.pack('<'+fmt*len(values),*values))
            views.append({'buffer':0,'byteOffset':start,'byteLength':len(data)-start})
            width={'VEC3':3,'SCALAR':1}[kind]
            row={'bufferView':len(views)-1,'componentType':component,'count':len(values)//width,'type':kind}
            if kind=='VEC3':row.update(min=[min(values[i::3]) for i in range(3)],max=[max(values[i::3]) for i in range(3)])
            accessors.append(row);return len(accessors)-1
        for name,center,size,mat,angle,pos,norm,idx in self.parts:
            p=accessor(pos,'VEC3',5126,'f');n=accessor(norm,'VEC3',5126,'f');i=accessor(idx,'SCALAR',5123,'H')
            meshes.append({'name':name,'primitives':[{'attributes':{'POSITION':p,'NORMAL':n},'indices':i,'material':mat}]})
            nodes.append({'name':name,'mesh':len(meshes)-1,'translation':center,'scale':size,
                          'rotation':[0,math.sin(angle/2),0,math.cos(angle/2)]})
        doc={'asset':{'version':'2.0','generator':'Vorntek original industrial concept equipment'},'scene':0,
             'scenes':[{'nodes':list(range(len(nodes)))}],'nodes':nodes,'meshes':meshes,'materials':self.materials,
             'buffers':[{'byteLength':len(data)}],'bufferViews':views,'accessors':accessors}
        encoded=json.dumps(doc,separators=(',',':')).encode();encoded+=b' '*((-len(encoded))%4)
        data+=b'\0'*((-len(data))%4)
        result=struct.pack('<4sII',b'glTF',2,28+len(encoded)+len(data))+struct.pack('<I4s',len(encoded),b'JSON')+encoded+struct.pack('<I4s',len(data),b'BIN\0')+data
        path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(result)


def make(key):
    m=Model()
    dims={'inspect':(1,1.8,.8),'measure':(1.2,1.5,.8),'assemble':(1.6,2,.8),
          'convey':(2,.95,.6),'mark':(.9,1.7,.75),'connect':(.6,1.2,.4)}
    if key not in dims:raise ValueError('No original geometry for '+key)
    w,h,d=dims[key]
    def legs(top=.86):
        for x in (-w/2+.045,w/2-.045):
            for z in (-d/2+.045,d/2-.045):
                m.box('steel support',(x,top/2,z),(.055,top,.055),0)
                m.box('levelling foot',(x,.025,z),(.085,.05,.085),5)
        for z in (-d/2+.045,d/2-.045):m.box('base cross rail',(0,.2,z),(w-.07,.045,.045),0)
    if key in ('inspect','mark'):
        m.box('lower control cabinet',(0,.43,0),(w-.1,.76,d-.1),4)
        for x in (-w*.24,w*.24):
            m.box('cabinet door',(x,.45,d/2-.035),(w*.46,.68,.025),4)
            m.box('door handle',(x+w*.17,.47,d/2-.012),(.025,.13,.02),0)
        legs(.88)
        m.box('working surface',(0,.88,0),(w,.04,d),1)
        for x in (-w/2+.025,w/2-.025):
            for z in (-d/2+.025,d/2-.025):m.box('cell extrusion',(x,(h+.9)/2,z),(.05,h-.9,.05),0)
        for z in (-d/2+.025,d/2-.025):m.box('top cross extrusion',(0,h-.025,z),(w,.05,.05),0)
        for x in (-w/2+.025,w/2-.025):
            m.box('side guard',(x,(h+.9)/2,0),(.012,h-.99,d-.09),3)
            m.box('top side extrusion',(x,h-.025,0),(.05,.05,d),0)
        m.box('rear guard',(0,(h+.9)/2,-d/2+.025),(w-.08,h-.99,.012),3)
        m.box('front guard',(0,(h+.9)/2,d/2-.025),(w-.08,h-.99,.012),3)
        m.box('guard handle',(w*.35,1.19,d/2-.013),(.025,.16,.025),5)
        m.box('fixture plate',(0,.925,0),(w*.58,.045,d*.58),1)
        for x in (-.12,.12):m.box('fixture locator',(x,.965,.05),(.025,.035,.025),5)
        m.box('sample component',(0,.975,0),(.18,.045,.1),0)
        m.box('camera bridge',(0,h-.15,-.12),(w*.77,.055,.055),1)
        m.box('camera support',(0,h-.32,-.12),(.035,.3,.035),1)
        m.box('inspection device',(0,h-.49,-.12),(.1,.14,.11),0)
        m.box('lens',(0,h-.58,-.12),(.055,.05,.055),5)
        m.box('inspection light',(0,h-.66,-.12),(.24,.025,.2),6)
        m.box('operator panel bracket',(w/2-.1,1.05,.12),(.035,.12,.2),0)
        m.box('operator panel',(w/2-.065,1.12,.15),(.03,.18,.22),0)
        m.box('emergency stop',(w*.37,.84,d/2-.03),(.05,.05,.035),2)
    if key=='measure':
        legs(.84);m.box('reference table',(0,.84,0),(w,.08,d),0)
        m.box('reference plate',(0,.89,0),(.9,.02,.6),5)
        m.box('instrument column',(0,1.2,-.22),(.08,.6,.08),1)
        m.box('instrument arm',(.13,1.43,-.1),(.34,.07,.07),0)
        m.box('probe housing',(.28,1.32,-.1),(.045,.18,.045),1)
        m.box('probe tip',(.28,1.19,-.1),(.012,.08,.012),5)
        m.box('sample fixture',(0,.96,0),(.26,.06,.18),4)
        m.box('display housing',(-.35,1.2,-.1),(.21,.18,.04),0)
        m.box('display',(-.35,1.2,-.075),(.18,.14,.008),4)
    if key=='assemble':
        legs(.86);m.box('worktop',(0,.88,0),(w,.04,d),1)
        m.box('lower shelf',(0,.24,0),(w-.1,.035,d-.08),4)
        for x in (-w/2+.025,w/2-.025):m.box('upright tool support',(x,1.45,-d/2+.025),(.05,1.1,.05),0)
        m.box('tool panel',(0,1.42,-d/2+.05),(w-.1,.72,.025),4)
        for i in range(8):
            x=-w*.35+i*w*.1
            for y in (1.18,1.32,1.46,1.6):m.box('mounting hole',(x,y,-d/2+.066),(.012,.012,.002),0)
        m.box('overhead rail',(0,1.975,-d*.2),(w,.05,.05),0)
        m.box('task light',(0,1.92,-d*.2),(w*.66,.025,.08),6)
        for i in range(3):
            x=-.45+i*.3
            m.box('component bin',(x,.98,-.22),(.23,.16,.16),4)
        m.box('assembly fixture',(.25,.93,.12),(.35,.06,.24),0)
    if key=='convey':
        legs(.8)
        for z in (-d/2+.025,d/2-.025):
            m.box('conveyor side frame',(0,.82,z),(w,.12,.05),4)
            m.box('guide rail',(0,.9375,z),(w,.025,.025),1)
        # Cylindrical rollers assembled from narrow radial strips.
        for i in range(20):
            x=-.94+i*.099
            for a in range(12):
                theta=a*math.tau/12
                m.box('roller segment',(x+math.sin(theta)*.023,.87+math.cos(theta)*.023,0),(.018,.013,d-.08),1)
    if key=='connect':
        m.box('cabinet body',(0,h/2,-.04),(w,h,d-.08),4)
        m.box('front door',(0,h/2,d/2-.04),(w-.035,h-.035,.025),1)
        m.box('display housing',(0,.92,d/2-.02),(.38,.24,.03),0)
        m.box('operator screen',(0,.92,d/2-.004),(.32,.18,.008),4)
        m.box('door latch',(w*.37,.5,d/2-.02),(.025,.12,.025),0)
        for y in (.25,.35,.45):
            for i in range(8):m.box('ventilation slot',(-.18+i*.05,y,d/2-.025),(.03,.008,.002),0)
        for x in (-.2,.2):
            for z in (-.13,.13):m.box('cabinet foot',(x,.025,z),(.065,.05,.065),0)
    return m


def main():
    catalogue=json.loads((ROOT/'docs/vorntekDemo/catalog.json').read_text(encoding='utf-8'))
    for row in catalogue['productLines']:
        model=make(row['id']);model.export(OUT/row['model'])
        print(row['id'],len(model.parts),'manufactured parts')


if __name__=='__main__':main()
