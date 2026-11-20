"""PrepareTranscript - runs after Amazon Transcribe finishes.

1. Reads Transcribe's JSON and writes a clean transcript (sentences with start
   times) for ClipSensei to generate learning objectives from later.
2. Copies the English subtitles to subtitles.en.vtt (the name the player expects).
3. Marks the lesson's captions TRANSCRIBED and adds "en" to its subtitle languages.
"""
import json
import os

import boto3

TABLE_NAME = os.environ.get("TABLE_NAME", "")
MEDIA_BUCKET = os.environ.get("MEDIA_BUCKET", "")

_s3 = None
_table = None


def s3():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3")
    return _s3


def table():
    global _table
    if _table is None:
        _table = boto3.resource("dynamodb").Table(TABLE_NAME)
    return _table


def mmss(seconds):
    """76.4 -> '01:16' (used for 'Rewatch at' links later)."""
    seconds = int(float(seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def clean_transcript(transcribe_json):
    """Turn Transcribe output into {"text": ..., "segments": [{"start": "mm:ss", "text": ...}]}."""
    results = transcribe_json["results"]
    full_text = results["transcripts"][0]["transcript"] if results.get("transcripts") else ""
    segments = [
        {"start": mmss(seg["start_time"]), "text": seg["transcript"].strip()}
        for seg in results.get("audio_segments", [])
        if seg.get("transcript", "").strip()
    ]
    if not segments and full_text:              # older output format: one big segment
        segments = [{"start": "00:00", "text": full_text}]
    return {"text": full_text, "segments": segments}


def handler(event, context):
    video_id = event["videoId"]
    prefix = f"processed/{video_id}/transcript"
    job = f"streamdojo-{video_id}"               # Transcribe names its files after the job

    raw = s3().get_object(Bucket=MEDIA_BUCKET, Key=f"{prefix}/{job}.json")["Body"].read()
    clean = clean_transcript(json.loads(raw))
    s3().put_object(Bucket=MEDIA_BUCKET, Key=f"{prefix}/clean.json",
                    Body=json.dumps(clean).encode(), ContentType="application/json")

    s3().copy_object(Bucket=MEDIA_BUCKET, Key=f"{prefix}/subtitles.en.vtt",
                     CopySource={"Bucket": MEDIA_BUCKET, "Key": f"{prefix}/{job}.vtt"},
                     ContentType="text/vtt", MetadataDirective="REPLACE")

    table().update_item(
        Key={"PK": f"VIDEO#{video_id}", "SK": "META"},
        UpdateExpression="SET captionStatus = :t, transcriptPath = :p ADD subtitleLanguages :en",
        ExpressionAttributeValues={":t": "TRANSCRIBED", ":p": f"{prefix}/clean.json", ":en": {"en"}},
    )
    return {"videoId": video_id, "segments": len(clean["segments"])}
