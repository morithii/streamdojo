"""Unit tests for POST/GET /videos with fake DynamoDB and S3 (no AWS calls)."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load():
    spec = importlib.util.spec_from_file_location("videos", ROOT / "src" / "videos" / "app.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeTable:
    def __init__(self, items=None):
        self.put = []
        self.items = items or []

    def put_item(self, **kwargs):
        self.put.append(kwargs)

    def query(self, **kwargs):
        return {"Items": self.items}


class FakeS3:
    def generate_presigned_post(self, **kwargs):
        self.kwargs = kwargs
        return {"url": "https://s3.example/upload", "fields": {"key": kwargs["Key"]}}


def event(groups, method="POST", body=None):
    return {
        "httpMethod": method,
        "body": json.dumps(body) if body is not None else None,
        "requestContext": {"authorizer": {"claims": {"sub": "creator-1", "cognito:groups": groups}}},
    }


def setup(items=None):
    videos = load()
    videos._table, videos._s3 = FakeTable(items), FakeS3()
    return videos


GOOD = {"title": "CloudFront and private S3", "contentType": "video/mp4", "fileSizeBytes": 50_000_000}


def test_creator_gets_signed_upload_and_lesson_record():
    videos = setup()
    res = videos.handler(event("[creators, educators]", body=GOOD), None)
    assert res["statusCode"] == 201
    item = videos._table.put[0]["Item"]
    assert item["processingStatus"] == "UPLOADING"
    assert item["captionStatus"] == "PENDING" and item["objectivesStatus"] == "PENDING"
    assert item["GSI2PK"] == "CREATOR#creator-1"
    assert item["rawKey"].startswith("raw/") and item["rawKey"].endswith(".mp4")
    # S3 enforces the size limit and file type from the signed policy
    conditions = videos._s3.kwargs["Conditions"]
    assert ["content-length-range", 1, videos.MAX_BYTES] in conditions
    assert {"Content-Type": "video/mp4"} in conditions


def test_learner_cannot_upload():
    videos = setup()
    res = videos.handler(event("learners", body=GOOD), None)
    assert res["statusCode"] == 403
    assert videos._table.put == []


def test_rejects_bad_input():
    videos = setup()
    for bad in [{**GOOD, "title": "x"}, {**GOOD, "contentType": "image/png"},
                {**GOOD, "fileSizeBytes": 3 * 1024 ** 3}, {**GOOD, "fileSizeBytes": "big"}]:
        assert videos.handler(event("creators", body=bad), None)["statusCode"] == 400


def test_lists_only_my_videos_newest_first():
    videos = setup(items=[{"videoId": "v1", "title": "Lesson", "processingStatus": "UPLOADING", "GSI2PK": "x"}])
    res = videos.handler(event("creators", method="GET"), None)
    body = json.loads(res["body"])
    assert body["videos"][0]["videoId"] == "v1"
    assert "GSI2PK" not in body["videos"][0]          # internal keys never leave the API


def test_list_includes_progress_details_as_plain_json():
    from decimal import Decimal
    videos = setup(items=[{"videoId": "v1", "title": "Lesson", "processingStatus": "READY", "captionStatus": "PENDING",
                           "subtitleLanguages": {"fr", "en"}, "objectiveCount": Decimal(2)}])   # what DynamoDB returns
    body = json.loads(videos.handler(event("creators", method="GET"), None)["body"])
    assert body["videos"][0]["subtitleLanguages"] == ["en", "fr"] and body["videos"][0]["objectiveCount"] == 2


# ---------------------------------------------------------------------------
# DELETE /videos/{videoId}
# ---------------------------------------------------------------------------
class FakeLessonTable:
    def __init__(self, item):
        self.item, self.updates = item, []

    def get_item(self, Key):
        return {"Item": dict(self.item)} if self.item else {}

    def update_item(self, **kwargs):
        if "attribute_not_exists(deletedAt)" in kwargs.get("ConditionExpression", "") and self.item.get("deletedAt"):
            raise RuntimeError("ConditionalCheckFailedException")
        self.updates.append(kwargs)
        self.item.update(deletedAt=kwargs["ExpressionAttributeValues"][":now"], deletedBy=kwargs["ExpressionAttributeValues"][":u"],
                         processingStatus="DELETED")
        for gsi in ("GSI1PK", "GSI1SK", "GSI2PK", "GSI2SK"):
            self.item.pop(gsi, None)


class FakeEvents:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    def put_events(self, Entries):
        self.sent.extend(Entries)
        return {"FailedEntryCount": 1 if self.fail else 0}


def finished_lesson(**extra):
    return {"PK": "VIDEO#v1", "SK": "META", "title": "OAC explained", "creatorSub": "creator-1", "topicId": "cloud",
            "createdAt": "2026-11-01T10:00:00+00:00", "processingStartedAt": "2026-11-01T10:00:05+00:00",
            "processingStatus": "READY", "captionStatus": "READY", "objectivesStatus": "PUBLISHED",
            "GSI1PK": "CATALOGUE", "GSI1SK": "x", "GSI2PK": "CREATOR#creator-1", "GSI2SK": "y", **extra}


def delete_event(groups, sub="creator-1", body=None):
    return {"httpMethod": "DELETE", "pathParameters": {"videoId": "v1"}, "body": json.dumps(body) if body else None,
            "requestContext": {"authorizer": {"claims": {"sub": sub, "cognito:groups": groups}}}}


def setup_delete(item, fail=False):
    videos = load()
    videos._table, videos._events = FakeLessonTable(item), FakeEvents(fail)
    return videos


def test_creator_deletes_own_video_it_disappears_at_once_and_cleanup_is_announced():
    videos = setup_delete(finished_lesson())
    res = videos.handler(delete_event("[creators]"), None)
    assert res["statusCode"] == 202
    item = videos._table.item
    assert item["processingStatus"] == "DELETED" and "GSI1PK" not in item and "GSI2PK" not in item   # out of every list
    (sent,) = videos._events.sent
    detail = json.loads(sent["Detail"])
    assert sent["DetailType"] == "LessonDeleted" and detail["videoId"] == "v1" and detail["byEducator"] is False


def test_another_creator_cannot_delete_but_an_educator_can_with_a_reason():
    videos = setup_delete(finished_lesson())
    assert videos.handler(delete_event("[creators]", sub="someone-else"), None)["statusCode"] == 403
    assert videos.handler(delete_event("[learners]"), None)["statusCode"] == 403
    res = videos.handler(delete_event("[educators]", sub="edu-1", body={"reason": "Duplicate of lesson 3"}), None)
    assert res["statusCode"] == 202
    detail = json.loads(videos._events.sent[0]["Detail"])
    assert detail["byEducator"] is True and detail["reason"] == "Duplicate of lesson 3" and detail["creatorSub"] == "creator-1"


def test_cannot_delete_while_the_pipeline_is_still_working_on_it():
    from datetime import datetime, timezone
    just_now = datetime.now(timezone.utc).isoformat()
    busy = finished_lesson(processingStatus="PROCESSING", captionStatus="PENDING", objectivesStatus="PENDING",
                           createdAt=just_now, processingStartedAt=just_now)
    videos = setup_delete(busy)
    assert videos.handler(delete_event("[creators]"), None)["statusCode"] == 409
    assert videos._events.sent == []
    # an upload that never arrived, or a failed one, can be deleted straight away
    for status in ("UPLOADING", "FAILED"):
        videos = setup_delete({**busy, "processingStatus": status})
        assert videos.handler(delete_event("[creators]"), None)["statusCode"] == 202


def test_if_the_cleanup_event_fails_a_retry_resends_it():
    videos = setup_delete(finished_lesson(), fail=True)
    assert videos.handler(delete_event("[creators]"), None)["statusCode"] == 502       # hidden, but cleanup didn't start
    videos._events = FakeEvents()
    assert videos.handler(delete_event("[creators]"), None)["statusCode"] == 202       # already DELETED -> just re-announce
    assert len(videos._table.updates) == 1                                            # hidden only once


def test_unknown_video_is_404():
    videos = setup_delete(None)
    assert videos.handler(delete_event("[creators]"), None)["statusCode"] == 404
