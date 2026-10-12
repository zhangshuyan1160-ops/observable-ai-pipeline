"""Versioned, immutable workflow definition and validated task inputs."""

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

StepKey = Literal["probe", "extract", "stylize", "thumbnail", "compose", "package"]
SUCCESS = {"SUCCEEDED", "CACHED"}
TERMINAL = SUCCESS | {"FAILED", "BLOCKED"}
DEFINITION_VERSION = "animation-v1"
STEPS = [
    {"key": "probe", "label": "素材检查", "depends_on": [], "resource": "cpu"},
    {"key": "extract", "label": "生成帧序列", "depends_on": ["probe"], "resource": "cpu"},
    {"key": "stylize", "label": "逐批风格化", "depends_on": ["extract"], "resource": "gpu"},
    {"key": "thumbnail", "label": "缩略图", "depends_on": ["extract"], "resource": "cpu"},
    {"key": "compose", "label": "合成动画", "depends_on": ["stylize"], "resource": "cpu"},
    {
        "key": "package",
        "label": "打包交付",
        "depends_on": ["compose", "thumbnail"],
        "resource": "cpu",
    },
]


class TaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(default="Orbit / 动画素材", min_length=1, max_length=80)
    asset: Literal["orbit", "bounce", "invalid"] = "orbit"
    frames: int = Field(default=16, ge=4, le=64)
    seed: int = Field(default=42, ge=0, le=65535)
    model_version: str = Field(default="mock-v1", min_length=1, max_length=32, pattern=r"^[\w.-]+$")
    step_delay: float = Field(default=1.2, ge=0.01, le=10)
    cache_enabled: bool = True
    fault_step: StepKey = "stylize"
    fault_mode: Literal["none", "transient_once", "permanent", "exhaust_once", "timeout_once"] = (
        "none"
    )
    max_attempts: int = Field(default=3, ge=1, le=5)
    attempt_timeout: float = Field(default=120, ge=0.1, le=3600)
    task_timeout: float = Field(default=3600, ge=1, le=14400)
    gpu_memory_mb: int = Field(default=4096, ge=256, le=32768)


def fingerprint(value: object) -> str:
    data = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(data.encode()).hexdigest()


def step_fingerprint(key: str, params: dict, upstream: dict) -> str:
    relevant = {k: params[k] for k in ("asset", "frames")} if key == "probe" else {}
    if key == "stylize":
        relevant = {k: params[k] for k in ("seed", "model_version")}
    return fingerprint(
        {
            "scope": "local-demo",
            "definition": DEFINITION_VERSION,
            "handler": "1",
            "step": key,
            "parameters": relevant,
            "upstream": {k: v["sha256"] for k, v in sorted(upstream.items())},
        }
    )


def validate_dag(specs: list[dict]) -> None:
    keys = {s["key"] for s in specs}
    if len(keys) != len(specs):
        raise ValueError("Duplicate step key")
    resolved: set[str] = set()
    while len(resolved) < len(keys):
        ready = {s["key"] for s in specs if set(s["depends_on"]) <= resolved} - resolved
        if not ready:
            raise ValueError("Missing dependency or cyclic DAG")
        resolved |= ready


validate_dag(STEPS)
