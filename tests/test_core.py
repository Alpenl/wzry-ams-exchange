"""Smoke tests — verify package imports and core logic."""

from wzry_ams import (
    REWARD_MAP,
    ExchangeClient,
    g_tk,
    get_user_info,
    md5,
    parse_cookies,
    parse_tyinfo,
    sdid,
    ts,
)


def test_reward_map():
    assert len(REWARD_MAP) == 6
    assert REWARD_MAP["1"]["name"] == "亲密玫瑰"
    assert REWARD_MAP["3"]["flowId"] == 407553
    assert REWARD_MAP["6"]["cost"] == 900
    for idx in map(str, range(1, 7)):
        assert idx in REWARD_MAP
        r = REWARD_MAP[idx]
        assert "name" in r
        assert "flowId" in r
        assert "cost" in r


def test_md5():
    assert md5("hello") == "5D41402ABC4B2A76B9719D911017C592"
    assert len(md5("")) == 32


def test_g_tk():
    # Known value from flowengine.js with skey='a1b2c3'
    assert g_tk("a1b2c3") == 1842395457
    assert g_tk("test") > 0
    assert g_tk("") == 5381


def test_sdid():
    assert len(sdid()) == 32
    assert sdid() != sdid()  # random


def test_ts():
    import time
    t = int(ts())
    now = int(time.time())
    assert abs(t - now) <= 1


def test_parse_tyinfo():
    raw = "iRet,0@sMsg,ok@exp_voucher,9981@zf_openid,ABC123@zf_area,1@zf_partition,1306@ty_openid,DEF456"
    info = parse_tyinfo(raw)
    assert info["iRet"] == "0"
    assert info["exp_voucher"] == "9981"
    assert info["zf_openid"] == "ABC123"
    assert info["ty_openid"] == "DEF456"
    assert info["zf_area"] == "1"
    assert info["zf_partition"] == "1306"


def test_parse_cookies_key_value():
    raw = "openid=ABC\naccess_token=XYZ\nappid=123\nacctype=qc"
    cookies = parse_cookies(raw)
    assert cookies["openid"] == "ABC"
    assert cookies["access_token"] == "XYZ"
    assert cookies["acctype"] == "qc"


def test_parse_cookies_semicolon():
    raw = "openid=ABC; access_token=XYZ; appid=123"
    cookies = parse_cookies(raw)
    assert cookies["openid"] == "ABC"
    assert cookies["access_token"] == "XYZ"


def test_parse_cookies_json():
    raw = '{"openid":"ABC","access_token":"XYZ"}'
    cookies = parse_cookies(raw)
    assert cookies["openid"] == "ABC"


def test_get_user_info():
    cookies = {"openid": "D4C31C0EF3CFE36CCC5793807699B380",
               "acctype": "qc",
               "a20161115tyf_tyinfo":
               "iRet%2C0@sMsg%2Cok@exp_voucher%2C9981@zf_openid%2CABC@"
               "zf_area%2C2@zf_partition%2C2001@ty_openid%2CDEF"}
    info = get_user_info(cookies)
    assert info["acctype"] == "qc"
    assert info["exp_voucher"] == "9981"
    assert info["area"] == "2"
    assert info["partition"] == "2001"
    assert info["has_tyinfo"] is True


def test_get_user_info_no_tyinfo():
    cookies = {"openid": "test", "acctype": "qc"}
    info = get_user_info(cookies)
    assert info["has_tyinfo"] is False
    assert info["exp_voucher"] == "?"


def test_exchange_client_init():
    cookies = {"openid": "test", "access_token": "tok",
               "appid": "123", "acctype": "qc",
               "a20161115tyf_tyinfo":
               "iRet%2C0@exp_voucher%2C500@zf_openid%2CZFO@"
               "zf_area%2C1@zf_partition%2C1306@ty_openid%2CTYO"}
    client = ExchangeClient(cookies)
    assert client.iuin == "ZFO"
    assert client.ty_openid == "TYO"
    assert client.area == "1"
    assert client.partition == "1306"
    assert client.exp_voucher == "500"


def test_exchange_reward_invalid():
    cookies = {"openid": "x", "access_token": "y", "appid": "z", "acctype": "qc"}
    client = ExchangeClient(cookies)
    result = client.exchange_reward("99")
    assert result["ok"] is False
    assert "无效" in result["msg"]
