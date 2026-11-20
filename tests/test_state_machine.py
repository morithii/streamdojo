"""Checks on the ProcessVideo workflow definition (no AWS calls)."""
import json
from pathlib import Path

ASL = json.loads((Path(__file__).resolve().parent.parent / "statemachine" / "process_video.asl.json").read_text())
STATES = ASL["States"]


def test_starts_by_parsing_the_upload_event():
    assert ASL["StartAt"] == "ParseUploadEvent"
    assert STATES["ParseUploadEvent"]["Next"] == "MarkProcessing"


def test_duplicate_events_stop_quietly():
    mark = STATES["MarkProcessing"]["Parameters"]
    assert mark["ConditionExpression"] == "processingStatus = :uploading"
    first_catch = STATES["MarkProcessing"]["Catch"][0]
    assert first_catch["ErrorEquals"] == ["DynamoDB.ConditionalCheckFailedException"]
    assert STATES[first_catch["Next"]]["Type"] == "Succeed"


def test_every_working_step_routes_failures_to_mark_failed():
    for name in ("MarkProcessing", "Transcode", "MarkReady"):
        targets = [c["Next"] for c in STATES[name]["Catch"]]
        assert "MarkFailed" in targets, name
    assert STATES["MarkFailed"]["Next"] == "ProcessingFailed"


def test_transcode_waits_for_mediaconvert_and_makes_three_renditions():
    transcode = STATES["Transcode"]
    assert transcode["Resource"].endswith("mediaconvert:createJob.sync")
    hls = transcode["Parameters"]["Settings"]["OutputGroups"][0]
    assert [o["NameModifier"] for o in hls["Outputs"]] == ["_360p", "_720p", "_1080p"]


def test_every_state_link_points_to_a_real_state():
    for name, state in STATES.items():
        for target in [state.get("Next")] + [c["Next"] for c in state.get("Catch", [])]:
            assert target is None or target in STATES, f"{name} -> {target}"



def test_caption_failures_never_mark_the_video_failed():
    # The video is already READY to stream; a captions problem only affects captionStatus
    for name in ("StartTranscription", "CheckTranscription", "PrepareTranscript"):
        targets = [c["Next"] for c in STATES[name]["Catch"]]
        assert targets == ["MarkCaptionsFailed"], name
    assert STATES["MarkReady"]["Next"] == "StartTranscription"


def test_translates_four_languages_in_parallel():
    assert STATES["SetLanguages"]["Result"] == ["fr", "es", "it", "el"]
    assert STATES["TranslateAll"]["Type"] == "Map"
    assert STATES["TranslateAll"]["MaxConcurrency"] == 4



def test_clipsensei_runs_after_captions_and_its_failure_is_contained():
    assert STATES["MarkCaptionsReady"]["Next"] == "GenerateObjectives"
    assert STATES["MarkCaptionsPartial"]["Next"] == "GenerateObjectives"
    assert [c["Next"] for c in STATES["GenerateObjectives"]["Catch"]] == ["MarkObjectivesFailed"]
    assert STATES["MarkObjectivesFailed"]["Next"] == "Done"
