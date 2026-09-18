import asyncio
from pathlib import Path

from app.config import settings
from app.storage.base import StorageService


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

    async def delete(self, key: str) -> None:
        path = self._path(key)
        if path.exists():
            await asyncio.to_thread(path.unlink)

    async def get_secure_url(self, key: str) -> str:
        raise NotImplementedError("Local evidence is served by authorized content routes")
