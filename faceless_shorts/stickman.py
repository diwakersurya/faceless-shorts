"""
Stickman scene engine: draws a scene as an animated stick-figure clip with Pillow and pipes
the frames to FFmpeg. No model and no network, so it is instant, free and works offline.

What a scene shows comes from its optional `stick` storyboard in the script JSON:

    {"action": "think", "other": "angry", "bubble": "?", "other_bubble": "!!", "headline": "ASK ONE QUESTION"}

    action        what the main figure does (one of ACTIONS)
    other         adds a second figure; it acts first, then the main figure reacts
    bubble        a few characters in the main figure's bubble (a thought bubble when it thinks)
    other_bubble  the same for the second figure
    headline      up to six words that pop in along the top

A scene without a storyboard gets its action from keywords in its text (storyboard_for).
"""

import math
import os
import re
import subprocess
from functools import lru_cache
from typing import Callable, Dict, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFont

# Everything is laid out on a 1080x1920 design canvas and scaled to the size asked for.
W, H = 1080, 1920
S = 2                      # lines are drawn at twice the size and reduced, which smooths them
GROUND = 1260              # y of the line the figures stand on
FIG = 1.3                  # figure size
CAPTION_MARGIN_V = 400     # bottom margin (on a 1920 canvas) that puts captions under the ground line
INK, PAPER, ACCENT = (28, 28, 32), (250, 247, 240), (226, 62, 52)

TORSO, UPPER_ARM, FOREARM, THIGH, SHIN, HEAD = (n * FIG for n in (175, 88, 82, 105, 105, 58))
LEGS = (THIGH + SHIN) * 0.985          # hip height when standing
SIT = LEGS - 45                        # how far the hips drop to sit on the ground
STAND_HEAD_Y = GROUND - LEGS - TORSO - HEAD - 6 * FIG

# ponytail: Latin-only hand-drawn fonts; a headline in Hindi, Japanese or Arabic needs a font with
# those glyphs added to this list (the captions are not affected, FFmpeg draws them).
_FONTS = (
    "/System/Library/Fonts/MarkerFelt.ttc",
    "/System/Library/Fonts/Supplemental/Comic Sans MS Bold.ttf",
    "C:/Windows/Fonts/comicbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
)


@lru_cache(maxsize=None)
def _font(size: int) -> ImageFont.ImageFont:
    for path in _FONTS:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    try:
        return ImageFont.load_default(size)
    except TypeError:  # Pillow before 10.1 has one fixed size
        return ImageFont.load_default()


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def _smooth(x: float) -> float:
    x = _clamp(x)
    return x * x * (3 - 2 * x)


def _pop(x: float) -> float:
    """0 -> 1 with a small overshoot, for things that pop in."""
    x = _clamp(x)
    c = 1.70158
    return 1 + (c + 1) * (x - 1) ** 3 + c * (x - 1) ** 2


# ------------------------------------------------------------------ POSES
# Angles are degrees from straight down, positive toward the way the figure faces.
# L is the far arm/leg, R the near one. el/kn bend relative to the upper limb. dx/dy move the hips.
STAND = dict(lean=0, shL=-10, elL=8, shR=10, elR=8, hipL=-7, knL=0, hipR=7, knR=0, dx=0, dy=0)
Pose = Dict[str, float]
sin = math.sin


def _p(**changes: float) -> Pose:
    return {**STAND, **changes}


def _stride(t: float, speed: float, swing: float, lean: float, elbow: float) -> Pose:
    p = 2 * math.pi * t * speed
    return _p(
        lean=lean, shL=swing * sin(p), elL=elbow, shR=-swing * sin(p), elR=elbow,
        hipL=-swing * sin(p), knL=-swing * (0.5 + 0.5 * sin(p + 1.2)),
        hipR=swing * sin(p), knR=-swing * (0.5 + 0.5 * sin(p + math.pi + 1.2)),
        dy=-9 * abs(sin(p)),
    )


def _lift(t: float) -> Pose:
    r = 0.5 + 0.5 * sin(3 * t)  # 0 = bar at the shoulders, 1 = pressed overhead
    up, bend = 75 + 85 * r, 100 - 85 * r
    return _p(shL=-up, elL=-bend, shR=up, elR=bend, hipL=-14, hipR=14)


# Each action is a pose that keeps moving: t is seconds since the action started.
ACTIONS: Dict[str, Callable[[float], Pose]] = {
    "stand": lambda t: _p(),
    "talk": lambda t: _p(lean=2, shR=38 + 6 * sin(5 * t), elR=85 + 22 * sin(5 * t)),
    "point": lambda t: _p(lean=6, shL=-18, elL=12, shR=86 + 5 * sin(9 * t), elR=4, hipL=-12, hipR=12),
    "angry": lambda t: _p(lean=7 + 2 * sin(16 * t), shL=-18, elL=12, shR=86 + 7 * sin(16 * t), elR=4, hipL=-12, hipR=12),
    "think": lambda t: _p(lean=-3, shR=50, elR=125 + 4 * sin(2 * t)),
    "shrug": lambda t: _p(shL=-35, elL=-75 - 8 * sin(3 * t), shR=35, elR=75 + 8 * sin(3 * t), dy=-5 * max(0.0, sin(3 * t))),
    "celebrate": lambda t: _p(shL=-150, elL=-20, shR=150, elR=20, hipL=-12, hipR=12, dy=-60 * abs(sin(5 * t))),
    "slump": lambda t: _p(lean=22 + 2 * sin(1.5 * t), shL=4, elL=4, shR=14, elR=4),
    "recoil": lambda t: _p(lean=-10, shL=-25, elL=20, shR=40, elR=110, dx=3 * sin(40 * t)),
    "wave": lambda t: _p(shR=150, elR=20 + 28 * sin(8 * t)),
    "walk": lambda t: _stride(t, 1.6, 26, 4, 18),
    "run": lambda t: _stride(t, 3.0, 42, 12, 80),
    "lift": _lift,
    "meditate": lambda t: _p(shL=-28, elL=4, shR=28, elR=-4, hipL=-80, knL=165, hipR=80, knR=-165, dy=SIT + 4 * sin(1.6 * t)),
}
_FACES = {"angry": "angry", "celebrate": "happy", "wave": "happy", "slump": "sad", "meditate": "closed"}
_THOUGHT = {"think", "meditate"}   # these get a thought bubble instead of a speech bubble
_MOVING = {"walk": 260, "run": 620}  # ground speed (px/s) shown under a figure that strides on the spot


def _mix(a: Pose, b: Pose, k: float) -> Pose:
    return {j: a[j] + (b[j] - a[j]) * k for j in STAND}


# ------------------------------------------------------------- STORYBOARD
_ROTATION = ("talk", "point", "shrug", "think")
# First rule with a word in the scene text wins: (words, action, bubble).
_RULES: Tuple[Tuple[set, str, Optional[str]], ...] = (
    ({"gym", "lift", "lifting", "muscle", "muscles", "weight", "weights", "workout", "barbell", "deadlift", "squat", "strength"}, "lift", None),
    ({"run", "running", "fast", "faster", "speed", "chase", "race", "escape"}, "run", None),
    ({"calm", "still", "stillness", "peace", "breathe", "meditate", "meditation", "silence", "patience"}, "meditate", None),
    ({"money", "cash", "rich", "wealth", "dollar", "dollars", "income", "profit", "millionaire", "billionaire"}, "celebrate", "$"),
    ({"win", "winner", "success", "victory", "achieve", "champion", "unstoppable"}, "celebrate", None),
    ({"fear", "afraid", "danger", "dangerous", "scary", "terrifying", "horror", "ghost", "death"}, "recoil", "!"),
    ({"sad", "fail", "failure", "lose", "lost", "tired", "weak", "lonely", "pain", "broke"}, "slump", None),
    ({"angry", "anger", "offended", "hate", "rage", "disrespect"}, "angry", "!!"),
    ({"stop", "never", "don't", "avoid", "quit", "warning", "rule"}, "point", "!"),
    ({"follow", "subscribe", "share", "comment"}, "wave", None),
    ({"why", "how", "what", "question", "wonder", "secret", "mystery", "think", "mind", "brain", "imagine"}, "think", "?"),
)


def storyboard_for(text: str, index: int = 0, stick: Optional[Dict] = None) -> Dict[str, Optional[str]]:
    """What to draw for a scene: the script's own storyboard when it has one, else keywords in its text.
    Always returns a plan the renderer can draw (an unknown action never raises)."""
    plan: Dict[str, Optional[str]] = {
        "action": _ROTATION[index % len(_ROTATION)], "other": None, "bubble": None, "other_bubble": None, "headline": None,
    }
    stick = stick if isinstance(stick, dict) else {}
    if not stick.get("action"):
        words = set(re.findall(r"[a-z']+", (text or "").lower()))
        for keys, action, bubble in _RULES:
            if words & keys:
                plan.update(action=action, bubble=bubble)
                break
        else:
            if "?" in (text or ""):
                plan.update(action="think", bubble="?")
    for key in plan:
        if stick.get(key) is not None:
            plan[key] = str(stick[key]).strip()[:12 if "bubble" in key else 60] or None  # a bubble only has room for a few characters
    if plan["action"] not in ACTIONS:
        plan["action"] = "talk"
    if plan["other"] not in ACTIONS:
        plan["other"] = None
    return plan


# ---------------------------------------------------------------- DRAWING
def _figure(d: ImageDraw.ImageDraw, x: float, pose: Pose, facing: int, face: str = "neutral") -> Dict[str, Tuple[float, float]]:
    """Draw one figure standing at x on the ground line. Returns where its head and hands ended up."""
    def limb_end(start, angle, length):
        a = math.radians(angle)
        return (start[0] + facing * math.sin(a) * length, start[1] + math.cos(a) * length)

    w = int(15 * FIG * S)

    def line(points, colour=INK, width=w):
        pts = [(px * S, py * S) for px, py in points]
        d.line(pts, fill=colour, width=width, joint="curve")
        for px, py in pts:  # round ends and joints
            d.ellipse([px - width / 2, py - width / 2, px + width / 2, py + width / 2], fill=colour)

    hip = (x + pose["dx"], GROUND - LEGS + pose["dy"])
    neck = limb_end(hip, 180 - pose["lean"], TORSO)
    head = limb_end(neck, 180 - pose["lean"], HEAD + 6 * FIG)
    hands = {}
    for side in "LR":
        knee = limb_end(hip, pose["hip" + side], THIGH)
        line([hip, knee, limb_end(knee, pose["hip" + side] + pose["kn" + side], SHIN)])
    line([hip, neck])
    for side in "LR":
        elbow = limb_end(neck, pose["sh" + side], UPPER_ARM)
        hands[side] = limb_end(elbow, pose["sh" + side] + pose["el" + side], FOREARM)
        line([neck, elbow, hands[side]])

    d.ellipse([(head[0] - HEAD) * S, (head[1] - HEAD) * S, (head[0] + HEAD) * S, (head[1] + HEAD) * S], fill=PAPER, outline=INK, width=w)
    thin = int(7 * FIG * S)
    for near, off in ((False, 8), (True, 36)):  # both eyes sit toward the side the figure faces
        ex, ey, r = head[0] + facing * off * FIG, head[1] - 6 * FIG, 6 * FIG
        if face == "closed":
            line([(ex - r * 1.6, ey), (ex + r * 1.6, ey)], width=thin)
        else:
            d.ellipse([(ex - r) * S, (ey - r) * S, (ex + r) * S, (ey + r) * S], fill=INK)
        if face == "angry":  # brows slanting down toward the middle
            inner, outer = (-facing if near else facing) * 13 * FIG, (facing if near else -facing) * 13 * FIG
            line([(ex + outer, ey - 25 * FIG), (ex + inner, ey - 15 * FIG)], width=thin)
    if face in ("happy", "sad"):
        mx, my = head[0] + facing * 22 * FIG, head[1] + (16 if face == "happy" else 30) * FIG
        box = [(mx - 20 * FIG) * S, (my - 12 * FIG) * S, (mx + 20 * FIG) * S, (my + 12 * FIG) * S]
        start, end = (20, 160) if face == "happy" else (200, 340)  # lower half of the box smiles, upper half frowns
        d.arc(box, start, end, fill=INK, width=thin)
    return {"head": head, "L": hands["L"], "R": hands["R"]}


def _barbell(d: ImageDraw.ImageDraw, hands: Dict[str, Tuple[float, float]]) -> None:
    (lx, ly), (rx, ry) = sorted((hands["L"], hands["R"]))
    y = (ly + ry) / 2
    d.line([((lx - 95) * S, y * S), ((rx + 95) * S, y * S)], fill=INK, width=int(12 * S))
    for end in (lx - 95, rx + 95):
        d.rounded_rectangle([(end - 16) * S, (y - 62) * S, (end + 16) * S, (y + 62) * S], radius=int(10 * S), fill=ACCENT)


def _fit(text: str, size: int, room: float, smallest: int) -> int:
    """The largest font size (stepping down from size) at which text is no wider than room."""
    while size > smallest and _font(size * S).getlength(text) > room * S:
        size -= 6
    return size


def _bubble(d: ImageDraw.ImageDraw, cx: float, cy: float, k: float, text: str, head: Tuple[float, float], thought: bool, colour) -> None:
    if k <= 0.03:
        return
    rx, ry = 170 * k, 130 * k
    tail = (head[0], head[1] - HEAD - 14)
    if thought:  # a trail of small circles from the head to the bubble
        for i, r in enumerate((14, 22, 32)):
            f = 0.2 + 0.24 * i
            px, py = tail[0] + (cx - tail[0]) * f, tail[1] + (cy + ry - tail[1]) * f
            d.ellipse([(px - r * k) * S, (py - r * k) * S, (px + r * k) * S, (py + r * k) * S], fill=PAPER, outline=colour, width=int(8 * S))
    else:
        d.polygon([((cx - 34 * k) * S, (cy + ry * 0.7) * S), ((cx + 34 * k) * S, (cy + ry * 0.7) * S), (tail[0] * S, tail[1] * S)], fill=colour)
    d.ellipse([(cx - rx) * S, (cy - ry) * S, (cx + rx) * S, (cy + ry) * S], fill=PAPER, outline=colour, width=int(10 * S))
    size = _fit(text, 150, 240, 44)
    d.text((cx * S, cy * S), text, font=_font(int(size * k * S)), fill=colour, anchor="mm")


def _headline(d: ImageDraw.ImageDraw, text: str, t: float, start: float) -> None:
    words = text.upper().split()[:6]
    if not words:
        return
    size = _fit(" ".join(words), 120, 940, 54)
    font = _font(size * S)
    x = (W * S - font.getlength(" ".join(words))) / 2
    for i, word in enumerate(words):  # the words pop in one after another; the last one is the accent
        k = _pop((t - start - i * 0.22) / 0.3)
        width = font.getlength(word)
        if k > 0.03:
            d.text((x + width / 2, 215 * S), word, font=_font(int(size * k * S)), fill=ACCENT if i == len(words) - 1 and len(words) > 1 else INK, anchor="mm")
        x += width + font.getlength(" ")


PHOTO_HEAD = 1.35  # a face from a photo is drawn this much bigger than a plain head, so it can be recognised
PROP_SIDE = 260    # an object model's card, on the design canvas


def _photo_head(img: Image.Image, d: ImageDraw.ImageDraw, head: Tuple[float, float], photo: Image.Image) -> None:
    """Put a round face cut from a model's photo over a figure's head, with the same ink outline."""
    r = HEAD * PHOTO_HEAD
    box = [int((head[0] - r) * S), int((head[1] - r) * S)]
    img.paste(photo, box, photo)
    d.ellipse([box[0], box[1], box[0] + photo.width, box[1] + photo.height], outline=INK, width=int(12 * FIG * S))


def _prop(img: Image.Image, d: ImageDraw.ImageDraw, cx: float, k: float, photo: Image.Image) -> None:
    """An object model as a framed card standing on the ground, popping in with k (0 to 1)."""
    if k <= 0.03:
        return
    side = int(photo.width * k)
    card = photo.resize((side, side), Image.LANCZOS)
    left, top = int(cx * S - side / 2), int((GROUND + 2) * S - side)
    img.paste(card, (left, top))
    d.rounded_rectangle([left, top, left + side, top + side], radius=int(14 * S * k), outline=INK, width=int(9 * S))


def draw_frame(t: float, plan: Dict[str, Optional[str]], duration: float, walk_in: bool = False,
               cast: Optional[Dict[str, Optional[Image.Image]]] = None) -> Image.Image:
    """One frame of a scene, t seconds in, on the supersampled design canvas. *cast* (from your models):
    "main" and "other" are round faces for the two figures, "prop" an object's square photo."""
    cast = cast or {}
    img = Image.new("RGB", (W * S, H * S), PAPER)
    d = ImageDraw.Draw(img)
    action, other = plan["action"], plan["other"]

    # Timeline: (walk in) -> the second figure acts -> the main figure acts and holds to the end.
    arrive = min(1.3, 0.3 * duration) if walk_in else 0.0
    first = arrive + 0.15
    main_start = first + 0.45 * max(duration - first, 0.0) if other else first
    blend = _smooth((t - main_start) / 0.35)

    d.line([(90 * S, (GROUND + 8) * S), ((W - 90) * S, (GROUND + 8) * S)], fill=INK, width=int(8 * S))
    if action in _MOVING and blend > 0:  # ground marks sliding back under a figure striding on the spot
        for i in range(6):
            mx = (i * 230 - _MOVING[action] * (t - main_start)) % (W + 230) - 115
            d.line([(mx * S, (GROUND + 52) * S), ((mx + 70) * S, (GROUND + 52) * S)], fill=INK, width=int(7 * S))

    breathe = 2.5 * sin(2.4 * t)
    main_x = 330 if other else W / 2
    if cast.get("prop") is not None:
        _prop(img, d, W / 2 + 20 if other else 860, _pop((t - first - 0.1) / 0.4), cast["prop"])
    if t < arrive:
        k = t / arrive
        pose, x = ACTIONS["walk"](t), -150 + (main_x + 150) * (1 - (1 - k) ** 2)
    else:
        pose, x = _mix(_p(dy=breathe), ACTIONS[action](max(t - main_start, 0.0)), blend), main_x
    main = _figure(d, x, pose, 1, _FACES.get(action, "neutral") if blend > 0.5 else "neutral")
    if cast.get("main") is not None:
        _photo_head(img, d, main["head"], cast["main"])
    if action == "lift" and blend > 0.5:
        _barbell(d, main)

    if other:
        k_other = _smooth((t - first) / 0.35) * (1 - _smooth((t - main_start + 0.3) / 0.35))
        second = _figure(d, 790, _mix(_p(dy=-breathe), ACTIONS[other](max(t - first, 0.0)), k_other), -1,
                         _FACES.get(other, "neutral") if k_other > 0.5 else "neutral")
        if cast.get("other") is not None:
            _photo_head(img, d, second["head"], cast["other"])
        if other == "lift" and k_other > 0.5:
            _barbell(d, second)
        if plan["other_bubble"]:
            k = _pop((t - first - 0.2) / 0.35) * (1 - _smooth((t - main_start + 0.3) / 0.3))
            _bubble(d, 750, STAND_HEAD_Y - 235, k, plan["other_bubble"], second["head"], other in _THOUGHT, ACCENT)
    if plan["bubble"]:
        _bubble(d, main_x + (30 if other else 190), STAND_HEAD_Y - 235, _pop((t - main_start - 0.35) / 0.35),
                plan["bubble"], main["head"], action in _THOUGHT, INK)
    if plan["headline"]:
        _headline(d, plan["headline"], t, main_start + 0.5 if not other else first + 0.4)
    return img


def render_clip(
    text: str,
    output_video_path: str,
    duration: float,
    fps: int,
    width: int,
    height: int,
    storyboard: Optional[Dict] = None,
    index: int = 0,
    models: Sequence = (),
) -> Optional[str]:
    """
    Draw scene number index (0-based) as a stick-figure clip of the given length and size.
    The figure walks in on the first scene. *models* (my_models.Model): the first person's face goes
    on the main figure, a second person's on the other figure, the first object stands beside them.
    Returns None on any failure so the caller can fall back to the image + Ken Burns path.
    """
    from faceless_shorts import my_models

    plan = storyboard_for(text, index, storyboard)
    people = [m for m in models if m.is_person]
    things = [m for m in models if not m.is_person]
    if len(people) > 1 and not plan["other"]:
        plan["other"] = "talk"  # two people featured: both are in the scene
    size = int(2 * HEAD * PHOTO_HEAD * S)
    try:
        cast = {
            "main": my_models.head(people[0], size) if people else None,
            "other": my_models.head(people[1], size) if len(people) > 1 and plan["other"] else None,
            "prop": my_models.card(things[0], PROP_SIDE * S) if things else None,
        }
    except OSError as e:  # the photo went missing since the model was chosen
        print(f"    [!] A model's photo could not be read ({e}); drawing plain figures.")
        cast = {}
    frames = max(math.ceil(duration * fps), 1)
    os.makedirs(os.path.dirname(os.path.abspath(output_video_path)), exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}", "-r", str(fps), "-i", "-",
        "-c:v", "libx264", "-preset", "faster", "-pix_fmt", "yuv420p", output_video_path,
    ]
    try:
        ffmpeg = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # ponytail: one frame at a time on one core, about 15 frames a second; draw scenes in
        # parallel processes if a 60 fps render gets too slow.
        for i in range(frames):
            frame = draw_frame(i / fps, plan, duration, walk_in=index == 0, cast=cast)
            frame = frame.reduce(S) if (width, height) == (W, H) else frame.resize((width, height), Image.LANCZOS)
            ffmpeg.stdin.write(frame.tobytes())
        ffmpeg.stdin.close()
        if ffmpeg.wait() != 0:
            raise OSError("ffmpeg could not encode the frames")
    except (OSError, ValueError) as e:  # ffmpeg missing, or it closed the pipe
        print(f"    [!] Could not draw the stickman clip ({e}).")
        return None
    return output_video_path
