"""Unit tests for the catalogue API (fake DynamoDB, no AWS calls)."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "src" / name / "app.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeTable:
    def __init__(self, items):
        self.items = {(i["PK"], i["SK"]): i for i in items}

    def query(self, **kwargs):            # GSI1 CATALOGUE, newest first (already in that order here)
        return {"Items": [i for i in self.items.values() if i.get("GSI1PK") == "CATALOGUE"]}

    def get_item(self, Key):
        item = self.items.get((Key["PK"], Key["SK"]))
        return {"Item": item} if item else {}


def lesson(video_id, topic, published=True, **extra):
    return {"PK": f"VIDEO#{video_id}", "SK": "META", "videoId": video_id, "title": f"Lesson {video_id}",
            "topicId": topic, "topicName": topic.upper(), "processingStatus": "READY",
            "objectivesStatus": "PUBLISHED" if published else "DRAFT_READY",
            "GSI1PK": "CATALOGUE" if published else "DRAFTS",
            "hlsPath": f"processed/{video_id}/hls/index.m3u8", "thumbnailPath": f"processed/{video_id}/thumb.0000000.jpg",
            "subtitleLanguages": {"fr", "en", "el"}, **extra}


ITEMS = [lesson("v3", "cdn"), lesson("v2", "careers"), lesson("v1", "cdn"), lesson("v0", "cdn", published=False)]


def setup():
    m = load("catalogue")
    m._tables = {"app": FakeTable(ITEMS)}
    return m


def event(resource, params=None, groups="learners"):
    return {"httpMethod": "GET", "resource": resource, "pathParameters": params,
            "requestContext": {"authorizer": {"claims": {"sub": "u1", "cognito:groups": groups}}}}


def test_courses_list_lessons_in_order_and_number_them():
    m = load("catalogue")
    m._tables = {"app": FakeTable([
        lesson("a", "cdn", publishedAt="2026-11-20T10:00:00Z"),
        lesson("b", "cdn", publishedAt="2026-11-20T09:00:00Z"),               # published first -> lesson 1
        lesson("c", "cdn", publishedAt="2026-11-20T11:00:00Z", position=1),   # an educator moved it to the front
        lesson("d", "careers", publishedAt="2026-11-20T08:00:00Z")])}
    body = json.loads(m.handler(event("/catalogue"), None)["body"])
    assert [r["topicId"] for r in body["rows"]] == ["careers", "cdn"]                  # courses A-Z
    cdn = body["rows"][1]
    assert [l["videoId"] for l in cdn["lessons"]] == ["c", "b", "a"]
    assert [l["lessonNumber"] for l in cdn["lessons"]] == [1, 2, 3] and cdn["lessonCount"] == 3
    assert body["rows"][1]["lessons"][0]["thumbnailUrl"] == "/media/processed/c/thumb.0000000.jpg"


def test_lesson_has_stream_and_subtitles_english_first():
    body = json.loads(setup().handler(event("/catalogue/{videoId}", {"videoId": "v1"}), None)["body"])
    assert body["hlsUrl"] == "/media/processed/v1/hls/index.m3u8"
    assert [s["lang"] for s in body["subtitles"]] == ["en", "el", "fr"]
    assert body["subtitles"][1]["label"] == "Ελληνικά"
    assert body["subtitles"][0]["url"] == "/media/processed/v1/transcript/subtitles.en.vtt"


def test_unpublished_lessons_are_hidden_and_signed_out_users_refused():
    m = setup()
    assert m.handler(event("/catalogue/{videoId}", {"videoId": "v0"}), None)["statusCode"] == 404
    assert m.handler(event("/catalogue", groups=""), None)["statusCode"] == 403
