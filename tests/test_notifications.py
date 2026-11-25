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


def test_draft_ready_warns_educators_when_the_safety_check_flagged_it():
    m = notifier()
    m.handler(event("LessonDraftReady", {"videoId": "v1", "title": "AWS", "objectiveCount": 3,
                                         "moderationStatus": "FLAGGED", "flagCount": 2}), None)
    message = next(iter(m._clients["table"].items.values()))["message"]
    assert message.startswith("⚠") and "flagged 2 moment(s)" in message
    m = notifier()
    m.handler(event("LessonDraftReady", {"videoId": "v1", "title": "AWS", "objectiveCount": 3, "moderationStatus": "CLEAN"}), None)
    assert "safety" not in next(iter(m._clients["table"].items.values()))["message"]


def test_creator_is_told_when_an_educator_deletes_their_video_but_not_when_they_do():
    m = notifier()
    m.handler(event("LessonDeleted", {"videoId": "v1", "title": "AWS", "creatorSub": "c1", "byEducator": True,
                                      "reason": "Duplicate"}), None)
    ((key, item),) = m._clients["table"].items.items()
    assert key[0] == "USER#c1" and "An educator removed “AWS”. Reason: Duplicate" in item["message"]
    m = notifier()
    m.handler(event("LessonDeleted", {"videoId": "v1", "title": "AWS", "creatorSub": "c1", "byEducator": False}), None)
    assert m._clients["table"].items == {}


def test_a_new_belt_is_celebrated_for_the_learner_and_reported_to_educators():
    m = notifier()
    m.handler(event("BeltAwarded", {"learnerSub": "u9", "learnerName": "Sam", "topicId": "cloud",
                                    "topicName": "Cloud Skills", "belt": "GREEN", "score": 74}), None)
    by_user = {pk: item["message"] for (pk, _), item in m._clients["table"].items.items()}
    assert by_user["USER#u9"].startswith("🥋 You earned your Green belt in Cloud Skills (74%)")
    educator_messages = [msg for pk, msg in by_user.items() if pk != "USER#u9"]
    assert educator_messages and all(msg == "Sam earned a Green belt in Cloud Skills (74%)." for msg in educator_messages)


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


# ---------------------------------------------------------------------------
# Educator emails (Amazon SES)
# ---------------------------------------------------------------------------
class FakeCognitoWithEmail:
    def list_users_in_group(self, **kwargs):
        return {"Users": [
            {"Attributes": [{"Name": "sub", "Value": "edu1"}, {"Name": "email", "Value": "edu1@school.test"}], "Enabled": True},
            {"Attributes": [{"Name": "sub", "Value": "edu2"}], "Enabled": True}]}            # no email on file


class FakeSes:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    def send_email(self, **kwargs):
        if self.fail:
            raise RuntimeError("MessageRejected: Email address is not verified")
        self.sent.append(kwargs)


def emailer(fail=False):
    m = load("notify")
    m.EMAIL_FROM, m.SITE_URL = "dojo@streamdojo.test", "https://d123.cloudfront.net"
    m._clients = {"table": FakeTable(), "cognito-idp": FakeCognitoWithEmail(), "sesv2": FakeSes(fail)}
    return m


def test_draft_ready_emails_each_educator_with_a_link_and_escaped_title():
    m = emailer()
    result = m.handler(event("LessonDraftReady", {"videoId": "v1", "title": "<b>OAC</b>", "objectiveCount": 3,
                                                  "moderationStatus": "FLAGGED", "flagCount": 2}), None)
    assert result == {"written": 2, "emailed": 1}                       # edu2 has no address: bell only
    (mail,) = m._clients["sesv2"].sent
    assert mail["Destination"] == {"ToAddresses": ["edu1@school.test"]}  # one email each, nobody sees others
    assert mail["FromEmailAddress"] == "StreamDojo <dojo@streamdojo.test>"
    content = mail["Content"]["Simple"]
    assert content["Subject"]["Data"].startswith("⚠ Check before publishing")
    assert "https://d123.cloudfront.net/#/teach/review/v1" in content["Body"]["Text"]["Data"]
    assert "<b>OAC</b>" not in content["Body"]["Html"]["Data"] and "&lt;b&gt;OAC&lt;/b&gt;" in content["Body"]["Html"]["Data"]


def test_a_duplicate_delivery_never_sends_a_second_email():
    m = emailer()
    m.handler(event("LessonDraftReady", {"videoId": "v1", "title": "AWS"}), None)
    m.handler(event("LessonDraftReady", {"videoId": "v1", "title": "AWS"}), None)      # same event id again
    assert len(m._clients["sesv2"].sent) == 1


def test_only_review_events_are_emailed_and_email_is_off_without_a_sender():
    m = emailer()
    m.handler(event("PracticeCompleted", {"videoId": "v1", "title": "AWS", "learnerName": "Sam"}), None)
    assert m._clients["sesv2"].sent == []                                # bell only: no inbox flooding
    m.EMAIL_FROM = ""
    m.handler(event("LessonDraftReady", {"videoId": "v2", "title": "S3"}, event_id="e2"), None)
    assert m._clients["sesv2"].sent == []


def test_a_failed_email_still_leaves_the_bell_notification():
    m = emailer(fail=True)
    result = m.handler(event("LessonDraftReady", {"videoId": "v1", "title": "AWS"}), None)
    assert result == {"written": 2, "emailed": 0}
