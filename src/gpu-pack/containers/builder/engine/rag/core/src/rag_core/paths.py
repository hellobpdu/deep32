from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RAGPaths:
    root: Path

    @classmethod
    def from_env(cls) -> "RAGPaths":
        value = os.environ.get("RAG_ROOT")
        if not value:
            raise RuntimeError(
                "RAG_ROOT is not set. "
                "Set it to the root the engine serves, e.g. /var/lib/deep32/rag-data/<generation>."
            )
        return cls(Path(value).expanduser().resolve())

    @property
    def inbox(self) -> Path:
        return self.root / "inbox"

    @property
    def originals(self) -> Path:
        return self.root / "originals"

    @property
    def normalized(self) -> Path:
        return self.root / "normalized"

    @property
    def assets(self) -> Path:
        return self.root / "assets"

    @property
    def metadata(self) -> Path:
        return self.root / "metadata"

    @property
    def sqlite(self) -> Path:
        return self.root / "sqlite"

    @property
    def qdrant(self) -> Path:
        return self.root / "qdrant"

    @property
    def pylate(self) -> Path:
        return self.root / "pylate"

    @property
    def cache(self) -> Path:
        return self.root / "cache"

    @property
    def failed(self) -> Path:
        return self.root / "failed"

    @property
    def archive(self) -> Path:
        return self.root / "archive"

    @property
    def eval(self) -> Path:
        return self.root / "eval"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    def ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

        for path in (
            self.inbox,
            self.originals,
            self.normalized,
            self.assets,
            self.metadata,
            self.sqlite,
            self.qdrant,
            self.pylate,
            self.cache,
            self.failed,
            self.archive,
            self.eval,
            self.logs,
        ):
            path.mkdir(parents=True, exist_ok=True)
