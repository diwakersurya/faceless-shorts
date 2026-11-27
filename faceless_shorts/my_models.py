"""
Your models: a person (man, woman, boy, girl) or an object made from one photo and kept on this
computer, so the same face or thing can appear in the scenes of any short.

Each model is a folder in library_dir(): photo.jpg (the photo, upright, at most PHOTO_SIDE pixels)
and model.json (name, kind, description). Engines that can look at a picture (PHOTO_IMAGE_ENGINES,
PHOTO_VIDEO_ENGINES) get the photo itself; every other engine gets the description as words.
"""

import json
import os
import re
import shutil
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Dict, List, Optional, Sequence

from PIL import Image, ImageDraw, ImageOps, UnidentifiedImageError

from faceless_shorts.config import MODEL_KINDS, PEOPLE_KINDS

PHOTO_SIDE = 1024           # the photo is kept at most this many pixels on its long side
MAX_FEATURED = 3            # models in one short: more than this and image models start to mix them up
MAX_NAME, MAX_DESCRIPTION = 40, 400
ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")


def library_dir() -> str:
    """Where the models are kept (override: FACELESS_MY_MODELS_DIR). Outside the repository: these are
    photos of people, so they never end up in a commit."""
    return os.environ.get("FACELESS_MY_MODELS_DIR") or os.path.join(
        os.path.expanduser("~"), ".config", "faceless-shorts", "my-models"
    )


@dataclass
class Model:
    id: str
    name: str
    kind: str
    description: str
    created: float

    @property
    def folder(self) -> str:
        return os.path.join(library_dir(), self.id)

    @property
    def photo(self) -> str:
        return os.path.join(self.folder, "photo.jpg")

    @property
    def is_person(self) -> bool:
        return self.kind in PEOPLE_KINDS

    def words(self) -> str:
        """The model as words, for an engine that cannot look at the photo: "a man (curly black hair, ...)"."""
        return f"{MODEL_KINDS[self.kind]} ({self.description})" if self.description else MODEL_KINDS[self.kind]

    def to_json(self) -> Dict:
        return {"id": self.id, "name": self.name, "kind": self.kind, "description": self.description, "created": self.created}


def _read(folder: str) -> Optional[Model]:
    try:
        with open(os.path.join(folder, "model.json"), "r", encoding="utf-8") as handle:
            data = json.load(handle)
        model = Model(
            id=os.path.basename(folder),
            name=str(data["name"]),
            kind=str(data["kind"]) if data.get("kind") in MODEL_KINDS else "object",
            description=str(data.get("description") or ""),
            created=float(data.get("created") or 0),
        )
        return model if os.path.exists(model.photo) else None
    except (OSError, ValueError, KeyError, TypeError):
        return None  # a folder that is not a model (or half written) is skipped


def _write(model: Model) -> None:
    data = {"name": model.name, "kind": model.kind, "description": model.description, "created": model.created}
    with open(os.path.join(model.folder, "model.json"), "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def all_models() -> List[Model]:
    """Every model, oldest first."""
    root = library_dir()
    if not os.path.isdir(root):
        return []
    found = [_read(os.path.join(root, name)) for name in os.listdir(root) if ID_PATTERN.match(name)]
    return sorted((m for m in found if m), key=lambda m: (m.created, m.id))


def get(name_or_id: str) -> Optional[Model]:
    """A model by its id or its name (any case)."""
    wanted = (name_or_id or "").strip().lower()
    return next((m for m in all_models() if wanted in (m.id, m.name.lower())), None)


def resolve(names: Sequence[str]) -> List[Model]:
    """The models named (ids or names), in that order, without repeats. Raises ValueError naming any that do not exist."""
    models: List[Model] = []
    for name in (n.strip() for n in names if n and n.strip()):
        model = get(name)
        if model is None:
            known = ", ".join(m.name for m in all_models()) or "none yet: add one with 'models add'"
            raise ValueError(f"There is no model called '{name}'. Your models: {known}.")
        if model not in models:
            models.append(model)
    if len(models) > MAX_FEATURED:
        raise ValueError(f"A short can feature at most {MAX_FEATURED} models; {len(models)} were given.")
    return models


def _clean_name(name: str) -> str:
    name = " ".join((name or "").split())
    if not name:
        raise ValueError("Give the model a name.")
    if len(name) > MAX_NAME:
        raise ValueError(f"A model's name has at most {MAX_NAME} characters.")
    return name


def _check(name: str, kind: str, description: str, keep: Optional[str] = None) -> None:
    if kind not in MODEL_KINDS:
        raise ValueError(f"The kind must be one of: {', '.join(MODEL_KINDS)}.")
    if len(description) > MAX_DESCRIPTION:
        raise ValueError(f"A description has at most {MAX_DESCRIPTION} characters.")
    if any(m.name.lower() == name.lower() and m.id != keep for m in all_models()):
        raise ValueError(f"There is a model called '{name}' already. Choose another name.")


def add(name: str, kind: str, image_path: str, description: str = "") -> Model:
    """Make a model from a photo. With no description, Gemini writes one from the photo when a key is
    saved (free); otherwise the model has none and engines that cannot see the photo only get its kind."""
    name, description = _clean_name(name), " ".join((description or "").split())
    _check(name, kind, description)
    try:
        with Image.open(image_path) as image:
            photo = ImageOps.exif_transpose(image).convert("RGB")  # phones store the rotation separately
    except (OSError, UnidentifiedImageError):
        raise ValueError("That file is not a picture this tool can read. Use a JPEG, PNG or WebP photo.")
    photo.thumbnail((PHOTO_SIDE, PHOTO_SIDE), Image.LANCZOS)

    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:32].strip("-") or "model"
    taken = {m.id for m in all_models()} | set(os.listdir(library_dir()) if os.path.isdir(library_dir()) else [])
    model_id, n = base, 2
    while model_id in taken:
        model_id, n = f"{base}-{n}", n + 1
    model = Model(id=model_id, name=name, kind=kind, description=description, created=time.time())
    os.makedirs(model.folder, exist_ok=True)
    try:
        photo.save(model.photo, quality=92)
        if not model.description:
            model.description = describe(model.photo, kind)
        _write(model)
    except BaseException:
        shutil.rmtree(model.folder, ignore_errors=True)
        raise
    return model


def edit(name_or_id: str, name: Optional[str] = None, kind: Optional[str] = None, description: Optional[str] = None) -> Model:
    """Change a model's name, kind or description. Its id, and so every script that names it by id, stays."""
    model = get(name_or_id)
    if model is None:
        raise ValueError(f"There is no model called '{name_or_id}'.")
    if name is not None:
        model.name = _clean_name(name)
    if kind is not None:
        model.kind = kind
    if description is not None:
        model.description = " ".join(description.split())
    _check(model.name, model.kind, model.description, keep=model.id)
    _write(model)
    _head.cache_clear()
    return model


def remove(name_or_id: str) -> Model:
    model = get(name_or_id)
    if model is None:
        raise ValueError(f"There is no model called '{name_or_id}'.")
    shutil.rmtree(model.folder)
    _head.cache_clear()
    return model


def describe(photo: str, kind: str) -> str:
    """One line describing the model in the photo, written by Gemini (free limits). Empty without a key or an answer."""
    from faceless_shorts.integrations import GeminiClient

    if not GeminiClient.available():
        return ""
    what = (
        "apparent age, build, skin tone, face shape, hair (colour, length, style), facial hair, glasses, and the "
        "clothes with their colours" if kind in PEOPLE_KINDS else
        "what it is, its shape, colours, materials and any distinctive marks or details"
    )
    text = GeminiClient().look(
        photo,
        f"Describe the {kind} in this photo so an image generator can draw the same {kind} again in other scenes. "
        f"Cover {what}. Reply with one line of at most 45 words: no name, no background, no opinions, no lead-in.",
    )
    return " ".join((text or "").split())[:MAX_DESCRIPTION].rstrip(".")


def with_models(prompt: str, models: Sequence[Model], photos: bool) -> str:
    """The scene prompt with the models in it. photos=True: the engine gets each model's photo, in this
    order, as a reference image, so the prompt points at them; otherwise the models are described in words."""
    if not models:
        return prompt
    if not photos:
        return f"{prompt}. Featuring {' and '.join(m.words() for m in models)}"
    parts = []
    for n, m in enumerate(models, 1):
        keep = "face, hair, build and clothes" if m.is_person else "shape, colours and details"
        parts.append(f"the {m.kind} from reference image {n} ({m.description or MODEL_KINDS[m.kind]}), with the same {keep} as in that image")
    return f"{prompt}. Featuring {' and '.join(parts)}. Only the reference subjects are kept; the setting comes from this description"


@lru_cache(maxsize=16)
def _head(photo: str, size: int) -> Image.Image:
    with Image.open(photo) as image:
        image = image.convert("RGB")
    w, h = image.size
    # ponytail: no face detection; a portrait photo has the face in the top middle. A photo framed
    # otherwise gets a poor crop; ask Gemini for the face box when describing if this matters.
    side = int(min(w, h) * (0.62 if h > w else 0.5))
    left, top = (w - side) // 2, int(h * 0.06) if h > w else (h - side) // 4
    face = image.crop((left, top, left + side, top + side)).resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, size - 1, size - 1], fill=255)
    face.putalpha(mask)
    return face


def head(model: Model, size: int) -> Image.Image:
    """A round crop of the face in a person's photo, size x size, transparent outside the circle."""
    return _head(model.photo, size)


@lru_cache(maxsize=16)
def _card(photo: str, side: int) -> Image.Image:
    with Image.open(photo) as image:
        card = ImageOps.fit(image.convert("RGB"), (side, side), Image.LANCZOS)
    return card


def card(model: Model, side: int) -> Image.Image:
    """The photo cut to a square, for an object shown in a stick-figure scene."""
    return _card(model.photo, side)
