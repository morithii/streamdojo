"""Safety check - records what Amazon Rekognition found in an uploaded video.

The pipeline STARTS a Rekognition content-moderation job on the original upload right at the beginning,
so it runs in parallel with transcoding, transcription and translation. Just before ClipSensei drafts the
questions, the pipeline waits for the job to finish and calls this function, which:

  1. reads the results (AggregateBy=SEGMENTS: one entry per continuous stretch, not one per frame),
  2. merges them into a short list of flagged moments  "Explicit Nudity, 02:14-02:20, 91%",
  3. saves moderationStatus on the lesson:

       CLEAN      Rekognition found nothing at or above the confidence threshold
       FLAGGED    at least one moment found - an educator must watch it before publishing
       UNCHECKED  the check couldn't run (e.g. not MP4/MOV with H.264) - the educator is told to watch it all

The AI never blocks or deletes anything on its own: it flags, and a person decides (same rule as ClipSensei).
This function never fails the pipeline - any problem is recorded as UNCHECKED.
"""
import json
import os
from datetime import datetime, timezone

import boto3

TABLE_NAME = os.environ.get("TABLE_NAME", "")
MIN_CONFIDENCE = float(os.environ.get("MIN_CONFIDENCE", "80"))   # below this a label is ignored
MAX_FLAGS = 10                                                   # keep the review list short and readable
MERGE_GAP_MS = 2000                                              # same label within 2 s = one moment

_clients = {}


def client(name):
    if name not in _clients:
        _clients[name] = boto3.client(name)
    return _clients[name]


def table():
    if "table" not in _clients:
        _clients["table"] = boto3.resource("dynamodb").Table(TABLE_NAME)
    return _clients["table"]


def mmss(ms):
    """82000 -> '01:22' (the same format the educator page uses for 'Play from here')."""
    seconds = int(ms // 1000)
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def all_detections(job_id):
    """Every segment Rekognition found, following NextToken. Returns (status, message, detections)."""
    detections, kwargs = [], {"JobId": job_id, "AggregateBy": "SEGMENTS", "SortBy": "TIMESTAMP", "MaxResults": 1000}
    while True:
        page = client("rekognition").get_content_moderation(**kwargs)
        status = page.get("JobStatus")
        if status != "SUCCEEDED":
            return status, page.get("StatusMessage", ""), []
        detections.extend(page.get("ModerationLabels", []))
        if not page.get("NextToken"):
            return status, "", detections
        kwargs["NextToken"] = page["NextToken"]


def summarise(detections, min_confidence=MIN_CONFIDENCE):
    """Rekognition segments -> at most MAX_FLAGS moments, earliest first.

    Rekognition reports a hierarchy (e.g. 'Explicit' > 'Exposed Male Genitalia'); each level comes back as its
    own entry. We keep the most specific name and show its parent for context, merge overlapping or nearly
    touching stretches of the same label, and keep the strongest moments if there are many."""
    moments = []
    for d in detections:
        label = d.get("ModerationLabel") or {}
        name, confidence = (label.get("Name") or "").strip(), float(label.get("Confidence") or 0)
        if not name or confidence < min_confidence:
            continue
        start = int(d.get("StartTimestampMillis", d.get("Timestamp", 0)) or 0)
        end = int(d.get("EndTimestampMillis", d.get("Timestamp", start)) or start)
        moments.append({"label": name, "parent": (label.get("ParentName") or "").strip(),
                        "confidence": confidence, "startMs": start, "endMs": max(end, start)})

    # a parent label covering the same stretch as one of its children adds nothing - drop it
    children = [m for m in moments if m["parent"]]
    moments = [m for m in moments if not any(c["parent"] == m["label"] and c["startMs"] <= m["endMs"] and m["startMs"] <= c["endMs"]
                                             for c in children)]

    merged = []
    for m in sorted(moments, key=lambda m: (m["label"], m["startMs"])):
        last = merged[-1] if merged else None
        if last and last["label"] == m["label"] and m["startMs"] <= last["endMs"] + MERGE_GAP_MS:
            last["endMs"] = max(last["endMs"], m["endMs"])
            last["confidence"] = max(last["confidence"], m["confidence"])
        else:
            merged.append(dict(m))

    strongest = sorted(merged, key=lambda m: -m["confidence"])[:MAX_FLAGS]
    return [{"label": m["label"], "parent": m["parent"], "confidence": int(round(m["confidence"])),
             "startMs": m["startMs"], "endMs": m["endMs"], "at": mmss(m["startMs"]), "until": mmss(m["endMs"])}
            for m in sorted(strongest, key=lambda m: m["startMs"])]


def record(video_id, status, flags, note):
    table().update_item(
        Key={"PK": f"VIDEO#{video_id}", "SK": "META"},
        UpdateExpression="SET moderationStatus = :s, moderationFlags = :f, moderationNote = :n, moderationCheckedAt = :at",
        ExpressionAttributeValues={":s": status, ":f": flags, ":n": note,
                                   ":at": datetime.now(timezone.utc).isoformat(timespec="seconds")})


def handler(event, context):
    """Called by Step Functions with the whole workflow state: {videoId, moderation: {jobId} | {skipped}, ...}."""
    video_id = event["videoId"]
    job_id = (event.get("moderation") or {}).get("jobId")
    try:
        if not job_id:
            status, flags, note = "UNCHECKED", [], ("The safety check couldn't start - Rekognition needs an MP4 or MOV "
                                                     "video encoded with H.264. Please watch the whole video before publishing.")
        else:
            job_status, message, detections = all_detections(job_id)
            if job_status != "SUCCEEDED":
                status, flags, note = "UNCHECKED", [], f"The safety check didn't finish ({job_status}: {message}). Please watch the whole video before publishing."
            else:
                flags = summarise(detections)
                status = "FLAGGED" if flags else "CLEAN"
                note = (f"Rekognition flagged {len(flags)} moment(s). Watch each one before deciding." if flags
                        else f"No unsafe content found at {int(MIN_CONFIDENCE)}% confidence or above.")
    except Exception as error:                        # noqa: BLE001 - a safety-check problem must never break the pipeline
        print(json.dumps({"level": "WARNING", "message": "safety check failed", "videoId": video_id, "error": str(error)}))
        status, flags, note = "UNCHECKED", [], "The safety check hit an error. Please watch the whole video before publishing."

    record(video_id, status, flags, note)
    print(json.dumps({"level": "INFO", "message": "safety check recorded", "videoId": video_id,
                      "status": status, "flags": len(flags)}))
    return {"videoId": video_id, "moderationStatus": status, "flags": len(flags)}
