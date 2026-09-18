from app.config import settings
from app.storage.local import LocalStorageService
from app.storage.s3 import S3StorageService

storage_service = LocalStorageService() if settings.storage_backend == "local" else S3StorageService()
