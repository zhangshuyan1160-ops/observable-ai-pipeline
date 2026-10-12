"""Attempt-isolated immutable artifacts; database publication is a separate fenced step."""

import hashlib
import os
from pathlib import Path
from uuid import uuid4

from pipeline.blocking import run_blocking


class ArtifactStore:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def resolve(self, relative: str) -> Path:
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("Artifact path escaped storage root")
        return path

    async def write(self, data: bytes, name: str, media_type: str) -> dict:
        def save() -> dict:
            artifact_id = uuid4().hex
            relative = f"{artifact_id[:2]}/{artifact_id}/{Path(name).name}"
            path = self.resolve(relative)
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(path.suffix + ".tmp")
            with temporary.open("xb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            # Persist the rename on systems that support directory fsync.
            descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            return {
                "id": artifact_id,
                "path": relative,
                "sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data),
                "media_type": media_type,
                "name": name,
            }

        return await run_blocking(save)

    async def valid(self, artifact: dict) -> bool:
        def check() -> bool:
            try:
                path = self.resolve(artifact["path"])
                return (
                    path.stat().st_size == artifact["size"]
                    and hashlib.sha256(path.read_bytes()).hexdigest() == artifact["sha256"]
                )
            except (OSError, ValueError, KeyError):
                return False

        return await run_blocking(check)

    async def read(self, artifact: dict) -> bytes:
        if not await self.valid(artifact):
            raise FileNotFoundError("Upstream artifact is missing or corrupted")
        return await run_blocking(self.resolve(artifact["path"]).read_bytes)
