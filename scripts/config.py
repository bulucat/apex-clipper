"""One validated configuration shared by scanning, review, planning and export."""
import copy
import hashlib
import json
import math
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION = "2.7.1"


def numeric(value, name, low=0, high=3600, positive=False):
    if isinstance(value, bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    if value < low or value > high or (positive and value <= 0):
        raise ValueError(f"{name} outside allowed range")
    return value


def merge(base, override, path="config"):
    if not isinstance(override,dict):
        raise ValueError(f"{path} must be an object")
    result = copy.deepcopy(base)
    for key,value in override.items():
        if key not in base:
            raise ValueError(f"Unknown setting: {path}.{key}")
        result[key] = merge(base[key],value,path+"."+key) if isinstance(base[key],dict) else value
    return result


def validate_policy(policy):
    if not isinstance(policy,dict) or not {"post_roll","chain_gap"} <= set(policy) or set(policy)-{"post_roll","chain_gap","pre_roll","fire_padding"}:
        raise ValueError("Policy requires post_roll/chain_gap and optional pre_roll/fire_padding")
    result = {"pre_roll":3,"fire_padding":3,**policy}
    for name in result:
        numeric(result[name],"timing."+name,high=120,positive=name in ("post_roll","chain_gap"))
    if result["chain_gap"] < result["post_roll"]:
        raise ValueError("chain_gap must be >= post_roll to keep separate clips from covering the next anchor")
    return result


def validate(config):
    if type(config["config_version"]) is not int or config["config_version"] != 1:
        raise ValueError("Unsupported config_version")
    scan = config["scan"]
    numeric(scan["fps"],"scan.fps",low=2,high=60)
    numeric(scan["audit_seconds"],"scan.audit_seconds",positive=True)
    if scan["mode"] not in ("economy","full") or scan["ocr"] not in ("auto","required","off"):
        raise ValueError("Invalid scan mode or OCR setting")
    gate = config["prefilter"]
    if type(gate["enabled"]) is not bool:
        raise ValueError("prefilter.enabled must be boolean")
    for name in ("minute_seconds", "min_damage", "fallback_divisions", "max_window"):
        if type(gate[name]) is not int or gate[name] <= 0:
            raise ValueError("prefilter."+name+" must be a positive integer")
    if gate["minute_seconds"] != 60 or gate["fallback_divisions"] != 10 or gate["max_window"] != 30:
        raise ValueError("This gate uses 60-second minutes, ten-way recovery and 30-second maximum windows")
    if gate["enabled"] and gate["min_damage"] != 150:
        raise ValueError("The minute HUD admission threshold is 150 damage growth")
    numeric(gate["boundary_padding"],"prefilter.boundary_padding",high=2)
    from marker_scope import validate_scope
    validate_scope(gate.get("marker_scope"))
    if gate.get("marker_scope") and not gate["enabled"]:
        raise ValueError("Marked scans require the normal HUD gate")
    quality = config["quality"]
    if quality["goal"] not in ("hud_gate", "complete", "fast"):
        raise ValueError("quality.goal must be hud_gate/complete/fast")
    numeric(quality["long_source_seconds"],"quality.long_source_seconds",positive=True,high=86400)
    for key in ("min_scan_fraction","max_unresolved_fraction"):
        numeric(quality[key],"quality."+key,high=1)
    timing = config["timing"]
    validate_policy({k:timing[k] for k in ("post_roll","chain_gap","pre_roll","fire_padding")})
    numeric(timing["lookback"],"timing.lookback",positive=True,high=120)
    numeric(timing["max_lookback"],"timing.max_lookback",positive=True,high=120)
    if timing["max_lookback"] < timing["lookback"]:
        raise ValueError("max_lookback must be >= lookback")
    for name,roi in config["roi"].items():
        if not isinstance(roi,list) or len(roi)!=4:
            raise ValueError(f"roi.{name} requires [x,y,width,height]")
        for value in roi:
            numeric(value,"roi."+name,high=1)
        x,y,w,h = roi
        if w<=0 or h<=0 or x+w>1.000001 or y+h>1.000001:
            raise ValueError(f"roi.{name} must be inside the frame")
    for name,value in config["review"].items():
        numeric(value,"review."+name,positive=True,high=60)
    for name,value in config["tools"].items():
        if value is not None and (not isinstance(value,str) or not value.strip()):
            raise ValueError(f"tools.{name} must be a path/string or null")
    return config


def load_config(path=None, use_local=True):
    base = json.loads((ROOT/"profiles/default.json").read_text(encoding="utf-8-sig"))
    local = ROOT/".local/config.json"
    if path is not None:
        selected = Path(path)
    elif use_local and local.is_file():
        selected = local
    else:
        selected = None
    if selected is not None:
        base = merge(base,json.loads(selected.read_text(encoding="utf-8-sig")))
    # Portable ROI profiles reuse private machine tool paths, not unrelated preferences.
    if path is not None and use_local and local.is_file() and selected.resolve()!=local.resolve():
        machine=json.loads(local.read_text(encoding="utf-8-sig")).get("tools",{})
        for name in ("ffmpeg","ffprobe"):
            if base["tools"][name] is None and machine.get(name):
                base["tools"][name]=machine[name]
    return validate(base)


def policy_from_config(config):
    return {k:config["timing"][k] for k in ("post_roll","chain_gap","pre_roll","fire_padding")}


def apply_tools(config):
    for name,value in config["tools"].items():
        if value:
            os.environ[name.upper()+"_BINARY"] = value


def digest(config):
    return hashlib.sha256(json.dumps(config,sort_keys=True).encode()).hexdigest()
