"""Generate source-resolution ROI overlays and a portable JSON profile."""
from pathlib import Path

from PIL import Image, ImageDraw

from common import binary, probe, roi_box, run, save_json
from config import validate


def calibrate(source,directory,config,times=None,save_config=None):
    validate(config)
    media=probe(source)
    root=Path(directory).resolve()
    root.mkdir(parents=True,exist_ok=True)
    if times is None:
        times=sorted({0,media["duration"]/2,max(0,media["duration"]-1)})
    frames=[]
    colours={"low":"#00ff80","high":"#ffbf00","ammo":"#00bfff","stats":"#ff60d0"}
    for n,timestamp in enumerate(times):
        if not 0<=timestamp<media["duration"]:
            raise ValueError("Calibration timestamp outside source")
        source_frame=root/f"source_{n:02d}.png"
        overlay=root/f"overlay_{n:02d}.png"
        run([binary("ffmpeg"),"-y","-v","error","-ss",str(timestamp),"-i",str(source),"-frames:v","1",str(source_frame)])
        with Image.open(source_frame) as image:
            image=image.convert("RGB")
            draw=ImageDraw.Draw(image)
            for kind in ("low","high","ammo","stats"):
                box=roi_box(image.width,image.height,kind,config["roi"][kind])
                draw.rectangle(box,outline=colours[kind],width=max(2,image.width//400))
                draw.text((box[0]+5,box[1]+5),kind.upper(),fill=colours[kind])
            image.save(overlay)
        frames.append({"time":timestamp,"source":str(source_frame),"overlay":str(overlay)})
    profile=Path(save_config).resolve() if save_config else root/"config.json"
    # Machine-specific tool paths never get copied into a shareable calibration profile.
    import copy
    portable=copy.deepcopy(config)
    portable["tools"]={"ffmpeg":None,"ffprobe":None}
    save_json(profile,portable)
    result={"media":media,"frames":frames,"config":str(profile),
            "instructions":"Check LOW contains both banner lines, HIGH both spectator rows, AMMO the large magazine digits, STATS all four counters (kills/assists/participation/damage). Adjust roi in config and rerun; frame extraction alone does not confirm calibration."}
    save_json(root/"calibration.json",result)
    print(f"CALIBRATION: {len(frames)} overlays; config={profile}")
    return result
