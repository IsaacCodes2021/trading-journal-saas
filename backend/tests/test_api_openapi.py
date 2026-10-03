import json

from tradejournal.api import openapi


def spec():
    return json.loads(openapi.render())


def walk(node):
    """Every dict and list nested anywhere in the document."""
    yield node
    children = node.values() if isinstance(node, dict) else node if isinstance(node, list) else ()
    for child in children:
        yield from walk(child)


def test_the_schema_is_deterministic_and_valid_json():
    first, second = openapi.render(), openapi.render()
    assert first == second
    document = json.loads(first)
    assert document["openapi"].startswith("3.")
    assert document["info"]["title"] == "Trading Journal API"
    assert document["info"]["version"] == "1"


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


def test_the_session_routes_are_in_the_schema():
    paths = spec()["paths"]
    assert set(paths["/api/v1/session"]) == {"get"}
    assert set(paths["/api/v1/settings"]) == {"patch"}
    assert "/healthz" not in paths  # server plumbing, not part of the contract the SPA codes against


def test_operation_ids_are_the_unique_handler_names():
    ids = [operation["operationId"] for path in spec()["paths"].values() for operation in path.values()]
    assert sorted(ids) == ["get_session", "patch_settings"]
    assert len(ids) == len(set(ids))  # two handlers with one name would silently merge in the generated client


def test_error_responses_are_typed_as_problem_not_fastapis_default():
    # FastAPI's built-in 422 body is not what errors.py sends; a client typed from it would be wrong.
    document = spec()
    assert "HTTPValidationError" not in document["components"]["schemas"]
    for path in document["paths"].values():
        for operation in path.values():
            for status, response in operation["responses"].items():
                if status.startswith(("4", "5")):
                    assert response["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/Problem"}


def test_no_money_can_be_typed_as_a_number():
    # Pydantic documents a plain Decimal in a request as "number or string", which would let the generated TypeScript
    # accept a JavaScript float. Money fields must use the `Money` type (doc 07 section 2).
    for node in walk(spec()):
        options = node.get("anyOf") if isinstance(node, dict) else None
        if options:
            types = {option.get("type") for option in options if isinstance(option, dict)}
            assert not {"number", "string"} <= types, f"a Decimal that is not Money: {node}"


def test_response_schemas_list_defaulted_fields_as_required():
    # The pairing with `openapi-typescript --default-non-nullable false` (doc 07 section 2).
    problem = spec()["components"]["schemas"]["Problem"]
    assert {"type", "detail", "errors"} <= set(problem["required"])
