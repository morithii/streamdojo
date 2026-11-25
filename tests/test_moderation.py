"""Safety check: Rekognition results -> a short list of flagged moments -> CLEAN / FLAGGED / UNCHECKED. Fake AWS."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load():
    spec = importlib.util.spec_from_file_location("moderation", ROOT / "src" / "moderation" / "app.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def seg(name, confidence, start, end, parent=""):
    return {"ModerationLabel": {"Name": name, "ParentName": parent, "Confidence": confidence},
            "StartTimestampMillis": start, "EndTimestampMillis": end}


class FakeTable:
    def __init__(self):
        self.saved = None

    def update_item(self, Key, UpdateExpression, ExpressionAttributeValues):
        self.saved = {"key": Key, **ExpressionAttributeValues}


class FakeRekognition:
    def __init__(self, pages=None, error=None):
        self.pages, self.error, self.calls = list(pages or []), error, []

    def get_content_moderation(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.pages.pop(0)


def wire(mod, rekognition=None):
    table = FakeTable()
    mod._clients.update({"table": table, "rekognition": rekognition or FakeRekognition()})
    return table


def test_summary_keeps_specific_labels_merges_nearby_moments_and_ignores_low_confidence():
    mod = load()
    flags = mod.summarise([
        seg("Explicit", 95, 134000, 140000),                                  # parent of the next one, same stretch
        seg("Exposed Male Genitalia", 91, 134000, 139000, parent="Explicit"),
        seg("Exposed Male Genitalia", 85, 140500, 142000, parent="Explicit"),  # 1.5 s later -> same moment
        seg("Violence", 62, 5000, 6000),                                       # under 80% -> ignored
        seg("Graphic Violence", 88, 30000, 31000, parent="Violence"),
    ])
    assert [f["label"] for f in flags] == ["Graphic Violence", "Exposed Male Genitalia"]   # earliest first
    nude = flags[1]
    assert (nude["at"], nude["until"], nude["confidence"], nude["parent"]) == ("02:14", "02:22", 91, "Explicit")


def test_summary_keeps_only_the_ten_strongest_moments():
    mod = load()
    flags = mod.summarise([seg(f"Label{i}", 80 + i, i * 60000, i * 60000 + 1000) for i in range(15)])
    assert len(flags) == 10 and min(f["confidence"] for f in flags) == 85


def test_flagged_video_is_recorded_as_flagged_and_follows_pagination():
    mod = load()
    rek = FakeRekognition(pages=[
        {"JobStatus": "SUCCEEDED", "ModerationLabels": [seg("Violence", 90, 1000, 2000)], "NextToken": "page2"},
        {"JobStatus": "SUCCEEDED", "ModerationLabels": [seg("Drugs", 92, 60000, 61000)]}])
    table = wire(mod, rek)
    result = mod.handler({"videoId": "v1", "moderation": {"jobId": "job-1"}}, None)
    assert result == {"videoId": "v1", "moderationStatus": "FLAGGED", "flags": 2}
    assert table.saved[":s"] == "FLAGGED" and [f["label"] for f in table.saved[":f"]] == ["Violence", "Drugs"]
    assert rek.calls[0]["AggregateBy"] == "SEGMENTS" and rek.calls[1]["NextToken"] == "page2"


def test_clean_video():
    mod = load()
    table = wire(mod, FakeRekognition(pages=[{"JobStatus": "SUCCEEDED", "ModerationLabels": []}]))
    assert mod.handler({"videoId": "v1", "moderation": {"jobId": "job-1"}}, None)["moderationStatus"] == "CLEAN"
    assert table.saved[":f"] == []


def test_problems_are_recorded_as_unchecked_never_raised():
    mod = load()
    # job never started (e.g. not MP4/MOV with H.264)
    table = wire(mod)
    assert mod.handler({"videoId": "v1", "moderation": {"skipped": True}}, None)["moderationStatus"] == "UNCHECKED"
    assert "H.264" in table.saved[":n"]
    # job failed
    table = wire(mod, FakeRekognition(pages=[{"JobStatus": "FAILED", "StatusMessage": "Unsupported codec"}]))
    assert mod.handler({"videoId": "v1", "moderation": {"jobId": "j"}}, None)["moderationStatus"] == "UNCHECKED"
    assert "Unsupported codec" in table.saved[":n"]
    # AWS error
    table = wire(mod, FakeRekognition(error=RuntimeError("throttled")))
    assert mod.handler({"videoId": "v1", "moderation": {"jobId": "j"}}, None)["moderationStatus"] == "UNCHECKED"
