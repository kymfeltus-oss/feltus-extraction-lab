"""Exercise the raw GoTrue signup shapes and orphaned-account recovery."""

import unittest
import io
import json
import urllib.error
from unittest import mock

from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.auth import SupabaseAuth
from app.main import app
from app.signup_routes import _supabase_request


USER_ID = "11111111-1111-4111-8111-111111111111"
ORG = {"id": "22222222-2222-4222-8222-222222222222", "name": "Test Org", "slug": "test-org"}
PAYLOAD = {
    "full_name": "Test User",
    "organization_name": "Test Org",
    "email": "test@example.invalid",
    "password": "long-enough-password",
}


class SignupResponseTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def _register(self, signup_response, membership=None):
        calls = []

        def request(method, path, body=None, **kwargs):
            calls.append((method, path))
            if path == "/auth/v1/signup":
                return signup_response
            if method == "GET" and path.startswith("/rest/v1/organization_members?"):
                return [{"organization": ORG}] if membership else []
            if path == "/rest/v1/organizations?select=id,name,slug":
                return [ORG]
            if path == "/rest/v1/organization_members" and method == "POST":
                return {}
            raise AssertionError((method, path))

        with mock.patch("app.signup_routes._supabase_request", side_effect=request):
            result = self.client.post("/api/auth/register", json=PAYLOAD)
        return result, calls

    def test_confirmation_signup_uses_top_level_user_and_provisions_workspace(self):
        response, calls = self._register({
            "id": USER_ID, "email": PAYLOAD["email"],
            "identities": [{"id": "identity-1"}],
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["user"]["id"], USER_ID)
        self.assertTrue(response.json()["requires_confirmation"])
        self.assertEqual(response.json()["organization"]["id"], ORG["id"])
        self.assertIn(("POST", "/rest/v1/organization_members"), calls)

    def test_repeat_signup_repairs_orphan_without_duplicate_workspace(self):
        response, calls = self._register(
            {"id": USER_ID, "identities": [{"id": "identity-1"}]},
            membership=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["organization"]["id"], ORG["id"])
        self.assertNotIn(("POST", "/rest/v1/organization_members"), calls)

    def test_obfuscated_existing_user_is_not_provisioned(self):
        response, calls = self._register({"id": USER_ID, "identities": []})
        self.assertEqual(response.status_code, 409)
        self.assertFalse(any(path.startswith("/rest/v1/") for _, path in calls))

    def test_immediate_session_still_uses_nested_user(self):
        response, _ = self._register({
            "user": {"id": USER_ID, "identities": [{"id": "identity-1"}]},
            "access_token": "test-access-token",
            "expires_in": 3600,
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["authenticated"])

    def test_confirmed_orphan_gets_workspace_after_validating_token(self):
        auth = SupabaseAuth()
        user = {
            "id": USER_ID,
            "email": PAYLOAD["email"],
            "user_metadata": {"full_name": "Test User"},
        }
        memberships = [{"id": ORG["id"], "name": ORG["name"], "slug": ORG["slug"], "role": "owner"}]
        with (
            mock.patch.object(auth, "get_user", return_value=user),
            mock.patch.object(auth, "get_organizations", side_effect=[[], memberships]) as get_orgs,
            mock.patch("app.signup_routes.ensure_organization", return_value=ORG) as ensure,
        ):
            context = auth.get_auth_context("valid-token")
        self.assertEqual(context.organization_id, ORG["id"])
        self.assertEqual(get_orgs.call_count, 2)
        ensure.assert_called_once_with(USER_ID, "Test User's Workspace")

    def test_provider_rate_limit_is_not_masked_as_server_error(self):
        error = urllib.error.HTTPError(
            url="https://example.supabase.co/auth/v1/signup",
            code=429,
            msg="Too Many Requests",
            hdrs={"Retry-After": "120"},
            fp=io.BytesIO(json.dumps({
                "code": 429,
                "msg": "Email rate limit exceeded",
            }).encode()),
        )
        with mock.patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(HTTPException) as caught:
                _supabase_request("POST", "/auth/v1/signup", {"email": PAYLOAD["email"]})
        self.assertEqual(caught.exception.status_code, 429)
        self.assertIn("Email rate limit exceeded", caught.exception.detail)
        self.assertEqual(caught.exception.headers["Retry-After"], "120")


if __name__ == "__main__":
    unittest.main()
