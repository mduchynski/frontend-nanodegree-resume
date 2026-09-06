"""Tripo3D — text-to-3D, image-to-3D, and format conversion.

Written against the Tripo v3 REST API:
  base      https://openapi.tripo3d.ai/v3   (overseas)  /  .com (China)
  auth      Authorization: Bearer <key>
  envelope  {"code": 0, "data": {...}, "message": ..., "suggestion": ...}
  upload    POST /v3/files            multipart field "file"  -> data.file_token
  generate  POST /v3/generation/{text-to-model,image-to-model}  -> data.task_id
  poll      GET  /v3/tasks/{task_id}  -> data.{status, progress, output}
  convert   POST /v3/models/convert   {input: task_id, format}
  balance   GET  /v3/account/balance

A generation takes tens of seconds to a few minutes, which is a long silence in
a spoken conversation, so progress is streamed to the HUD while we wait and the
job id is handed back if it outlives the turn.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import httpx2 as httpx

from core.config import cfg

from .base import Tool, ToolError, produce, report
from .imagegen import resolve_image

_TIMEOUT = httpx.Timeout(90.0, connect=10.0)

TERMINAL = {"success", "failed", "cancelled", "banned", "expired", "unknown"}

# Output key names have drifted across Tripo model versions; check all of them.
_MODEL_KEYS = (
    "pbr_model",
    "model",
    "model_url",
    "pbr_model_url",
    "base_model",
    "base_model_url",
    "model_urls",
)
_PREVIEW_KEYS = ("rendered_image", "rendered_image_url", "preview_image", "thumbnail")

FORMATS = ("GLB", "GLTF", "FBX", "OBJ", "STL", "USDZ", "3MF")


def _pick(output: dict, keys) -> str:
    for key in keys:
        value = output.get(key)
        if isinstance(value, str) and value.startswith("http"):
            return value
        if isinstance(value, dict) and isinstance(value.get("url"), str):
            return value["url"]
        if isinstance(value, list) and value and isinstance(value[0], str):
            return value[0]
    return ""


class TripoClient:
    """Thin async client over the v3 REST surface."""

    def __init__(self) -> None:
        if not cfg.tripo_api_key:
            raise ToolError(
                "Tripo3D is not configured. Put your key in .env as TRIPO_API_KEY "
                "(get one at platform.tripo3d.ai). See jarvis/README.md, 'Images and 3D'."
            )
        self.base = cfg.tripo_base_url
        self.headers = {"Authorization": f"Bearer {cfg.tripo_api_key}"}

    async def _request(self, method: str, path: str, **kw) -> Any:
        url = path if path.startswith("http") else f"{self.base}{path}"
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.request(method, url, headers=self.headers, **kw)
        except httpx.HTTPError as exc:
            raise ToolError(f"Could not reach Tripo3D: {exc}") from exc

        if resp.status_code == 401:
            raise ToolError("Tripo3D rejected TRIPO_API_KEY.")
        if resp.status_code == 402:
            raise ToolError("Tripo3D reports insufficient credits for this job.")

        try:
            payload = resp.json()
        except ValueError:
            raise ToolError(f"Tripo3D returned a non-JSON response ({resp.status_code}).") from None

        # Tripo signals failure both by HTTP status and by a non-zero envelope code.
        if resp.status_code >= 400 or (isinstance(payload, dict) and payload.get("code", 0) != 0):
            message = ""
            suggestion = ""
            if isinstance(payload, dict):
                message = str(payload.get("message") or "")
                suggestion = str(payload.get("suggestion") or "")
            detail = " ".join(p for p in (message, suggestion) if p) or resp.text[:200]
            raise ToolError(f"Tripo3D error {resp.status_code}: {detail}")

        return payload.get("data", payload) if isinstance(payload, dict) else payload

    async def balance(self) -> dict:
        return await self._request("GET", "/account/balance")

    async def upload(self, path: Path) -> str:
        suffix = path.suffix.lower().lstrip(".") or "png"
        mime = "image/jpeg" if suffix in ("jpg", "jpeg") else f"image/{suffix}"
        files = {"file": (path.name, path.read_bytes(), mime)}
        data = await self._request("POST", "/files", files=files)
        token = (data or {}).get("file_token", "")
        if not token:
            raise ToolError("Tripo3D accepted the upload but returned no file_token.")
        return token

    async def create(self, endpoint: str, payload: dict) -> str:
        data = await self._request("POST", endpoint, json=payload)
        task_id = (data or {}).get("task_id", "")
        if not task_id:
            raise ToolError(f"Tripo3D did not return a task id for {endpoint}.")
        return task_id

    async def task(self, task_id: str) -> dict:
        return await self._request("GET", f"/tasks/{task_id}")

    async def wait(self, task_id: str, label: str, budget: int | None = None) -> dict:
        """Poll to a terminal state, narrating progress, until the budget runs out."""
        deadline = time.monotonic() + (budget if budget is not None else cfg.tripo_wait_seconds)
        last_pct = -1
        delay = 2.0

        while True:
            task = await self.task(task_id)
            status = str(task.get("status", "unknown")).lower()
            pct = int(task.get("progress") or 0)

            if status in TERMINAL:
                return task

            if pct != last_pct and pct > 0:
                await report(f"{label}: {pct}%")
                last_pct = pct
            elif last_pct < 0:
                await report(f"{label}: queued")
                last_pct = 0

            if time.monotonic() >= deadline:
                return task  # still running; caller hands back the job id

            await asyncio.sleep(delay)
            delay = min(delay * 1.25, 6.0)  # ease off; these jobs are not fast


def _describe(task: dict, task_id: str, what: str) -> str:
    """Turn a terminal (or timed-out) task into something worth saying aloud."""
    status = str(task.get("status", "unknown")).lower()
    output = task.get("output") or {}

    if status == "success":
        model_url = _pick(output, _MODEL_KEYS)
        preview = _pick(output, _PREVIEW_KEYS)
        if not model_url:
            return (
                f"Tripo finished {what} (job {task_id}) but returned no model file. "
                f"Check it in the Tripo web app."
            )
        return (
            f"{what.capitalize()} is ready. Job id {task_id}.\n"
            f"Model: {model_url}\n"
            + (f"Preview: {preview}\n" if preview else "")
            + "The download link expires in about two hours, and the model is also "
            "in your Tripo workspace. Offer to convert it to another format "
            "(FBX, OBJ, STL, USDZ) with convert_3d_model if the user needs one."
        )

    if status in ("failed", "banned", "cancelled", "expired"):
        reason = task.get("message") or task.get("error") or status
        return f"Tripo could not complete {what}: {reason} (job {task_id})."

    pct = int(task.get("progress") or 0)
    return (
        f"{what.capitalize()} is still running at {pct} percent (job {task_id}). "
        f"It outlived this turn. Tell the user it is still going and offer to "
        f"check again with check_3d_job."
    )


async def _emit_artifact(task: dict, label: str) -> None:
    output = task.get("output") or {}
    preview = _pick(output, _PREVIEW_KEYS)
    model_url = _pick(output, _MODEL_KEYS)
    if model_url:
        await produce("model", label, url=model_url, path=preview)


class TripoTool(Tool):
    def available(self) -> bool:
        return True  # listed even when unset, so Jarvis can say how to enable it


class TextTo3DTool(TripoTool):
    name = "text_to_3d"
    description = """
    Generate a 3D model directly from a text description, using Tripo3D.

    Prefer this over generate_image + image_to_3d whenever the user just wants
    an object -- it is one step instead of two, costs one Tripo credit instead
    of an image plus a credit, and usually gives cleaner geometry. Only go via
    an image when the user wants to see and approve the look first, or when
    they already have an image.

    Describe one object, with its material and form. Do not describe scenes,
    lighting, or camera angles; this is geometry, not a photograph.
    """
    schema = {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "The object to model, e.g. 'a brass Victorian desk telescope on a tripod'.",
            },
            "style": {
                "type": "string",
                "description": "Optional Tripo style preset, e.g. 'person:person2cartoon', 'object:clay'.",
            },
            "quad": {
                "type": "boolean",
                "description": "Quad topology instead of triangles. Set true if the user mentions rigging, animation, or clean retopology.",
            },
            "face_limit": {
                "type": "integer",
                "description": "Cap the polygon count. Use when the user mentions game assets, low-poly, or real-time.",
            },
        },
        "required": ["prompt"],
    }

    async def run(
        self,
        prompt: str,
        style: str | None = None,
        quad: bool = False,
        face_limit: int | None = None,
    ) -> str:
        client = TripoClient()
        payload: dict = {
            "prompt": prompt,
            "model": cfg.tripo_model_version,
            "texture": True,
            "pbr": True,
        }
        if style:
            payload["style"] = style
        if quad:
            payload["quad"] = True
        if face_limit:
            payload["face_limit"] = int(face_limit)

        await report(f"Submitting to Tripo: {prompt[:40]}")
        task_id = await client.create("/generation/text-to-model", payload)
        task = await client.wait(task_id, "Sculpting")
        await _emit_artifact(task, prompt[:70])
        return _describe(task, task_id, "the model")


class ImageTo3DTool(TripoTool):
    name = "image_to_3d"
    description = """
    Convert an existing image into a 3D model with Tripo3D.

    Pass the filename returned by generate_image, or 'latest' for the most
    recent one. Works best on a single object, centred, fully visible, on a
    plain background -- which is exactly what generate_image with for_3d=true
    produces.
    """
    schema = {
        "type": "object",
        "properties": {
            "image": {
                "type": "string",
                "description": "Filename from generate_image, a full path, or 'latest'.",
            },
            "quad": {"type": "boolean", "description": "Quad topology, for rigging or animation."},
            "face_limit": {"type": "integer", "description": "Polygon cap for game or real-time use."},
            "align_to_image": {
                "type": "boolean",
                "description": "Orient the model to match the image's viewpoint rather than a default pose.",
            },
        },
        "required": ["image"],
    }

    async def run(
        self,
        image: str,
        quad: bool = False,
        face_limit: int | None = None,
        align_to_image: bool = False,
    ) -> str:
        client = TripoClient()
        path = resolve_image(image)

        await report(f"Uploading {path.name}")
        file_token = await client.upload(path)

        payload: dict = {
            "file": {"file_token": file_token, "type": path.suffix.lower().lstrip(".") or "png"},
            "model": cfg.tripo_model_version,
            "texture": True,
            "pbr": True,
            "enable_image_autofix": True,
        }
        if quad:
            payload["quad"] = True
        if face_limit:
            payload["face_limit"] = int(face_limit)
        if align_to_image:
            payload["orientation"] = "align_image"

        task_id = await client.create("/generation/image-to-model", payload)
        task = await client.wait(task_id, "Reconstructing")
        await _emit_artifact(task, path.stem)
        return _describe(task, task_id, f"the model from {path.name}")


class Check3DJobTool(TripoTool):
    name = "check_3d_job"
    description = """
    Check a Tripo3D job that was still running when a previous turn ended.
    Pass the job id you were given. Use this when the user asks whether their
    model is done.
    """
    schema = {
        "type": "object",
        "properties": {
            "job_id": {"type": "string"},
            "wait": {
                "type": "boolean",
                "description": "Keep waiting for up to a minute rather than reporting the current state and returning.",
            },
        },
        "required": ["job_id"],
    }

    async def run(self, job_id: str, wait: bool = False) -> str:
        client = TripoClient()
        if wait:
            task = await client.wait(job_id, "Still working", budget=60)
        else:
            task = await client.task(job_id)
        await _emit_artifact(task, f"job {job_id[:8]}")
        return _describe(task, job_id, "the model")


class Convert3DModelTool(TripoTool):
    name = "convert_3d_model"
    description = """
    Convert a finished Tripo3D model into another file format. Pass the job id
    of the original generation. Use when the user names a format or a target
    program: FBX for Unreal or Maya, OBJ for most modelling tools, STL for
    3D printing, USDZ for Apple AR, GLB for the web and Blender.
    """
    schema = {
        "type": "object",
        "properties": {
            "job_id": {"type": "string", "description": "Job id of the completed generation."},
            "format": {"type": "string", "enum": list(FORMATS)},
            "face_limit": {"type": "integer", "description": "Optionally decimate during conversion."},
        },
        "required": ["job_id", "format"],
    }

    async def run(self, job_id: str, format: str, face_limit: int | None = None) -> str:
        fmt = (format or "").strip().upper()
        if fmt not in FORMATS:
            raise ToolError(f"{format!r} is not a supported format. Choose from: {', '.join(FORMATS)}.")

        client = TripoClient()
        payload: dict = {"input": job_id, "format": fmt}
        if face_limit:
            payload["face_limit"] = int(face_limit)

        await report(f"Converting to {fmt}")
        task_id = await client.create("/models/convert", payload)
        task = await client.wait(task_id, f"Converting to {fmt}")
        await _emit_artifact(task, f"{fmt} export")
        return _describe(task, task_id, f"the {fmt} conversion")


class TripoBalanceTool(TripoTool):
    name = "check_3d_credits"
    description = "Check the remaining Tripo3D credit balance. Use when the user asks what 3D generation is costing them."
    schema = {"type": "object", "properties": {}}

    async def run(self) -> str:
        data = await TripoClient().balance()
        balance = data.get("balance", data.get("credits", "unknown"))
        frozen = data.get("frozen")
        extra = f" ({frozen} reserved by running jobs)" if frozen else ""
        return f"Tripo3D balance: {balance} credits{extra}."
