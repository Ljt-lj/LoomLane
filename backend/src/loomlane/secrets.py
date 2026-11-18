from __future__ import annotations

import ctypes
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import keyring

from .models import SecretStatus
from .settings import data_dir


class CredentialError(RuntimeError):
    pass


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", ctypes.c_ulong),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


def _dpapi_protect(data: bytes) -> bytes:
    if sys.platform != "win32":
        raise CredentialError(
            "Encrypted local credential fallback is only available on Windows"
        )

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        ctypes.c_wchar_p,
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptProtectData.restype = ctypes.c_int
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p

    buffer = ctypes.create_string_buffer(data, len(data))
    input_blob = _DataBlob(
        len(data),
        ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)),
    )
    output_blob = _DataBlob()
    if not crypt32.CryptProtectData(
        ctypes.byref(input_blob),
        "LoomLane API credential",
        None,
        None,
        None,
        0x1,
        ctypes.byref(output_blob),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32.LocalFree(output_blob.pbData)


def _dpapi_unprotect(data: bytes) -> bytes:
    if sys.platform != "win32":
        raise CredentialError(
            "Encrypted local credential fallback is only available on Windows"
        )

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        ctypes.POINTER(ctypes.c_wchar_p),
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptUnprotectData.restype = ctypes.c_int
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p

    buffer = ctypes.create_string_buffer(data, len(data))
    input_blob = _DataBlob(
        len(data),
        ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)),
    )
    output_blob = _DataBlob()
    if not crypt32.CryptUnprotectData(
        ctypes.byref(input_blob),
        None,
        None,
        None,
        None,
        0x1,
        ctypes.byref(output_blob),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32.LocalFree(output_blob.pbData)


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
        try:
            path.chmod(0o600)
        except OSError:
            pass
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def normalize_origin(base_url: str) -> str:
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise CredentialError("API Profile base_url must be an absolute HTTP URL")
    port = parsed.port
    default_port = 443 if parsed.scheme == "https" else 80
    authority = parsed.hostname
    if ":" in authority and not authority.startswith("["):
        authority = f"[{authority}]"
    if port and port != default_port:
        authority = f"{authority}:{port}"
    return f"{parsed.scheme}://{authority.lower()}"


@dataclass(frozen=True)
class CredentialRecord:
    api_key: str
    origin: str


class CredentialStore:
    """Stores API keys in the OS vault with an encrypted Windows fallback.

    The account name is scoped to a project and profile, while the record also
    binds the key to the profile's API origin. Moving an SDL to another project
    therefore never reuses a key accidentally.
    """

    def __init__(
        self,
        service_name: str = "LoomLane",
        fallback_path: Path | None = None,
    ) -> None:
        self.service_name = service_name
        self.fallback_path = fallback_path or data_dir() / "credentials.dpapi"

    def _account(self, project_id: str, profile_id: str) -> str:
        return f"{project_id}/{profile_id}"

    def _read_fallback_entries(self) -> dict[str, str]:
        if not self.fallback_path.exists():
            return {}
        try:
            protected = self.fallback_path.read_bytes()
            decoded = _dpapi_unprotect(protected).decode("utf-8")
            data = json.loads(decoded)
            services = data["services"]
            entries = services[self.service_name]
        except CredentialError:
            raise
        except Exception as exc:
            raise CredentialError(
                "Encrypted local credential store could not be read"
            ) from exc
        if not isinstance(entries, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in entries.items()
        ):
            raise CredentialError("Encrypted local credential store is malformed")
        return entries

    def _write_fallback_entries(self, entries: dict[str, str]) -> None:
        services = {self.service_name: entries}
        payload = json.dumps(
            {"version": 1, "services": services},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        try:
            protected = _dpapi_protect(payload)
        except CredentialError:
            raise
        except Exception as exc:
            raise CredentialError(
                "Encrypted local credential store could not be written"
            ) from exc
        try:
            _atomic_write_bytes(self.fallback_path, protected)
        except OSError as exc:
            raise CredentialError(
                "Encrypted local credential store could not be written"
            ) from exc

    def _get_fallback(self, account: str) -> str | None:
        return self._read_fallback_entries().get(account)

    def _put_fallback(self, account: str, payload: str) -> None:
        entries = self._read_fallback_entries()
        entries[account] = payload
        self._write_fallback_entries(entries)

    def _delete_fallback(self, account: str) -> bool:
        entries = self._read_fallback_entries()
        if account not in entries:
            return False
        del entries[account]
        if entries:
            self._write_fallback_entries(entries)
        else:
            try:
                self.fallback_path.unlink(missing_ok=True)
            except OSError as exc:
                raise CredentialError(
                    "Encrypted local credential store could not be updated"
                ) from exc
        return True

    def put(
        self,
        project_id: str,
        profile_id: str,
        api_key: str,
        base_url: str,
    ) -> SecretStatus:
        origin = normalize_origin(base_url)
        payload = json.dumps(
            {"api_key": api_key, "origin": origin},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        try:
            keyring.set_password(
                self.service_name,
                self._account(project_id, profile_id),
                payload,
            )
        except Exception:
            try:
                self._put_fallback(self._account(project_id, profile_id), payload)
            except CredentialError as fallback_exc:
                raise CredentialError(
                    "System credential vault and encrypted local fallback "
                    "are both unavailable"
                ) from fallback_exc
        return SecretStatus(profile_id=profile_id, configured=True, origin=origin)

    def _read(self, project_id: str, profile_id: str) -> CredentialRecord | None:
        payload = None
        try:
            payload = keyring.get_password(
                self.service_name,
                self._account(project_id, profile_id),
            )
        except Exception:
            pass
        if not payload:
            payload = self._get_fallback(self._account(project_id, profile_id))
        if not payload:
            return None
        try:
            data = json.loads(payload)
            api_key = data["api_key"]
            origin = data["origin"]
        except (KeyError, TypeError, ValueError) as exc:
            raise CredentialError("Stored credential record is malformed") from exc
        if not isinstance(api_key, str) or not isinstance(origin, str):
            raise CredentialError("Stored credential record is malformed")
        return CredentialRecord(api_key=api_key, origin=origin)

    def get(
        self,
        project_id: str,
        profile_id: str,
        base_url: str,
    ) -> str | None:
        record = self._read(project_id, profile_id)
        if record is None or record.origin != normalize_origin(base_url):
            return None
        return record.api_key

    def status(
        self,
        project_id: str,
        profile_id: str,
        base_url: str,
    ) -> SecretStatus:
        origin = normalize_origin(base_url)
        record = self._read(project_id, profile_id)
        configured = record is not None and record.origin == origin
        return SecretStatus(
            profile_id=profile_id,
            configured=configured,
            origin=origin,
        )

    def delete(self, project_id: str, profile_id: str) -> None:
        keyring_available = False
        try:
            keyring.delete_password(
                self.service_name,
                self._account(project_id, profile_id),
            )
            keyring_available = True
        except keyring.errors.PasswordDeleteError:
            keyring_available = True
        except Exception:
            pass
        fallback_deleted = self._delete_fallback(
            self._account(project_id, profile_id)
        )
        if not keyring_available and not fallback_deleted:
            raise CredentialError(
                "System credential vault is unavailable and no encrypted "
                "local fallback credential exists"
            )
