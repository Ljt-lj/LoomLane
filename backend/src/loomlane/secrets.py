from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.parse import urlsplit

import keyring

from .models import SecretStatus


class CredentialError(RuntimeError):
    pass


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
    """Stores API keys in the operating-system credential vault.

    The account name is scoped to a project and profile, while the record also
    binds the key to the profile's API origin. Moving an SDL to another project
    therefore never reuses a key accidentally.
    """

    def __init__(self, service_name: str = "LoomLane") -> None:
        self.service_name = service_name

    def _account(self, project_id: str, profile_id: str) -> str:
        return f"{project_id}/{profile_id}"

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
        except keyring.errors.KeyringError as exc:
            raise CredentialError(f"System credential store is unavailable: {exc}") from exc
        return SecretStatus(profile_id=profile_id, configured=True, origin=origin)

    def _read(self, project_id: str, profile_id: str) -> CredentialRecord | None:
        try:
            payload = keyring.get_password(
                self.service_name,
                self._account(project_id, profile_id),
            )
        except keyring.errors.KeyringError as exc:
            raise CredentialError(
                f"System credential store is unavailable: {exc}"
            ) from exc
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
        try:
            keyring.delete_password(
                self.service_name,
                self._account(project_id, profile_id),
            )
        except keyring.errors.PasswordDeleteError:
            return
        except keyring.errors.KeyringError as exc:
            raise CredentialError(f"System credential store is unavailable: {exc}") from exc
