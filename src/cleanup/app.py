"""Removes everything that belongs to a deleted video. Triggered by the LessonDeleted event.

The DELETE API has already hidden the lesson (it's out of every list and marked DELETED). This function then
removes, in this order:

  1. learners' attempts on it          USER#learner   / ATTEMPT#videoId#...    (who: from the schedules below)
  2. the lesson's place in its course   TOPIC#topicId  / LESSON#videoId
  3. the answer keys                    AnswerKeys table, VIDEO#videoId / OBJ#...
  4. the files                          uploads: raw/videoId.*   media: processed/videoId/...
  5. CloudFront's cached copies         /media/processed/videoId/*
  6. the lesson itself, last            VIDEO#videoId / META, OBJ#, STATS#, LEARNER# (review schedules)

The lesson item goes last so that, if anything fails part-way, a retry still finds what's left to remove.
Every step is safe to repeat. Errors are raised on purpose: EventBridge then retries, and finally parks the
event in a dead-letter queue instead of losing it.
"""
import json
import os
import time

import boto3
from boto3.dynamodb.conditions import Key

TABLE_NAME = os.environ.get("TABLE_NAME", "")
KEYS_TABLE_NAME = os.environ.get("KEYS_TABLE_NAME", "")
UPLOAD_BUCKET = os.environ.get("UPLOAD_BUCKET", "")
MEDIA_BUCKET = os.environ.get("MEDIA_BUCKET", "")
DISTRIBUTION_ID = os.environ.get("DISTRIBUTION_ID", "")

_clients = {}


def client(name):
    if name not in _clients:
        _clients[name] = boto3.client(name)
    return _clients[name]


def table(name):
    if name not in _clients:
        _clients[name] = boto3.resource("dynamodb").Table(name)
    return _clients[name]


def query_all(tbl, condition):
    """Every item matching a key condition, following pagination."""
    items, kwargs = [], {"KeyConditionExpression": condition}
    while True:
        page = tbl.query(**kwargs)
        items.extend(page.get("Items", []))
        if "LastEvaluatedKey" not in page:
            return items
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def delete_items(tbl, items):
    with tbl.batch_writer() as batch:           # 25 per request, retries unprocessed items for us
        for item in items:
            batch.delete_item(Key={"PK": item["PK"], "SK": item["SK"]})
    return len(items)


def delete_prefix(bucket, prefix):
    """Delete every object under a prefix, 1,000 per request."""
    deleted, token = 0, None
    while True:
        kwargs = {"Bucket": bucket, "Prefix": prefix, **({"ContinuationToken": token} if token else {})}
        page = client("s3").list_objects_v2(**kwargs)
        keys = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
        if keys:
            client("s3").delete_objects(Bucket=bucket, Delete={"Objects": keys, "Quiet": True})
            deleted += len(keys)
        if not page.get("IsTruncated"):
            return deleted
        token = page["NextContinuationToken"]


def handler(event, context):
    detail = event["detail"]
    video_id = detail["videoId"]
    app, keys = table(TABLE_NAME), table(KEYS_TABLE_NAME)
    removed = {}

    lesson_items = query_all(app, Key("PK").eq(f"VIDEO#{video_id}"))
    meta = next((i for i in lesson_items if i["SK"] == "META"), {})
    topic_id = meta.get("topicId") or detail.get("topicId")

    # 1. attempts: we know who practised it from their review schedules
    learners = {i["learnerSub"] for i in lesson_items if i["SK"].startswith("LEARNER#") and i.get("learnerSub")}
    attempts = []
    for learner in learners:
        attempts += query_all(app, Key("PK").eq(f"USER#{learner}") & Key("SK").begins_with(f"ATTEMPT#{video_id}#"))
    removed["attempts"] = delete_items(app, attempts)

    # 2. its place in the course
    if topic_id:
        app.delete_item(Key={"PK": f"TOPIC#{topic_id}", "SK": f"LESSON#{video_id}"})

    # 3. answer keys
    removed["answerKeys"] = delete_items(keys, query_all(keys, Key("PK").eq(f"VIDEO#{video_id}")))

    # 4. files: the original upload, then everything the pipeline produced
    removed["uploadFiles"] = delete_prefix(UPLOAD_BUCKET, f"raw/{video_id}.")
    removed["mediaFiles"] = delete_prefix(MEDIA_BUCKET, f"processed/{video_id}/")

    # 5. CloudFront may still hold cached copies at the edge: invalidate them (one wildcard path)
    if DISTRIBUTION_ID and removed["mediaFiles"]:
        client("cloudfront").create_invalidation(
            DistributionId=DISTRIBUTION_ID,
            InvalidationBatch={"Paths": {"Quantity": 1, "Items": [f"/media/processed/{video_id}/*"]},
                               "CallerReference": f"delete-{video_id}-{int(time.time())}"})

    # 6. the lesson, its objectives, stats and every learner's review schedule (removes their due reviews too)
    removed["lessonItems"] = delete_items(app, lesson_items)

    print(json.dumps({"level": "INFO", "message": "video cleaned up", "videoId": video_id, "learners": len(learners), **removed}))
    return {"videoId": video_id, **removed}
