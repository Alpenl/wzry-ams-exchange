import wzry_ams


def test_public_interface_exposes_domain_modules():
    assert wzry_ams.Credentials
    assert wzry_ams.CredentialStore
    assert wzry_ams.ExchangeClient
    assert wzry_ams.ExchangeReport
    assert wzry_ams.RedemptionOutcome
    assert wzry_ams.LoginResult


def test_reward_catalog_uses_stable_reward_ids():
    rewards = wzry_ams.reward_catalog()

    assert [reward.id for reward in rewards] == ["1", "2", "3", "4", "5", "6"]
    assert rewards[2].name == "星币福袋"
    assert rewards[5].cost == 900
