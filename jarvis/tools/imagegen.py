"""Image generation.

Same pluggable shape as the messaging tool. FLUX schnell is the default
because it is both the cheapest good option (~$0.003 an image on fal, and
Together has run a free schnell endpoint) and fast enough that a voice turn
does not stall.
"""
from __future__ import annotations

import base64
import re
import time
from dataclasses import dataclass
from pathlib import Path

import httpx2 as httpx

from core.config import cfg

from .base import Tool, ToolError, produce, report

_TIMEOUT = httpx.Timeout(120.0, connect=10.0)

# Aspect names the model can use, mapped per provider.
ASPECTS = {
    "square": ("square_hd", 1024, 1024),
    "portrait": ("portrait_4_3", 768, 1024),
    "landscape": ("landscape_4_3", 1024, 768),
    "wide": ("landscape_16_9", 1280, 720),
}

# Appended when an image is destined for photogrammetry-style 3D conversion.
# Tripo reconstructs one subject: clutter, crops and hard shadows all become
# geometry errors.
FOR_3D_SUFFIX = (
    ", single centered subject, entire object visible with margin on all sides, "
    "plain flat neutral grey background, even diffuse studio lighting, no shadows, "
    "no reflections, three-quarter view, sharp focus, product photograph"
)


@dataclass
class GeneratedImage:
    data: bytes
    url: str
    filename: str
    path: Path


class ImageProvider:
    name = "none"

    def available(self) -> bool:
        return False

    async def generate(self, prompt: str, aspect: str) -> tuple[bytes, str]:
        """Return (image bytes, source url or '')."""
        raise ToolError(
            "Image generation is not configured. Set JARVIS_IMAGE_PROVIDER and the "
            "matching API key in .env. See jarvis/README.md, 'Images and 3D'."
        )


class FalProvider:
    """fal.ai — FLUX schnell, about $0.003 an image."""

    name = "fal"

    def available(self) -> bool:
        return bool(cfg.fal_key)

    async def generate(self, prompt: str, aspect: str) -> tuple[bytes, str]:
        size = ASPECTS.get(aspect, ASPECTS["square"])[0]
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                "https://fal.run/fal-ai/flux/schnell",
                headers={
                    "Authorization": f"Key {cfg.fal_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "prompt": prompt,
                    "image_size": size,
                    "num_inference_steps": 4,
                    "num_images": 1,
                    "enable_safety_checker": True,
                },
            )
            if resp.status_code == 401:
                raise ToolError("fal.ai rejected FAL_KEY.")
            if resp.status_code >= 400:
                raise ToolError(f"fal.ai error {resp.status_code}: {resp.text[:200]}")
            images = (resp.json() or {}).get("images") or []
            if not images:
                raise ToolError("fal.ai returned no image (the prompt may have been filtered).")
            url = images[0].get("url", "")
            if not url:
                raise ToolError("fal.ai returned an image with no URL.")
            blob = await client.get(url)
            blob.raise_for_status()
            return blob.content, url


class TogetherProvider:
    """Together AI — FLUX schnell, including their free schnell endpoint."""

    name = "together"

    def available(self) -> bool:
        return bool(cfg.together_api_key)

    async def generate(self, prompt: str, aspect: str) -> tuple[bytes, str]:
        _, width, height = ASPECTS.get(aspect, ASPECTS["square"])
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                "https://api.together.xyz/v1/images/generations",
                headers={
                    "Authorization": f"Bearer {cfg.together_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": cfg.together_image_model,
                    "prompt": prompt,
                    "width": width,
                    "height": height,
                    "steps": 4,
                    "n": 1,
                },
            )
            if resp.status_code == 401:
                raise ToolError("Together AI rejected TOGETHER_API_KEY.")
            if resp.status_code >= 400:
                raise ToolError(f"Together AI error {resp.status_code}: {resp.text[:200]}")

            entries = (resp.json() or {}).get("data") or []
            if not entries:
                raise ToolError("Together AI returned no image.")
            entry = entries[0]

            # Depending on the endpoint this is either a URL or inline base64.
            if entry.get("b64_json"):
                return base64.b64decode(entry["b64_json"]), ""
            url = entry.get("url", "")
            if not url:
                raise ToolError("Together AI returned neither a URL nor image data.")
            blob = await client.get(url)
            blob.raise_for_status()
            return blob.content, url


_PROVIDERS = {"none": ImageProvider, "fal": FalProvider, "together": TogetherProvider}


def provider():
    return _PROVIDERS.get(cfg.image_provider, ImageProvider)()


def _slug(text: str, limit: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (s[:limit].rstrip("-")) or "image"


def save_image(data: bytes, prompt: str) -> tuple[str, Path]:
    filename = f"{_slug(prompt)}-{int(time.time()) % 100000}.jpg"
    path = cfg.images_dir / filename
    path.write_bytes(data)
    return filename, path


def resolve_image(reference: str) -> Path:
    """Turn whatever the model passed into a real file on disk.

    Accepts a bare filename from a previous `generate_image`, a path, or
    'latest'.
    """
    ref = (reference or "").strip().strip("'\"")
    if not ref or ref.lower() in {"latest", "last", "the last one", "most recent"}:
        images = sorted(cfg.images_dir.glob("*.*"), key=lambda p: p.stat().st_mtime)
        if not images:
            raise ToolError("No images have been generated yet.")
        return images[-1]

    candidate = Path(ref)
    if candidate.is_absolute() and candidate.exists():
        return candidate
    local = cfg.images_dir / candidate.name
    if local.exists():
        return local
    if candidate.exists():
        return candidate
    raise ToolError(
        f"No image named {candidate.name!r}. Generate one first, or say 'the last image'."
    )


class GenerateImageTool(Tool):
    name = "generate_image"
    description = """
    Generate an image from a text description and save it locally. The image is
    shown to the user immediately.

    Write a properly detailed prompt -- subject, materials, lighting, style,
    camera angle. Expand a terse request into something specific rather than
    passing the user's words through verbatim; you are the one who knows what
    makes a good image prompt.

    Set for_3d=true whenever the image is going to be turned into a 3D model.
    That constrains it to a single centered object on a plain background, which
    is what the 3D reconstruction needs. Do not use for_3d for scenes,
    landscapes, or anything with more than one subject.
    """
    schema = {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "Detailed image description. Be specific and visual.",
            },
            "aspect": {
                "type": "string",
                "enum": ["square", "portrait", "landscape", "wide"],
                "description": "Default square. Use square for anything headed to 3D.",
            },
            "for_3d": {
                "type": "boolean",
                "description": "True if this image will be converted to a 3D model.",
            },
        },
        "required": ["prompt"],
    }

    def available(self) -> bool:
        return True  # always listed, so Jarvis can explain how to enable it

    async def run(self, prompt: str, aspect: str = "square", for_3d: bool = False) -> str:
        engine = provider()
        if not engine.available():
            raise ToolError(
                "Image generation is not configured. Set JARVIS_IMAGE_PROVIDER to "
                "'fal' or 'together' in .env, with the matching API key. "
                "See jarvis/README.md, 'Images and 3D'."
            )

        full_prompt = prompt.strip()
        if for_3d:
            full_prompt += FOR_3D_SUFFIX
            aspect = "square"

        await report(f"Generating image: {prompt[:40]}")
        data, url = await engine.generate(full_prompt, aspect)
        filename, path = save_image(data, prompt)

        await produce("image", prompt[:70], url=f"/files/images/{filename}", path=str(path))

        note = " Composed for 3D conversion." if for_3d else ""
        return (
            f"Image generated and saved as '{filename}' ({len(data) // 1024} KB).{note} "
            f"It is on screen now. Full path: {path}. "
            f"To turn it into a 3D model, pass image='{filename}' to image_to_3d."
        )
