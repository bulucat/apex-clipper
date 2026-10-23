"""Write-ahead metadata rollback for interrupted visual review commits."""
import base64
import hashlib
import json
import os
from pathlib import Path

from common import save_json

FORMAT='apex-review-transaction-v1'
ROOT_METADATA=('job.json','scan.json','hud_state.json','match_registry.json','confirmed_events.json',
    'ammo_tasks.json','final.json','effective-config.json','marker-config.json','quality_report.json',
    'assist_pending.json','rejected.json','review_pending.json','damage_exclusions.json')


def _digest(body):
    return hashlib.sha256(json.dumps(body,sort_keys=True).encode('utf-8')).hexdigest()


def begin(root,paths):
    root=Path(root).resolve();journal=root/'workflow/transaction.json'
    if journal.exists():raise ValueError('An unfinished review transaction must be recovered first')
    records=[]
    for path in sorted({Path(p).resolve() for p in paths},key=str):
        if not path.is_relative_to(root):raise ValueError('Review transaction metadata must stay inside this task')
        if path==journal:raise ValueError('Transaction cannot snapshot itself')
        records.append({'path':path.relative_to(root).as_posix(),
                        'data':base64.b64encode(path.read_bytes()).decode('ascii') if path.is_file() else None})
    body={'format':FORMAT,'root':str(root),'files':records}
    save_json(journal,{**body,'checksum':_digest(body)})


def recover(root):
    root=Path(root).resolve();journal=root/'workflow/transaction.json'
    if not journal.is_file():return False
    saved=json.loads(journal.read_text(encoding='utf-8'))
    body={k:v for k,v in saved.items() if k!='checksum'}
    if (body.get('format')!=FORMAT or body.get('root')!=str(root)
            or saved.get('checksum')!=_digest(body) or not isinstance(body.get('files'),list)):
        raise ValueError('Interrupted review journal is invalid; preserve the task for recovery')
    validated=[]
    for item in body['files']:
        path=(root/item['path']).resolve()
        if not path.is_relative_to(root) or path==journal or path.suffix!='.json':
            raise ValueError('Interrupted review journal has an invalid metadata path')
        data=base64.b64decode(item['data'],validate=True) if item['data'] is not None else None
        validated.append((path,data))
    for path,data in validated:
        if data is None:path.unlink(missing_ok=True)
        else:
            path.parent.mkdir(parents=True,exist_ok=True)
            temporary=path.with_name(path.name+'.recovery.tmp')
            temporary.write_bytes(data);os.replace(temporary,path)
    journal.unlink()
    return True


def commit(root):
    # Removing the durable journal is the commit point. Until then recovery rolls back.
    (Path(root)/'workflow/transaction.json').unlink()
