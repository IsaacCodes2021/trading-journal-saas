import json

from tradejournal.api import openapi


def test_the_schema_is_deterministic_and_valid_json():
    first, second = openapi.render(), openapi.render()
    assert first == second
    spec = json.loads(first)
    assert spec["openapi"].startswith("3.")
    assert spec["info"]["title"] == "Trading Journal API"
    assert spec["info"]["version"] == "1"


def test_the_committed_schema_is_up_to_date():
    # Same check as CI's: if this fails, run `python -m tradejournal.api.openapi` and commit backend/openapi.json,
    # then `pnpm gen:api` in frontend/ and commit the regenerated types.
    assert openapi.main(["--check"]) == 0


def test_check_fails_when_the_file_is_stale_or_missing(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(openapi, "SPEC_PATH", tmp_path / "openapi.json")
    assert openapi.main(["--check"]) == 1  # missing
    (tmp_path / "openapi.json").write_text("{}\n", encoding="utf-8")
    assert openapi.main(["--check"]) == 1  # stale
    assert "out of date" in capsys.readouterr().err
    assert openapi.main([]) == 0  # writing fixes it
    assert openapi.main(["--check"]) == 0
