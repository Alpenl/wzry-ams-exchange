"""Credential Bundle parsing, validation, and persistence."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Literal, cast, overload
from urllib.parse import unquote

ACTIVITY_INFO_COOKIE = "a20161115tyf_tyinfo"
LOGIN_REQUIRED_FIELDS = ("openid", "access_token", "appid", "acctype")
EXCHANGE_REQUIRED_FIELDS = ("iegams_milo_proxylogin_qc",)
ACTIVITY_REQUIRED_FIELDS = ("zf_openid", "ty_openid", "zf_area", "zf_partition")


class CredentialError(ValueError):
    """Base error for invalid or unusable Credential Bundles."""


class CredentialFormatError(CredentialError):
    """Raised when credential input cannot be represented as string cookies."""


class MissingCredentialFieldsError(CredentialError):
    """Raised when a Credential Bundle is not ready for an operation."""

    def __init__(self, purpose: str, missing_fields: tuple[str, ...]) -> None:
        self.purpose = purpose
        self.missing_fields = missing_fields
        missing = ", ".join(missing_fields)
        super().__init__(
            f"Credential Bundle is not {purpose}-ready; missing fields: {missing}"
        )


class CredentialStoreError(CredentialError):
    """Raised when a Credential Bundle cannot be loaded or persisted."""


@dataclass(frozen=True)
class ActivityIdentity:
    """The account identifiers required by the AMS activity."""

    experience_openid: str
    official_openid: str
    area: str
    partition: str


@dataclass(frozen=True)
class Credentials:
    """An immutable Credential Bundle."""

    values: Mapping[str, str]

    def __post_init__(self) -> None:
        normalized: dict[str, str] = {}
        for name, value in self.values.items():
            if not isinstance(name, str):
                raise CredentialFormatError("Cookie names must be strings")
            if not isinstance(value, str):
                raise CredentialFormatError(f"Cookie value for {name!r} must be a string")
            normalized[name] = value
        object.__setattr__(self, "values", MappingProxyType(normalized))

    @classmethod
    def parse(cls, raw: str) -> Credentials:
        source = raw.strip()
        try:
            parsed = json.loads(source)
        except json.JSONDecodeError as exc:
            if source.startswith(("{", "[", '"')):
                raise CredentialFormatError(f"Invalid JSON credential input: {exc.msg}") from exc
            parsed = _parse_cookie_text(source)
        else:
            if not isinstance(parsed, dict):
                raise CredentialFormatError("Credential JSON must be a JSON object")
        return cls.from_mapping(cast(Mapping[str, object], parsed))

    @classmethod
    def from_mapping(cls, values: Mapping[str, object]) -> Credentials:
        if not isinstance(values, Mapping):
            raise CredentialFormatError("Credentials must be provided as a mapping")
        normalized: dict[str, str] = {}
        for name, value in values.items():
            if not isinstance(name, str):
                raise CredentialFormatError("Cookie names must be strings")
            if not isinstance(value, str):
                raise CredentialFormatError(f"Cookie value for {name!r} must be a string")
            normalized[name] = value
        return cls(normalized)

    def to_dict(self) -> dict[str, str]:
        return dict(self.values)

    @property
    def activity_identity(self) -> ActivityIdentity | None:
        info = _parse_activity_info(self.values.get(ACTIVITY_INFO_COOKIE, ""))
        if any(not info.get(field) for field in ACTIVITY_REQUIRED_FIELDS):
            return None
        return ActivityIdentity(
            experience_openid=info["ty_openid"],
            official_openid=info["zf_openid"],
            area=info["zf_area"],
            partition=info["zf_partition"],
        )

    @property
    def experience_voucher(self) -> str:
        info = _parse_activity_info(self.values.get(ACTIVITY_INFO_COOKIE, ""))
        return info.get("exp_voucher") or "?"

    @property
    def is_login_ready(self) -> bool:
        return not self._missing_login_fields()

    @property
    def is_exchange_ready(self) -> bool:
        return not self._missing_exchange_fields()

    def require_login_ready(self) -> Credentials:
        missing = self._missing_login_fields()
        if missing:
            raise MissingCredentialFieldsError("login", missing)
        return self

    def require_exchange_ready(self) -> Credentials:
        missing = self._missing_exchange_fields()
        if missing:
            raise MissingCredentialFieldsError("exchange", missing)
        return self

    def _missing_login_fields(self) -> tuple[str, ...]:
        return tuple(field for field in LOGIN_REQUIRED_FIELDS if not self.values.get(field))

    def _missing_exchange_fields(self) -> tuple[str, ...]:
        missing = list(self._missing_login_fields())
        missing.extend(
            field for field in EXCHANGE_REQUIRED_FIELDS if not self.values.get(field)
        )
        info = _parse_activity_info(self.values.get(ACTIVITY_INFO_COOKIE, ""))
        missing.extend(
            f"{ACTIVITY_INFO_COOKIE}.{field}"
            for field in ACTIVITY_REQUIRED_FIELDS
            if not info.get(field)
        )
        return tuple(missing)


class CredentialStore:
    """File-backed persistence for a Credential Bundle."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)

    @overload
    def load(self, required: Literal[True]) -> Credentials: ...

    @overload
    def load(self, required: Literal[False] = False) -> Credentials | None: ...

    def load(self, required: bool = False) -> Credentials | None:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            if required:
                raise CredentialStoreError(
                    f"Credential file does not exist: {self.path}"
                ) from exc
            return None
        except OSError as exc:
            raise CredentialStoreError(f"Cannot read credential file {self.path}: {exc}") from exc
        return Credentials.parse(raw)

    def replace(
        self,
        raw_or_credentials: str | Credentials,
        preserve_activity_identity: bool = True,
    ) -> Credentials:
        credentials = (
            Credentials.parse(raw_or_credentials)
            if isinstance(raw_or_credentials, str)
            else raw_or_credentials
        )
        if not isinstance(credentials, Credentials):
            raise TypeError("replace expects credential text or a Credentials instance")

        if preserve_activity_identity and credentials.activity_identity is None:
            existing = self.load()
            same_account = bool(
                existing is not None
                and credentials.values.get("openid")
                and credentials.values.get("openid") == existing.values.get("openid")
            )
            if same_account and existing is not None and existing.activity_identity is not None:
                values = credentials.to_dict()
                values[ACTIVITY_INFO_COOKIE] = existing.values[ACTIVITY_INFO_COOKIE]
                credentials = Credentials.from_mapping(values)

        self._atomic_write(credentials)
        return credentials

    def clear(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            return
        except OSError as exc:
            raise CredentialStoreError(f"Cannot clear credential file {self.path}: {exc}") from exc

    def _atomic_write(self, credentials: Credentials) -> None:
        parent = self.path.parent
        try:
            parent.mkdir(parents=True, exist_ok=True)
            fd, temporary_path = tempfile.mkstemp(
                dir=parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
            )
        except OSError as exc:
            raise CredentialStoreError(
                f"Cannot prepare credential file {self.path}: {exc}"
            ) from exc

        open_fd = fd
        try:
            os.fchmod(fd, 0o600)
            handle = os.fdopen(fd, "w", encoding="utf-8")
            open_fd = -1
            with handle:
                handle.write(
                    json.dumps(
                        credentials.to_dict(),
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.path)
        except OSError as exc:
            raise CredentialStoreError(f"Cannot write credential file {self.path}: {exc}") from exc
        finally:
            if open_fd >= 0:
                os.close(open_fd)
            with suppress(FileNotFoundError):
                os.unlink(temporary_path)


def _parse_cookie_text(source: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in source.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#") and not line.startswith("#HttpOnly_"):
            continue

        netscape_parts = line.split("\t")
        if len(netscape_parts) >= 7:
            values.setdefault(netscape_parts[5].strip(), netscape_parts[6].strip())
            continue

        for pair in line.split(";"):
            name, separator, value = pair.strip().partition("=")
            if separator and name:
                values.setdefault(name.strip(), value.strip())
    return values


def _parse_activity_info(cookie_value: str) -> dict[str, str]:
    info: dict[str, str] = {}
    for pair in unquote(cookie_value).split("@"):
        name, separator, value = pair.partition(",")
        if separator and name:
            info[name] = value
    return info
