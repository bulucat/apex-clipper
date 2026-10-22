"""Build a clean source-only release ZIP with hashes; no upload or git operations."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import zipfile

from config import VERSION

ROOT_FILES={"apex.py","SKILL.md","README.md","README.en.md","LICENSE","NOTICE.md","CONTRIBUTING.md","CHANGELOG.md",".gitignore"}
ROOT_DIRS={"scripts","references","profiles","assets","agents",".github","obs"}
EXTENSIONS={".py",".md",".txt",".json",".png",".svg",".yml",".yaml",".lua"}
EXCLUDED={".venv",".local","__pycache__","exports","dist",".git",".pytest_cache"}


def release_files(root):
    root=Path(root).resolve()
    files=[]
    for directory,dirs,names in os.walk(root,followlinks=False):
        current=Path(directory)
        dirs[:]=sorted(d for d in dirs if d not in EXCLUDED and not d.endswith("_cache") and
                      (current!=root or d in ROOT_DIRS))
        for name in dirs+names:
            if (current/name).is_symlink():
                raise ValueError(f"Symlinks are not packaged: {(current/name).relative_to(root)}")
        for name in names:
            path=current/name
            relative=path.relative_to(root)
            allowed=relative.name in ROOT_FILES if current==root else path.suffix in EXTENSIONS
            if allowed:
                files.append(path)
    for required in ("SKILL.md","README.md","LICENSE","apex.py","profiles/default.json"):
        if not (root/required).is_file():
            raise ValueError(f"Missing release file: {required}")
    return sorted(files)


def package(root,output):
    root=Path(root).resolve()
    output=Path(output).resolve()
    files=release_files(root)
    entries=[]
    for path in files:
        data=path.read_bytes()
        if path.suffix in {".py",".md",".json",".txt",".yml",".yaml",".lua"}:
            text=data.decode("utf-8-sig")
            if re.search(r"[A-Za-z]:[\\/]+Users[\\/]+[^\\/\r\n]+",text):
                raise ValueError(f"Personal absolute user path found in {path.relative_to(root)}")
        if path.relative_to(root).parts[0]=="profiles" and path.suffix==".json":
            if any(json.loads(data.decode("utf-8-sig")).get("tools",{}).values()):
                raise ValueError("Public profiles must not contain machine-specific tool locations")
        entries.append({"path":path.relative_to(root).as_posix(),"size":len(data),"sha256":hashlib.sha256(data).hexdigest()})
    output.parent.mkdir(parents=True,exist_ok=True)
    partial=output.with_suffix(output.suffix+".partial")
    try:
        with zipfile.ZipFile(partial,"w",zipfile.ZIP_DEFLATED) as archive:
            for path in files:
                archive.write(path,"apex-clipper/"+path.relative_to(root).as_posix())
            archive.writestr("apex-clipper/release-manifest.json",json.dumps({"version":VERSION,"files":entries},indent=2))
        with zipfile.ZipFile(partial) as archive:
            if archive.testzip() is not None:
                raise ValueError("Release ZIP integrity check failed")
        partial.replace(output)
    finally:
        if partial.exists():
            partial.unlink()
    return {"version":VERSION,"zip":str(output),"files":len(entries),"bytes":output.stat().st_size,
            "sha256":hashlib.sha256(output.read_bytes()).hexdigest(),"uploaded":False}


if __name__=="__main__":
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root",default=str(Path(__file__).resolve().parent.parent))
    ap.add_argument("--output",required=True)
    args=ap.parse_args()
    try:
        print(json.dumps(package(args.root,args.output),ensure_ascii=False))
    except (ValueError,OSError) as exc:
        raise SystemExit(f"ERROR: {exc}")
