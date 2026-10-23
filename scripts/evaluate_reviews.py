"""Offline task-decision evaluation. Never calls a model or invents labels/usage."""
import json
import math
from pathlib import Path
from common import save_json


def records(path):
    rows=[]
    for n,line in enumerate(Path(path).read_text(encoding='utf-8-sig').splitlines(),1):
        if not line.strip():continue
        item=json.loads(line)
        if not isinstance(item,dict):raise ValueError('JSONL line '+str(n)+' must be an object')
        rows.append(item)
    return rows


def evaluate(labels_path,answers_path,output):
    labels=records(labels_path);answers=records(answers_path)
    truth={r.get('case_id'):r for r in labels}
    if not labels or len(truth)!=len(labels) or any(not isinstance(k,str) or not k.strip() for k in truth):
        raise ValueError('Provide nonempty uniquely identified human-labelled cases')
    for row in labels:
        if not isinstance(row.get('expected'),dict) or not row['expected']:
            raise ValueError('Each case needs actual expected visual fields')
    groups={};seen=set()
    for answer in answers:
        case=answer.get('case_id');model=answer.get('model');run_id=answer.get('run_id')
        if case not in truth or not isinstance(model,str) or not model.strip() or not isinstance(run_id,str) or not run_id.strip():
            raise ValueError('Every answer needs a known case_id and explicit model/run_id')
        key=(model,run_id,case)
        if key in seen:raise ValueError('Duplicate model/run/case answer')
        seen.add(key);groups.setdefault((model,run_id),{})[case]=answer
    results=[]
    for (model,run_id),predictions in sorted(groups.items()):
        cases=[];elapsed=0;timed=0
        for case,label in truth.items():
            answer=predictions.get(case);errors=[]
            if answer is None:errors.append('missing_answer')
            elif answer.get('outcome')!='answered':errors.append('unresolved')
            elif not isinstance(answer.get('result'),dict):errors.append('invalid_result')
            else:
                tolerance=label.get('tolerance',{})
                for field,expected in label['expected'].items():
                    actual=answer['result'].get(field)
                    tol=tolerance.get(field,0)
                    if type(tol) not in (int,float) or not math.isfinite(tol) or tol<0:raise ValueError('Invalid label tolerance')
                    numeric=type(expected) in (int,float) and type(actual) in (int,float)
                    if numeric:
                        equal=math.isfinite(expected) and math.isfinite(actual) and abs(expected-actual)<=tol
                    else:equal=type(expected) is type(actual) and actual==expected
                    if not equal:errors.append('wrong_'+field)
            if answer and answer.get('elapsed_seconds') is not None:
                value=answer['elapsed_seconds']
                if type(value) not in (int,float) or not math.isfinite(value) or value<0:raise ValueError('Invalid measured elapsed time')
                elapsed+=value;timed+=1
            cases.append({'case_id':case,'kind':label.get('kind'),'passed':not errors,'errors':errors,
                          'reported_usage':answer.get('usage') if answer else None})
        results.append({'model':model,'run_id':run_id,'cases':cases,'passed':sum(c['passed'] for c in cases),
                        'total':len(cases),'pass_fraction':sum(c['passed'] for c in cases)/len(cases),
                        'reported_elapsed_seconds':elapsed if timed else None})
    if not results:raise ValueError('No actual model answer records supplied; no accuracy was calculated')
    report={'format':'apex-review-evaluation-v1','runs':results,
            'note':'Agreement with supplied human labels only. Model/usage/time are reported inputs; this tool does not certify gameplay recall or infer cost savings.'}
    save_json(output,report);return report


def add_commands(sub):
    p=sub.add_parser('evaluate',help='Compare real task answers with supplied human labels, offline')
    p.add_argument('--labels',required=True);p.add_argument('--answers',required=True);p.add_argument('--output',required=True)
