"""Environment-only configuration; runtime files stay outside source directories."""

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database_url: str
    artifact_root: Path
    lease_seconds: float = 10.0
    heartbeat_seconds: float = 2.0
    poll_seconds: float = 0.3
    retry_base_seconds: float = 1.0

    def __post_init__(self) -> None:
        if not 0 < self.heartbeat_seconds < self.lease_seconds / 2:
            raise ValueError("Heartbeat must be positive and less than half the lease")
        if self.poll_seconds <= 0 or self.retry_base_seconds < 0:
            raise ValueError("Invalid poll/retry interval")

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_url=os.environ["DATABASE_URL"],
            artifact_root=Path(os.getenv("ARTIFACT_ROOT", ".local/artifacts")).resolve(),
            lease_seconds=float(os.getenv("LEASE_SECONDS", "10")),
            heartbeat_seconds=float(os.getenv("HEARTBEAT_SECONDS", "2")),
            poll_seconds=float(os.getenv("POLL_SECONDS", "0.3")),
            retry_base_seconds=float(os.getenv("RETRY_BASE_SECONDS", "1")),
        )
