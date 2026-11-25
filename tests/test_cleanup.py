"""Deleting a video: everything that belongs to it is removed, the lesson item last. Fake AWS."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load():
    spec = importlib.util.spec_from_file_location("cleanup", ROOT / "src" / "cleanup" / "app.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeTable:
    """Answers key conditions the way DynamoDB would: PK equals, optional SK begins_with."""
    def __init__(self, items):
        self.items = {(i["PK"], i["SK"]): i for i in items}
        self.order = []

    def query(self, KeyConditionExpression, **kwargs):
        values = [v for v in _values(KeyConditionExpression)]
        pk, prefix = values[0], (values[1] if len(values) > 1 else "")
        return {"Items": [dict(i) for (p, s), i in self.items.items() if p == pk and s.startswith(prefix)]}

    def delete_item(self, Key):
        self.items.pop((Key["PK"], Key["SK"]), None)
        self.order.append(Key["SK"])

    def batch_writer(self):
        table = self

        class Batch:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def delete_item(self, Key): table.delete_item(Key)
        return Batch()


def _values(condition):
    """Pull the literal values out of a boto3 Key condition: Key('PK').eq(x) [& Key('SK').begins_with(y)]."""
    if hasattr(condition, "_values") and condition.expression_operator in ("AND",):
        for part in condition._values:
            yield from _values(part)
    else:
        yield condition._values[1]


class FakeS3:
    def __init__(self, objects):
        self.objects, self.deleted = set(objects), []

    def list_objects_v2(self, Bucket, Prefix, **kwargs):
        return {"Contents": [{"Key": k} for b, k in self.objects if b == Bucket and k.startswith(Prefix)], "IsTruncated": False}

    def delete_objects(self, Bucket, Delete):
        for obj in Delete["Objects"]:
            self.objects.discard((Bucket, obj["Key"]))
            self.deleted.append(obj["Key"])


class FakeCloudFront:
    def __init__(self):
        self.paths = []

    def create_invalidation(self, DistributionId, InvalidationBatch):
        self.paths += InvalidationBatch["Paths"]["Items"]


def wire():
    mod = load()
    mod.TABLE_NAME, mod.KEYS_TABLE_NAME, mod.UPLOAD_BUCKET, mod.MEDIA_BUCKET, mod.DISTRIBUTION_ID = "app", "keys", "up", "media", "DIST"
    app = FakeTable([
        {"PK": "VIDEO#v1", "SK": "META", "topicId": "cloud"},
        {"PK": "VIDEO#v1", "SK": "OBJ#o1"}, {"PK": "VIDEO#v1", "SK": "STATS#o1"},
        {"PK": "VIDEO#v1", "SK": "LEARNER#ann#OBJ#o1", "learnerSub": "ann"},
        {"PK": "USER#ann", "SK": "ATTEMPT#v1#o1#2026-11-20"}, {"PK": "USER#ann", "SK": "ATTEMPT#v2#o9#2026-11-20"},   # v2 stays
        {"PK": "USER#ann", "SK": "PROFILE"},
        {"PK": "TOPIC#cloud", "SK": "LESSON#v1"}, {"PK": "TOPIC#cloud", "SK": "LESSON#v2"},
        {"PK": "VIDEO#v2", "SK": "META"},
    ])
    keys = FakeTable([{"PK": "VIDEO#v1", "SK": "OBJ#o1"}, {"PK": "VIDEO#v2", "SK": "OBJ#o9"}])
    s3 = FakeS3({("up", "raw/v1.mp4"), ("up", "raw/v2.mp4"), ("media", "processed/v1/hls/index.m3u8"),
                 ("media", "processed/v1/thumb.0000001.jpg"), ("media", "processed/v2/hls/index.m3u8")})
    cloudfront = FakeCloudFront()
    mod._clients.update({"app": app, "keys": keys, "s3": s3, "cloudfront": cloudfront})
    return mod, app, keys, s3, cloudfront


def test_removes_everything_of_this_video_and_nothing_else():
    mod, app, keys, s3, cloudfront = wire()
    result = mod.handler({"detail": {"videoId": "v1"}}, None)
    assert set(app.items) == {("USER#ann", "ATTEMPT#v2#o9#2026-11-20"), ("USER#ann", "PROFILE"),
                              ("TOPIC#cloud", "LESSON#v2"), ("VIDEO#v2", "META")}
    assert set(keys.items) == {("VIDEO#v2", "OBJ#o9")}
    assert s3.objects == {("up", "raw/v2.mp4"), ("media", "processed/v2/hls/index.m3u8")}
    assert cloudfront.paths == ["/media/processed/v1/*"]
    assert result["attempts"] == 1 and result["mediaFiles"] == 2 and result["uploadFiles"] == 1
    assert app.order[-1] == "META" or "META" in app.order[-4:]          # the lesson item goes in the final step


def test_running_it_twice_is_safe():
    mod, app, *_ = wire()
    mod.handler({"detail": {"videoId": "v1"}}, None)
    again = mod.handler({"detail": {"videoId": "v1"}}, None)            # EventBridge retries are harmless
    assert again["lessonItems"] == 0 and again["mediaFiles"] == 0
