"""Unit tests for the case-list Lambda. boto3 (the DynamoDB resource) + the
Cognito verifier are mocked -- NO live AWS, NO network.

Covers the auth contract + owner-scoping the handler decides:

  * SIGNED-IN owner -> 200 with the UNION of both GSIs (user_id-index +
    owner_user_id-index), de-duped by ``_id``, tombstones (deleted/archived)
    excluded, marshaled to CaseSummary-shaped dicts (``_id -> case_id``, no
    user-link fields, Decimal coerced).
  * ANONYMOUS (no token / verify -> None) -> 200 with an EMPTY list, NEVER 401,
    and the table is never even queried.
  * UNSET table (CASES_TABLE="") -> 200 EMPTY list, no query.

The verifier (``cognito_verify``) is patched per test the same way the wake
tests patch it -- the real JWKS/RS256 verify is exercised by its own copies; here
we only drive the handler's branches. The DynamoDB resource is a MagicMock whose
``Table(...).query`` returns per-GSI item pages, mirroring the boto3 resource
``query`` shape (``{"Items": [...]}``, optional ``LastEvaluatedKey``).
"""

from __future__ import annotations

import importlib.util
import json
from decimal import Decimal
from pathlib import Path
from unittest import mock

import pytest

_HERE = Path(__file__).resolve().parent
_CASE_LIST_HANDLER = _HERE.parent / "handler.py"

_UID = "user-abc-123"
_OTHER_UID = "user-xyz-999"


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("AWS_REGION", "us-west-2")
    monkeypatch.setenv("CASES_TABLE", "grace2_cases")
    # A configured pool so cognito_verify is the only gate the test patches.
    monkeypatch.setenv("GRACE2_COGNITO_USER_POOL_ID", "us-west-2_TESTPOOL")
    monkeypatch.setenv("GRACE2_COGNITO_CLIENT_ID", "testclientid")


def _load(*, table=None):
    """Import the case-list handler fresh with boto3.resource replaced by a mock
    DynamoDB resource. The resource is constructed at module import, so patch it
    first. Returns ``(module, resource, table)``.
    """
    if table is None:
        table = mock.MagicMock(name="table")
    resource = mock.MagicMock(name="ddb_resource")
    resource.Table.return_value = table
    spec = importlib.util.spec_from_file_location(
        "case_list_handler_under_test", _CASE_LIST_HANDLER
    )
    module = importlib.util.module_from_spec(spec)
    with mock.patch("boto3.resource", return_value=resource):
        spec.loader.exec_module(module)
    return module, resource, table


def _body(resp):
    return json.loads(resp["body"])


def _set_verify(monkeypatch, module, claims):
    """Patch the module's Cognito verifier to return ``claims`` for any token."""
    monkeypatch.setattr(module, "cognito_verify", lambda token: claims)


def _gsi_query_table(pages_by_index: dict[str, list[list[dict]]]):
    """Build a fake DynamoDB Table whose ``query`` returns pre-canned pages
    keyed by ``IndexName``.

    ``pages_by_index`` maps a GSI name -> a list of page item-lists. Each
    ``query`` call (per ExclusiveStartKey) pops the next page; the last page
    omits ``LastEvaluatedKey`` so the handler's pagination loop terminates.
    """
    table = mock.MagicMock(name="table")
    state = {idx: list(pages) for idx, pages in pages_by_index.items()}

    def _query(**kwargs):
        idx = kwargs.get("IndexName")
        pages = state.get(idx, [])
        if not pages:
            return {"Items": []}
        items = pages.pop(0)
        resp = {"Items": items}
        if pages:
            resp["LastEvaluatedKey"] = {"_id": "cursor"}
        return resp

    table.query.side_effect = _query
    return table


def _get(*, token=None):
    """Build an API Gateway payload-2.0 GET event with an optional bearer."""
    event: dict = {"requestContext": {"http": {"method": "GET"}}}
    if token is not None:
        event["headers"] = {"authorization": f"Bearer {token}"}
    return event


# --------------------------------------------------------------------------- #
# Signed-in owner-scoped union.
# --------------------------------------------------------------------------- #


def test_signed_in_unions_both_gsis_dedup_by_id(env, monkeypatch):
    """A verified uid -> 200 with the union of both GSIs, de-duped by _id.

    Case A is found only via user_id-index, Case B only via owner_user_id-index,
    and Case C is projected into BOTH -> it must appear exactly once.
    """
    case_a = {"_id": "01A", "title": "Alpha", "user_id": _UID, "status": "active"}
    case_b = {
        "_id": "01B",
        "title": "Bravo",
        "owner_user_id": _UID,
        "status": "active",
    }
    case_c = {
        "_id": "01C",
        "title": "Charlie",
        "user_id": _UID,
        "owner_user_id": _UID,
        "status": "active",
    }
    table = _gsi_query_table(
        {
            "user_id-index": [[case_a, case_c]],
            "owner_user_id-index": [[case_b, case_c]],
        }
    )
    module, _resource, _table = _load(table=table)
    _set_verify(monkeypatch, module, {"uid": _UID})

    resp = module.handler(_get(token="good.jwt"), None)
    assert resp["statusCode"] == 200
    body = _body(resp)
    assert body["envelope_type"] == "case-list"
    ids = sorted(c["case_id"] for c in body["cases"])
    assert ids == ["01A", "01B", "01C"]  # C once, not twice
    # Both GSIs were Queried with the uid.
    queried_indexes = {
        call.kwargs.get("IndexName") for call in table.query.call_args_list
    }
    assert queried_indexes == {"user_id-index", "owner_user_id-index"}


def test_signed_in_marshal_strips_user_link_and_renames_id(env, monkeypatch):
    """Marshal: _id -> case_id, user_id/owner_user_id dropped, Decimal coerced,
    only CaseSummary fields survive."""
    doc = {
        "_id": "01D",
        "title": "Delta",
        "user_id": _UID,
        "owner_user_id": _UID,
        "status": "active",
        # bbox carries DynamoDB Decimals (floats become Decimal on write).
        "bbox": [Decimal("-82.5"), Decimal("26.0"), Decimal("-82.0"), Decimal("26.5")],
        # a storage-only field the contract envelope must NOT carry.
        "deleted_at": None,
        "primary_hazard": "flood",
    }
    table = _gsi_query_table({"user_id-index": [[doc]]})
    module, _resource, _table = _load(table=table)
    _set_verify(monkeypatch, module, {"uid": _UID})

    resp = module.handler(_get(token="good.jwt"), None)
    assert resp["statusCode"] == 200
    cases = _body(resp)["cases"]
    assert len(cases) == 1
    case = cases[0]
    assert case["case_id"] == "01D"
    assert "_id" not in case
    assert "user_id" not in case
    assert "owner_user_id" not in case
    assert "deleted_at" not in case  # storage-only, dropped
    assert case["title"] == "Delta"
    assert case["primary_hazard"] == "flood"
    # Decimals coerced to JSON floats.
    assert case["bbox"] == [-82.5, 26.0, -82.0, 26.5]
    # The body is JSON-serializable (no Decimal leaked through).
    assert json.dumps(case)


def test_signed_in_excludes_tombstones(env, monkeypatch):
    """deleted / archived Cases are excluded; a doc with no status is kept."""
    live = {"_id": "01L", "title": "Live", "user_id": _UID, "status": "active"}
    deleted = {"_id": "01X", "title": "Gone", "user_id": _UID, "status": "deleted"}
    archived = {
        "_id": "01Y",
        "title": "Old",
        "user_id": _UID,
        "status": "archived",
    }
    # A pre-status record (no status field) is live by definition.
    no_status = {"_id": "01Z", "title": "Legacy", "user_id": _UID}
    table = _gsi_query_table(
        {"user_id-index": [[live, deleted, archived, no_status]]}
    )
    module, _resource, _table = _load(table=table)
    _set_verify(monkeypatch, module, {"uid": _UID})

    resp = module.handler(_get(token="good.jwt"), None)
    assert resp["statusCode"] == 200
    ids = sorted(c["case_id"] for c in _body(resp)["cases"])
    assert ids == ["01L", "01Z"]  # tombstones gone, legacy kept


def test_signed_in_only_queries_with_own_uid(env, monkeypatch):
    """Owner-scoping: the GSI Query value is the VERIFIED uid, never another
    user's -- a verified user can only ever list their own Cases."""
    table = _gsi_query_table({"user_id-index": [[]], "owner_user_id-index": [[]]})
    module, _resource, _table = _load(table=table)
    _set_verify(monkeypatch, module, {"uid": _UID})

    resp = module.handler(_get(token="good.jwt"), None)
    assert resp["statusCode"] == 200
    assert _body(resp)["cases"] == []
    # Every Query's KeyConditionExpression must bind the verified uid (not
    # _OTHER_UID). boto3 Key(...).eq(value) -> an Equals condition whose
    # get_expression()["values"] is (Key, bound_value).
    assert table.query.call_args_list  # both GSIs were queried
    for call in table.query.call_args_list:
        cond = call.kwargs["KeyConditionExpression"]
        bound = cond.get_expression()["values"]
        assert bound[1] == _UID
        assert bound[1] != _OTHER_UID


# --------------------------------------------------------------------------- #
# Anonymous / unset-table -> 200 empty (never 401).
# --------------------------------------------------------------------------- #


def test_no_token_is_200_empty_and_no_query(env, monkeypatch):
    """No Authorization header -> 200 EMPTY list, NEVER 401, table never read."""
    table = _gsi_query_table({"user_id-index": [[{"_id": "leak", "title": "x"}]]})
    module, _resource, _table = _load(table=table)
    _set_verify(monkeypatch, module, None)  # belt-and-suspenders

    resp = module.handler(_get(), None)  # no token
    assert resp["statusCode"] == 200
    body = _body(resp)
    assert body["envelope_type"] == "case-list"
    assert body["cases"] == []
    table.query.assert_not_called()


def test_invalid_token_is_200_empty_and_no_query(env, monkeypatch):
    """An invalid token (verify -> None) -> 200 EMPTY, never 401, no query."""
    table = _gsi_query_table({"user_id-index": [[{"_id": "leak", "title": "x"}]]})
    module, _resource, _table = _load(table=table)
    _set_verify(monkeypatch, module, None)

    resp = module.handler(_get(token="bogus.jwt.token"), None)
    assert resp["statusCode"] == 200
    assert _body(resp)["cases"] == []
    table.query.assert_not_called()


def test_unset_table_is_200_empty_and_no_query(env, monkeypatch):
    """CASES_TABLE unset -> 200 EMPTY list, table never queried, even signed-in.

    Must clear the env BEFORE the module loads (CASES_TABLE is read at import).
    """
    monkeypatch.setenv("CASES_TABLE", "")
    table = _gsi_query_table({"user_id-index": [[{"_id": "leak", "title": "x"}]]})
    module, _resource, _table = _load(table=table)
    _set_verify(monkeypatch, module, {"uid": _UID})  # signed in, but no table

    resp = module.handler(_get(token="good.jwt"), None)
    assert resp["statusCode"] == 200
    assert _body(resp)["cases"] == []
    table.query.assert_not_called()


def test_options_preflight_is_200(env):
    """OPTIONS preflight -> 200, no verify, no query."""
    table = _gsi_query_table({})
    module, _resource, _table = _load(table=table)
    resp = module.handler(
        {"requestContext": {"http": {"method": "OPTIONS"}}}, None
    )
    assert resp["statusCode"] == 200
    table.query.assert_not_called()
    # CORS open for the browser.
    assert resp["headers"]["Access-Control-Allow-Origin"] == "*"


def test_query_error_degrades_to_empty_not_500(env, monkeypatch):
    """A DynamoDB error on the GSI Query degrades to a 200 empty list (the
    cold-open path must never surface a 500)."""
    table = mock.MagicMock(name="table")
    table.query.side_effect = RuntimeError("throttled")
    module, _resource, _table = _load(table=table)
    _set_verify(monkeypatch, module, {"uid": _UID})

    resp = module.handler(_get(token="good.jwt"), None)
    assert resp["statusCode"] == 200
    assert _body(resp)["cases"] == []
