from __future__ import annotations

from pathlib import Path

import pytest

from loomlane import secrets
from loomlane.secrets import CredentialError, CredentialStore


def test_store_uses_keyring_when_available(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    entries: dict[tuple[str, str], str] = {}

    monkeypatch.setattr(
        secrets.keyring,
        "set_password",
        lambda service, account, payload: entries.__setitem__(
            (service, account), payload
        ),
    )
    monkeypatch.setattr(
        secrets.keyring,
        "get_password",
        lambda service, account: entries.get((service, account)),
    )
    monkeypatch.setattr(
        secrets.keyring,
        "delete_password",
        lambda service, account: entries.pop((service, account), None),
    )
    store = CredentialStore(fallback_path=tmp_path / "credentials.dpapi")

    status = store.put(
        "project",
        "profile",
        "secret-value",
        "https://api.deepseek.com/v1",
    )

    assert status.configured is True
    assert status.origin == "https://api.deepseek.com"
    assert store.get("project", "profile", "https://api.deepseek.com/v1") == (
        "secret-value"
    )
    assert not store.fallback_path.exists()


def test_store_uses_encrypted_fallback_when_keyring_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class NativeKeyringError(Exception):
        pass

    def unavailable(*_args: object) -> None:
        raise NativeKeyringError("CredWrite failed")

    def encrypt(data: bytes) -> bytes:
        return bytes(value ^ 0x5A for value in data)

    monkeypatch.setattr(secrets.keyring, "set_password", unavailable)
    monkeypatch.setattr(secrets.keyring, "get_password", unavailable)
    monkeypatch.setattr(secrets.keyring, "delete_password", unavailable)
    monkeypatch.setattr(secrets, "_dpapi_protect", encrypt)
    monkeypatch.setattr(secrets, "_dpapi_unprotect", encrypt)
    store = CredentialStore(fallback_path=tmp_path / "credentials.dpapi")

    status = store.put(
        "project",
        "profile",
        "secret-value",
        "https://api.deepseek.com/v1",
    )

    assert status.configured is True
    assert store.fallback_path.exists()
    assert b"secret-value" not in store.fallback_path.read_bytes()
    assert store.get("project", "profile", "https://api.deepseek.com/v1") == (
        "secret-value"
    )
    assert store.status(
        "project",
        "profile",
        "https://api.deepseek.com/v1",
    ).configured is True
    assert store.get("project", "profile", "https://api.openai.com/v1") is None

    store.delete("project", "profile")

    assert store.get("project", "profile", "https://api.deepseek.com/v1") is None
    assert not store.fallback_path.exists()


def test_store_reports_when_vault_and_fallback_are_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        secrets.keyring,
        "set_password",
        lambda *_args: (_ for _ in ()).throw(OSError("CredWrite failed")),
    )
    monkeypatch.setattr(
        secrets,
        "_dpapi_protect",
        lambda _data: (_ for _ in ()).throw(CredentialError("DPAPI failed")),
    )
    store = CredentialStore(fallback_path=tmp_path / "credentials.dpapi")

    with pytest.raises(
        CredentialError,
        match="System credential vault and encrypted local fallback",
    ):
        store.put(
            "project",
            "profile",
            "secret-value",
            "https://api.deepseek.com/v1",
        )


def test_delete_clears_keyring_and_stale_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    entries: dict[tuple[str, str], str] = {}

    def encrypt(data: bytes) -> bytes:
        return bytes(value ^ 0x5A for value in data)

    monkeypatch.setattr(
        secrets.keyring,
        "set_password",
        lambda service, account, payload: entries.__setitem__(
            (service, account), payload
        ),
    )
    monkeypatch.setattr(
        secrets.keyring,
        "get_password",
        lambda service, account: entries.get((service, account)),
    )
    monkeypatch.setattr(
        secrets.keyring,
        "delete_password",
        lambda service, account: entries.pop((service, account), None),
    )
    monkeypatch.setattr(secrets, "_dpapi_protect", encrypt)
    monkeypatch.setattr(secrets, "_dpapi_unprotect", encrypt)
    store = CredentialStore(fallback_path=tmp_path / "credentials.dpapi")
    store._put_fallback(
        "project/profile",
        '{"api_key":"stale-value","origin":"https://api.deepseek.com"}',
    )

    store.delete("project", "profile")

    assert store.get("project", "profile", "https://api.deepseek.com/v1") is None
    assert not store.fallback_path.exists()


def test_delete_reports_when_no_credential_store_is_available(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        secrets.keyring,
        "delete_password",
        lambda *_args: (_ for _ in ()).throw(OSError("CredDelete failed")),
    )
    store = CredentialStore(fallback_path=tmp_path / "credentials.dpapi")

    with pytest.raises(
        CredentialError,
        match="System credential vault is unavailable",
    ):
        store.delete("project", "profile")
