"""Learner API (read-only, NO access to answer keys).

GET /learn/lessons/{videoId}/practice   one recall prompt per learning objective of a published lesson
GET /learn/reviews/due                  the learner's reviews that are due now (spaced repetition),
                                        each with a DIFFERENT prompt version from the one seen last time
"""
import json
import os
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Key

TABLE_NAME = os.environ.get("TABLE_NAME", "")
ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "*")
_tables = {}


def table():
    if "app" not in _tables:
        _tables["app"] = boto3.resource("dynamodb").Table(TABLE_NAME)
    return _tables["app"]


def response(status, body):
    return {"statusCode": status,
            "headers": {"Content-Type": "application/json", "Access-Control-Allow-Origin": ALLOWED_ORIGIN},
            "body": json.dumps(body, default=lambda v: int(v) if v == int(v) else float(v))}


def caller(event):
    claims = event["requestContext"]["authorizer"]["claims"]
    raw = claims.get("cognito:groups") or ""
    groups = raw if isinstance(raw, list) else raw.strip("[]").replace(",", " ").split()
    return claims["sub"], set(groups)


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def published_objectives(video_id):
    items = table().query(KeyConditionExpression=Key("PK").eq(f"VIDEO#{video_id}") & Key("SK").begins_with("OBJ#"))["Items"]
    return {i["objectiveId"]: i for i in items if i["SK"].startswith("OBJ#") and i.get("status") == "PUBLISHED"}


def practice(video_id):
    lesson = table().get_item(Key={"PK": f"VIDEO#{video_id}", "SK": "META"}).get("Item")
    if not lesson or lesson.get("objectivesStatus") != "PUBLISHED":
        return response(404, {"message": "Lesson not found or not published"})
    objectives = published_objectives(video_id)
    prompts = [{"objectiveId": oid, "promptIndex": 0, "prompt": o["prompts"][0], "timestamp": o["timestamp"]}
               for oid, o in sorted(objectives.items())]
    return response(200, {"videoId": video_id, "title": lesson.get("title"), "topicName": lesson.get("topicName"),
                          "prompts": prompts})    # answers live in another table this function can't read


def reviews_due(user_id):
    due = table().query(IndexName="GSI3",
                        KeyConditionExpression=Key("GSI3PK").eq(f"DUE#{user_id}") & Key("GSI3SK").lte(now()))["Items"]
    reviews, cache = [], {}
    for item in due:
        video_id, objective_id = item.get("videoId"), item.get("objectiveId")
        if not video_id or not objective_id:
            continue
        if video_id not in cache:
            cache[video_id] = published_objectives(video_id)
        obj = cache[video_id].get(objective_id)
        if not obj:
            continue
        index = (int(item.get("lastPromptIndex", 0)) + 1) % len(obj["prompts"])   # never the same wording twice in a row
        reviews.append({"videoId": video_id, "objectiveId": objective_id, "promptIndex": index,
                        "prompt": obj["prompts"][index], "timestamp": obj["timestamp"],
                        "intervalDays": item.get("intervalDays")})
    return response(200, {"reviews": reviews})


def progress(user_id):
    """Which lessons this learner has practised (and how many of each lesson's ideas).
    Every idea a learner has practised has a schedule item in GSI3 under DUE#<sub>, whatever its date."""
    counts, kwargs = {}, {"IndexName": "GSI3", "KeyConditionExpression": Key("GSI3PK").eq(f"DUE#{user_id}")}
    while True:
        page = table().query(**kwargs)
        for item in page.get("Items", []):
            if item.get("videoId"):
                counts[item["videoId"]] = counts.get(item["videoId"], 0) + 1
        if "LastEvaluatedKey" not in page:
            break
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    # their karate belt in each course (no item yet = white belt)
    belts = table().query(KeyConditionExpression=Key("PK").eq(f"USER#{user_id}") & Key("SK").begins_with("BELT#")).get("Items", [])
    return response(200, {"practised": counts, "belts": {b["SK"].split("#", 1)[1]: b.get("belt", "WHITE") for b in belts}})


def handler(event, context):
    user_id, groups = caller(event)
    if not groups & {"learners", "educators", "creators"}:
        return response(403, {"message": "Sign in to practise"})
    route, params = f"{event['httpMethod']} {event['resource']}", event.get("pathParameters") or {}
    if route == "GET /learn/lessons/{videoId}/practice":
        return practice(params["videoId"])
    if route == "GET /learn/reviews/due":
        return reviews_due(user_id)
    if route == "GET /learn/progress":
        return progress(user_id)
    return response(404, {"message": "Unknown route"})
