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

# Hand-drawn Latin fonts, plus script-aware fallbacks so Hindi/Japanese/Arabic/Russian headlines render cleanly.
_FONTS = (
    "/System/Library/Fonts/MarkerFelt.ttc",
    "/System/Library/Fonts/Supplemental/Comic Sans MS Bold.ttf",
    "C:/Windows/Fonts/comicbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
)

_SCRIPT_FONTS = {
    "hi": (
        "/System/Library/Fonts/Kohinoor.ttc",
        "/System/Library/Fonts/Supplemental/DevanagariMT.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf",
    ),
    "ja": (
        "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    ),
    "ar": (
        "/System/Library/Fonts/GeezaPro.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Bold.ttf",
    ),
    "ru": (
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ),
}


def _detect_script(text: str) -> str:
    for ch in text:
        code = ord(ch)
        if 0x0900 <= code <= 0x097F:
            return "hi"
        if 0x3040 <= code <= 0x30FF or 0x4E00 <= code <= 0x9FFF:
            return "ja"
        if 0x0600 <= code <= 0x06FF:
            return "ar"
        if 0x0400 <= code <= 0x04FF:
            return "ru"
    return "en"


@lru_cache(maxsize=None)
def _font(size: int, script: str = "en") -> ImageFont.ImageFont:
    candidates = (_SCRIPT_FONTS.get(script, ()) + _FONTS) if script != "en" else _FONTS
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
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


def _clap(t: float) -> Pose:
    c = 0.5 + 0.5 * sin(14 * t)  # 0 = hands apart, 1 = hands clapping together
    return _p(lean=3, shL=28 + 26 * c, elL=75 + 22 * c, shR=32 + 24 * c, elR=75 + 22 * c, dy=-4 * c)


def _dance(t: float) -> Pose:
    s = sin(6.5 * t)
    b = abs(sin(6.5 * t))
    return _p(
        lean=10 * s,
        shL=-110 + 45 * s, elL=-35 - 25 * s,
        shR=110 + 45 * s, elR=35 - 25 * s,
        hipL=-14 - 10 * s, knL=-18 * b,
        hipR=14 - 10 * s, knR=-18 * (1 - b),
        dx=22 * s, dy=-18 * b,
    )


def _punch(t: float) -> Pose:
    jab = max(0.0, sin(9 * t)) ** 2  # snappy forward jab
    return _p(
        lean=12 + 10 * jab,
        shL=45 - 20 * jab, elL=110,
        shR=50 + 42 * jab, elR=115 - 110 * jab,
        hipL=-22, knL=-10, hipR=20, knR=-12,
        dx=18 * jab,
    )


def _jump(t: float) -> Pose:
    air = max(0.0, sin(4.2 * t))  # 0 = on ground, 1 = peak of jump
    return _p(
        lean=-4 * air,
        shL=-30 - 125 * air, elL=-15,
        shR=30 + 125 * air, elR=15,
        hipL=-10 - 22 * air, knL=-55 * air,
        hipR=10 + 22 * air, knR=-55 * air,
        dy=-120 * air,
    )


# Each action is a pose that keeps moving: t is seconds since the action started.
ACTIONS: Dict[str, Callable[[float], Pose]] = {
    "stand": lambda t: _p(),
    "talk": lambda t: _p(lean=2, shR=38 + 6 * sin(5 * t), elR=85 + 22 * sin(5 * t)),
    "point": lambda t: _p(lean=6, shL=-18, elL=12, shR=86 + 5 * sin(9 * t), elR=4, hipL=-12, hipR=12),
    "present": lambda t: _p(lean=-3, shL=-20, elL=14, shR=115 + 10 * sin(3 * t), elR=12 + 8 * sin(3 * t), hipL=-10, hipR=10),
    "angry": lambda t: _p(lean=7 + 2 * sin(16 * t), shL=-18, elL=12, shR=86 + 7 * sin(16 * t), elR=4, hipL=-12, hipR=12),
    "think": lambda t: _p(lean=-3, shR=50, elR=125 + 4 * sin(2 * t)),
    "idea": lambda t: _p(lean=-6, shL=-16, elL=10, shR=162 + 4 * sin(6 * t), elR=6, dy=-12 * abs(sin(3 * t))),
    "shock": lambda t: _p(lean=-14, shL=55, elL=118, shR=62, elR=118, hipL=-12, hipR=12, dx=4 * sin(34 * t), dy=-10 * abs(sin(6 * t))),
    "facepalm": lambda t: _p(lean=10 + 2 * sin(2.5 * t), shL=-12, elL=10, shR=68, elR=122 + 3 * sin(5 * t)),
    "stress": lambda t: _p(lean=6 + 3 * sin(18 * t), shL=-125, elL=95, shR=125, elR=-95, hipL=-10, hipR=10, dx=5 * sin(30 * t)),
    "shrug": lambda t: _p(shL=-35, elL=-75 - 8 * sin(3 * t), shR=35, elR=75 + 8 * sin(3 * t), dy=-5 * max(0.0, sin(3 * t))),
    "celebrate": lambda t: _p(shL=-150, elL=-20, shR=150, elR=20, hipL=-12, hipR=12, dy=-60 * abs(sin(5 * t))),
    "clap": _clap,
    "laugh": lambda t: _p(lean=16 + 7 * sin(12 * t), shL=24, elL=75, shR=36, elR=88, hipL=-12, knL=-14, hipR=12, knR=-14, dy=12 - 12 * abs(sin(12 * t))),
    "cry": lambda t: _p(lean=18 + 3 * sin(5 * t), shL=58, elL=118 + 6 * sin(7 * t), shR=62, elR=118 - 6 * sin(7 * t)),
    "slump": lambda t: _p(lean=22 + 2 * sin(1.5 * t), shL=4, elL=4, shR=14, elR=4),
    "recoil": lambda t: _p(lean=-10, shL=-25, elL=20, shR=40, elR=110, dx=3 * sin(40 * t)),
    "wave": lambda t: _p(shR=150, elR=20 + 28 * sin(8 * t)),
    "dance": _dance,
    "flex": lambda t: _p(lean=-6, shL=-88, elL=-98 - 6 * sin(4 * t), shR=88, elR=98 + 6 * sin(4 * t), hipL=-16, hipR=16),
    "punch": _punch,
    "jump": _jump,
    "walk": lambda t: _stride(t, 1.6, 26, 4, 18),
    "run": lambda t: _stride(t, 3.0, 42, 12, 80),
    "sneak": lambda t: _p(**{**_stride(t, 1.0, 20, 18, 65), "dy": 26 - 6 * abs(sin(2 * math.pi * t))}),
    "lift": _lift,
    "scroll": lambda t: _p(lean=12, shL=34, elL=82, shR=38, elR=86 + 7 * sin(7 * t)),
    "work": lambda t: _p(lean=14, shL=42, elL=68 + 10 * sin(18 * t), shR=46, elR=68 - 10 * sin(18 * t), hipL=-75, knL=85, hipR=-65, knR=85, dy=SIT - 10),
    "read": lambda t: _p(lean=9 + 2 * sin(2 * t), shL=38, elL=76, shR=42, elR=76),
    "sleep": lambda t: _p(lean=24 + 2 * sin(1.4 * t), shL=-18, elL=12, shR=18, elR=20, hipL=-80, knL=150, hipR=70, knR=-145, dy=SIT + 6 * sin(1.4 * t)),
    "bow": lambda t: _p(lean=44 * (0.5 - 0.5 * math.cos(min(t, 1.6) * math.pi / 1.6)), shL=-8, elL=6, shR=8, elR=6),
    "meditate": lambda t: _p(shL=-28, elL=4, shR=28, elR=-4, hipL=-80, knL=165, hipR=80, knR=-165, dy=SIT + 4 * sin(1.6 * t)),
}
_FACES = {
    "angry": "angry",
    "punch": "angry",
    "celebrate": "happy",
    "wave": "happy",
    "clap": "happy",
    "dance": "happy",
    "jump": "happy",
    "idea": "happy",
    "present": "happy",
    "laugh": "laugh",
    "flex": "smug",
    "sneak": "smug",
    "shock": "surprised",
    "recoil": "surprised",
    "stress": "worried",
    "slump": "sad",
    "cry": "sad",
    "facepalm": "closed",
    "sleep": "closed",
    "bow": "closed",
    "meditate": "closed",
}
_THOUGHT = {"think", "meditate", "idea", "sleep"}   # these get a thought bubble instead of a speech bubble
_MOVING = {"walk": 260, "run": 620, "sneak": 140}   # ground speed (px/s) shown under a figure that strides on the spot

# Stick figure character variants: Classic (default), Lady (dress + ponytail), Kid (smaller + hair tuft), Kid Girl (smaller + dress + pigtails).
CHARACTERS: Dict[str, str] = {
    "default": "Classic",
    "lady": "Lady",
    "kid": "Kid",
    "girl": "Kid (girl)",
}
_MODEL_KIND_TO_CHARACTER = {
    "man": "default",
    "woman": "lady",
    "boy": "kid",
    "girl": "girl",
}


def _mix(a: Pose, b: Pose, k: float) -> Pose:
    return {j: a[j] + (b[j] - a[j]) * k for j in STAND}


# ------------------------------------------------------------- STORYBOARD
_ROTATION = ("talk", "point", "shrug", "think", "present")
# First rule with a word in the scene text wins: (words, action, bubble).
_RULES: Tuple[Tuple[set, str, Optional[str]], ...] = (
    ({"gym", "lift", "lifting", "muscle", "muscles", "weight", "weights", "workout", "barbell", "deadlift", "squat", "strength"}, "lift", None),
    ({"flex", "strong", "stronger", "alpha", "beast", "physique", "built"}, "flex", None),
    ({"punch", "fight", "boxing", "hit", "strike", "attack", "battle", "warrior"}, "punch", None),
    ({"run", "running", "fast", "faster", "speed", "chase", "race", "escape"}, "run", None),
    ({"sneak", "secretly", "quietly", "spy", "hidden", "stealth", "creep"}, "sneak", None),
    ({"sleep", "sleeping", "nap", "insomnia", "dream", "bed", "midnight", "exhausted"}, "sleep", None),
    ({"calm", "still", "stillness", "peace", "breathe", "meditate", "meditation", "silence", "patience"}, "meditate", None),
    ({"phone", "scroll", "scrolling", "doomscroll", "tiktok", "instagram", "app", "screen", "notification"}, "scroll", None),
    ({"code", "coding", "laptop", "computer", "office", "job", "work", "working", "hustle", "desk", "email"}, "work", None),
    ({"book", "books", "read", "reading", "study", "studying", "learn", "research", "library"}, "read", None),
    ({"idea", "eureka", "discover", "discovery", "breakthrough", "realize", "realized", "genius", "hack", "trick"}, "idea", "!"),
    ({"shocking", "shocked", "insane", "crazy", "unbelievable", "mind", "blew", "jaw", "wtf", "impossible"}, "shock", "?!"),
    ({"mistake", "wrong", "stupid", "dumb", "regret", "oops", "embarrassing", "amateur"}, "facepalm", None),
    ({"stress", "stressed", "anxiety", "anxious", "panic", "overwhelmed", "burnout", "overthinking"}, "stress", None),
    ({"funny", "laugh", "laughing", "hilarious", "joke", "comedy", "humor", "ridiculous"}, "laugh", "HA"),
    ({"cry", "crying", "tears", "heartbreak", "depressed", "grief"}, "cry", None),
    ({"dance", "dancing", "party", "vibe", "music", "groove", "fun"}, "dance", None),
    ({"clap", "applause", "bravo", "respect", "props", "congrats"}, "clap", None),
    ({"jump", "leap", "skyrocket", "soar", "explode", "boost"}, "jump", None),
    ({"thank", "thanks", "grateful", "humble", "honor", "respectful"}, "bow", None),
    ({"money", "cash", "rich", "wealth", "dollar", "dollars", "income", "profit", "millionaire", "billionaire"}, "celebrate", "$"),
    ({"win", "winner", "success", "victory", "achieve", "champion", "unstoppable"}, "celebrate", None),
    ({"fear", "afraid", "danger", "dangerous", "scary", "terrifying", "horror", "ghost", "death"}, "recoil", "!"),
    ({"sad", "fail", "failure", "lose", "lost", "tired", "weak", "lonely", "pain", "broke"}, "slump", None),
    ({"angry", "anger", "offended", "hate", "rage", "disrespect"}, "angry", "!!"),
    ({"stop", "never", "don't", "avoid", "quit", "warning", "rule"}, "point", "!"),
    ({"follow", "subscribe", "share", "comment"}, "wave", None),
    ({"why", "how", "what", "question", "wonder", "secret", "mystery", "think", "brain", "imagine"}, "think", "?"),
)


def storyboard_for(text: str, index: int = 0, stick: Optional[Dict] = None) -> Dict[str, Optional[str]]:
    """What to draw for a scene: the script's own storyboard when it has one, else keywords in its text.
    Always returns a plan the renderer can draw (an unknown action never raises)."""
    plan: Dict[str, Optional[str]] = {
        "action": _ROTATION[index % len(_ROTATION)],
        "other": None,
        "character": None,
        "other_character": None,
        "bubble": None,
        "other_bubble": None,
        "headline": None,
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
    if plan["character"] not in CHARACTERS:
        plan["character"] = None
    if plan["other_character"] not in CHARACTERS:
        plan["other_character"] = None
    return plan


# ---------------------------------------------------------------- DRAWING
def _figure(
    d: ImageDraw.ImageDraw,
    x: float,
    pose: Pose,
    facing: int,
    face: str = "neutral",
    character: str = "default",
    has_photo_head: bool = False,
) -> Dict[str, object]:
    """Draw one figure standing at x on the ground line. Returns where its head, head radius, and hands ended up."""
    is_kid = character in ("kid", "girl")
    has_dress = character in ("lady", "girl")
    bs = 0.72 if is_kid else 1.0   # body limb scale (kids are shorter, anchored cleanly on GROUND)
    hs = 0.88 if is_kid else 1.0   # head scale (slightly larger head-to-body ratio for kids)
    head_r = HEAD * hs

    def limb_end(start, angle, length):
        a = math.radians(angle)
        return (start[0] + facing * math.sin(a) * length, start[1] + math.cos(a) * length)

    w = int(15 * FIG * (0.88 if is_kid else 1.0) * S)

    def line(points, colour=INK, width=w):
        pts = [(px * S, py * S) for px, py in points]
        d.line(pts, fill=colour, width=width, joint="curve")
        for px, py in pts:  # round ends and joints
            d.ellipse([px - width / 2, py - width / 2, px + width / 2, py + width / 2], fill=colour)

    hip = (x + pose["dx"] * bs, GROUND - LEGS * bs + pose["dy"] * bs)
    neck = limb_end(hip, 180 - pose["lean"], TORSO * bs)
    head = limb_end(neck, 180 - pose["lean"], head_r + 6 * FIG * bs)
    hands = {}
    for side in "LR":
        knee = limb_end(hip, pose["hip" + side], THIGH * bs)
        line([hip, knee, limb_end(knee, pose["hip" + side] + pose["kn" + side], SHIN * bs)])
    line([hip, neck])

    # A-line dress/skirt for lady and kid-girl variants (drawn over lower torso, aligned with torso lean)
    if has_dress:
        waist = (hip[0] + (neck[0] - hip[0]) * 0.48, hip[1] + (neck[1] - hip[1]) * 0.48)
        hem_center = limb_end(waist, pose["lean"], TORSO * bs * 0.62)
        hem_half = (66 if character == "lady" else 56) * bs
        waist_half = 16 * bs
        dress_pts = [
            ((waist[0] - waist_half) * S, waist[1] * S),
            ((waist[0] + waist_half) * S, waist[1] * S),
            ((hem_center[0] + hem_half) * S, hem_center[1] * S),
            ((hem_center[0] - hem_half) * S, hem_center[1] * S),
        ]
        d.polygon(dress_pts, fill=PAPER, outline=INK, width=int(w * 0.8))
        # Subtle accent hem stripe
        d.line([dress_pts[3], dress_pts[2]], fill=ACCENT, width=int(w * 0.55))

    for side in "LR":
        elbow = limb_end(neck, pose["sh" + side], UPPER_ARM * bs)
        hands[side] = limb_end(elbow, pose["sh" + side] + pose["el" + side], FOREARM * bs)
        line([neck, elbow, hands[side]])

    # Hair silhouettes behind/around the head (skipped if a photo face covers the head)
    if not has_photo_head:
        if character == "lady":
            # Flowing ponytail at back of head + accent hair tie
            tie = (head[0] - facing * (head_r * 0.92), head[1] - head_r * 0.15)
            tail_mid = (tie[0] - facing * 42, tie[1] + 28)
            tail_end = (tie[0] - facing * 30, tie[1] + 92)
            line([tie, tail_mid, tail_end], colour=INK, width=int(w * 1.15))
            d.ellipse([(tie[0] - 11) * S, (tie[1] - 11) * S, (tie[0] + 11) * S, (tie[1] + 11) * S], fill=ACCENT)
        elif character == "girl":
            # Two cute side pigtails with accent ribbons
            for side_dir in (-1, 1):
                tie = (head[0] + side_dir * (head_r * 0.92), head[1] - head_r * 0.18)
                tip = (tie[0] + side_dir * 32, tie[1] + 54)
                line([tie, tip], colour=INK, width=int(w * 1.05))
                d.ellipse([(tie[0] - 10) * S, (tie[1] - 10) * S, (tie[0] + 10) * S, (tie[1] + 10) * S], fill=ACCENT)
        elif character == "kid":
            # Playful spiky hair tuft on top of head
            top_x, top_y = head[0], head[1] - head_r + 2
            line([(top_x - 16, top_y + 4), (top_x - 24, top_y - 24)], colour=INK, width=int(w * 0.7))
            line([(top_x, top_y), (top_x + facing * 6, top_y - 30)], colour=INK, width=int(w * 0.7))
            line([(top_x + 16, top_y + 4), (top_x + 28, top_y - 20)], colour=INK, width=int(w * 0.7))

    d.ellipse([(head[0] - head_r) * S, (head[1] - head_r) * S, (head[0] + head_r) * S, (head[1] + head_r) * S], fill=PAPER, outline=INK, width=w)
    thin = int(7 * FIG * hs * S)
    for near, off in ((False, 8), (True, 36)):  # both eyes sit toward the side the figure faces
        ex, ey, r = head[0] + facing * off * FIG * hs, head[1] - 6 * FIG * hs, 6 * FIG * hs
        if face == "closed":
            line([(ex - r * 1.6, ey), (ex + r * 1.6, ey)], width=thin)
        elif face == "laugh":
            line([(ex - r * 1.5, ey + r * 0.4), (ex, ey - r * 1.1), (ex + r * 1.5, ey + r * 0.4)], width=thin)
        elif face == "surprised":
            d.ellipse([(ex - r * 1.35) * S, (ey - r * 1.35) * S, (ex + r * 1.35) * S, (ey + r * 1.35) * S], fill=INK)
        else:
            d.ellipse([(ex - r) * S, (ey - r) * S, (ex + r) * S, (ey + r) * S], fill=INK)
        if character == "lady" and face != "closed" and not has_photo_head:
            # Subtle outer eyelash flick
            lash_dir = facing if near else -facing
            line([(ex + lash_dir * r * 0.9, ey - r * 0.6), (ex + lash_dir * r * 1.8, ey - r * 1.4)], width=max(2, int(thin * 0.7)))
        if face == "angry":  # brows slanting down toward the middle
            inner, outer = (-facing if near else facing) * 13 * FIG * hs, (facing if near else -facing) * 13 * FIG * hs
            line([(ex + outer, ey - 25 * FIG * hs), (ex + inner, ey - 15 * FIG * hs)], width=thin)
        elif face == "worried":  # brows slanting up toward the middle
            inner, outer = (-facing if near else facing) * 13 * FIG * hs, (facing if near else -facing) * 13 * FIG * hs
            line([(ex + outer, ey - 15 * FIG * hs), (ex + inner, ey - 25 * FIG * hs)], width=thin)
        elif face == "smug" and near:  # one confident raised brow
            line([(ex - 11 * FIG * hs, ey - 22 * FIG * hs), (ex + 11 * FIG * hs, ey - 26 * FIG * hs)], width=thin)

    mx, my = head[0] + facing * 22 * FIG * hs, head[1] + 20 * FIG * hs
    if face in ("happy", "sad"):
        my = head[1] + (16 if face == "happy" else 30) * FIG * hs
        box = [(mx - 20 * FIG * hs) * S, (my - 12 * FIG * hs) * S, (mx + 20 * FIG * hs) * S, (my + 12 * FIG * hs) * S]
        start, end = (20, 160) if face == "happy" else (200, 340)  # lower half of the box smiles, upper half frowns
        d.arc(box, start, end, fill=INK, width=thin)
    elif face == "laugh":
        box = [(mx - 20 * FIG * hs) * S, (my - 10 * FIG * hs) * S, (mx + 20 * FIG * hs) * S, (my + 16 * FIG * hs) * S]
        d.chord(box, 0, 180, fill=INK, outline=INK, width=thin)
    elif face == "surprised":
        or_ = 11 * FIG * hs
        d.ellipse([(mx - or_) * S, (my - or_ + 4 * FIG * hs) * S, (mx + or_) * S, (my + or_ + 4 * FIG * hs) * S], fill=PAPER, outline=INK, width=thin)
    elif face == "smug":
        line([(mx - 14 * FIG * hs, my + 4 * FIG * hs), (mx + 6 * FIG * hs, my + 4 * FIG * hs), (mx + 18 * FIG * hs, my - 4 * FIG * hs)], width=thin)
    elif face == "worried":
        line([
            (mx - 16 * FIG * hs, my + 6 * FIG * hs), (mx - 6 * FIG * hs, my),
            (mx + 6 * FIG * hs, my + 6 * FIG * hs), (mx + 16 * FIG * hs, my),
        ], width=thin)
    return {"head": head, "head_r": head_r, "L": hands["L"], "R": hands["R"]}


def _barbell(d: ImageDraw.ImageDraw, hands: Dict[str, Tuple[float, float]]) -> None:
    (lx, ly), (rx, ry) = sorted((hands["L"], hands["R"]))
    y = (ly + ry) / 2
    d.line([((lx - 95) * S, y * S), ((rx + 95) * S, y * S)], fill=INK, width=int(12 * S))
    for end in (lx - 95, rx + 95):
        d.rounded_rectangle([(end - 16) * S, (y - 62) * S, (end + 16) * S, (y + 62) * S], radius=int(10 * S), fill=ACCENT)


def _action_extras(d: ImageDraw.ImageDraw, action: str, parts: Dict[str, Tuple[float, float]], facing: int, t: float) -> None:
    """Draw expressive comic props and motion accents (lightbulb, shock lines, phone, laptop, book, Zzz, tears)."""
    hx, hy = parts["head"]
    thin = int(7 * S)
    gold = (242, 186, 46)
    tear_blue = (72, 154, 224)

    if action == "lift":
        _barbell(d, parts)
    elif action == "idea":
        # Glowing comic lightbulb popping above head with pulsing rays
        bx, by = hx, hy - HEAD - 105
        r = 34 * (0.94 + 0.08 * sin(8 * t))
        d.ellipse([(bx - r) * S, (by - r) * S, (bx + r) * S, (by + r) * S], fill=gold, outline=INK, width=thin)
        d.rounded_rectangle([(bx - 14) * S, (by + r - 4) * S, (bx + 14) * S, (by + r + 18) * S], radius=int(5 * S), fill=INK)
        for deg in (-140, -100, -60, -20, 200, 220):
            a = math.radians(deg)
            r1, r2 = r + 14, r + 32 + 6 * sin(10 * t)
            d.line([((bx + math.cos(a) * r1) * S, (by + math.sin(a) * r1) * S),
                    ((bx + math.cos(a) * r2) * S, (by + math.sin(a) * r2) * S)], fill=gold, width=int(8 * S))
    elif action == "shock":
        # Radiating comic shock bursts around head
        for deg in (-150, -120, -60, -30, 10, 170):
            a = math.radians(deg)
            pulse = 10 * sin(20 * t + deg)
            r1, r2 = HEAD + 28, HEAD + 68 + pulse
            d.line([((hx + math.cos(a) * r1) * S, (hy + math.sin(a) * r1) * S),
                    ((hx + math.cos(a) * r2) * S, (hy + math.sin(a) * r2) * S)], fill=ACCENT, width=int(8 * S))
    elif action == "stress":
        # Scribbly dark stress cloud + animated sweat drop
        cx, cy = hx, hy - HEAD - 70
        pts = [((cx + 38 * math.cos(k * 1.4 + t * 5)) * S, (cy + 18 * math.sin(k * 2.1 + t * 4)) * S) for k in range(10)]
        d.line(pts, fill=ACCENT, width=int(6 * S), joint="curve")
        dy = (t * 65) % 42
        sx, sy = hx - facing * (HEAD + 24), hy - 18 + dy
        d.ellipse([(sx - 9) * S, (sy - 13) * S, (sx + 9) * S, (sy + 13) * S], fill=tear_blue, outline=INK, width=int(4 * S))
    elif action == "cry":
        # Falling teardrops from eyes
        for offset in (0.0, 0.5):
            drop = ((t * 1.8 + offset) % 1.0)
            tx = hx + facing * (20 + 18 * offset)
            ty = hy + 10 + drop * 110
            d.ellipse([(tx - 8) * S, (ty - 12) * S, (tx + 8) * S, (ty + 12) * S], fill=tear_blue, outline=INK, width=int(3 * S))
    elif action == "sleep":
        # Floating rising "Z z z"
        for i, ch in enumerate(("z", "Z", "Z")):
            phase = (t * 0.65 + i * 0.33) % 1.0
            zx = hx + facing * (40 + phase * 85)
            zy = hy - HEAD - 20 - phase * 145
            sz = int((34 + i * 10) * S)
            d.text((zx * S, zy * S), ch, font=_font(sz), fill=INK, anchor="mm")
    elif action == "dance":
        # Floating musical notes
        for i, dx_off in enumerate((-115, 115)):
            bob = 18 * sin(6 * t + i * 2.0)
            nx, ny = hx + dx_off, hy - HEAD - 45 + bob
            d.text((nx * S, ny * S), "♪", font=_font(int(64 * S)), fill=ACCENT if i else INK, anchor="mm")
    elif action == "clap":
        # Little impact sparks when hands come together
        if sin(14 * t) > 0.65:
            mx = (parts["L"][0] + parts["R"][0]) / 2
            my = (parts["L"][1] + parts["R"][1]) / 2
            for deg in (-60, 0, 60, 120, 180, 240):
                a = math.radians(deg)
                d.line([((mx + math.cos(a) * 20) * S, (my + math.sin(a) * 20) * S),
                        ((mx + math.cos(a) * 44) * S, (my + math.sin(a) * 44) * S)], fill=ACCENT, width=int(6 * S))
    elif action == "punch":
        # Comic impact burst at the extended fist on jab peak
        if sin(9 * t) > 0.72:
            fx, fy = parts["R"]
            for deg in range(0, 360, 45):
                a = math.radians(deg)
                r2 = 52 if deg % 90 == 0 else 32
                d.line([((fx + math.cos(a) * 14) * S, (fy + math.sin(a) * 14) * S),
                        ((fx + math.cos(a) * r2) * S, (fy + math.sin(a) * r2) * S)], fill=ACCENT, width=int(7 * S))
    elif action == "scroll":
        # Smartphone in hands with subtle screen glow lines
        px, py = parts["R"]
        d.rounded_rectangle([(px - 14) * S, (py - 32) * S, (px + 14) * S, (py + 22) * S], radius=int(6 * S), fill=INK)
        d.rectangle([(px - 9) * S, (py - 24) * S, (px + 9) * S, (py + 12) * S], fill=(210, 232, 255))
    elif action == "work":
        # Minimal desk + open laptop in front of seated figure
        rx, ry = parts["R"]
        desk_y = ry + 18
        d.line([((rx - 45) * S, desk_y * S), ((rx + 120) * S, desk_y * S)], fill=INK, width=int(10 * S))
        d.line([((rx + 85) * S, desk_y * S), ((rx + 85) * S, GROUND * S)], fill=INK, width=int(10 * S))
        # Laptop base + angled screen
        d.line([((rx - 5) * S, (desk_y - 6) * S), ((rx + 65) * S, (desk_y - 6) * S)], fill=INK, width=int(9 * S))
        d.line([((rx + 60) * S, (desk_y - 6) * S), ((rx + 82) * S, (desk_y - 68) * S)], fill=ACCENT, width=int(9 * S))
    elif action == "read":
        # Open book held in hands
        rx, ry = parts["R"]
        d.polygon([((rx - 32) * S, (ry - 24) * S), (rx * S, (ry - 10) * S), (rx * S, (ry + 28) * S), ((rx - 32) * S, (ry + 16) * S)], fill=PAPER, outline=INK, width=int(6 * S))
        d.polygon([((rx + 32) * S, (ry - 24) * S), (rx * S, (ry - 10) * S), (rx * S, (ry + 28) * S), ((rx + 32) * S, (ry + 16) * S)], fill=PAPER, outline=INK, width=int(6 * S))
    elif action == "angry":
        # Comic anger cross-vein near forehead
        vx, vy = hx - facing * (HEAD - 8), hy - HEAD + 12
        pulse = 1.0 + 0.15 * sin(18 * t)
        s_ = 12 * pulse
        d.line([((vx - s_) * S, (vy - s_) * S), ((vx - 3) * S, (vy - 3) * S)], fill=ACCENT, width=int(5 * S))
        d.line([((vx + s_) * S, (vy - s_) * S), ((vx + 3) * S, (vy - 3) * S)], fill=ACCENT, width=int(5 * S))
        d.line([((vx - s_) * S, (vy + s_) * S), ((vx - 3) * S, (vy + 3) * S)], fill=ACCENT, width=int(5 * S))
        d.line([((vx + s_) * S, (vy + s_) * S), ((vx + 3) * S, (vy + 3) * S)], fill=ACCENT, width=int(5 * S))


def _fit(text: str, size: int, room: float, smallest: int, script: str = "en") -> int:
    """The largest font size (stepping down from size) at which text is no wider than room."""
    while size > smallest and _font(size * S, script).getlength(text) > room * S:
        size -= 6
    return size


def _bubble(d: ImageDraw.ImageDraw, cx: float, cy: float, k: float, text: str, head: Tuple[float, float], thought: bool, colour) -> None:
    if k <= 0.03:
        return
    script = _detect_script(text)
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
    size = _fit(text, 150, 240, 44, script)
    d.text((cx * S, cy * S), text, font=_font(max(1, int(size * k * S)), script), fill=colour, anchor="mm")


def _headline(d: ImageDraw.ImageDraw, text: str, t: float, start: float) -> None:
    script = _detect_script(text)
    display = text if script in ("hi", "ja", "ar") else text.upper()
    words = display.split()[:6]
    if not words:
        return
    size = _fit(" ".join(words), 120, 940, 54, script)
    font = _font(size * S, script)
    x = (W * S - font.getlength(" ".join(words))) / 2
    for i, word in enumerate(words):  # the words pop in one after another; the last one is the accent
        k = _pop((t - start - i * 0.22) / 0.3)
        width = font.getlength(word)
        if k > 0.03:
            d.text((x + width / 2, 215 * S), word, font=_font(max(1, int(size * k * S)), script), fill=ACCENT if i == len(words) - 1 and len(words) > 1 else INK, anchor="mm")
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
    main_char = plan.get("character") or "default"
    other_char = plan.get("other_character") or "default"
    main = _figure(
        d, x, pose, 1,
        _FACES.get(action, "neutral") if blend > 0.5 else "neutral",
        character=main_char,
        has_photo_head=cast.get("main") is not None,
    )
    if cast.get("main") is not None:
        _photo_head(img, d, main["head"], cast["main"])
    if blend > 0.5:
        _action_extras(d, action, main, 1, max(t - main_start, 0.0))

    if other:
        k_other = _smooth((t - first) / 0.35) * (1 - _smooth((t - main_start + 0.3) / 0.35))
        second = _figure(
            d, 790, _mix(_p(dy=-breathe), ACTIONS[other](max(t - first, 0.0)), k_other), -1,
            _FACES.get(other, "neutral") if k_other > 0.5 else "neutral",
            character=other_char,
            has_photo_head=cast.get("other") is not None,
        )
        if cast.get("other") is not None:
            _photo_head(img, d, second["head"], cast["other"])
        if k_other > 0.5:
            _action_extras(d, other, second, -1, max(t - first, 0.0))
        if plan["other_bubble"]:
            k = _pop((t - first - 0.2) / 0.35) * (1 - _smooth((t - main_start + 0.3) / 0.3))
            _bubble(d, 750, second["head"][1] - 235, k, plan["other_bubble"], second["head"], other in _THOUGHT, ACCENT)
    if plan["bubble"]:
        _bubble(d, main_x + (30 if other else 190), main["head"][1] - 235, _pop((t - main_start - 0.35) / 0.35),
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
    # If the storyboard didn't pick a character variant, match the featured model's kind (woman -> lady, boy -> kid, girl -> girl).
    if not plan["character"] and people:
        plan["character"] = _MODEL_KIND_TO_CHARACTER.get(people[0].kind, "default")
    if not plan["other_character"] and len(people) > 1:
        plan["other_character"] = _MODEL_KIND_TO_CHARACTER.get(people[1].kind, "default")
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
