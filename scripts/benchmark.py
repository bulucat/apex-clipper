"""Measure timestamp recall/precision against supplied human labels; never invent labels."""
import json
from pathlib import Path

from common import number,save_json


def anchors(data):
    if "anchors" in data:
        result=data["anchors"]
    elif "segments" in data:
        result=[a for s in data["segments"] if s.get("class")=="A" for a in s["anchors"]]
    else:
        result=[e["anchor"] for e in data.get("events",[]) if e.get("class")=="A"]
    if not isinstance(result,list):
        raise ValueError("anchors must be an array of seconds")
    for value in result:
        if number(value,"anchor")<0:
            raise ValueError("Negative event timestamp")
    return sorted(result)


def compare(truth,predictions,tolerance=1):
    if number(tolerance,"tolerance")<0:
        raise ValueError("tolerance must be nonnegative")
    expected,actual=anchors(truth),anchors(predictions)
    i=j=0
    matches,missed,extra=[],[],[]
    while i<len(expected) and j<len(actual):
        if abs(expected[i]-actual[j])<=tolerance:
            matches.append({"expected":expected[i],"actual":actual[j]})
            i+=1
            j+=1
        elif actual[j]<expected[i]-tolerance:
            extra.append(actual[j]); j+=1
        else:
            missed.append(expected[i]); i+=1
    missed.extend(expected[i:]); extra.extend(actual[j:])
    return {"labelled_events":len(expected),"predicted_events":len(actual),"matched":len(matches),
            "recall":len(matches)/len(expected) if expected else None,
            "precision":len(matches)/len(actual) if actual else None,
            "missed":missed,"extra":extra,"matches":matches,"tolerance_seconds":tolerance}


def report(truth_path,prediction_path,output,tolerance=1,elapsed=None,usage=None):
    truth=json.loads(Path(truth_path).read_text(encoding="utf-8"))
    predictions=json.loads(Path(prediction_path).read_text(encoding="utf-8"))
    result=compare(truth,predictions,tolerance)
    if elapsed is not None and number(elapsed,"elapsed_seconds")<0:
        raise ValueError("elapsed_seconds must be nonnegative")
    result["elapsed_seconds"]=elapsed
    result["reported_model_usage"]=json.loads(Path(usage).read_text(encoding="utf-8")) if usage else None
    result["note"]="Accuracy is only relative to supplied labels/tolerance. Runtime/usage are reported inputs; no unmeasured cost or zero-miss claim."
    save_json(output,result)
    return result
