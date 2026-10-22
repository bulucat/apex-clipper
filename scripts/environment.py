"""Standard-library-only isolated setup and actionable environment diagnostics."""
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import venv

from config import ROOT, VERSION, apply_tools, load_config


def env_python(root=ROOT):
    return Path(root)/".venv"/("Scripts/python.exe" if os.name=="nt" else "bin/python")


def setup(without_ocr=False, create_only=False, root=ROOT):
    if sys.version_info < (3,10):
        raise ValueError("Python 3.10+ required; Python 3.11/3.12 recommended")
    target = Path(root)/".venv"
    python = env_python(root)
    if target.exists() and not python.is_file():
        raise ValueError(".venv exists but is incomplete; choose a clean project folder instead of overwriting it")
    if not python.is_file():
        print("SETUP: creating isolated .venv (system Python is unchanged)",flush=True)
        venv.EnvBuilder(with_pip=True).create(target)
    if create_only:
        print(f"ENV READY: {python}; packages have not been installed")
        return python
    requirement = Path(root)/"scripts"/("requirements.txt" if without_ocr else "requirements-ocr.txt")
    log = Path(root)/".local/setup.log"
    log.parent.mkdir(parents=True,exist_ok=True)
    print("SETUP: installing local dependencies; first use needs internet",flush=True)
    with log.open("w",encoding="utf-8") as handle:
        result = subprocess.run([str(python),"-m","pip","install","--disable-pip-version-check","-r",str(requirement)],
                                stdout=handle,stderr=subprocess.STDOUT)
    if result.returncode:
        tail = log.read_text(encoding="utf-8",errors="replace")[-1600:]
        raise ValueError(f"Dependency setup failed. Log: {log}\n{tail}")
    print(f"SETUP COMPLETE: {python}; next run: python apex.py doctor")
    return python


def doctor(config=None, source=None):
    config = config or load_config()
    apply_tools(config)
    checks = []
    checks.append({"name":"Python","ok":sys.version_info>=(3,10),"detail":platform.python_version(),
                   "fix":"Install Python 3.11/3.12"})
    imports = {"numpy":"numpy","Pillow":"PIL","Local OCR":"rapidocr_onnxruntime"}
    for name,module in imports.items():
        try:
            result = subprocess.run([sys.executable,"-c",f"import {module}"],capture_output=True,timeout=45)
            ok = result.returncode==0
        except (OSError,subprocess.TimeoutExpired):
            ok=False
        checks.append({"name":name,"ok":ok,"detail":"available" if ok else "missing or cannot import",
                       "fix":"python apex.py setup"})
    for name in ("ffmpeg","ffprobe"):
        tool = os.environ.get(name.upper()+"_BINARY") or shutil.which(name)
        try:
            result = subprocess.run([str(tool),"-version"],capture_output=True,text=True,encoding="utf-8",errors="replace",timeout=10) if tool else None
            ok = result is not None and result.returncode==0
            detail = result.stdout.splitlines()[0] if ok else "not found or cannot execute"
        except (OSError,subprocess.TimeoutExpired):
            ok,detail=False,"cannot execute"
        checks.append({"name":name,"ok":ok,"detail":detail,
                       "fix":"Install FFmpeg/ffprobe or set tools paths in a local config"})
    media=None
    if source and all(c["ok"] for c in checks if c["name"] in ("ffmpeg","ffprobe")):
        from common import probe
        try:
            media=probe(source)
            checks.append({"name":"Input video","ok":True,"detail":media,"fix":""})
        except (ValueError,OSError) as exc:
            checks.append({"name":"Input video","ok":False,"detail":str(exc),"fix":"Check path and source integrity"})
    required = [c for c in checks if c["name"]!="Local OCR"]
    return {"version":VERSION,"platform":platform.system(),"python":platform.python_version(),
            "ready_for_local_scan":all(c["ok"] for c in required),
            "local_ocr_available":next(c["ok"] for c in checks if c["name"]=="Local OCR"),
            "model_vision":"Host must provide image viewing and script execution; cannot be checked by this script",
            "checks":checks,"media":media}
