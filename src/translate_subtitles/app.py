"""TranslateSubtitles - translates the English WebVTT subtitles into one language.

Called once per language by the workflow's Map state (fr, es, it, el run in parallel).
Translates cue by cue so every timestamp stays exactly the same as the English file.
Never raises for a bad translation: it reports ok=false, so one failed language
can't break the others or the lesson.
"""
import os

import boto3

TABLE_NAME = os.environ.get("TABLE_NAME", "")
MEDIA_BUCKET = os.environ.get("MEDIA_BUCKET", "")

_s3 = None
_translate = None
_table = None


def s3():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3")
    return _s3


def translator():
    global _translate
    if _translate is None:
        _translate = boto3.client("translate")
    return _translate


def table():
    global _table
    if _table is None:
        _table = boto3.resource("dynamodb").Table(TABLE_NAME)
    return _table


def translate_vtt(vtt_text, translate_line):
    """Replace only the spoken text of each cue; keep WEBVTT header, cue numbers and timings."""
    blocks = vtt_text.replace("\r\n", "\n").strip().split("\n\n")
    out = []
    for block in blocks:
        lines = block.split("\n")
        timing = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if timing is None:                      # header (WEBVTT) or a note: keep as is
            out.append(block)
            continue
        text = " ".join(lines[timing + 1:]).strip()
        new_text = translate_line(text) if text else ""
        out.append("\n".join(lines[: timing + 1] + [new_text]))
    return "\n\n".join(out) + "\n"


def handler(event, context):
    video_id, lang = event["videoId"], event["lang"]
    prefix = f"processed/{video_id}/transcript"
    try:
        english = s3().get_object(Bucket=MEDIA_BUCKET, Key=f"{prefix}/subtitles.en.vtt")["Body"].read().decode()
        translated = translate_vtt(english, lambda text: translator().translate_text(
            Text=text, SourceLanguageCode="en", TargetLanguageCode=lang)["TranslatedText"])
        key = f"{prefix}/subtitles.{lang}.vtt"
        s3().put_object(Bucket=MEDIA_BUCKET, Key=key, Body=translated.encode(), ContentType="text/vtt")
        # ADD to a string set is atomic, so the 4 parallel translations never overwrite each other
        table().update_item(
            Key={"PK": f"VIDEO#{video_id}", "SK": "META"},
            UpdateExpression="ADD subtitleLanguages :lang",
            ExpressionAttributeValues={":lang": {lang}},
        )
        return {"lang": lang, "ok": True, "key": key}
    except Exception as err:                    # report, don't crash: the workflow marks captions PARTIAL
        print(f"Translation to {lang} failed: {type(err).__name__}")
        return {"lang": lang, "ok": False}
