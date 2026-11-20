"""Unit tests for the Step 4 Lambda functions (no AWS calls)."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load(name):
    """Load src/<name>/app.py as its own module (every function's file is called app.py)."""
    spec = importlib.util.spec_from_file_location(name, ROOT / "src" / name / "app.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def me_event(groups):
    return {"requestContext": {"authorizer": {"claims": {
        "sub": "user-123", "name": "Sam", "email": "sam@example.com", "cognito:groups": groups}}}}


def test_me_returns_user_and_groups_without_email():
    me = load("me")
    body = json.loads(me.handler(me_event("[creators, educators]"), None)["body"])
    assert body == {"userId": "user-123", "displayName": "Sam", "groups": ["creators", "educators"]}
    assert "email" not in body


def test_me_handles_single_group_and_no_group():
    me = load("me")
    assert me.parse_groups("learners") == ["learners"]
    assert me.parse_groups(None) == []
    assert me.parse_groups("[admins, learners]") == ["learners"]   # unknown groups ignored


class FakeCognito:
    def __init__(self):
        self.calls = []

    def admin_add_user_to_group(self, **kwargs):
        self.calls.append(kwargs)


def test_new_sign_up_is_added_to_learners():
    pc = load("post_confirmation")
    fake = FakeCognito()
    pc._client = fake
    event = {"triggerSource": "PostConfirmation_ConfirmSignUp", "userPoolId": "pool-1", "userName": "abc"}
    assert pc.handler(event, None) == event                          # event returned unchanged
    assert fake.calls == [{"UserPoolId": "pool-1", "Username": "abc", "GroupName": "learners"}]


def test_password_reset_does_not_change_groups():
    pc = load("post_confirmation")
    fake = FakeCognito()
    pc._client = fake
    pc.handler({"triggerSource": "PostConfirmation_ConfirmForgotPassword", "userPoolId": "p", "userName": "u"}, None)
    assert fake.calls == []
