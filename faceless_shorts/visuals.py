"""
Visuals Module for Faceless Shorts.
Handles free AI image generation (Pollinations Flux / local ComfyUI / offline procedural),
offline PIL art-style transforms (pixel art, chalkboard, glow sketch, halftone, ...),
artistic FFmpeg grading, and punchy dynamic Ken Burns motion video synthesis.
"""

import hashlib
import math
import os
import random
import shutil
import subprocess
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Sequence
from PIL import Image, ImageDraw, ImageFilter

from faceless_shorts.config import (
    CANVAS_WIDTH,
    CANVAS_HEIGHT,
    DEFAULT_FPS,
    ART_STYLES,
    DEFAULT_IMAGE_ENGINE,
    MFLUX_MODELS,
    PHOTO_IMAGE_ENGINES,
)
from faceless_shorts.my_models import Model, with_models
from faceless_shorts.style_processor import StyleProcessor
from faceless_shorts.vfx.theatre_easing import get_theatre_motion_expr


def _file_hash(path: str) -> str:
    """What a picture holds, for cache keys: a model whose photo is replaced must not reuse old results."""
    with open(path, "rb") as handle:
        return hashlib.sha1(handle.read()).hexdigest()[:16]

class VisualManager:
    def __init__(
        self,
        cache_dir: str = ".cache_visuals",
        art_style: str = "hyperreal",
        engine: str = DEFAULT_IMAGE_ENGINE,
        width: int = CANVAS_WIDTH,
        height: int = CANVAS_HEIGHT,
        fps: int = DEFAULT_FPS,
    ):
        self.cache_dir = cache_dir
        self.art_style = art_style
        self.engine = engine or DEFAULT_IMAGE_ENGINE
        self.width = width
        self.height = height
        self.fps = fps
        os.makedirs(self.cache_dir, exist_ok=True)

    # ------------------------------------------------------------------ IMAGES
    def _pollinations_url(self, prompt: str, model: str, seed: int) -> str:
        style_config = ART_STYLES.get(self.art_style, ART_STYLES["hyperreal"])
        enhanced_prompt = f"{prompt}, {style_config['prompt_suffix']}"
        encoded_prompt = urllib.parse.quote(enhanced_prompt)
        return (
            f"https://image.pollinations.ai/prompt/{encoded_prompt}"
            f"?width={self.width}&height={self.height}"
            f"&model={model}&seed={seed}&nologo=true"
        )

    def generate_ai_image(
        self,
        prompt: str,
        output_path: str,
        model: str = "flux",
        seed: Optional[int] = None,
        models: Sequence[Model] = (),
        fallback: bool = True,
    ) -> Optional[str]:
        """
        Generate a vertical AI image using the selected free engine:
          - pollinations: keyless cloud Flux generation
          - comfyui: local ComfyUI instance (private, GPU, free)
          - flux2-klein / z-image-turbo: local image model on Apple Silicon (private, free)
          - gemini: Google's online image model (paid per image)
          - procedural: offline Pillow canvas (zero network)
        Always degrades to the procedural canvas so rendering never fails.
        *models*: your models the scene shows. Engines in PHOTO_IMAGE_ENGINES get their photos; the
        others (and every fallback) get them described in the prompt.
        fallback=False: None when the chosen engine fails, instead of another engine's picture or the plain canvas.
        """
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        chosen_seed = seed if seed is not None else random.randint(1000, 999999)
        photos = [m.photo for m in models] if self.engine in PHOTO_IMAGE_ENGINES else []
        photo_prompt = with_models(prompt, models, photos=True) if photos else prompt
        prompt = with_models(prompt, models, photos=False)  # what every engine without the photos gets

        if self.engine in MFLUX_MODELS:
            from faceless_shorts.integrations import MfluxClient
            style_config = ART_STYLES.get(self.art_style, ART_STYLES["hyperreal"])
            full_prompt = f"{photo_prompt if photos else prompt}, {style_config['prompt_suffix']}"
            portrait = self.height >= self.width
            # Local mflux takes ~35s per image; cache by prompt/model/seed/photos so re-renders reuse them.
            key = "|".join([self.engine, full_prompt, str(portrait), str(chosen_seed), *map(_file_hash, photos)])
            cached = os.path.join(self.cache_dir, f"{self.engine}_{hashlib.sha1(key.encode()).hexdigest()[:16]}.jpg")
            if os.path.exists(cached):
                shutil.copyfile(cached, output_path)
                return output_path
            path = MfluxClient(self.engine).generate(
                prompt=full_prompt,
                output_path=cached,
                seed=chosen_seed,
                portrait=portrait,
                refs=photos,
            )
            if path and os.path.exists(cached):
                shutil.copyfile(cached, output_path)
                return output_path
            if not fallback:
                return None
            print("    [!] Local image model unavailable, falling back to Pollinations Flux.")

        if self.engine == "gemini":
            from faceless_shorts.integrations import GeminiClient
            client = GeminiClient()
            style_config = ART_STYLES.get(self.art_style, ART_STYLES["hyperreal"])
            full_prompt = f"{photo_prompt if photos else prompt}, {style_config['prompt_suffix']}"
            portrait = self.height >= self.width
            # Every image is paid for, so it is kept by what produced it and reused on the next render.
            key = "|".join([full_prompt, client.model("image"), str(portrait), *map(_file_hash, photos)])
            cached = os.path.join(self.cache_dir, f"gemini_{hashlib.sha1(key.encode()).hexdigest()[:16]}.jpg")
            if os.path.exists(cached) or client.generate_image(full_prompt, cached, portrait=portrait, refs=photos):
                shutil.copyfile(cached, output_path)
                return output_path
            if not fallback:
                return None
            print("    [!] Gemini image unavailable, falling back to Pollinations Flux.")

        if self.engine == "comfyui":
            try:
                from faceless_shorts.integrations import ComfyUIClient
                client = ComfyUIClient()
                path = client.generate(
                    prompt=prompt,
                    output_path=output_path,
                    width=self.width,
                    height=self.height,
                    seed=chosen_seed,
                )
                if path and os.path.exists(path):
                    with Image.open(path) as img:
                        img.verify()
                    return path
                print("    [!] ComfyUI unavailable, falling back to Pollinations Flux.")
            except Exception as e:
                print(f"    [!] ComfyUI engine failed ({e}), falling back to Pollinations Flux.")
            if not fallback:
                return None

        if self.engine != "procedural":
            style_config = ART_STYLES.get(self.art_style, ART_STYLES["hyperreal"])
            url = self._pollinations_url(prompt, model, chosen_seed)
            headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
            }
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    req = urllib.request.Request(url, headers=headers)
                    with urllib.request.urlopen(req, timeout=35) as response:
                        with open(output_path, "wb") as f:
                            f.write(response.read())

                    # Validate image file integrity
                    with Image.open(output_path) as img:
                        img.verify()

                    return output_path
                except Exception as e:
                    time.sleep(2)
                    if attempt == max_retries - 1:
                        print(f"    [!] Warning: Visual generation failed ({e}). Using sleek procedural canvas.")

        return self.generate_procedural_background(output_path, prompt) if fallback else None

    def generate_ai_images_parallel(
        self,
        jobs: List[Dict],
        max_workers: int = 4
    ) -> Dict[str, str]:
        """
        Generate several AI images concurrently (network-bound -> big wall-clock win).
        jobs: [{"prompt": str, "output_path": str}, ...] in scene order.
        Returns {output_path: path} (failed jobs fall back to procedural canvases).
        """
        def _one(job: Dict) -> Dict[str, str]:
            try:
                return {job["output_path"]: self.generate_ai_image(
                    prompt=job["prompt"],
                    output_path=job["output_path"],
                    seed=job.get("seed"),
                    models=job.get("models", ()),
                )}
            except Exception:
                return {job["output_path"]: self.generate_procedural_background(
                    output_path=job["output_path"], topic=job.get("prompt", ""))}

        if self.engine in MFLUX_MODELS:
            max_workers = 1  # one local model at a time: each copy would need its own memory

        results: Dict[str, str] = {}
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            for partial in pool.map(_one, jobs):
                results.update(partial)
        return results

    def generate_procedural_background(
        self,
        output_path: str,
        topic: str = "general"
    ) -> str:
        """
        Generate a moody abstract dark gradient background offline using Pillow.
        Zero dependencies on external APIs, ensuring 100% offline reliability.
        """
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        img = Image.new("RGB", (self.width, self.height), color=(12, 12, 18))
        draw = ImageDraw.Draw(img)

        # Draw deep moody vertical gradient
        color_top = (8, 8, 14)
        color_bottom = (26, 20, 38)

        for y in range(self.height):
            ratio = y / self.height
            r = int(color_top[0] * (1 - ratio) + color_bottom[0] * ratio)
            g = int(color_top[1] * (1 - ratio) + color_bottom[1] * ratio)
            b = int(color_top[2] * (1 - ratio) + color_bottom[2] * ratio)
            draw.line([(0, y), (self.width, y)], fill=(r, g, b))

        # Add ambient glowing sphere
        glow = Image.new("RGBA", (self.width, self.height), (0, 0, 0, 0))
        glow_draw = ImageDraw.Draw(glow)
        glow_draw.ellipse(
            [(self.width // 4, self.height // 3), (self.width * 3 // 4, self.height * 2 // 3)],
            fill=(65, 45, 95, 90)
        )
        glow = glow.filter(ImageFilter.GaussianBlur(120))
        img.paste(glow, (0, 0), glow)

        img.save(output_path, quality=95)
        return output_path

    def apply_art_style(self, image_path: str, output_path: str) -> str:
        """
        Apply the offline PIL pixel-op stage of the current art style
        (pixel art, chalkboard, glow sketch, halftone, flat vector, ...).
        Styles without a pixel_op pass through untouched.
        """
        style_config = ART_STYLES.get(self.art_style, ART_STYLES["hyperreal"])
        pixel_op = style_config.get("pixel_op")
        if not pixel_op:
            return image_path
        try:
            return StyleProcessor.apply(
                image_path=image_path,
                output_path=output_path,
                op=pixel_op,
                params=style_config.get("pixel_op_params"),
            )
        except Exception as e:
            print(f"    [!] Style transform '{pixel_op}' failed ({e}). Using raw visual.")
            return image_path

    # --------------------------------------------------------------- AI VIDEO
    def render_ai_video_clip(
        self,
        prompt: str,
        output_video_path: str,
        duration: float,
        fps: Optional[int] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
        engine: str = "fastmetal",
        models: Sequence[Model] = (),
        start_image: Optional[str] = None,
    ) -> Optional[str]:
        """
        Generate a real-motion scene clip with a video model (*engine*: the local FastMetal
        model, or "gemini" for Google's online one) and fit it to the canvas, frame rate and
        scene duration. Returns None on any failure so the caller can fall back to the
        image + Ken Burns path. *models*: your models the scene shows, described in the prompt;
        *start_image* (Gemini only): the picture the clip opens on, which already shows them.
        """
        from faceless_shorts.integrations import FastMetalClient, FASTMETAL_FPS, GeminiClient

        fps = fps or self.fps
        width = width or self.width
        height = height or self.height
        style_config = ART_STYLES.get(self.art_style, ART_STYLES["hyperreal"])
        start_image = start_image if engine == "gemini" and start_image and os.path.exists(start_image) else None
        if start_image:
            prompt = f"Bring this opening frame to life: {prompt}. Everyone and everything in the frame keeps exactly the same look"
        else:
            prompt = with_models(prompt, models, photos=False)
        full_prompt = f"{prompt}, {style_config['prompt_suffix']}"
        portrait = height >= width

        if engine == "stock":
            from faceless_shorts.integrations import StockBRoll
            # Use first 6 meaningful words of the visual prompt as a stock search query.
            query = " ".join(prompt.replace(",", " ").split()[:6])
            key = f"stock|{query}|{portrait}"
            raw_path = os.path.join(self.cache_dir, f"stock_{hashlib.sha1(key.encode()).hexdigest()[:16]}.mp4")
            if not os.path.exists(raw_path):
                downloaded = StockBRoll.search_clip(query, orientation="portrait" if portrait else "landscape")
                if not downloaded or not os.path.exists(downloaded):
                    return None
                shutil.copyfile(downloaded, raw_path)
            return self.fit_custom_video_clip(raw_path, output_video_path, duration, fps=fps, width=width, height=height)

        if engine == "gemini":
            client = GeminiClient()
            full_prompt += ". No captions or text on screen."  # the subtitles are added later, by this tool
            clip_seconds = float(client.seconds_for(duration))
            made_by = [str(clip_seconds), str(portrait), client.model("video"), client.video_resolution(),
                       *([_file_hash(start_image)] if start_image else [])]
        else:
            client = FastMetalClient()
            frames = FastMetalClient.frames_for(duration)
            clip_seconds = frames / FASTMETAL_FPS
            made_by = [str(frames), str(portrait), *FastMetalClient.extra_args()]

        # A clip costs minutes (FastMetal) or money (Gemini), so raw clips are cached by what produced them.
        key = "|".join([full_prompt, *made_by])
        raw_path = os.path.join(self.cache_dir, f"{engine}_{hashlib.sha1(key.encode()).hexdigest()[:16]}.mp4")
        if not os.path.exists(raw_path):
            opening = {"start_image": start_image} if start_image else {}
            if not client.generate(full_prompt, raw_path, duration, portrait=portrait, **opening):
                return None

        # A scene longer than the model's longest clip (~5 s FastMetal, 15 s Gemini) is slowed down to fill it.
        # TODO: chain a second clip (or interpolate frames) if slow motion on 6s+ scenes looks choppy.
        stretch = max(1.0, duration / clip_seconds)
        art_filter = style_config.get("ffmpeg_filter", "eq=contrast=1.05:saturation=1.05")
        fit_filter = (
            f"setpts={stretch:.4f}*PTS,"
            f"scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos,"
            f"crop={width}:{height},fps={fps},{art_filter},format=yuv420p"
        )
        os.makedirs(os.path.dirname(os.path.abspath(output_video_path)), exist_ok=True)
        cmd = [
            "ffmpeg", "-y",
            "-i", raw_path,
            "-vf", fit_filter,
            "-t", str(duration),
            "-an",
            "-c:v", "libx264",
            "-preset", "faster",
            "-pix_fmt", "yuv420p",
            output_video_path
        ]
        try:
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError) as e:
            print(f"    [!] Could not fit the generated clip ({e}).")
            return None
        return output_video_path

    def fit_custom_video_clip(
        self,
        source_video_path: str,
        output_video_path: str,
        duration: float,
        fps: Optional[int] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
    ) -> Optional[str]:
        """Fit a user-provided or stock video clip to canvas, fps, art grade, and exact duration (looping if shorter)."""
        if not source_video_path or not os.path.exists(source_video_path):
            return None
        fps = fps or self.fps
        width = width or self.width
        height = height or self.height
        style_config = ART_STYLES.get(self.art_style, ART_STYLES["hyperreal"])
        art_filter = style_config.get("ffmpeg_filter", "eq=contrast=1.05:saturation=1.05")
        fit_filter = (
            f"scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos,"
            f"crop={width}:{height},fps={fps},{art_filter},format=yuv420p"
        )
        os.makedirs(os.path.dirname(os.path.abspath(output_video_path)), exist_ok=True)
        cmd = [
            "ffmpeg", "-y",
            "-stream_loop", "-1",
            "-i", source_video_path,
            "-vf", fit_filter,
            "-t", str(duration),
            "-an",
            "-c:v", "libx264",
            "-preset", "faster",
            "-pix_fmt", "yuv420p",
            output_video_path,
        ]
        try:
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError) as e:
            print(f"    [!] Could not fit custom video clip ({e}).")
            return None
        return output_video_path

    # -------------------------------------------------------------- KEN BURNS
    def render_ken_burns_clip(
        self,
        image_path: str,
        output_video_path: str,
        duration: float,
        motion: str = "punch_zoom_in",
        fps: Optional[int] = None,
        width: Optional[int] = None,
        height: Optional[int] = None
    ) -> str:
        """
        Convert static image into a dynamic vertical video clip with punchy Ken Burns motion
        and artistic video post-processing filters (sketch, vintage film, papercraft, cyberpunk).
        """
        os.makedirs(os.path.dirname(os.path.abspath(output_video_path)), exist_ok=True)
        fps = fps or self.fps
        width = width or self.width
        height = height or self.height
        total_frames = max(int(duration * fps), 30)

        # Dynamic high-impact camera motion formulas
        if motion.startswith("theatre_"):
            z_expr, x_expr, y_expr = get_theatre_motion_expr(motion, total_frames)
        elif motion == "dramatic_zoom_out":
            # Starts close (1.38x) and smoothly reveals out to 1.02x
            z_expr = "if(eq(on,1),1.38,max(zoom-0.0035,1.02))"
            x_expr = "iw/2-(iw/zoom/2)"
            y_expr = "ih/2-(ih/zoom/2)"
        elif motion == "pan_left":
            # Smooth cinematic left tracking pan
            z_expr = "1.25"
            x_expr = f"min((iw-iw/zoom)*(on/{total_frames}),iw-iw/zoom)"
            y_expr = "ih/2-(ih/zoom/2)"
        elif motion == "pan_right":
            # Smooth cinematic right tracking pan
            z_expr = "1.25"
            x_expr = f"max((iw-iw/zoom)*(1-on/{total_frames}),0)"
            y_expr = "ih/2-(ih/zoom/2)"
        elif motion == "vertigo_shake":
            # Organic handheld camera float
            z_expr = "min(zoom+0.002,1.3)"
            x_expr = "iw/2-(iw/zoom/2)+sin(on*0.06)*20"
            y_expr = "ih/2-(ih/zoom/2)+cos(on*0.05)*15"
        elif motion == "whip_zoom":
            # High-impact dynamic whip zoom surge
            z_expr = f"min(1.0+0.45*pow(on/{total_frames},1.6),1.48)"
            x_expr = "iw/2-(iw/zoom/2)"
            y_expr = "ih/2-(ih/zoom/2)"
        elif motion == "rise_up":
            # Slow vertical crane rise (reveals the scene upward)
            z_expr = "1.22"
            x_expr = "iw/2-(iw/zoom/2)"
            y_expr = f"max((ih-ih/zoom)*(1-on/{total_frames}),0)"
        elif motion == "orbit_sway":
            # Gentle floating orbital drift (documentary hover)
            z_expr = "1.18+0.04*sin(on*0.08)"
            x_expr = "iw/2-(iw/zoom/2)+sin(on*0.045)*38"
            y_expr = "ih/2-(ih/zoom/2)+cos(on*0.037)*26"
        elif motion == "pulse_heartbeat":
            # Double-thump zoom pulse synced to audio tension
            z_expr = f"1.06+0.10*abs(sin(on*0.45))+0.03*sin(on*0.11)"
            x_expr = "iw/2-(iw/zoom/2)"
            y_expr = "ih/2-(ih/zoom/2)"
        elif motion == "shockwave_out":
            # Explosive pull-back with decaying micro-shake (impact reveal)
            z_expr = f"if(lte(on,6),1.0+0.06*on,max(1.36-0.005*(on-6),1.04))"
            x_expr = "iw/2-(iw/zoom/2)+sin(on*0.9)*12*exp(-on/14)"
            y_expr = "ih/2-(ih/zoom/2)+cos(on*1.1)*9*exp(-on/14)"
        else: # punch_zoom_in default
            # Fast, punchy camera push-in (1.0x -> 1.38x)
            z_expr = "min(zoom+0.0035,1.38)"
            x_expr = "iw/2-(iw/zoom/2)"
            y_expr = "ih/2-(ih/zoom/2)"

        # Pre-scale and supersample to prevent distortion & zoompan jitter on any input
        base_prep = "scale=2160:3840:force_original_aspect_ratio=increase,crop=2160:3840"

        # Ken Burns motion filter
        motion_filter = f"zoompan=z='{z_expr}':x='{x_expr}':y='{y_expr}':d={total_frames}:s={width}x{height}:fps={fps}"

        # Artistic style filter chain
        style_config = ART_STYLES.get(self.art_style, ART_STYLES["hyperreal"])
        art_filter = style_config.get("ffmpeg_filter", "eq=contrast=1.05:saturation=1.05")

        full_filter_chain = f"{base_prep},{motion_filter},{art_filter},format=yuv420p"

        cmd = [
            "ffmpeg", "-y",
            "-loop", "1",
            "-i", image_path,
            "-vf", full_filter_chain,
            "-t", str(duration),
            "-c:v", "libx264",
            "-preset", "faster",
            "-pix_fmt", "yuv420p",
            output_video_path
        ]

        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return output_video_path
