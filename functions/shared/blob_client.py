"""Generic Azure Blob Storage client."""
from __future__ import annotations

import json
import logging
from typing import Any, List, Optional

from azure.storage.blob.aio import BlobServiceClient
from azure.core.exceptions import ResourceNotFoundError

from .settings import get_settings

LOGGER = logging.getLogger(__name__)


class BlobStorageClient:
    """Client for basic blob operations in a single container."""

    def __init__(self) -> None:
        self._settings = get_settings()
        self._container_name = self._settings.azure_storage_blob_container

    async def _get_blob_service_client(self) -> BlobServiceClient:
        return BlobServiceClient.from_connection_string(
            self._settings.azure_storage_connection_string
        )

    async def _ensure_container_exists(self, blob_service_client: BlobServiceClient) -> None:
        try:
            container_client = blob_service_client.get_container_client(self._container_name)
            await container_client.create_container()
        except Exception:
            pass

    async def upload_text(self, blob_path: str, data: str, content_type: str = "text/plain") -> str:
        bsc = await self._get_blob_service_client()
        await self._ensure_container_exists(bsc)
        try:
            blob_client = bsc.get_blob_client(container=self._container_name, blob=blob_path)
            await blob_client.upload_blob(data=data, overwrite=True, content_type=content_type)
            return blob_path
        finally:
            await bsc.close()

    async def upload_json(self, blob_path: str, obj: Any) -> str:
        return await self.upload_text(blob_path, json.dumps(obj, indent=2), content_type="application/json")

    async def download_text(self, blob_path: str) -> Optional[str]:
        bsc = await self._get_blob_service_client()
        try:
            blob_client = bsc.get_blob_client(container=self._container_name, blob=blob_path)
            blob_data = await blob_client.download_blob()
            content = await blob_data.readall()
            return content.decode("utf-8")
        except ResourceNotFoundError:
            return None
        finally:
            await bsc.close()

    async def download_json(self, blob_path: str) -> Optional[Any]:
        text = await self.download_text(blob_path)
        if text is None:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None

    async def delete_blob(self, blob_path: str) -> bool:
        bsc = await self._get_blob_service_client()
        try:
            blob_client = bsc.get_blob_client(container=self._container_name, blob=blob_path)
            await blob_client.delete_blob()
            return True
        except Exception:
            return False
        finally:
            await bsc.close()

    async def list_paths(self, prefix: str = "", limit: int = 1000) -> List[str]:
        bsc = await self._get_blob_service_client()
        try:
            container_client = bsc.get_container_client(self._container_name)
            results: List[str] = []
            async for blob in container_client.list_blobs(name_starts_with=prefix):
                results.append(blob.name)
                if len(results) >= limit:
                    break
            return results
        finally:
            await bsc.close()


# Singleton instance
_blob_client: Optional[BlobStorageClient] = None


def get_blob_client() -> BlobStorageClient:
    """Get a singleton instance of the BlobStorageClient."""
    global _blob_client
    if _blob_client is None:
        _blob_client = BlobStorageClient()
    return _blob_client
