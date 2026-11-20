"""Catalogue API - the Netflix-style home page and the lesson player. Read-only.

GET /catalogue              courses (one per topic), each with its lessons IN ORDER: 1, 2, 3...
GET /catalogue/{videoId}    one published lesson: video stream, subtitle tracks, thumbnail

Any signed-in user (learners, educators, creators). Only PUBLISHED lessons are ever returned.
Media links are relative ("/media/..."): the browser loads them from the same CloudFront
address as the website, and CloudFront fetches them from the private media bucket via OAC.
"""
import json
import os

import boto3
from boto3.dynamodb.conditions import Key

TABLE_NAME = os.environ.get("TABLE_NAME", "")
ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "*")
MEDIA_PREFIX = "/media/"
LANGUAGE_NAMES = {"en": "English", "fr": "Français", "es": "Español", "it": "Italiano", "el": "Ελληνικά"}
_tables = {}


def table():
    if "app" not in _tables:
        _tables["app"] = boto3.resource("dynamodb").Table(TABLE_NAME)
    return _tables["app"]


def response(status, body):
    return {"statusCode": status,
            "headers": {"Content-Type": "application/json", "Access-Control-Allow-Origin": ALLOWED_ORIGIN},
            "body": json.dumps(body, ensure_ascii=False,
                               default=lambda v: sorted(v) if isinstance(v, set) else (int(v) if v == int(v) else float(v)))}


def caller_groups(event):
    claims = event["requestContext"]["authorizer"]["claims"]
    raw = claims.get("cognito:groups") or ""
    return set(raw if isinstance(raw, list) else raw.strip("[]").replace(",", " ").split())


def media_url(path):
    return MEDIA_PREFIX + path if path else None


def card(item):
    """What a thumbnail tile on the home page needs - nothing more."""
    return {"videoId": item["videoId"], "title": item.get("title"), "topicId": item.get("topicId"),
            "topicName": item.get("topicName"), "durationMs": item.get("durationMs"),
            "thumbnailUrl": media_url(item.get("thumbnailPath")), "publishedAt": item.get("publishedAt"),
            "position": item.get("position")}


def lesson_order(lesson):
    """An educator-set position wins; otherwise lessons follow the order they were published."""
    position = lesson.get("position")
    return (int(position) if position is not None else 10 ** 6, lesson.get("publishedAt") or "")


def list_catalogue():
    items, kwargs = [], {"IndexName": "GSI1", "KeyConditionExpression": Key("GSI1PK").eq("CATALOGUE"),
                         "ScanIndexForward": False}          # GSI1SK = publishedAt#videoId: newest first
    while len(items) < 200:                                 # plenty for the MVP; paginate the API later
        page = table().query(**kwargs)
        items.extend(page.get("Items", []))
        if "LastEvaluatedKey" not in page:
            break
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    courses = {}
    for item in items:
        if item.get("objectivesStatus") != "PUBLISHED":     # belt and braces: GSI1 CATALOGUE is published only
            continue
        topic = item.get("topicId") or "other"
        courses.setdefault(topic, {"topicId": topic, "topicName": item.get("topicName") or "Other", "lessons": []})
        courses[topic]["lessons"].append(card(item))
    for course in courses.values():                         # a course is a curriculum: number the lessons in order
        course["lessons"].sort(key=lesson_order)
        for number, lesson in enumerate(course["lessons"], start=1):
            lesson["lessonNumber"] = number
        course["lessonCount"] = len(course["lessons"])
        course["totalDurationMs"] = sum(int(l.get("durationMs") or 0) for l in course["lessons"])
    rows = sorted(courses.values(), key=lambda c: c["topicName"].lower())     # courses A-Z
    first = rows[0]["lessons"][0] if rows else None
    return response(200, {"featured": first, "rows": rows})


def get_lesson(video_id):
    item = table().get_item(Key={"PK": f"VIDEO#{video_id}", "SK": "META"}).get("Item")
    if not item or item.get("objectivesStatus") != "PUBLISHED" or item.get("processingStatus") != "READY":
        return response(404, {"message": "Lesson not found"})
    languages = sorted(item.get("subtitleLanguages", set()), key=lambda l: (l != "en", l))   # English first
    subtitles = [{"lang": lang, "label": LANGUAGE_NAMES.get(lang, lang),
                  "url": media_url(f"processed/{video_id}/transcript/subtitles.{lang}.vtt")} for lang in languages]
    return response(200, {**card(item), "hlsUrl": media_url(item.get("hlsPath")), "subtitles": subtitles,
                          "captionStatus": item.get("captionStatus"), "objectiveCount": item.get("objectiveCount")})


def handler(event, context):
    if not caller_groups(event) & {"learners", "educators", "creators"}:
        return response(403, {"message": "Sign in to watch lessons"})
    route, params = f"{event['httpMethod']} {event['resource']}", event.get("pathParameters") or {}
    if route == "GET /catalogue":
        return list_catalogue()
    if route == "GET /catalogue/{videoId}":
        return get_lesson(params["videoId"])
    return response(404, {"message": "Unknown route"})
