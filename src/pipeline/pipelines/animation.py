"""Synthetic animation processing with real immutable files and batch checkpoints."""

import asyncio
import hashlib
import io
import json
import math
import zipfile

from PIL import Image, ImageDraw

from pipeline.engine import Engine


class PermanentFailure(Exception):
    pass


def make_frame(index: int, count: int, asset: str, model: str = "source", seed: int = 0) -> bytes:
    digest = hashlib.sha256(f"{model}:{seed}".encode()).digest()
    accent = (60 + digest[0] % 130, 100 + digest[1] % 130, 120 + digest[2] % 120)
    image = Image.new("RGB", (256, 192), (18, 29, 47))
    draw = ImageDraw.Draw(image)
    for x in range(0, 256, 24):
        draw.line((x, 0, x, 192), fill=(26, 40, 59))
    for y in range(0, 192, 24):
        draw.line((0, y, 256, y), fill=(26, 40, 59))
    angle = 2 * math.pi * index / count
    x = 128 + int(72 * math.cos(angle))
    y = 95 + int(46 * math.sin(angle)) if asset == "orbit" else 120 - int(70 * abs(math.sin(angle)))
    draw.ellipse((40, 140, 215, 159), fill=(33, 48, 67))
    draw.ellipse((x - 24, y - 24, x + 24, y + 24), fill=accent)
    draw.ellipse((x - 12, y - 15, x - 2, y - 5), fill=(234, 248, 255))
    draw.text((14, 14), "MOCK / " + model[:18], fill=(199, 216, 235))
    draw.text((14, 174), f"FRAME {index + 1:02d} / {count:02d}", fill=(155, 176, 197))
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def pack(files: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(name, (2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    return output.getvalue()


async def execute(engine: Engine, job: dict) -> dict:
    p, step, attempt = job["task"]["params"], job["step"], job["attempt"]
    key = step["key"]
    fault = p["fault_mode"] if p["fault_step"] == key else "none"
    if fault == "permanent":
        raise PermanentFailure("注入永久错误：该步骤输入不可处理")
    if attempt["retry_round"] == 0:
        if fault == "exhaust_once" or (fault == "transient_once" and attempt["attempt_no"] == 1):
            raise ConnectionError("注入临时错误：模拟推理服务不可用")
        if fault == "timeout_once" and attempt["attempt_no"] == 1:
            await asyncio.Event().wait()  # Heartbeats continue, execution deadline still applies.
    await asyncio.sleep(p["step_delay"])
    # Validate inputs even if a handler only uses their semantic metadata.
    inputs = {name: await engine.files.read(value) for name, value in job["upstream"].items()}
    if key == "probe":
        if p["asset"] == "invalid":
            raise PermanentFailure("素材校验失败：不支持的演示素材")
        data = json.dumps(
            {"asset": p["asset"], "frames": p["frames"], "width": 256, "height": 192, "mock": True},
            sort_keys=True,
        ).encode()
        return await engine.files.write(data, "metadata.json", "application/json")
    if key == "extract":
        metadata = json.loads(inputs["probe"])
        data = json.dumps(
            {"metadata": metadata, "frames": list(range(p["frames"]))}, sort_keys=True
        ).encode()
        return await engine.files.write(data, "frame-manifest.json", "application/json")
    if key == "thumbnail":
        data = await asyncio.to_thread(make_frame, 0, p["frames"], p["asset"])
        return await engine.files.write(data, "thumbnail.png", "image/png")
    if key == "stylize":
        saved = step["checkpoint"]
        valid = (
            saved.get("schema") == 1
            and saved.get("fingerprint") == step["fingerprint"]
            and isinstance(saved.get("frames"), list)
            and len(saved["frames"]) <= p["frames"]
        )
        if valid:
            valid = all(await asyncio.gather(*(engine.files.valid(a) for a in saved["frames"])))
        frames = list(saved["frames"]) if valid else []
        if saved and not valid:
            await engine.checkpoint(job, {}, 0, reset=True)
        for begin in range(len(frames), p["frames"], 4):
            await asyncio.sleep(p["step_delay"])
            for index in range(begin, min(begin + 4, p["frames"])):
                data = await asyncio.to_thread(
                    make_frame, index, p["frames"], p["asset"], p["model_version"], p["seed"]
                )
                frames.append(await engine.files.write(data, f"frame-{index:03d}.png", "image/png"))
            checkpoint = {"schema": 1, "fingerprint": step["fingerprint"], "frames": frames}
            await engine.checkpoint(job, checkpoint, len(frames) / p["frames"])
        frame_data = {
            f"frame-{i:03d}.png": await engine.files.read(a) for i, a in enumerate(frames)
        }
        return await engine.files.write(
            await asyncio.to_thread(pack, frame_data), "styled-frames.zip", "application/zip"
        )
    if key == "compose":

        def compose() -> bytes:
            with zipfile.ZipFile(io.BytesIO(inputs["stylize"])) as archive:
                images = [
                    Image.open(io.BytesIO(archive.read(name))).convert("RGB")
                    for name in sorted(archive.namelist())
                ]
            output = io.BytesIO()
            images[0].save(
                output, format="GIF", save_all=True, append_images=images[1:], duration=100, loop=0
            )
            return output.getvalue()

        return await engine.files.write(
            await asyncio.to_thread(compose), "animation.gif", "image/gif"
        )
    if key == "package":
        manifest = {
            name: {k: value[k] for k in ("sha256", "size", "name")}
            for name, value in job["upstream"].items()
        }
        data = await asyncio.to_thread(
            pack,
            {
                "animation.gif": inputs["compose"],
                "thumbnail.png": inputs["thumbnail"],
                "manifest.json": json.dumps(manifest, sort_keys=True).encode(),
            },
        )
        return await engine.files.write(data, "animation-bundle.zip", "application/zip")
    raise PermanentFailure("Unknown handler")
