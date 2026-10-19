"""Startup applies a changed test-case file to the running database."""
import asyncio
import json

from app.models import CompetitionSetting, TestCase
from app.services import competition

LEVEL = {"enemies": [{"enemyId": 0, "count": 3}]}


class FakeResult:
    def __init__(self, rows):
        self.rows = rows

    def first(self):
        return self.rows[0] if self.rows else None

    def scalars(self):
        return list(self.rows)


class FakeDB:
    """Just enough of AsyncSession for seed_test_cases."""

    def __init__(self):
        self.cases, self.settings = [], {}

    async def execute(self, _stmt):
        return FakeResult(self.cases)

    async def get(self, model, key):
        assert model is CompetitionSetting
        return self.settings.get(key)

    def add(self, row):
        if isinstance(row, CompetitionSetting):
            self.settings[row.key] = row
        else:
            self.cases.append(row)

    def add_all(self, rows):
        for row in rows:
            self.add(row)

    async def commit(self):
        pass


def write(path, cases):
    path.write_text(json.dumps(cases), encoding="utf-8")


def test_a_changed_file_updates_its_cases_and_keeps_admin_edits_otherwise(tmp_path, monkeypatch):
    public, hidden = tmp_path / "public.json", tmp_path / "hidden.json"
    monkeypatch.setattr(competition, "PUBLIC_SEED", public)
    monkeypatch.setattr(competition, "HIDDEN_SEED", hidden)
    write(public, [{"testCaseId": 1, "name": "TC01_Easy", **LEVEL}])
    write(hidden, [{"testCaseId": 1, "name": "H01_Secret", **LEVEL}])
    db = FakeDB()

    asyncio.run(competition.seed_test_cases(db))
    assert sorted(c.code for c in db.cases) == ["H01", "TC01"]

    # An admin edit survives a restart when the file did not change.
    tc01 = next(c for c in db.cases if c.code == "TC01")
    tc01.name = "Edited in admin"
    asyncio.run(competition.seed_test_cases(db))
    assert tc01.name == "Edited in admin" and len(db.cases) == 2

    # A pushed file change updates the case and adds new ones; the other file is untouched.
    write(public, [{"testCaseId": 1, "name": "TC01_Harder", "enemies": [{"enemyId": 2, "count": 9}]},
                   {"testCaseId": 2, "name": "TC02_New", **LEVEL}])
    asyncio.run(competition.seed_test_cases(db))
    assert tc01.name == "Harder" and tc01.level["enemies"] == [{"enemyId": 2, "count": 9}]
    assert sorted(c.code for c in db.cases) == ["H01", "TC01", "TC02"]


def test_a_broken_file_is_skipped_instead_of_stopping_the_server(tmp_path, monkeypatch):
    public = tmp_path / "public.json"
    monkeypatch.setattr(competition, "PUBLIC_SEED", public)
    monkeypatch.setattr(competition, "HIDDEN_SEED", tmp_path / "missing.json")
    write(public, [{"testCaseId": 1, "name": "TC01", **LEVEL}])
    db = FakeDB()
    asyncio.run(competition.seed_test_cases(db))
    write(public, [{"testCaseId": 1, "name": "TC01", "enemies": [{"enemyId": 7, "count": 3}]}])
    asyncio.run(competition.seed_test_cases(db))
    assert db.cases[0].level["enemies"] == LEVEL["enemies"]
