"""Pluggable object storage (Sprint 5.5) — durable persistence for the ICP library and artifacts.

A tiny key/blob interface with two backends, selected by ``STORAGE_BACKEND``:

  - ``LocalStorage``  — filesystem (default; local dev). Keys map to files under a root dir, so the
    existing on-disk layout (``data/icp_library/...``) is preserved.
  - ``ObjectStorage`` — Oracle Cloud Infrastructure (OCI) Object Storage, for the containerised
    deployment where the container filesystem is ephemeral. The ``oci`` SDK is imported lazily, so
    local dev never needs it.

Keys are POSIX-style paths (``icp_library/<id>/meta.json``); ``/`` is the pseudo-directory delimiter
in both backends. Nothing here knows about ICPs — callers own the key scheme.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional

import config

# key prefixes owned by the app (callers build keys under these)
ICP_PREFIX = "icp_library"
ARTIFACT_PREFIX = "artifacts"


class Storage:
    """Abstract key/blob store."""

    def put_bytes(self, key: str, data: bytes) -> None:
        raise NotImplementedError

    def get_bytes(self, key: str) -> bytes:
        raise NotImplementedError

    def exists(self, key: str) -> bool:
        raise NotImplementedError

    def list_children(self, prefix: str) -> list[str]:
        """Immediate child 'directory' names directly under ``prefix`` (which should end with '/')."""
        raise NotImplementedError

    def delete_prefix(self, prefix: str) -> None:
        raise NotImplementedError

    # text convenience
    def put_text(self, key: str, text: str) -> None:
        self.put_bytes(key, text.encode("utf-8"))

    def get_text(self, key: str) -> str:
        return self.get_bytes(key).decode("utf-8")

    def describe(self, key: str = "") -> str:
        """Human-readable location (for the UI)."""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Local filesystem
# ---------------------------------------------------------------------------

class LocalStorage(Storage):
    def __init__(self, root: Path):
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        key = key.strip("/")
        p = (self.root / key).resolve()
        if not str(p).startswith(str(self.root.resolve())):   # guard against path traversal
            raise ValueError(f"unsafe key: {key!r}")
        return p

    def put_bytes(self, key: str, data: bytes) -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def get_bytes(self, key: str) -> bytes:
        p = self._path(key)
        if not p.is_file():
            raise KeyError(key)
        return p.read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def list_children(self, prefix: str) -> list[str]:
        d = self._path(prefix)
        if not d.is_dir():
            return []
        return sorted(c.name for c in d.iterdir() if c.is_dir())

    def delete_prefix(self, prefix: str) -> None:
        p = self._path(prefix)
        if p.is_dir():
            shutil.rmtree(p)
        elif p.is_file():
            p.unlink()

    def describe(self, key: str = "") -> str:
        return str((self.root / key).resolve())


# ---------------------------------------------------------------------------
# OCI Object Storage
# ---------------------------------------------------------------------------

class ObjectStorage(Storage):
    """OCI Object Storage backend. ``oci`` is imported lazily so this file loads without it."""

    def __init__(self, *, namespace: Optional[str], bucket: str, region: Optional[str],
                 auth: str = "instance_principal", config_file: str = "~/.oci/config",
                 profile: str = "DEFAULT"):
        if not bucket:
            raise ValueError("OCI_BUCKET is required when STORAGE_BACKEND=oci")
        import oci  # lazy — only needed in prod

        if auth == "instance_principal":
            signer = oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
            client = oci.object_storage.ObjectStorageClient(config={}, signer=signer)
        else:  # config_file / api key
            cfg = oci.config.from_file(file_location=config_file, profile_name=profile)
            client = oci.object_storage.ObjectStorageClient(cfg)
        if region:
            client.base_client.set_region(region)

        self._client = client
        self._oci = oci
        self.bucket = bucket
        self.namespace = namespace or client.get_namespace().data

    def put_bytes(self, key: str, data: bytes) -> None:
        self._client.put_object(self.namespace, self.bucket, key, data)

    def get_bytes(self, key: str) -> bytes:
        try:
            resp = self._client.get_object(self.namespace, self.bucket, key)
        except self._oci.exceptions.ServiceError as exc:
            if exc.status == 404:
                raise KeyError(key) from exc
            raise
        return resp.data.content

    def exists(self, key: str) -> bool:
        try:
            self._client.head_object(self.namespace, self.bucket, key)
            return True
        except self._oci.exceptions.ServiceError as exc:
            if exc.status == 404:
                return False
            raise

    def list_children(self, prefix: str) -> list[str]:
        prefix = prefix if prefix.endswith("/") else prefix + "/"
        names: set[str] = set()
        start = None
        while True:
            resp = self._client.list_objects(self.namespace, self.bucket, prefix=prefix,
                                             delimiter="/", start=start)
            for p in (resp.data.prefixes or []):             # e.g. "icp_library/<id>/"
                child = p[len(prefix):].strip("/")
                if child:
                    names.add(child)
            start = resp.data.next_start_with
            if not start:
                break
        return sorted(names)

    def delete_prefix(self, prefix: str) -> None:
        start = None
        while True:
            resp = self._client.list_objects(self.namespace, self.bucket, prefix=prefix, start=start)
            for obj in (resp.data.objects or []):
                self._client.delete_object(self.namespace, self.bucket, obj.name)
            start = resp.data.next_start_with
            if not start:
                break

    def describe(self, key: str = "") -> str:
        return f"oci://{self.bucket}/{key}" if key else f"oci://{self.bucket}"


# ---------------------------------------------------------------------------
# Factory (cached singleton)
# ---------------------------------------------------------------------------

_STORE: Optional[Storage] = None


def get_storage() -> Storage:
    global _STORE
    if _STORE is not None:
        return _STORE
    if (config.STORAGE_BACKEND or "local").lower() == "oci":
        _STORE = ObjectStorage(
            namespace=config.OCI_NAMESPACE, bucket=config.OCI_BUCKET, region=config.OCI_REGION,
            auth=config.OCI_AUTH, config_file=config.OCI_CONFIG_FILE,
            profile=config.OCI_CONFIG_PROFILE)
    else:
        _STORE = LocalStorage(config.STORAGE_LOCAL_ROOT)
    return _STORE
