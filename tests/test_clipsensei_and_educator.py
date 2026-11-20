"""Unit tests for ClipSensei objective generation and the educator API (fake AWS, no calls)."""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "src" / name / "app.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeTable:
    """Just enough of a DynamoDB Table for these tests, keyed by (PK, SK)."""
    def __init__(self):
        self.items = {}

    def put_item(self, Item, **kwargs):
        self.items[(Item["PK"], Item["SK"])] = dict(Item)

    def get_item(self, Key):
        item = self.items.get((Key["PK"], Key["SK"]))
        return {"Item": dict(item)} if item else {}

    def update_item(self, Key, ExpressionAttributeValues, ConditionExpression=None, **kwargs):
        item = self.items.setdefault((Key["PK"], Key["SK"]), {"PK": Key["PK"], "SK": Key["SK"]})
        if ConditionExpression == "objectivesStatus = :draft" and item.get("objectivesStatus") != "DRAFT_READY":
            raise RuntimeError("ConditionalCheckFailed")
        names = kwargs.get("ExpressionAttributeNames", {})
        for part in kwargs["UpdateExpression"].replace("SET ", "").split(","):
            attr, value = [s.strip() for s in part.split("=")]
            item[names.get(attr, attr)] = ExpressionAttributeValues[value]

    def query(self, **kwargs):
        return {"Items": [dict(i) for i in self.items.values()]}   # tests filter what they need


SEGMENTS = {"segments": [{"start": "00:05", "text": "OAC keeps the bucket private."},
                         {"start": "01:16", "text": "CloudFront signs its requests."}]}
GOOD = {"objectives": [{
    "objective": "Explain why Origin Access Control keeps an S3 origin private",
    "essentialPoints": ["The bucket blocks public access", "CloudFront signs its requests to S3"],
    "modelAnswer": "OAC lets CloudFront sign requests so the bucket can stay private.",
    "hint": "How does S3 know the request came from CloudFront?",
    "timestamp": "01:16",
    "prompts": ["Why use OAC?", "What happens if someone opens the S3 URL directly?", "Explain OAC to a colleague."],
}]}


class FakeBedrock:
    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        return {"output": {"message": {"content": [{"toolUse": {"input": self.answers.pop(0)}}]}},
                "usage": {"inputTokens": 1000, "outputTokens": 300}}


class FakeS3:
    def get_object(self, **kwargs):
        class Body:
            def read(self_inner):
                return json.dumps(SEGMENTS).encode()
        return {"Body": Body()}


def setup_generator(*answers):
    gen = load("generate_objectives")
    gen.TABLE_NAME, gen.KEYS_TABLE_NAME = "app", "keys"
    app, keys = FakeTable(), FakeTable()
    app.put_item(Item={"PK": "VIDEO#v1", "SK": "META", "createdAt": "2026-11-20T09:00:00Z",
                       "transcriptPath": "processed/v1/transcript/clean.json"})
    gen._clients = {"bedrock-runtime": FakeBedrock(*answers), "s3": FakeS3(), "table:app": app, "table:keys": keys}
    return gen, app, keys


def test_drafts_objectives_into_two_tables_and_joins_drafts_queue():
    gen, app, keys = setup_generator(GOOD)
    result = gen.handler({"videoId": "v1"}, None)
    assert result["objectives"] == 1
    public = app.items[("VIDEO#v1", "OBJ#obj1")]
    assert "essentialPoints" not in public and "modelAnswer" not in public      # no answers in the public item
    assert keys.items[("VIDEO#v1", "OBJ#obj1")]["essentialPoints"][1]["pointId"] == "p2"
    lesson = app.items[("VIDEO#v1", "META")]
    assert lesson["objectivesStatus"] == "DRAFT_READY" and lesson["GSI1PK"] == "DRAFTS"
    assert lesson["inputTokens"] == 1000


def test_transcript_is_wrapped_as_data_and_tool_use_is_forced():
    gen, _, _ = setup_generator(GOOD)
    gen.handler({"videoId": "v1"}, None)
    call = gen._clients["bedrock-runtime"].calls[0]
    assert call["messages"][0]["content"][0]["text"].startswith("<transcript>\n[00:05] OAC")
    assert call["toolConfig"]["toolChoice"] == {"tool": {"name": "save_objectives"}}


def test_retries_once_then_gives_up_on_invalid_drafts():
    bad = {"objectives": [{**GOOD["objectives"][0], "prompts": ["Same?", "Same?", "Same?"]}]}
    gen, _, _ = setup_generator(bad, GOOD)
    assert gen.handler({"videoId": "v1"}, None)["objectives"] == 1               # second draft accepted
    gen, _, _ = setup_generator(bad, bad)
    with pytest.raises(ValueError):
        gen.handler({"videoId": "v1"}, None)


def educator_event(groups, method, resource, params=None, body=None):
    return {"httpMethod": method, "resource": resource, "pathParameters": params,
            "body": json.dumps(body) if body is not None else None,
            "requestContext": {"authorizer": {"claims": {"sub": "edu-1", "cognito:groups": groups}}}}


def setup_educator(objectives_status="DRAFT_READY"):
    edu = load("educator")
    edu.TABLE_NAME, edu.KEYS_TABLE_NAME = "app", "keys"
    app, keys = FakeTable(), FakeTable()
    app.put_item(Item={"PK": "VIDEO#v1", "SK": "META", "videoId": "v1", "title": "OAC", "processingStatus": "READY",
                       "objectivesStatus": objectives_status, "GSI1PK": "DRAFTS"})
    app.put_item(Item={"PK": "VIDEO#v1", "SK": "OBJ#obj1", "objectiveId": "obj1", "objective": "Explain OAC",
                       "prompts": ["a?", "b?", "c?"], "timestamp": "01:16", "status": "DRAFT"})
    keys.put_item(Item={"PK": "VIDEO#v1", "SK": "OBJ#obj1", "objectiveId": "obj1",
                        "essentialPoints": [{"pointId": "p1", "text": "x"}], "modelAnswer": "m", "hint": "h", "status": "DRAFT"})
    edu._tables = {"app": app, "keys": keys}
    return edu, app, keys


def test_learners_and_creators_cannot_use_educator_api():
    edu, _, _ = setup_educator()
    for groups in ("learners", "creators"):
        assert edu.handler(educator_event(groups, "GET", "/lessons"), None)["statusCode"] == 403


def test_educator_sees_prompts_and_answer_key_together():
    edu, _, _ = setup_educator()
    res = edu.handler(educator_event("educators", "GET", "/lessons/{videoId}/objectives", {"videoId": "v1"}), None)
    obj = json.loads(res["body"])["objectives"][0]
    assert obj["prompts"] == ["a?", "b?", "c?"] and obj["modelAnswer"] == "m"


def test_publish_moves_lesson_from_drafts_to_catalogue():
    edu, app, keys = setup_educator()
    res = edu.handler(educator_event("[creators, educators]", "POST", "/lessons/{videoId}/publish",
                                     {"videoId": "v1"}, {"topic": "CloudFront & S3"}), None)
    assert res["statusCode"] == 200
    lesson = app.items[("VIDEO#v1", "META")]
    assert lesson["GSI1PK"] == "CATALOGUE" and lesson["objectivesStatus"] == "PUBLISHED"
    assert lesson["topicId"] == "cloudfront-s3"
    assert app.items[("VIDEO#v1", "OBJ#obj1")]["status"] == "PUBLISHED"
    assert keys.items[("VIDEO#v1", "OBJ#obj1")]["status"] == "PUBLISHED"
    assert ("TOPIC#cloudfront-s3", "LESSON#v1") in app.items


def test_cannot_publish_twice_or_without_drafts():
    edu, _, _ = setup_educator(objectives_status="PUBLISHED")
    res = edu.handler(educator_event("educators", "POST", "/lessons/{videoId}/publish",
                                     {"videoId": "v1"}, {"topic": "CloudFront"}), None)
    assert res["statusCode"] == 409


def test_edit_is_validated():
    edu, _, _ = setup_educator()
    res = edu.handler(educator_event("educators", "PUT", "/lessons/{videoId}/objectives/{objectiveId}",
                                     {"videoId": "v1", "objectiveId": "obj1"}, {"objective": "x"}), None)
    assert res["statusCode"] == 400
