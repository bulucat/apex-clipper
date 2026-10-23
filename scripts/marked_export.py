"""Explicit user-marked excerpt export; never represents verified A highlights."""
from datetime import datetime
import json
import os
from pathlib import Path
from uuid import uuid4

from common import binary, identity, media_check, probe, run, save_json
from marker_scope import load_sidecar, check_duration


def export_marked(source, output=None, markers=None):
    source=Path(source).resolve();source_id=identity(source);media=probe(source)
    sidecar=Path(markers) if markers else Path(str(source)+'.apex-markers.json')
    if not sidecar.is_file():raise ValueError('No marker file. Select the original recording and its marker sidecar.')
    scope=load_sidecar(sidecar,source_id);check_duration(scope,media)
    root=Path(output).resolve() if output else (Path('exports/marked')/source.stem/(datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid4().hex[:8])).resolve()
    if root.exists() and any(root.iterdir()):raise ValueError('Choose a new empty output directory; existing exports are preserved.')
    if root==source.parent:raise ValueError('Use a separate output directory; original recording is preserved.')
    root.mkdir(parents=True,exist_ok=True)
    products=[];temporary=[]
    try:
        for n,(a,b) in enumerate(scope['ranges'],1):
            a=max(0,a);b=min(media['duration'],b)
            target=root/('marked_'+str(n).zfill(3)+'.mp4');partial=target.with_suffix('.partial.mp4')
            temporary.append(partial)
            run([binary('ffmpeg'),'-y','-v','error','-ss',str(a),'-i',str(source),'-t',str(b-a),
                 '-map','0:v:0','-map','0:a:0?','-c:v','libx264','-preset','fast','-crf','18',
                 '-c:a','aac','-movflags','+faststart',str(partial)])
            media_check(partial,media,b-a)
            products.append({'start':a,'end':b,'path':str(target),'partial':str(partial)})
        if identity(source)!=source_id:raise ValueError('Source changed during export; outputs were not published.')
        for product in products:
            os.replace(product.pop('partial'),product['path']);product['identity']=identity(product['path'])
        result={'format':'apex-marked-export-v1','source':source_id,'marker_scope':scope,'outputs':products,
                'reviewed_for_knockdown':False,'damage_threshold_applied':False,
                'note':'Explicit marked excerpts only. No AI/HUD/event/damage review; these are not verified A highlights.'}
        save_json(root/'marked-export.json',result)
        return {'stage':'marked_export_complete','workdir':str(root),**result}
    finally:
        for partial in temporary:partial.unlink(missing_ok=True)


def add_commands(sub):
    p=sub.add_parser('marked-export',help='Explicitly export marked excerpts without claiming verified kill highlights')
    p.add_argument('--source',required=True);p.add_argument('--output');p.add_argument('--markers');p.add_argument('--config')
