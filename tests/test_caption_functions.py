"""Unit tests for the caption functions (no AWS calls)."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "src" / name / "app.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VTT = """WEBVTT

1
00:00:00.000 --> 00:00:03.500
Origin Access Control keeps the bucket private.

2
00:00:03.500 --> 00:00:07.000
CloudFront signs its requests
to S3.
"""


def test_translation_keeps_every_timestamp_and_changes_only_text():
    tr = load("translate_subtitles")
    out = tr.translate_vtt(VTT, lambda text: f"[fr] {text}")
    assert out.startswith("WEBVTT")
    assert "00:00:00.000 --> 00:00:03.500" in out and "00:00:03.500 --> 00:00:07.000" in out
    assert "[fr] Origin Access Control keeps the bucket private." in out
    assert "[fr] CloudFront signs its requests to S3." in out   # multi-line cue joined once
    assert out.count("-->") == 2


def test_failed_translation_reports_instead_of_crashing():
    tr = load("translate_subtitles")

    class BrokenS3:
        def get_object(self, **kwargs):
            raise RuntimeError("boom")

    tr._s3 = BrokenS3()
    assert tr.handler({"videoId": "v1", "lang": "el"}, None) == {"lang": "el", "ok": False}


def test_clean_transcript_has_timestamped_segments():
    pt = load("prepare_transcript")
    data = {"results": {
        "transcripts": [{"transcript": "Hello dojo. Today we learn OAC."}],
        "audio_segments": [
            {"start_time": "0.5", "transcript": "Hello dojo."},
            {"start_time": "76.4", "transcript": "Today we learn OAC."},
        ]}}
    clean = pt.clean_transcript(data)
    assert clean["text"] == "Hello dojo. Today we learn OAC."
    assert clean["segments"] == [{"start": "00:00", "text": "Hello dojo."},
                                 {"start": "01:16", "text": "Today we learn OAC."}]


def test_clean_transcript_falls_back_without_segments():
    pt = load("prepare_transcript")
    clean = pt.clean_transcript({"results": {"transcripts": [{"transcript": "Just text"}]}})
    assert clean["segments"] == [{"start": "00:00", "text": "Just text"}]
