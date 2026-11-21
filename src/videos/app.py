"""POST /videos and GET /videos - creators start uploads and list their own videos.

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
REGION = os.environ.get("AWS_REGION", "eu-west-2")

MAX_BYTES = 2 * 1024 ** 3                 # 2 GB per video
URL_EXPIRES_SECONDS = 15 * 60             # the upload form is valid for 15 minutes
CONTENT_TYPES = {"video/mp4": "mp4", "video/quicktime": "mov"}

_table = None
_s3 = None


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
              "rejectionReason"]
    videos = []
    for item in result.get("Items", []):
        video = {f: item.get(f) for f in fields}
        # For the creator's progress tracker: which subtitle languages exist so far, and how many questions
        video["subtitleLanguages"] = sorted(item.get("subtitleLanguages", set()))     # a DynamoDB set -> JSON list
        video["objectiveCount"] = int(item.get("objectiveCount", 0))                  # a Decimal -> JSON number
        videos.append(video)
    return response(200, {"videos": videos})


def handler(event, context):
    user_id, groups = caller(event)
    if "creators" not in groups:
        return response(403, {"message": "Only creators can upload or list uploads"})

    if event["httpMethod"] == "GET":
        return list_videos(user_id)

    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return response(400, {"message": "Body must be valid JSON"})
    return create_video(user_id, body)
