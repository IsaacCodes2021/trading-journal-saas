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


def test_the_routes_are_in_the_schema():
    paths = spec()["paths"]
    assert set(paths["/api/v1/session"]) == {"get"}
    assert set(paths["/api/v1/settings"]) == {"patch"}
    assert set(paths["/api/v1/dashboard"]) == {"get"}
    assert set(paths["/api/v1/filter-options"]) == {"get"}
    assert set(paths["/api/v1/tags"]) == {"get", "post"}
    assert set(paths["/api/v1/trades/{trade_id}/notes"]) == {"put"}
    assert set(paths["/api/v1/trades/{trade_id}/tags/{tag_id}"]) == {"put", "delete"}
    assert set(paths["/api/v1/accounts"]) == {"get", "post"}
    assert set(paths["/api/v1/accounts/{account_id}"]) == {"put"}
    assert set(paths["/api/v1/accounts/{account_id}/rules"]) == {"put"}
    assert set(paths["/api/v1/accounts/{account_id}/archive"]) == {"post"}
    assert set(paths["/api/v1/accounts/{account_id}/restore"]) == {"post"}
    assert set(paths["/api/v1/accounts/{account_id}/fees"]) == {"get"}
    assert set(paths["/api/v1/accounts/{account_id}/fees/{symbol_root}"]) == {"put", "delete"}
    assert set(paths["/api/v1/accounts/{account_id}/ledger"]) == {"get", "post"}
    assert set(paths["/api/v1/accounts/{account_id}/ledger/{entry_id}"]) == {"delete"}
    assert set(paths["/api/v1/account-overviews"]) == {"get"}
    assert set(paths["/api/v1/business-totals"]) == {"get"}
    assert set(paths["/api/v1/tag-groups"]) == {"post"}
    assert set(paths["/api/v1/tag-groups/{group_id}"]) == {"put", "delete"}
    assert set(paths["/api/v1/tags/{tag_id}"]) == {"put", "delete"}
    assert set(paths["/api/v1/tags/{tag_id}/merge"]) == {"post"}
    assert set(paths["/api/v1/imports"]) == {"get", "post"}
    assert set(paths["/api/v1/imports/preview"]) == {"post"}
    assert set(paths["/api/v1/imports/{import_id}/undo"]) == {"post"}
    assert set(paths["/api/v1/imports/{import_id}/undo-preview"]) == {"post"}
    assert set(paths["/api/v1/reports"]) == {"get"}
    assert set(paths["/api/v1/trades"]) == {"get"}
    assert set(paths["/api/v1/trades/{trade_id}"]) == {"get"}
    assert set(paths["/api/v1/trades/{trade_id}/neighbors"]) == {"get"}
    assert "/healthz" not in paths  # server plumbing, not part of the contract the SPA codes against


def test_operation_ids_are_the_unique_handler_names():
    ids = [operation["operationId"] for path in spec()["paths"].values() for operation in path.values()]
    assert len(ids) == len(set(ids))  # two handlers with one name would silently merge in the generated client
    # Named after what the handler does, not what FastAPI would derive from the path (a path change must not rename
    # a generated operation). A few of each kind are pinned; new routes are covered by the uniqueness check above.
    assert {"get_session", "patch_settings", "get_dashboard", "list_trades", "put_notes", "create_account"} <= set(ids)
    assert not [i for i in ids if i.endswith(("_get", "_put", "_post", "_delete", "_patch")) and "_api_v1_" in i]


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


def test_every_json_request_body_refuses_fields_it_does_not_know():
    """A misspelled or invented field is a 422, not a silent no-op (an optional field that is mistyped would otherwise be
    ignored, and a plan override would lose its end date). Multipart bodies are forms, checked by their own routes."""
    document = spec()
    schemas = document["components"]["schemas"]
    open_bodies = []
    for path, operations in document["paths"].items():
        for method, operation in operations.items():
            body = operation.get("requestBody", {}).get("content", {}).get("application/json")
            if body is None:
                continue
            ref = body["schema"].get("$ref")
            assert ref is not None, f"{method.upper()} {path} has an inline request body: give it a named model"
            if schemas[ref.split("/")[-1]].get("additionalProperties") is not False:
                open_bodies.append(f"{method.upper()} {path}")
    assert not open_bodies, f"these accept unknown fields: {open_bodies}"
