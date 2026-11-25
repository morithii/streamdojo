"""Live transcoding percentage for the creator's progress tracker.

MediaConvert sends a "MediaConvert Job State Change" event with status STATUS_UPDATE about every 10 seconds
while a job runs (the pipeline sets StatusUpdateInterval = SECONDS_10). An EventBridge rule on the default bus
sends those events here, and we save two numbers on the lesson:

    transcodePercent   0-100  how far the whole job is
    transcodePhase     PROBING (reading the file) / TRANSCODING / UPLOADING (saving the outputs)

The job carries the lesson's videoId in its UserMetadata. The write only happens while the lesson is still
PROCESSING, so a late or duplicate event can never overwrite a finished lesson, and events for jobs that aren't
ours (no such lesson) are ignored. This is a progress display: it never raises.
"""
import json
import os
from datetime import datetime, timezone

import boto3

TABLE_NAME = os.environ.get("TABLE_NAME", "")
_clients = {}


def table():
    if "table" not in _clients:
        _clients["table"] = boto3.resource("dynamodb").Table(TABLE_NAME)
    return _clients["table"]


def handler(event, context):
    detail = event.get("detail") or {}
    video_id = (detail.get("userMetadata") or {}).get("videoId")
    progress = detail.get("jobProgress") or {}
    percent = progress.get("jobPercentComplete")
    if not video_id or percent is None:
        return {"updated": False}

    try:
        table().update_item(
            Key={"PK": f"VIDEO#{video_id}", "SK": "META"},
            UpdateExpression="SET transcodePercent = :p, transcodePhase = :ph, transcodeUpdatedAt = :at",
            ConditionExpression="processingStatus = :processing",
            ExpressionAttributeValues={":p": max(0, min(100, int(percent))), ":ph": str(progress.get("currentPhase") or ""),
                                       ":at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                       ":processing": "PROCESSING"})
    except Exception as error:                            # noqa: BLE001 - finished lesson, not ours, or AWS hiccup
        print(json.dumps({"level": "INFO", "message": "progress not saved", "videoId": video_id, "reason": type(error).__name__}))
        return {"updated": False}
    return {"updated": True, "videoId": video_id, "percent": int(percent)}
