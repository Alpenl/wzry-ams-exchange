from dataclasses import FrozenInstanceError
from pathlib import Path
from stat import S_IMODE

import pytest

from wzry_ams.credentials import (
    ActivityIdentity,
    CredentialFormatError,
    Credentials,
    CredentialStore,
    CredentialStoreError,
    MissingCredentialFieldsError,
)


def test_parse_json_returns_immutable_credentials() -> None:
    credentials = Credentials.parse('{"openid":"ABC","access_token":"XYZ"}')

    assert credentials.to_dict() == {"openid": "ABC", "access_token": "XYZ"}
    with pytest.raises(TypeError):
        credentials.values["openid"] = "changed"  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        credentials.values = {}  # type: ignore[misc]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "openid=ABC\naccess_token=XYZ\nappid=101491592\nacctype=qc",
            {
                "openid": "ABC",
                "access_token": "XYZ",
                "appid": "101491592",
                "acctype": "qc",
            },
        ),
        (
            "openid=ABC; access_token=XYZ; appid=101491592; acctype=qc",
            {
                "openid": "ABC",
                "access_token": "XYZ",
                "appid": "101491592",
                "acctype": "qc",
            },
        ),
        (
            "# Netscape HTTP Cookie File\n"
            "#HttpOnly_.qq.com\tTRUE\t/\tTRUE\t0\topenid\tABC\n"
            ".qq.com\tTRUE\t/\tTRUE\t0\taccess_token\tXYZ",
            {"openid": "ABC", "access_token": "XYZ"},
        ),
    ],
)
def test_parse_supported_cookie_text_formats(raw: str, expected: dict[str, str]) -> None:
    assert Credentials.parse(raw).to_dict() == expected


def test_parse_keeps_the_first_same_name_cookie_scope() -> None:
    raw = (
        "pvp.qq.com\tFALSE\t/\tTRUE\t0\tskey\tACTIVITY\n"
        "smoba.ams.game.qq.com\tFALSE\t/\tTRUE\t0\tskey\tAMS"
    )

    assert Credentials.parse(raw).values["skey"] == "ACTIVITY"


@pytest.mark.parametrize("raw", ["[]", '"openid=ABC"', "123", "null"])
def test_parse_rejects_json_that_is_not_an_object(raw: str) -> None:
    with pytest.raises(CredentialFormatError, match="JSON object"):
        Credentials.parse(raw)


@pytest.mark.parametrize(
    "build",
    [
        lambda: Credentials.parse('{"openid":123}'),
        lambda: Credentials.from_mapping({"openid": 123}),
    ],
)
def test_credentials_reject_non_string_values(build: object) -> None:
    with pytest.raises(CredentialFormatError, match="openid"):
        build()  # type: ignore[operator]


def test_activity_identity_is_derived_from_encoded_tyinfo() -> None:
    credentials = Credentials.from_mapping(
        {
            "a20161115tyf_tyinfo": (
                "iRet%2C0@zf_openid%2COFFICIAL@ty_openid%2CEXPERIENCE@"
                "zf_area%2C2@zf_partition%2C2001@exp_voucher%2C9981"
            )
        }
    )

    assert credentials.activity_identity == ActivityIdentity(
        experience_openid="EXPERIENCE",
        official_openid="OFFICIAL",
        area="2",
        partition="2001",
    )
    assert credentials.experience_voucher == "9981"


def test_missing_experience_voucher_uses_unknown_marker() -> None:
    assert Credentials.from_mapping({}).experience_voucher == "?"


def test_exchange_readiness_requires_activity_identity_beyond_login_fields() -> None:
    login_values = {
        "openid": "ABC",
        "access_token": "XYZ",
        "appid": "101491592",
        "acctype": "qc",
    }
    login_credentials = Credentials.from_mapping(login_values)

    assert login_credentials.is_login_ready is True
    assert login_credentials.require_login_ready() is login_credentials
    assert login_credentials.is_exchange_ready is False
    with pytest.raises(MissingCredentialFieldsError) as error:
        login_credentials.require_exchange_ready()
    assert error.value.purpose == "exchange"
    assert error.value.missing_fields == (
        "iegams_milo_proxylogin_qc",
        "a20161115tyf_tyinfo.zf_openid",
        "a20161115tyf_tyinfo.ty_openid",
        "a20161115tyf_tyinfo.zf_area",
        "a20161115tyf_tyinfo.zf_partition",
    )

    exchange_credentials = Credentials.from_mapping(
        login_values
        | {
            "iegams_milo_proxylogin_qc": "PROXY-TICKET",
            "a20161115tyf_tyinfo": (
                "zf_openid,OFFICIAL@ty_openid,EXPERIENCE@"
                "zf_area,2@zf_partition,2001"
            )
        }
    )
    assert exchange_credentials.is_exchange_ready is True
    assert exchange_credentials.require_exchange_ready() is exchange_credentials


def test_login_readiness_reports_absent_and_empty_fields() -> None:
    credentials = Credentials.from_mapping(
        {"openid": "ABC", "access_token": "", "appid": "101491592"}
    )

    assert credentials.is_login_ready is False
    with pytest.raises(MissingCredentialFieldsError) as error:
        credentials.require_login_ready()
    assert error.value.purpose == "login"
    assert error.value.missing_fields == ("access_token", "acctype")
    assert str(error.value) == (
        "Credential Bundle is not login-ready; missing fields: access_token, acctype"
    )


def test_store_distinguishes_optional_and_required_missing_file(tmp_path: Path) -> None:
    path = tmp_path / "credentials.json"
    store = CredentialStore(path)

    assert store.load() is None
    with pytest.raises(CredentialStoreError, match=str(path)):
        store.load(required=True)


def test_store_replace_round_trips_with_private_permissions(tmp_path: Path) -> None:
    path = tmp_path / "state" / "credentials.json"
    store = CredentialStore(path)

    saved = store.replace(
        "openid=ABC; access_token=XYZ; appid=101491592; acctype=qc"
    )

    assert store.load(required=True) == saved
    assert saved.to_dict() == {
        "openid": "ABC",
        "access_token": "XYZ",
        "appid": "101491592",
        "acctype": "qc",
    }
    assert S_IMODE(path.stat().st_mode) == 0o600


def test_store_replace_preserves_only_complete_old_activity_identity(tmp_path: Path) -> None:
    store = CredentialStore(tmp_path / "credentials.json")
    old_tyinfo = (
        "zf_openid,OLD-OFFICIAL@ty_openid,OLD-EXPERIENCE@"
        "zf_area,1@zf_partition,1306@exp_voucher,500"
    )
    store.replace(
        Credentials.from_mapping(
            {
                "openid": "OLD",
                "access_token": "OLD-TOKEN",
                "appid": "101491592",
                "acctype": "qc",
                "stale": "do-not-copy",
                "a20161115tyf_tyinfo": old_tyinfo,
            }
        )
    )
    fresh_same_account = Credentials.from_mapping(
        {
            "openid": "OLD",
            "access_token": "NEW-TOKEN",
            "appid": "101491592",
            "acctype": "qc",
        }
    )

    preserved = store.replace(fresh_same_account)

    assert preserved.values["a20161115tyf_tyinfo"] == old_tyinfo
    assert preserved.activity_identity == ActivityIdentity(
        experience_openid="OLD-EXPERIENCE",
        official_openid="OLD-OFFICIAL",
        area="1",
        partition="1306",
    )
    assert "stale" not in preserved.values

    discarded = store.replace(fresh_same_account, preserve_activity_identity=False)
    assert discarded.activity_identity is None
    assert "a20161115tyf_tyinfo" not in discarded.values


def test_store_never_preserves_activity_identity_across_accounts(tmp_path: Path) -> None:
    store = CredentialStore(tmp_path / "credentials.json")
    store.replace(
        Credentials.from_mapping(
            {
                "openid": "OLD",
                "access_token": "OLD-TOKEN",
                "appid": "101491592",
                "acctype": "qc",
                "a20161115tyf_tyinfo": (
                    "zf_openid,OLD-OFFICIAL@ty_openid,OLD-EXPERIENCE@"
                    "zf_area,1@zf_partition,1306"
                ),
            }
        )
    )

    switched = store.replace(
        Credentials.from_mapping(
            {
                "openid": "NEW",
                "access_token": "NEW-TOKEN",
                "appid": "101491592",
                "acctype": "qc",
            }
        )
    )

    assert switched.activity_identity is None
    assert "a20161115tyf_tyinfo" not in switched.values


def test_failed_atomic_replace_keeps_previous_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "credentials.json"
    store = CredentialStore(path)
    original = store.replace("openid=OLD")

    def fail_replace(source: object, destination: object) -> None:
        raise OSError("simulated rename failure")

    monkeypatch.setattr("wzry_ams.credentials.os.replace", fail_replace)

    with pytest.raises(CredentialStoreError, match="simulated rename failure"):
        store.replace("openid=NEW", preserve_activity_identity=False)
    assert store.load(required=True) == original
    assert list(tmp_path.glob(".credentials.json.*.tmp")) == []


def test_store_clear_is_idempotent(tmp_path: Path) -> None:
    store = CredentialStore(tmp_path / "credentials.json")
    store.replace("openid=ABC")

    store.clear()
    assert store.load() is None

    store.clear()
    assert store.load() is None
