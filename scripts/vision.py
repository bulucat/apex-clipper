"""Cheap local visual proposals and cached CPU OCR. Never declares final A events."""
import hashlib
import json
from pathlib import Path
import re
import unicodedata

import numpy as np

from common import save_json

OCR_VERSION = "rapidocr_onnxruntime-1.4.4-maxside736-v2"


def text_mask(image):
    rgb = np.asarray(image.convert("RGB"), dtype=np.int16)
    red = (rgb[:, :, 0] > 120) & (rgb[:, :, 0] > rgb[:, :, 1] * 1.35) & (rgb[:, :, 0] > rgb[:, :, 2] * 1.2)
    white = (rgb.min(axis=2) > 165) & (rgb.max(axis=2) - rgb.min(axis=2) < 70)
    return red, white


def visual_proposal(image):
    red, white = text_mask(image)
    white_rows = np.count_nonzero(white.sum(axis=1) >= max(3, white.shape[1] * .012))
    red_count, white_count = int(red.sum()), int(white.sum())
    present = white_rows >= 2 and white_count >= 10
    return {"candidate": bool(present), "red_pixels": red_count, "white_pixels": white_count,
            "white_rows": int(white_rows), "kind": "red_white_layout" if red_count >= 5 else "white_layout"}


def glyph_signature(image):
    red, white = text_mask(image)
    data = np.stack([red, white], axis=-1)
    return hashlib.sha256(np.packbits(data).tobytes() + str(data.shape).encode()).hexdigest()


def normalize(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text)).casefold()


def hints(lines, region):
    compact = normalize(" ".join(line["text"] for line in lines))
    if region == "high":
        groups = [("即时", "即時"), ("总结", "總結"), ("观战", "觀戰"), ("小队", "小隊")]
        concepts = [group[0] for group in groups if any(word in compact for word in group)]
        return {"concepts": concepts, "spectate_hint": len(concepts) >= 2}
    return {"knockdown_hint": any(w in compact for w in ("击倒", "擊倒", "knockdown")),
            "damage_hint": any(w in compact for w in ("伤害", "傷害", "damage")),
            "weak_reward_hint": any(w in compact for w in ("弱者奖励", "弱者獎勵", "weakerreward")),
            "assist_hint": any(w in compact for w in ("助攻", "assist")),
            "assist_elimination_hint": (any(w in compact for w in ("助攻", "assist")) and any(w in compact for w in ("消灭", "消滅", "eliminat")))}


class LocalOCR:
    def __init__(self, cache, backend="auto", threads=2):
        self.cache = Path(cache)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.engine = None
        self.calls = self.hits = 0
        self.error = None
        if backend == "off":
            self.error = "disabled; visual review required"
            return
        try:
            from rapidocr_onnxruntime import RapidOCR
            self.engine = RapidOCR(intra_op_num_threads=threads, inter_op_num_threads=1,
                                   use_angle_cls=False, print_verbose=False,
                                   det_limit_type="max", det_limit_side_len=736)
        except Exception as exc:
            self.error = f"Local OCR unavailable: {type(exc).__name__}: {exc}"
            if backend == "required":
                raise ValueError(self.error) from exc

    def read(self, image, region="low", recognition_only=False):
        pixels = np.asarray(image.convert("RGB"))
        key = hashlib.sha256(pixels.tobytes() + str(pixels.shape).encode() +
                             f"{OCR_VERSION}|{region}|{recognition_only}".encode()).hexdigest()
        path = self.cache / (key + ".json")
        if path.is_file():
            try:
                result = json.loads(path.read_text(encoding="utf-8"))
                if result.get("status") == "ok":
                    self.hits += 1
                    return result
            except (ValueError, OSError):
                pass
        if self.engine is None:
            return {"status": "unavailable", "lines": [], "error": self.error, "hints": {}}
        try:
            raw, _ = self.engine(pixels[:, :, ::-1].copy(), use_cls=False, use_det=not recognition_only)
            self.calls += 1
            lines = []
            for item in raw or []:
                if recognition_only:
                    text, score = item[:2]
                    box = None
                else:
                    box, text, score = item
                    box = np.asarray(box).tolist()
                lines.append({"text": str(text), "score": float(score), "box": box})
            result = {"status": "ok", "lines": lines, "hints": hints(lines, region),
                      "uncertain": not lines or any(line["score"] < .9 for line in lines)}
            save_json(path, result)
            return result
        except Exception as exc:
            return {"status": "error", "lines": [], "error": str(exc), "hints": {}, "uncertain": True}


def representatives(records):
    """Only collapse consecutive EXACT glyph+spectator masks, keeping temporal extent."""
    groups = []
    for record in records:
        if groups and record["signature"] == groups[-1][-1]["signature"] and record["time"] - groups[-1][-1]["time"] <= .75:
            groups[-1].append(record)
        else:
            groups.append([record])
    selected = []
    for group in groups:
        for i in sorted({0, len(group) // 2, len(group) - 1}):
            selected.append(dict(group[i], represented_sequences=[r["seq"] for r in group]))
    return selected
