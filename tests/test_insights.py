"""Unit tests for educator insights (fake AWS, no calls)."""
import importlib.util
import json
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "src" / name / "app.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeTable:
    """Returns the items in two pages, to prove the function follows LastEvaluatedKey."""
    def __init__(self, items):
        self.items, self.calls = items, []

    def query(self, **kwargs):
        self.calls.append(kwargs)
        half = len(self.items) // 2
        if "ExclusiveStartKey" not in kwargs:
            return {"Items": self.items[:half], "LastEvaluatedKey": {"PK": "x", "SK": "y"}}
        return {"Items": self.items[half:]}


class FakeBedrock:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("model unavailable")
        return {"output": {"message": {"content": [{"text": "- Point one\n- Point two\n- Point three"}]}},
                "usage": {"inputTokens": 300, "outputTokens": 60}}


APP_ITEMS = [
    {"PK": "VIDEO#v1", "SK": "META", "title": "OAC", "topicName": "CDN", "objectivesStatus": "PUBLISHED"},
    {"PK": "VIDEO#v1", "SK": "OBJ#obj1", "objectiveId": "obj1", "objective": "Explain OAC", "timestamp": "01:16"},
    {"PK": "VIDEO#v1", "SK": "STATS#obj1", "attempts": Decimal(10), "unaidedFull": Decimal(4), "hintsUsed": Decimal(3),
     "dontRemember": Decimal(1), "missed_p1": Decimal(2), "missed_p2": Decimal(6)},
    {"PK": "VIDEO#v1", "SK": "LEARNER#u1#OBJ#obj1", "learnerSub": "u1", "displayName": "Sam", "intervalDays": Decimal(7),
     "nextReviewAt": "2999-01-01T00:00:00Z", "lastResult": "UNAIDED_FULL"},
    {"PK": "VIDEO#v1", "SK": "LEARNER#u2#OBJ#obj1", "learnerSub": "u2", "displayName": "Alex", "intervalDays": Decimal(1),
     "nextReviewAt": "2020-01-01T00:00:00Z", "lastResult": "NEEDS_PRACTICE"},
]
KEY_ITEMS = [{"PK": "VIDEO#v1", "SK": "OBJ#obj1", "objectiveId": "obj1", "essentialPoints": [
    {"pointId": "p1", "text": "The bucket stays private"}, {"pointId": "p2", "text": "CloudFront signs its requests"}]}]


def setup(bedrock=None, app_items=APP_ITEMS):
    m = load("insights")
    m.TABLE_NAME, m.KEYS_TABLE_NAME = "app", "keys"
    m._clients = {"table:app": FakeTable(app_items), "table:keys": FakeTable(KEY_ITEMS),
                  "bedrock-runtime": bedrock or FakeBedrock()}
    return m


def event(groups="educators", query=None):
    return {"httpMethod": "GET", "resource": "/insights/lessons/{videoId}", "pathParameters": {"videoId": "v1"},
            "queryStringParameters": query,
            "requestContext": {"authorizer": {"claims": {"sub": "e1", "cognito:groups": groups}}}}


def test_learners_cannot_see_insights():
    assert setup().handler(event(groups="learners"), None)["statusCode"] == 403


def test_recall_rate_and_most_missed_point_across_pages():
    body = json.loads(setup().handler(event(), None)["body"])
    obj = body["objectives"][0]
    assert obj["attempts"] == 10 and obj["unaidedRecallRate"] == 0.4
    assert obj["mostMissedPoint"] == "CloudFront signs its requests" and obj["points"][0]["missRate"] == 0.6
    assert obj["thinEvidence"] is False


def test_learners_who_need_help_come_first():
    learners = json.loads(setup().handler(event(), None)["body"])["learners"]
    assert [l["displayName"] for l in learners] == ["Alex", "Sam"]
    assert learners[0]["needsHelp"] is True and learners[0]["overdue"] == 1
    assert learners[1]["needsHelp"] is False and learners[1]["longestIntervalDays"] == 7


def test_summary_sees_numbers_but_never_learner_names():
    bedrock = FakeBedrock()
    body = json.loads(setup(bedrock).handler(event(), None)["body"])
    sent = bedrock.calls[0]["messages"][0]["content"][0]["text"]
    assert sent.startswith("<stats>") and "0.6" in sent
    assert "Sam" not in sent and "Alex" not in sent
    assert body["summary"].startswith("- Point one")


def test_numbers_still_returned_when_the_model_fails_or_is_skipped():
    body = json.loads(setup(FakeBedrock(fail=True)).handler(event(), None)["body"])
    assert body["summary"] is None and body["objectives"][0]["attempts"] == 10
    bedrock = FakeBedrock()
    setup(bedrock).handler(event(query={"summary": "false"}), None)
    assert bedrock.calls == []


def test_no_practice_yet_skips_the_model():
    bedrock = FakeBedrock()
    body = json.loads(setup(bedrock, app_items=APP_ITEMS[:2]).handler(event(), None)["body"])
    assert bedrock.calls == [] and body["summary"].startswith("No counted practice yet")
