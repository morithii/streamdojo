"""POST /videos and GET /videos - creators start uploads and list their own videos.
DELETE /videos/{videoId} - a creator deletes their own video, or an educator deletes any lesson.

POST returns a presigned POST: a short-lived, signed form that lets the browser
upload straight to S3. The video never passes through Lambda or API Gateway
(which have 6 MB / 10 MB limits), and S3 itself enforces the size limit and
file type written into the signed policy.
"""
import json
import os
import uuid
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Key
from botocore.config import Config

TABLE_NAME = os.environ.get("TABLE_NAME", "")
UPLOAD_BUCKET = os.environ.get("UPLOAD_BUCKET", "")
ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "*")
EVENT_BUS_NAME = os.environ.get("EVENT_BUS_NAME", "")
REGION = os.environ.get("AWS_REGION", "eu-west-2")

MAX_BYTES = 2 * 1024 ** 3                 # 2 GB per video
URL_EXPIRES_SECONDS = 15 * 60             # the upload form is valid for 15 minutes
CONTENT_TYPES = {"video/mp4": "mp4", "video/quicktime": "mov"}

_table = None
_s3 = None
_events = None
BUSY_MINUTES = 60                         # a video still in the pipeline after this long has stalled: deletable


def table():
    global _table
    if _table is None:
        _table = boto3.resource("dynamodb").Table(TABLE_NAME)
    return _table


def s3():
    """Regional endpoint + SigV4, so the signed form works in eu-west-2 without redirects."""
    global _s3
    if _s3 is None:
        _s3 = boto3.client(
            "s3",
            region_name=REGION,
            endpoint_url=f"https://s3.{REGION}.amazonaws.com",
            config=Config(signature_version="s3v4"),
        )
    return _s3


def response(status, body):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json", "Access-Control-Allow-Origin": ALLOWED_ORIGIN},
        "body": json.dumps(body),
    }


def caller(event):
    """The signed-in user's ID and groups, from the token API Gateway already verified."""
    claims = event["requestContext"]["authorizer"]["claims"]
    raw = claims.get("cognito:groups") or ""
    groups = raw if isinstance(raw, list) else raw.strip("[]").replace(",", " ").split()
    return claims["sub"], set(groups)


def validate(body):
    """Return (cleaned values, None) or (None, error message)."""
    title = str(body.get("title", "")).strip()
    content_type = body.get("contentType")
    size = body.get("fileSizeBytes")
    if not 3 <= len(title) <= 120:
        return None, "title must be 3-120 characters"
    if content_type not in CONTENT_TYPES:
        return None, "contentType must be video/mp4 or video/quicktime"
    if not isinstance(size, int) or not 0 < size <= MAX_BYTES:
        return None, "fileSizeBytes must be a whole number between 1 and 2 GB"
    return {"title": title, "contentType": content_type, "fileSizeBytes": size}, None


def create_video(user_id, body):
    values, error = validate(body)
    if error:
        return response(400, {"message": error})

    video_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    key = f"raw/{video_id}.{CONTENT_TYPES[values['contentType']]}"

    # Create the lesson record first (follows docs/data-model.md)
    table().put_item(
        Item={
            "PK": f"VIDEO#{video_id}",
            "SK": "META",
            "videoId": video_id,
            "title": values["title"],
            "creatorSub": user_id,
            "createdAt": now,
            "rawKey": key,
            "contentType": values["contentType"],
            "fileSizeBytes": values["fileSizeBytes"],
            "processingStatus": "UPLOADING",
            "captionStatus": "PENDING",
            "objectivesStatus": "PENDING",
            "GSI2PK": f"CREATOR#{user_id}",
            "GSI2SK": now,
        },
        ConditionExpression="attribute_not_exists(PK)",   # never overwrite an existing lesson
    )

    # Signed upload form: S3 rejects anything bigger than 2 GB or of another type
    upload = s3().generate_presigned_post(
        Bucket=UPLOAD_BUCKET,
        Key=key,
        Fields={"Content-Type": values["contentType"]},
        Conditions=[
            {"Content-Type": values["contentType"]},
            ["content-length-range", 1, MAX_BYTES],
        ],
        ExpiresIn=URL_EXPIRES_SECONDS,
    )
    return response(201, {
        "videoId": video_id,
        "upload": upload,                                  # {"url": ..., "fields": {...}}
        "expiresInSeconds": URL_EXPIRES_SECONDS,
    })


def list_videos(user_id):
    result = table().query(
        IndexName="GSI2",
        KeyConditionExpression=Key("GSI2PK").eq(f"CREATOR#{user_id}"),
        ScanIndexForward=False,                            # newest first
        Limit=50,
    )
    fields = ["videoId", "title", "createdAt", "processingStartedAt", "processingStatus", "captionStatus", "objectivesStatus",
              "rejectionReason",
              # exact progress for the creator's tracker: which stage is running, and when each one started
              "stage", "processedAt", "translatingAt", "safetyCheckAt", "draftingAt", "draftReadyAt",
              "transcodePhase", "moderationStatus"]
    videos = []
    for item in result.get("Items", []):
        video = {f: item.get(f) for f in fields}
        # For the creator's progress tracker: which subtitle languages exist so far, and how many questions
        video["subtitleLanguages"] = sorted(item.get("subtitleLanguages", set()))     # a DynamoDB set -> JSON list
        video["objectiveCount"] = int(item.get("objectiveCount", 0))                  # a Decimal -> JSON number
        if item.get("transcodePercent") is not None:
            video["transcodePercent"] = int(item["transcodePercent"])                   # live %, from MediaConvert events
        videos.append(video)
    return response(200, {"videos": videos})


# ---------------------------------------------------------------------------
# Delete: hide the lesson NOW, then let the cleanup function remove everything else
# ---------------------------------------------------------------------------
def pipeline_busy(item, now):
    """True while Step Functions is still working on this video (deleting then would race the pipeline)."""
    if item.get("processingStatus") in ("UPLOADING", "FAILED", "DELETED"):
        return False
    if item.get("captionStatus") == "FAILED" or item.get("objectivesStatus") in ("DRAFT_READY", "PUBLISHED", "REJECTED", "FAILED"):
        return False                                                                # finished, one way or another
    started = item.get("processingStartedAt") or item.get("createdAt")
    minutes = (now - datetime.fromisoformat(started.replace("Z", "+00:00"))).total_seconds() / 60
    return minutes < BUSY_MINUTES


def events():
    global _events
    if _events is None:
        _events = boto3.client("events")
    return _events


def delete_video(user_id, groups, video_id, body):
    key = {"PK": f"VIDEO#{video_id}", "SK": "META"}
    item = table().get_item(Key=key).get("Item")
    if not item:
        return response(404, {"message": "Video not found"})
    is_owner = item.get("creatorSub") == user_id and "creators" in groups
    is_educator = "educators" in groups
    if not (is_owner or is_educator):
        return response(403, {"message": "Only the video's creator or an educator can delete it"})
    reason = str(body.get("reason") or "").strip()[:500]
    now = datetime.now(timezone.utc)

    if not item.get("deletedAt"):                  # a retry after a failed announce skips straight to the event
        if pipeline_busy(item, now):
            return response(409, {"message": "This video is still being processed. Try again when the tracker shows it has finished."})
        try:
            # Gone from every list at once (catalogue, drafts queue, creator's uploads). DELETED also stops a
            # pipeline that hasn't started yet: its first step only claims videos that are still UPLOADING.
            table().update_item(
                Key=key,
                UpdateExpression=("SET deletedAt = :now, deletedBy = :u, processingStatus = :deleted "
                                  "REMOVE GSI1PK, GSI1SK, GSI2PK, GSI2SK"),
                ConditionExpression="attribute_not_exists(deletedAt)",
                ExpressionAttributeValues={":now": now.isoformat(timespec="seconds"), ":u": user_id, ":deleted": "DELETED"})
        except Exception as error:                 # noqa: BLE001
            if "ConditionalCheckFailed" not in type(error).__name__ + str(error):
                raise                              # someone else deleting it at the same moment is fine

    detail = {"videoId": video_id, "title": item.get("title"), "creatorSub": item.get("creatorSub"),
              "topicId": item.get("topicId"), "deletedBy": item.get("deletedBy", user_id),
              "byEducator": not is_owner, "reason": reason}
    # Unlike notifications, this event MUST go out: it's what starts the cleanup
    result = events().put_events(Entries=[{"EventBusName": EVENT_BUS_NAME, "Source": "streamdojo",
                                           "DetailType": "LessonDeleted", "Detail": json.dumps(detail, default=str)}])
    if result.get("FailedEntryCount"):
        return response(502, {"message": "The video is hidden, but its cleanup didn't start. Please try deleting it again."})
    print(json.dumps({"level": "INFO", "message": "video deleted", "videoId": video_id, "by": user_id, "byEducator": not is_owner}))
    return response(202, {"videoId": video_id, "message": "Deleted. Its files and learner data are being removed."})


def handler(event, context):
    user_id, groups = caller(event)
    if event["httpMethod"] == "DELETE":
        try:
            body = json.loads(event.get("body") or "{}")
        except json.JSONDecodeError:
            body = {}
        return delete_video(user_id, groups, event["pathParameters"]["videoId"], body)
    if "creators" not in groups:
        return response(403, {"message": "Only creators can upload or list uploads"})

    if event["httpMethod"] == "GET":
        return list_videos(user_id)

    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return response(400, {"message": "Body must be valid JSON"})
    return create_video(user_id, body)
