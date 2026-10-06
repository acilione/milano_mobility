"""S3-compatible immutable object storage adapter."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections.abc import Iterable
from contextlib import suppress
from pathlib import Path
from typing import Any

import boto3
from botocore.client import BaseClient
from botocore.exceptions import ClientError
from tenacity import retry, stop_after_attempt, wait_exponential

from milano_mobility.config import Settings


class LocalObjectStore:
    """Optional filesystem archive with atomic writes and immutable source files."""

    def __init__(self, directory: str) -> None:
        self.root = Path(directory).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, bucket: str, key: str = "") -> Path:
        path = (self.root / bucket / key).resolve()
        if not path.is_relative_to(self.root) or path == self.root:
            raise ValueError("Archive paths must remain inside the configured directory.")
        return path

    def ensure_buckets(self, buckets: Iterable[str]) -> None:
        for bucket in buckets:
            self._path(bucket).mkdir(parents=True, exist_ok=True)

    def exists(self, bucket: str, key: str) -> bool:
        return self._path(bucket, key).is_file()

    def put_file(self, bucket: str, key: str, path: Path, metadata: dict[str, str]) -> str:
        target = self._path(bucket, key)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as temporary:
                temporary_path = Path(temporary.name)
            try:
                shutil.copyfile(path, temporary_path)
                with suppress(FileExistsError):
                    os.link(temporary_path, target)
            finally:
                temporary_path.unlink(missing_ok=True)
        return target.as_uri()

    def put_json(self, bucket: str, key: str, payload: dict[str, Any]) -> str:
        target = self._path(bucket, key)
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=target.parent, delete=False
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(payload, temporary, sort_keys=True, ensure_ascii=False, indent=2)
        try:
            os.replace(temporary_path, target)
        finally:
            temporary_path.unlink(missing_ok=True)
        return target.as_uri()

    def copy(self, source_bucket: str, source_key: str, target_bucket: str, target_key: str) -> str:
        return self.put_file(target_bucket, target_key, self._path(source_bucket, source_key), {})


class ObjectStore:
    """Minimal S3 adapter that works with MinIO and public clouds."""

    def __init__(self, settings: Settings) -> None:
        self._client: BaseClient = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
        )

    def ensure_buckets(self, buckets: Iterable[str]) -> None:
        """Create missing buckets; existing buckets are left untouched."""
        for bucket in buckets:
            try:
                self._client.head_bucket(Bucket=bucket)
            except ClientError:
                self._client.create_bucket(Bucket=bucket)

    def exists(self, bucket: str, key: str) -> bool:
        """Return whether an object exists."""
        try:
            self._client.head_object(Bucket=bucket, Key=key)
        except ClientError as error:
            code = str(error.response.get("Error", {}).get("Code", ""))
            if code in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise
        return True

    @retry(stop=stop_after_attempt(4), wait=wait_exponential(multiplier=1, max=8), reraise=True)
    def put_file(self, bucket: str, key: str, path: Path, metadata: dict[str, str]) -> str:
        """Upload a file only when the immutable key does not already exist."""
        if not self.exists(bucket, key):
            self._client.upload_file(str(path), bucket, key, ExtraArgs={"Metadata": metadata})
        return f"s3://{bucket}/{key}"

    def put_json(self, bucket: str, key: str, payload: dict[str, Any]) -> str:
        """Write a deterministic UTF-8 JSON object."""
        body = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False).encode("utf-8")
        self._client.put_object(
            Bucket=bucket,
            Key=key,
            Body=body,
            ContentType="application/json",
        )
        return f"s3://{bucket}/{key}"

    def copy(self, source_bucket: str, source_key: str, target_bucket: str, target_key: str) -> str:
        """Copy an immutable object, for example into quarantine."""
        if not self.exists(target_bucket, target_key):
            self._client.copy_object(
                Bucket=target_bucket,
                Key=target_key,
                CopySource={"Bucket": source_bucket, "Key": source_key},
            )
        return f"s3://{target_bucket}/{target_key}"
