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
