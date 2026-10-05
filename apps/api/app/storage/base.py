from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

# Evidence is streamed to the browser in chunks of this size, so a download never holds a whole
# file in memory (uploads are capped at MAX_UPLOAD_BYTES).
CHUNK_SIZE = 64 * 1024


class StorageObjectNotFound(Exception):
    """The requested object does not exist in storage."""


class ObjectStream(ABC):
    """A stored object opened for reading. ``size`` is its length in bytes when known.

    ``close`` must always be called. It is synchronous and idempotent, so it also runs safely
    while a cancelled request (client disconnect) unwinds."""

    size: int | None = None

    @abstractmethod
    def chunks(self) -> AsyncIterator[bytes]: ...

    @abstractmethod
    def close(self) -> None: ...


class StorageService(ABC):
    @abstractmethod
    async def upload(self, key: str, body: bytes, content_type: str) -> None: ...

    @abstractmethod
    async def get(self, key: str) -> bytes: ...

    @abstractmethod
    async def open(self, key: str) -> ObjectStream:
        """Open ``key`` for streaming; raises ``StorageObjectNotFound`` when it does not exist."""

    @abstractmethod
    async def delete(self, key: str) -> None: ...
    @abstractmethod
    async def get_secure_url(self, key: str) -> str: ...
