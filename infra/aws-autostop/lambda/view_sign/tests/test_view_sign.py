"""Unit tests for the view-signer Lambda. boto3 (the S3 client + the DynamoDB
resource) + the Cognito verifier are mocked -- NO live AWS, NO network.

Covers the auth tiering + owner contract the handler decides, with a focus on
the Decision 10 sub -> internal ULID resolution (the box-off "signed-in owner
demoted to anon TTL" bug):

  * SIGNED-IN OWNER -> SIGNED_TTL (long). The snapshot owner metadata holds the
    INTERNAL ULID; the verified Cognito SUB is resolved to that ULID via the
    users table BEFORE the owner comparison, so the true owner gets the long TTL.
  * SIGNED-IN NON-OWNER (resolved ULID != owner) -> ANON_TTL (still a URL).
  * SIGNED-IN, NO users record (sub resolves to None) -> ANON_TTL (never a 500).
  * OWNER-LESS snapshot (no owner metadata) -> signed-in gets SIGNED_TTL (no
    owner-gate), anonymous gets ANON_TTL.
  * ANONYMOUS (verify -> None) -> ANON_TTL.
  * MISSING snapshot (HEAD 404) -> typed 404, no URL.

The S3 client is a fake that serves head_object (existence + owner metadata) and
generate_presigned_url. The DynamoDB resource is a MagicMock whose users-table
``query`` maps a sub -> {_id: ulid}. The verifier (``cognito_verify``) is patched
per test (the real JWKS/RS256 verify is exercised by the shared copies).
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from unittest import mock

import pytest

_HERE = Path(__file__).resolve().parent
_HANDLER = _HERE.parent / "handler.py"

# ``cognito_verify`` returns the Cognito SUB; the snapshot owner is the INTERNAL
# ULID resolved from the users table (Decision 10). Keep them distinct so the
# sub -> ULID resolution is actually exercised.
_SUB = "cognito-sub-abc-123"
_UID = "01ULIDOWNER0000000000000001"  # internal ULID the sub maps to
_OTHER_UID = "01ULIDOTHER0000000000000099"
_CASE_ID = "01CASE"

_USERS_TABLE = "grace2_users"
_RUNS_BUCKET = "grace2-hazard-runs-test"
_SIGNED_TTL = 43200
_ANON_TTL = 900


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("AWS_REGION", "us-west-2")
    monkeypatch.setenv("RUNS_BUCKET", _RUNS_BUCKET)
    monkeypatch.setenv("USERS_TABLE", _USERS_TABLE)
    monkeypatch.setenv("SIGNED_TTL", str(_SIGNED_TTL))
    monkeypatch.setenv("ANON_TTL", str(_ANON_TTL))
    monkeypatch.setenv("GRACE2_COGNITO_USER_POOL_ID", "us-west-2_TESTPOOL")
    monkeypatch.setenv("GRACE2_COGNITO_CLIENT_ID", "testclientid")


class _FakeS3:
    """In-memory S3: head_object serves existence + owner metadata; presign mints
    a fake URL. ``objects`` maps key -> {"owner": <ulid or None>} or is absent
    (a missing key raises a ClientError-shaped 404).
    """

    def __init__(self, objects=None):
        self.objects = dict(objects or {})

    def _not_found(self):
        from botocore.exceptions import ClientError

        return ClientError(
            {"Error": {"Code": "404"}, "ResponseMetadata": {"HTTPStatusCode": 404}},
            "HeadObject",
        )

    def head_object(self, Bucket, Key):  # noqa: N803
        if Key not in self.objects:
            raise self._not_found()
        owner = self.objects[Key].get("owner")
        meta = {"owner-user-id": owner} if owner else {}
        return {"Metadata": meta}

    def generate_presigned_url(self, ClientMethod, Params, ExpiresIn):  # noqa: N803
        return (
            f"https://{Params['Bucket']}.s3.amazonaws.com/"
            f"{Params['Key']}?X-Amz-Expires={ExpiresIn}&X-Amz-Signature=fake"
        )


def _users_table(sub_to_ulid):
    """A fake users Table whose firebase_uid-index Query maps sub -> {_id: ulid}.

    A sub absent from the mapping -> the GSI Query returns no Items (no record),
    so ``_resolve_internal_uid`` returns None.
    """
    mapping = dict(sub_to_ulid or {})
    table = mock.MagicMock(name="users_table")

    def _query(**kwargs):
        cond = kwargs.get("KeyConditionExpression")
        bound = cond.get_expression()["values"]
        sub = bound[1]
        ulid = mapping.get(sub)
        if ulid is None:
            return {"Items": []}
        return {"Items": [{"_id": ulid, "firebase_uid": sub}]}

    table.query.side_effect = _query
    return table


def _load(*, s3, sub_to_ulid=None):
    """Import the view-signer handler fresh with boto3.client (S3) +
    boto3.resource (DynamoDB) replaced. Both are constructed at module import, so
    patch first. ``sub_to_ulid`` (default {_SUB: _UID}) drives the sub -> internal
    ULID resolution that precedes the owner comparison.

    Returns ``(module, s3, users_table)``.
    """
    if sub_to_ulid is None:
        sub_to_ulid = {_SUB: _UID}
    users_table = _users_table(sub_to_ulid)
    resource = mock.MagicMock(name="ddb_resource")
    resource.Table.return_value = users_table

    def _client(name, **kwargs):
        assert name == "s3"
        return s3

    spec = importlib.util.spec_from_file_location("view_sign_handler_under_test", _HANDLER)
    module = importlib.util.module_from_spec(spec)
    with mock.patch("boto3.client", side_effect=_client), mock.patch(
        "boto3.resource", return_value=resource
    ):
        spec.loader.exec_module(module)
    return module, s3, users_table


def _body(resp):
    return json.loads(resp["body"])


def _set_verify(monkeypatch, module, claims):
    monkeypatch.setattr(module, "cognito_verify", lambda token: claims)
    module._uid_cache.clear()


def _get(*, token=None, case_id=_CASE_ID):
    event: dict = {"requestContext": {"http": {"method": "GET"}}}
    if case_id is not None:
        event["queryStringParameters"] = {"case_id": case_id}
    if token is not None:
        event["headers"] = {"authorization": f"Bearer {token}"}
    return event


def _key(case_id=_CASE_ID):
    return f"case-views/{case_id}.json"


# --------------------------------------------------------------------------- #
# Decision 10: sub -> internal ULID resolution drives the owner-tier decision.
# --------------------------------------------------------------------------- #


def test_signed_in_owner_resolved_ulid_gets_signed_ttl(env, monkeypatch):
    """The headline fix: the snapshot owner is the INTERNAL ULID; the verified
    SUB is resolved to that ULID before the owner comparison, so the true owner
    gets the LONG signed TTL (not wrongly demoted to anon)."""
    s3 = _FakeS3({_key(): {"owner": _UID}})
    module, _s3, _users = _load(s3=s3, sub_to_ulid={_SUB: _UID})
    _set_verify(monkeypatch, module, {"uid": _SUB})

    resp = module.handler(_get(token="good.jwt"), None)
    assert resp["statusCode"] == 200
    body = _body(resp)
    assert body["mode"] == "signed"
    assert body["expires_in"] == _SIGNED_TTL


def test_signed_in_non_owner_resolved_ulid_gets_anon_ttl(env, monkeypatch):
    """A signed-in user whose resolved ULID != the snapshot owner ULID still
    gets a URL, but at the anon (short) TTL."""
    s3 = _FakeS3({_key(): {"owner": _UID}})
    other_sub = "cognito-sub-xyz-999"
    module, _s3, _users = _load(s3=s3, sub_to_ulid={other_sub: _OTHER_UID})
    _set_verify(monkeypatch, module, {"uid": other_sub})

    resp = module.handler(_get(token="good.jwt"), None)
    assert resp["statusCode"] == 200
    body = _body(resp)
    assert body["mode"] == "anon"
    assert body["expires_in"] == _ANON_TTL


def test_signed_in_no_user_record_gets_anon_ttl_not_500(env, monkeypatch):
    """A verified sub with NO users record resolves to None -> the owner gate
    can't match -> anon TTL (never a 500)."""
    s3 = _FakeS3({_key(): {"owner": _UID}})
    module, _s3, _users = _load(s3=s3, sub_to_ulid={})  # no mapping
    _set_verify(monkeypatch, module, {"uid": _SUB})

    resp = module.handler(_get(token="good.jwt"), None)
    assert resp["statusCode"] == 200
    body = _body(resp)
    assert body["mode"] == "anon"
    assert body["expires_in"] == _ANON_TTL


def test_users_table_error_is_anon_ttl_not_500(env, monkeypatch):
    """A DynamoDB error resolving sub -> ULID fails closed to None -> anon TTL,
    never a 500."""
    s3 = _FakeS3({_key(): {"owner": _UID}})
    module, _s3, _users = _load(s3=s3, sub_to_ulid={_SUB: _UID})
    module._uid_cache.clear()
    boom = mock.MagicMock(name="users_table_boom")
    boom.query.side_effect = RuntimeError("throttled")
    module._ddb.Table.return_value = boom
    _set_verify(monkeypatch, module, {"uid": _SUB})

    resp = module.handler(_get(token="good.jwt"), None)
    assert resp["statusCode"] == 200
    assert _body(resp)["mode"] == "anon"


def test_owner_less_snapshot_signed_in_gets_signed_ttl(env, monkeypatch):
    """An owner-less snapshot (no owner metadata) is shareable: a signed-in user
    gets the signed TTL with no owner-gate (resolution is irrelevant)."""
    s3 = _FakeS3({_key(): {"owner": None}})
    module, _s3, _users = _load(s3=s3, sub_to_ulid={_SUB: _UID})
    _set_verify(monkeypatch, module, {"uid": _SUB})

    resp = module.handler(_get(token="good.jwt"), None)
    assert resp["statusCode"] == 200
    body = _body(resp)
    assert body["mode"] == "signed"
    assert body["expires_in"] == _SIGNED_TTL


# --------------------------------------------------------------------------- #
# Anonymous + missing-snapshot posture (unchanged by the resolution).
# --------------------------------------------------------------------------- #


def test_anonymous_gets_anon_ttl(env, monkeypatch):
    """No/invalid token (verify -> None) -> anon TTL; the users table is never
    queried (no sub to resolve)."""
    s3 = _FakeS3({_key(): {"owner": _UID}})
    module, _s3, users = _load(s3=s3)
    _set_verify(monkeypatch, module, None)

    resp = module.handler(_get(token="bogus.jwt"), None)
    assert resp["statusCode"] == 200
    body = _body(resp)
    assert body["mode"] == "anon"
    assert body["expires_in"] == _ANON_TTL
    users.query.assert_not_called()


def test_missing_snapshot_is_404(env, monkeypatch):
    """A snapshot that does not exist (HEAD 404) -> typed 404, no URL."""
    s3 = _FakeS3({})  # nothing stored
    module, _s3, _users = _load(s3=s3, sub_to_ulid={_SUB: _UID})
    _set_verify(monkeypatch, module, {"uid": _SUB})

    resp = module.handler(_get(token="good.jwt"), None)
    assert resp["statusCode"] == 404
    assert "error" in _body(resp)


def test_missing_case_id_is_400(env, monkeypatch):
    s3 = _FakeS3({_key(): {"owner": _UID}})
    module, _s3, _users = _load(s3=s3)
    _set_verify(monkeypatch, module, {"uid": _SUB})
    resp = module.handler(_get(token="good.jwt", case_id=None), None)
    assert resp["statusCode"] == 400


def test_options_preflight_is_200(env):
    s3 = _FakeS3({})
    module, _s3, _users = _load(s3=s3)
    resp = module.handler({"requestContext": {"http": {"method": "OPTIONS"}}}, None)
    assert resp["statusCode"] == 200
    assert resp["headers"]["Access-Control-Allow-Origin"] == "*"
