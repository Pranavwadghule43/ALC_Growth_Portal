import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import BinaryIO

from app.config import settings
from app.storage.base import CHUNK_SIZE, ObjectStream, StorageObjectNotFound, StorageService


class LocalObjectStream(ObjectStream):
    """An open file, read in chunks on a worker thread."""

    def __init__(self, handle: BinaryIO) -> None:
        self._handle = handle
        self.size = os.fstat(handle.fileno()).st_size

    async def chunks(self) -> AsyncIterator[bytes]:
        while chunk := await asyncio.to_thread(self._handle.read, CHUNK_SIZE):
            yield chunk

    def close(self) -> None:
        self._handle.close()


class LocalStorageService(StorageService):
    """Private development storage; files are served only through authorized API routes."""

    def __init__(self) -> None:
        self.root = Path(settings.local_storage_path).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("Invalid storage key")
        return path

    async def upload(self, key: str, body: bytes, content_type: str) -> None:
        path = self._path(key)
        await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_bytes, body)

    async def get(self, key: str) -> bytes:
        return await asyncio.to_thread(self._path(key).read_bytes)

    async def open(self, key: str) -> ObjectStream:
        try:
            path = self._path(key)
        except ValueError:  # the key resolves outside the storage root
            raise StorageObjectNotFound from None
        # Only a regular file is a stored object. Checked explicitly because opening a
        # directory fails differently per platform (IsADirectoryError on POSIX,
        # PermissionError on Windows); a genuinely unreadable file still raises.
        if not await asyncio.to_thread(path.is_file):
            raise StorageObjectNotFound
        try:
            handle = await asyncio.to_thread(path.open, "rb")
        except (FileNotFoundError, IsADirectoryError):  # removed/replaced since the check
            raise StorageObjectNotFound from None
        try:
            return LocalObjectStream(handle)
        except BaseException:
            handle.close()
            raise

    async def delete(self, key: str) -> None:
        path = self._path(key)
        if path.exists():
            await asyncio.to_thread(path.unlink)

    async def get_secure_url(self, key: str) -> str:
        raise NotImplementedError("Local evidence is served by authorized content routes")
