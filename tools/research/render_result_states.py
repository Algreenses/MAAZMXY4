"""Render complete result screens from a verified SWF without executing it.

Freeze Flash display lists at their settled frames, select the seven character
frames used by GameOverModule and render through FFDec's ordinary frame export.
This is an offline fixture tool, never a client/runtime dependency.
"""
import argparse
from copy import deepcopy
import hashlib
from itertools import product
import json
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET


def freeze(sprite, frame):
    display = {}
    at = 1
    for tag in sprite.find('subTags'):
        kind, depth = tag.get('type'), tag.get('depth')
        if kind == 'ShowFrameTag':
            if at == frame: break
            at += 1
        elif kind == 'RemoveObject2Tag':
            display.pop(depth, None)
        elif kind in ('PlaceObject2Tag', 'PlaceObject3Tag'):
            if tag.get('placeFlagMove') != 'true' or depth not in display:
                display[depth] = deepcopy(tag)
            else:
                item = display[depth]
                for key, value in tag.attrib.items():
                    if key.startswith('placeFlagHas') and value == 'false': continue
                    if key not in ('placeFlagMove', 'forceWriteAsLong'): item.set(key, value)
                for child in tag:
                    previous = item.find(child.tag)
                    if previous is not None: item.remove(previous)
                    item.append(deepcopy(child))
            display[depth].set('placeFlagMove', 'false')
    tags = sprite.find('subTags')
    tags[:] = [display[k] for k in sorted(display, key=int)]
    ET.SubElement(tags, 'item', type='ShowFrameTag', forceWriteAsLong='false')
    sprite.set('frameCount', '1')


def render(source, output, jar, *, roles=range(1, 8)):
    origin = next(s for s in json.loads((source/'manifest.json').read_text(encoding='utf8')) if s['name']=='gameOver')
    if hashlib.sha256((source/origin['file']).read_bytes()).hexdigest() != origin['sha256']:
        raise ValueError('Result SWF source changed')
    output.mkdir(parents=True, exist_ok=True)
    command = ['java', '-Djava.awt.headless=true', '-XX:ActiveProcessorCount=2', '-Xmx768m', '-jar', str(jar)]
    records = []
    with (output/'render.log').open('w', encoding='utf8') as log:
        for outcome, root_id in [('succeeded',205), ('failed',206)]:
            for role, state in product(roles, ('up','over','down')):
                tree = ET.parse(source/'gameOver.xml')
                root = tree.getroot()
                for item in root.iter():
                    if item.get('type') == 'DefineSpriteTag':
                        count = int(item.get('frameCount'))
                        selected = role if item.get('spriteId') in ('22','84') else count
                        freeze(item, selected)
                    elif item.get('buttonId') == '95':
                        button_records = item.find('characters')
                        selected = [r for r in button_records if r.get('buttonState'+state.capitalize())=='true']
                        button_records[:] = sorted(selected,key=lambda r:int(r.get('placeDepth')))
                        for record in button_records:record.set('buttonStateUp','true')
                tags = root.find('tags')
                tags[:] = [t for t in tags if t.get('type') not in
                           ('ShowFrameTag','PlaceObject2Tag','PlaceObject3Tag','RemoveObject2Tag')]
                placement = ET.SubElement(tags,'item',type='PlaceObject2Tag',characterId=str(root_id),depth='1',
                    forceWriteAsLong='false',placeFlagHasCharacter='true',placeFlagHasMatrix='true',
                    placeFlagMove='false',placeFlagHasColorTransform='false',placeFlagHasName='false',
                    placeFlagHasRatio='false',placeFlagHasClipDepth='false',placeFlagHasClipActions='false')
                ET.SubElement(placement,'matrix',type='MATRIX',hasRotate='false',hasScale='false',
                    nRotateBits='0',nScaleBits='0',nTranslateBits='0',translateX='0',translateY='0')
                ET.SubElement(tags,'item',type='ShowFrameTag',forceWriteAsLong='false')
                name = f'{outcome}-role{role}-{state}'
                xml, swf = output/(name+'.xml'), output/(name+'.swf')
                tree.write(xml,encoding='utf8',xml_declaration=True)
                subprocess.run(command+['-xml2swf',str(xml),str(swf)],stdout=log,stderr=log,check=True,timeout=90)
                subprocess.run(command+['-export','frame',str(output/name),str(swf)],stdout=log,stderr=log,check=True,timeout=90)
                images = list((output/name).rglob('*.png'))
                if len(images)!=1: raise ValueError('Expected one settled result frame')
                records.append(dict(outcome=outcome,role_frame=role,button_state=state,file=images[0].relative_to(output).as_posix(),
                                    sha256=hashlib.sha256(images[0].read_bytes()).hexdigest()))
                print(name,flush=True)
    (output/'manifest.json').write_text(json.dumps(dict(source=origin,cases=records),ensure_ascii=False,indent=2)+'\n',encoding='utf8',newline='\n')


def fixtures(rendered, destination):
    from PIL import Image
    data=json.loads((rendered/'manifest.json').read_text(encoding='utf8'))
    destination.mkdir(parents=True,exist_ok=True)
    for case in data['cases']:
        source=rendered/case['file']
        if hashlib.sha256(source.read_bytes()).hexdigest()!=case['sha256']:
            raise ValueError('Rendered frame checksum changed')
        name=f"{case['outcome']}-role{case['role_frame']}-{case.get('button_state','up')}.png"
        with Image.open(source) as im:
            im.convert('RGB').resize((1147,720),Image.Resampling.BICUBIC).save(destination/name)
        case['source_frame_sha256']=case.pop('sha256')
        case['file']=name
        case['sha256']=hashlib.sha256((destination/name).read_bytes()).hexdigest()
    data['method']='FFDec frozen complete display lists; seven character frames; independent BICUBIC 720p scaling. Offline renders, not live screenshots.'
    (destination/'cases.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf8',newline='\n')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path)
    parser.add_argument('output',type=Path)
    parser.add_argument('--ffdec-jar',type=Path,required=True)
    parser.add_argument('--role',type=int,action='append')
    parser.add_argument('--fixtures',type=Path)
    args=parser.parse_args()
    render(args.source,args.output,args.ffdec_jar,roles=args.role or range(1,8))
    if args.fixtures:fixtures(args.output,args.fixtures)
