import asyncio
from collections.abc import AsyncIterator

import boto3
from botocore.exceptions import ClientError

from app.config import settings
from app.storage.base import CHUNK_SIZE, ObjectStream, StorageObjectNotFound, StorageService

MISSING_OBJECT_CODES = {"NoSuchKey", "NotFound", "404"}


class S3ObjectStream(ObjectStream):
    """A ``get_object`` response body, read in chunks on a worker thread."""

    def __init__(self, body, size: int | None) -> None:
        self._body = body
        self.size = size

    async def chunks(self) -> AsyncIterator[bytes]:
        while chunk := await asyncio.to_thread(self._body.read, CHUNK_SIZE):
            yield chunk

    def close(self) -> None:
        # Releases the HTTP connection to the object store.
        self._body.close()


class S3StorageService(StorageService):
    def __init__(self) -> None:
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url or None,
            region_name=settings.s3_region,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
        )

    async def upload(self, key: str, body: bytes, content_type: str) -> None:
        await asyncio.to_thread(
            self.client.put_object,
            Bucket=settings.s3_bucket,
            Key=key,
            Body=body,
            ContentType=content_type,
        )

    async def get(self, key: str) -> bytes:
        obj = await asyncio.to_thread(self.client.get_object, Bucket=settings.s3_bucket, Key=key)
        return await asyncio.to_thread(obj["Body"].read)

    async def open(self, key: str) -> ObjectStream:
        try:
            obj = await asyncio.to_thread(
                self.client.get_object, Bucket=settings.s3_bucket, Key=key
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in MISSING_OBJECT_CODES:
                raise StorageObjectNotFound from None
            raise
        return S3ObjectStream(obj["Body"], obj.get("ContentLength"))

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self.client.delete_object, Bucket=settings.s3_bucket, Key=key)

    async def get_secure_url(self, key: str) -> str:
        return await asyncio.to_thread(
            self.client.generate_presigned_url,
            "get_object",
            Params={"Bucket": settings.s3_bucket, "Key": key},
            ExpiresIn=settings.s3_presign_seconds,
        )

