"""On-laptop clip matcher (setting match_engine = "clip"). Nothing leaves this computer.

Same job as Claude in match.py (score every sampled frame of every clip against every phrase of her voice-over,
0 to 10) but done by CLIP ViT-B/32 (ONNX, quantized; assets/models/clip/) running on this laptop's CPU with
onnxruntime. No network at any point: the model files and the tokenizer are read from disk.

How a score is made (fixed rules, nothing learned from her videos):
  1. Each phrase is written into fixed sentences (TEMPLATES) and their text embeddings are averaged. Three sentences
     ("a photo of ...", "a real estate photo of ...", ...) were tried; the phrase on its own did better on the
     tuning half of her tour, so TEMPLATES is just the phrase.
  2. Each frame (the 288x512 still match.extract_frames already makes) is resized so its short side is 224 and
     three 224x224 squares are cut from it: top, middle, bottom (a centre crop alone throws away the top and bottom
     of a vertical phone frame). The frame's similarity to a phrase is the best of its squares.
  3. Similarities are centred twice (CENTRE = "both"): minus each phrase's average over all frames, then minus each
     frame's average over all phrases. Divided by TEMPERATURE, plus 5, rounded and kept between 0 and 10. So 5 =
     "about average", and a phrase CLIP cannot tell apart (a greeting) stays near 5 everywhere, like Claude's
     "neutral". The choices (templates, crops, centring, temperature) were picked on half of her tour's phrases with
     tests/evaluate_local_matcher.py, which reports the result on the other half.

Embeddings are cached next to the frames (clip_embeddings.npz) so a remake does not redo them.
"""
import hashlib
from pathlib import Path

import numpy as np

from .config import ROOT

MODEL_DIR = ROOT / "assets" / "models" / "clip"
FILES = {"vision": "vision_model_quantized.onnx", "text": "text_model_quantized.onnx", "tokenizer": "tokenizer.json"}
MODEL_ID = "clip-vit-base-patch32-onnx-q"
VERSION = 2                      # bump when templates, crops or the score rule change (invalidates saved scores)
TEMPLATES = ("{}.",)           # the phrase itself (see note 1)
CROPS = ("top", "middle", "bottom")
CENTRE = "both"                 # "phrase" or "both" (see to_scale)
TEMPERATURE = 0.01               # 0.01 of cosine similarity = 1 point on the 0-10 scale
CONTEXT = 77                     # CLIP's fixed text length
MEAN = np.array([0.48145466, 0.4578275, 0.40821073], dtype=np.float32)
STD = np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)
EOT = 49407                      # <|endoftext|>: CLIP's end token, also used as padding

_models = {}


class LocalMatchError(RuntimeError):
    pass


def available(model_dir=None):
    d = Path(model_dir or MODEL_DIR)
    return all((d / f).is_file() for f in FILES.values())


def _load(model_dir=None):
    d = Path(model_dir or MODEL_DIR)
    if str(d) in _models:
        return _models[str(d)]
    if not available(d):
        raise LocalMatchError("The on-laptop matcher's model files are missing")
    import onnxruntime as ort
    from tokenizers import Tokenizer
    opts = ort.SessionOptions()
    opts.log_severity_level = 3
    vis = ort.InferenceSession(str(d / FILES["vision"]), opts, providers=["CPUExecutionProvider"])
    txt = ort.InferenceSession(str(d / FILES["text"]), opts, providers=["CPUExecutionProvider"])
    tok = Tokenizer.from_file(str(d / FILES["tokenizer"]))
    tok.no_padding()
    tok.no_truncation()
    _models[str(d)] = (vis, txt, tok)
    return _models[str(d)]


def _norm(x):
    x = np.asarray(x, dtype=np.float32)
    return x / np.maximum(np.linalg.norm(x, axis=-1, keepdims=True), 1e-8)


def tokenize(tok, texts):
    """CLIP ids, cut or padded to 77; a cut text still ends with the end token."""
    out = np.full((len(texts), CONTEXT), EOT, dtype=np.int64)
    for i, t in enumerate(texts):
        ids = tok.encode(t).ids
        if len(ids) > CONTEXT:
            ids = ids[:CONTEXT - 1] + [EOT]
        out[i, :len(ids)] = ids
    return out


def clean(text):
    return " ".join(str(text).replace("[", " ").replace("]", " ").split()).strip(" .,;:!?").lower() or "a house"


def embed_texts(texts, model_dir=None, templates=TEMPLATES):
    """One unit vector per text: the average of its template sentences."""
    _, txt, tok = _load(model_dir)
    sents = [tpl.format(clean(t)) for t in texts for tpl in templates]
    ids = tokenize(tok, sents)
    embs = []
    for k in range(0, len(ids), 32):
        embs.append(txt.run(None, {"input_ids": ids[k:k + 32]})[0])
    e = _norm(np.concatenate(embs)).reshape(len(texts), len(templates), -1)
    return _norm(e.mean(axis=1))


def crops_of(path, crops=None):
    """Standard CLIP preprocessing, then the requested 224x224 squares (top / middle / bottom, or left/right if wide)."""
    from PIL import Image
    with Image.open(path) as im:
        im = im.convert("RGB")
        w, h = im.size
        s = 224 / min(w, h)
        nw, nh = max(224, round(w * s)), max(224, round(h * s))
        im = im.resize((nw, nh), Image.BICUBIC)
        a = np.asarray(im, dtype=np.float32) / 255.0
    out = []
    for c in crops or CROPS:
        if nh >= nw:
            y = {"top": 0, "middle": (nh - 224) // 2, "bottom": nh - 224}[c]
            x = (nw - 224) // 2
        else:
            x = {"top": 0, "middle": (nw - 224) // 2, "bottom": nw - 224}[c]
            y = (nh - 224) // 2
        sq = (a[y:y + 224, x:x + 224] - MEAN) / STD
        out.append(sq.transpose(2, 0, 1))
    return np.stack(out).astype(np.float32)


def embed_frames(paths, model_dir=None, cache=None):
    """-> array [frames, crops, 512] of unit vectors. cache: an .npz file keyed by the frame's bytes."""
    vis, _, _ = _load(model_dir)
    saved = {}
    if cache:
        try:
            with np.load(cache) as z:
                saved = {k: z[k] for k in z.files}
        except (OSError, ValueError):
            saved = {}
    tag = f"{VERSION}|{'-'.join(CROPS)}|"
    keys = [hashlib.sha1((tag + hashlib.sha1(Path(p).read_bytes()).hexdigest()).encode()).hexdigest() for p in paths]
    todo = [i for i, k in enumerate(keys) if k not in saved]
    for start in range(0, len(todo), 8):
        part = todo[start:start + 8]
        batch = np.concatenate([crops_of(paths[i]) for i in part])
        e = _norm(vis.run(None, {"pixel_values": batch})[0]).reshape(len(part), len(CROPS), -1)
        for i, v in zip(part, e):
            saved[keys[i]] = v
    if cache and todo:
        try:
            np.savez(cache, **saved)
        except OSError:
            pass
    return np.stack([saved[k] for k in keys]) if keys else np.zeros((0, len(CROPS), 512), np.float32)


def similarities(frame_embs, text_embs):
    """[frames, crops, 512] x [phrases, 512] -> [frames, phrases]: best crop's cosine similarity."""
    return np.einsum("fcd,pd->fcp", frame_embs, text_embs).max(axis=1)


def to_scale(sim, temperature=TEMPERATURE, centre=None):
    """Cosine similarities [frames, phrases] -> integer 0-10 (5 = average for that phrase). See the module notes.

    centre "phrase": minus each phrase's average over frames. "both" also takes off each frame's average over
    phrases (so a frame that is close to every sentence, or a sentence close to every frame, does not win everything).
    """
    centre = centre or CENTRE
    d = sim - sim.mean(axis=0, keepdims=True)
    if centre == "both":
        d = d - d.mean(axis=1, keepdims=True)
    return np.clip(np.rint(5 + d / temperature), 0, 10).astype(int)


def ask(frames, per_clip, phr, model_dir=None):
    """Same return as match.ask_model: (scores[clip][frame] = list of 0-10 per phrase, usage). No network."""
    flat = [p for row in frames for p in row]
    if not flat or not phr:
        raise LocalMatchError("Nothing to score")
    if sum(len(t) for t in per_clip) != len(flat):
        raise LocalMatchError("The frames do not line up with the frame times")
    cache = Path(flat[0]).parent.parent / "clip_embeddings.npz"
    fe = embed_frames(flat, model_dir, cache)
    te = embed_texts([p["text"] for p in phr], model_dir)
    S = to_scale(similarities(fe, te))
    scores, k = [], 0
    for ts in per_clip:
        scores.append([S[k + j].tolist() for j in range(len(ts))])
        k += len(ts)
    return scores, {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
