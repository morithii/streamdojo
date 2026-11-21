"""Step 20: business events -> notifier -> bell. Fake AWS, no calls."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "src" / name / "app.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ConditionalFailed(Exception):
    def __init__(self):
        self.response = {"Error": {"Code": "ConditionalCheckFailedException"}}


class FakeTable:
    def __init__(self, items=()):
        self.items = {(i["PK"], i["SK"]): dict(i) for i in items}

    def put_item(self, Item, ConditionExpression=None):
        key = (Item["PK"], Item["SK"])
        if ConditionExpression and key in self.items:
            from botocore.exceptions import ClientError
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "PutItem")
        self.items[key] = dict(Item)

    def query(self, **kwargs):                  # newest first, like ScanIndexForward=False on NOTIF# keys
        return {"Items": sorted((dict(i) for i in self.items.values()), key=lambda i: i["SK"], reverse=True)}

    def update_item(self, Key, UpdateExpression, ExpressionAttributeValues, **kwargs):
        self.items[(Key["PK"], Key["SK"])]["read"] = ExpressionAttributeValues[":t"]


class FakeCognito:
    def list_users_in_group(self, **kwargs):
        return {"Users": [{"Attributes": [{"Name": "sub", "Value": "edu1"}], "Enabled": True},
                          {"Attributes": [{"Name": "sub", "Value": "edu2"}], "Enabled": True}]}


def notifier():
    m = load("notify")
    m._clients = {"table": FakeTable(), "cognito-idp": FakeCognito()}
    return m


def event(detail_type, detail, event_id="e1"):
    return {"id": event_id, "time": "2026-11-21T12:00:00Z", "detail-type": detail_type, "source": "streamdojo", "detail": detail}


def test_draft_ready_notifies_every_educator_with_a_link_to_review():
    m = notifier()
    m.handler(event("LessonDraftReady", {"videoId": "v1", "title": "AWS", "objectiveCount": 3}), None)
    items = m._clients["table"].items
    assert {k[0] for k in items} == {"USER#edu1", "USER#edu2"}
    item = next(iter(items.values()))
    assert "“AWS” is ready for review" in item["message"] and item["link"] == "#/teach/review/v1"
    assert item["read"] is False and item["expiresAt"] > 0


def test_duplicate_delivery_never_duplicates_a_notification():
    m = notifier()
    e = event("LessonRejected", {"videoId": "v1", "title": "AWS", "creatorSub": "c1", "reason": "Audio too quiet"})
    m.handler(e, None)
    assert m.handler(e, None)["written"] == 0                        # EventBridge is at-least-once
    (item,) = m._clients["table"].items.values()
    assert item["PK"] == "USER#c1" and "Audio too quiet" in item["message"]


def test_published_tells_only_the_creator():
    m = notifier()
    m.handler(event("LessonPublished", {"videoId": "v1", "title": "AWS", "creatorSub": "c1", "topicName": "Cloud Skills"}), None)
    assert list(m._clients["table"].items) == [("USER#c1", "NOTIF#2026-11-21T12:00:00Z#e1")]


def bell(items):
    m = load("notifications")
    m._tables = {"app": FakeTable(items)}
    return m


def bell_event(method, resource, sub="u1"):
    return {"httpMethod": method, "resource": resource, "requestContext": {"authorizer": {"claims": {"sub": sub}}}}


def test_bell_counts_unread_and_marks_them_read():
    items = [{"PK": "USER#u1", "SK": f"NOTIF#2026-11-21T1{i}:00:00Z#e{i}", "message": f"m{i}", "read": i == 0} for i in range(3)]
    m = bell(items)
    body = json.loads(m.handler(bell_event("GET", "/notifications"), None)["body"])
    assert body["unread"] == 2 and body["notifications"][0]["message"] == "m2"       # newest first
    assert json.loads(m.handler(bell_event("POST", "/notifications/read"), None)["body"])["marked"] == 2
    assert json.loads(m.handler(bell_event("GET", "/notifications"), None)["body"])["unread"] == 0
