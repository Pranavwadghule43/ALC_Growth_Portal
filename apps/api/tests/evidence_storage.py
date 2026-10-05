"""In-memory evidence storage for tests, with an instrumented streaming ``open``."""
from collections.abc import AsyncIterator

from app.storage import storage_service
from app.storage.base import ObjectStream, StorageObjectNotFound


class MemoryObjectStream(ObjectStream):
    """Serves ``body`` in ``chunk_size`` pieces and records what happened to it."""

    def __init__(self, body: bytes, chunk_size: int, fail_after: int | None = None) -> None:
        self.body = body
        self.size = len(body)
        self.chunk_size = chunk_size
        self.fail_after = fail_after  # raise after this many chunks (a mid-stream failure)
        self.chunks_read = 0
        self.close_calls = 0

    @property
    def closed(self) -> bool:
        return self.close_calls > 0

    async def chunks(self) -> AsyncIterator[bytes]:
        for start in range(0, len(self.body), self.chunk_size):
            if self.closed:
                raise RuntimeError("read after close")
            if self.fail_after is not None and self.chunks_read >= self.fail_after:
                raise ConnectionError("storage connection lost")
            self.chunks_read += 1
            yield self.body[start:start + self.chunk_size]

    def close(self) -> None:
        self.close_calls += 1


class MemoryStorage:
    """Replaces the storage service's methods; ``opened`` lists every stream handed out."""

    def __init__(self, monkeypatch, chunk_size: int = 4) -> None:
        self.files: dict[str, bytes] = {}
        self.opened: list[MemoryObjectStream] = []
        self.chunk_size = chunk_size
        self.open_error: Exception | None = None  # raised by ``open`` (storage unavailable)
        self.fail_after: int | None = None
        for name in ("upload", "get", "open", "delete"):
            monkeypatch.setattr(storage_service, name, getattr(self, name))

    async def upload(self, key: str, body: bytes, content_type: str) -> None:
        self.files[key] = body

    async def get(self, key: str) -> bytes:
        if key not in self.files:
            raise FileNotFoundError(key)
        return self.files[key]

    async def open(self, key: str) -> MemoryObjectStream:
        if self.open_error is not None:
            raise self.open_error
        if key not in self.files:
            raise StorageObjectNotFound
        stream = MemoryObjectStream(self.files[key], self.chunk_size, self.fail_after)
        self.opened.append(stream)
        return stream

    async def delete(self, key: str) -> None:
        self.files.pop(key, None)
