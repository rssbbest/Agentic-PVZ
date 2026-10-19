"""Team accounts and the competition helpers (no database needed)."""
import pytest
from fastapi import HTTPException

from app.services import accounts, competition


def test_passwords_are_salted_and_checked():
    stored = accounts.hash_password("correct horse")
    assert stored != accounts.hash_password("correct horse")  # salted
    assert accounts.check_password("correct horse", stored)
    assert not accounts.check_password("wrong", stored)
    assert not accounts.check_password("x", None) and not accounts.check_password("x", "garbage")


def test_login_tokens_name_their_team_until_they_expire():
    token = accounts.issue_token(42, now=1000.0)
    assert accounts.token_team(token, now=1001.0) == 42
    assert accounts.token_team(token, now=1000.0 + accounts.TOKEN_TTL_S + 1) is None
    team, expires, sig = token.split(".")
    assert accounts.token_team(f"43.{expires}.{sig}", now=1001.0) is None  # forged team
    assert accounts.token_team("nonsense") is None


def test_llm_token_matches_the_gateways(monkeypatch):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # the repo root holds integration/
    from integration.gateway import team_token

    monkeypatch.setattr(accounts.settings, "AUTOPILOT_GATEWAY_TEAM_SECRET", "s3cret")
    assert accounts.llm_token(4) == team_token("0004", "s3cret")
    monkeypatch.setattr(accounts.settings, "AUTOPILOT_GATEWAY_TEAM_SECRET", "")
    assert accounts.llm_token(4) is None


def test_registration_form_and_answers_are_validated():
    form = competition.validate_form({**competition.DEFAULT_FORM, "questions": [
        {"id": "email", "label": "Email", "type": "email", "required": True},
        {"id": "size", "label": "Team size", "type": "select", "options": ["1", "2", "3"]},
        {"id": "age", "label": "Age", "type": "number"},
    ]})
    assert competition.check_answers(form, {"email": " a@b.co ", "size": "2", "age": "19"}) == {
        "email": "a@b.co", "size": "2", "age": 19.0}
    for bad in ({}, {"email": "nope"}, {"email": "a@b.co", "size": "9"}, {"email": "a@b.co", "age": "x"}):
        with pytest.raises(HTTPException):
            competition.check_answers(form, bad)
    with pytest.raises(HTTPException):
        competition.validate_form({"questions": [{"id": "password", "label": "x"}]})
    with pytest.raises(HTTPException):
        competition.validate_form({"questions": [{"id": "a", "type": "colour"}]})


def test_levels_need_enemies_and_get_default_settings():
    level = competition.normalise_level({"enemies": [{"enemyId": 1, "count": 3}]})
    assert level["defencePlacementData"] == [] and level["gameSettings"]["reinforcementsEnabled"]
    for bad in ({}, {"enemies": []}, {"enemies": [{"enemyId": 1, "count": 0}]}):
        with pytest.raises(HTTPException):
            competition.normalise_level(bad)


def test_shipped_test_cases_load_as_public_and_hidden():
    public = competition._seed_cases(competition.PUBLIC_SEED, hidden=False)
    hidden = competition._seed_cases(competition.HIDDEN_SEED, hidden=True)
    assert [c.code for c in public] == [f"TC{i:02d}" for i in range(1, 11)]
    assert public[0].name == "Balanced Opening" and not public[0].hidden
    assert hidden and all(c.hidden and c.code.startswith("H") for c in hidden)


def test_a_passed_password_check_is_remembered_but_not_for_a_wrong_password(monkeypatch):
    stored = accounts.hash_password("right password")
    assert accounts.check_password("right password", stored)
    calls = []
    monkeypatch.setattr(accounts, "_check_password", lambda *a: calls.append(a) or False)
    assert accounts.check_password("right password", stored) and not calls   # remembered
    assert not accounts.check_password("wrong", stored) and len(calls) == 1  # still checked
    assert not accounts.check_password("right password", accounts.hash_password("new"))  # new hash: checked again


def test_generated_passwords_are_random_readable_and_long_enough():
    many = {accounts.new_password() for _ in range(200)}
    assert len(many) == 200
    for pw in many:
        assert len(pw) == 11 and pw[5] == "-" and not set(pw) & set("01ilo")
