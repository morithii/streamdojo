"""Live transcoding % from MediaConvert STATUS_UPDATE events. Fake DynamoDB."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load():
    spec = importlib.util.spec_from_file_location("transcode_progress", ROOT / "src" / "transcode_progress" / "app.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeTable:
    def __init__(self, status="PROCESSING"):
        self.status, self.saved = status, None

    def update_item(self, Key, UpdateExpression, ConditionExpression, ExpressionAttributeValues):
        if self.status != ExpressionAttributeValues[":processing"]:
            raise RuntimeError("ConditionalCheckFailedException")
        self.saved = {"key": Key, **ExpressionAttributeValues}


def status_update(video_id="v1", percent=64, phase="TRANSCODING"):
    # the shape MediaConvert sends to EventBridge (trimmed)
    return {"source": "aws.mediaconvert", "detail-type": "MediaConvert Job State Change",
            "detail": {"status": "STATUS_UPDATE", "userMetadata": {"videoId": video_id} if video_id else {},
                       "jobProgress": {"jobPercentComplete": percent, "currentPhase": phase}}}


def test_saves_the_percentage_and_phase_while_processing():
    mod = load()
    mod._clients["table"] = FakeTable()
    assert mod.handler(status_update(), None) == {"updated": True, "videoId": "v1", "percent": 64}
    saved = mod._clients["table"].saved
    assert saved["key"] == {"PK": "VIDEO#v1", "SK": "META"} and saved[":p"] == 64 and saved[":ph"] == "TRANSCODING"


def test_never_overwrites_a_finished_lesson_or_raises():
    mod = load()
    mod._clients["table"] = FakeTable(status="READY")            # a late event after the job finished
    assert mod.handler(status_update(percent=99), None) == {"updated": False}


def test_ignores_events_without_a_video_or_a_percentage():
    mod = load()
    mod._clients["table"] = FakeTable()
    assert mod.handler(status_update(video_id=None), None) == {"updated": False}
    assert mod.handler(status_update(percent=None), None) == {"updated": False}
    assert mod._clients["table"].saved is None
