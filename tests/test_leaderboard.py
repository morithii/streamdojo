"""Unit tests for the educator leaderboard (fake DynamoDB, no AWS calls)."""
import importlib.util
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 11, 25, 10, 0, tzinfo=timezone.utc)


def load():
    spec = importlib.util.spec_from_file_location("insights", ROOT / "src" / "insights" / "app.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeTable:
    """Answers Query by partition key, like DynamoDB: one partition per call."""
    def __init__(self, items):
        self.items, self.queried = items, []

    def query(self, KeyConditionExpression, **kwargs):
        pk = KeyConditionExpression.get_expression()["values"][1]
        self.queried.append(pk)
        return {"Items": [dict(i) for i in self.items if i["PK"] == pk]}


def schedule(video, sub, obj, interval, next_review, name):
    return {"PK": f"VIDEO#{video}", "SK": f"LEARNER#{sub}#OBJ#{obj}", "learnerSub": sub, "displayName": name,
            "intervalDays": Decimal(interval), "nextReviewAt": next_review}


ITEMS = [
    {"PK": "TOPIC#aws", "SK": "META", "name": "AWS Foundations"},
    {"PK": "TOPIC#aws", "SK": "LESSON#v1"}, {"PK": "TOPIC#aws", "SK": "LESSON#v2"}, {"PK": "TOPIC#aws", "SK": "LESSON#draft"},
    {"PK": "TOPIC#aws", "SK": "BELTSTATS", "YELLOW": Decimal(1)},
    # Sam: top points, every idea remembered, never graded -> ready to grade
    {"PK": "TOPIC#aws", "SK": "POINTS#sam", "learnerName": "Sam", "dojoPoints": Decimal(40), "lastActiveAt": "2026-11-25T08:00:00Z"},
    # Priya: yellow belt, failed her orange grading yesterday -> not ready (3-day wait), failed last grading
    {"PK": "TOPIC#aws", "SK": "POINTS#priya", "learnerName": "Priya", "dojoPoints": Decimal(40), "belt": "YELLOW",
     "lastActiveAt": "2026-11-24T09:00:00Z", "gradings": Decimal(2),
     "lastGrading": {"score": Decimal(41), "passed": False, "targetBelt": "ORANGE", "at": "2026-11-24T09:00:00Z"}},
    {"PK": "VIDEO#v1", "SK": "META", "objectivesStatus": "PUBLISHED"}, {"PK": "VIDEO#v1", "SK": "OBJ#o1"},
    {"PK": "VIDEO#v2", "SK": "META", "objectivesStatus": "PUBLISHED"}, {"PK": "VIDEO#v2", "SK": "OBJ#o2"},
    {"PK": "VIDEO#draft", "SK": "META", "objectivesStatus": "DRAFT_READY"}, {"PK": "VIDEO#draft", "SK": "OBJ#o3"},
    schedule("v1", "sam", "o1", 7, "2026-12-01T00:00:00Z", "Sam"), schedule("v2", "sam", "o2", 3, "2026-11-27T00:00:00Z", "Sam"),
    schedule("v1", "priya", "o1", 7, "2026-12-01T00:00:00Z", "Priya"), schedule("v2", "priya", "o2", 3, "2026-11-28T00:00:00Z", "Priya"),
    # Alex: practised once, review overdue since last week, no POINTS row yet (before the backfill) -> still listed
    schedule("v1", "alex", "o1", 1, "2026-11-17T00:00:00Z", "Alex"),
]


def setup(items=ITEMS):
    m = load()
    m.TABLE_NAME = "app"
    m._clients = {"table:app": FakeTable(items)}
    return m


def event(groups):
    return {"httpMethod": "GET", "resource": "/insights/leaderboard/{topicId}", "pathParameters": {"topicId": "aws"},
            "requestContext": {"authorizer": {"claims": {"sub": "x", "cognito:groups": groups}}}}


def test_only_educators_can_see_the_leaderboard():
    m = setup()
    assert m.handler(event("learners"), None)["statusCode"] == 403
    assert m.handler(event("creators"), None)["statusCode"] == 403
    assert m.handler(event("educators"), None)["statusCode"] == 200


def test_ranked_by_points_then_belt_with_the_draft_lesson_left_out():
    body = json.loads(setup().leaderboard("aws", NOW)["body"])
    assert body["topicName"] == "AWS Foundations" and body["ideas"] == 2           # the draft's idea doesn't count
    assert [(l["rank"], l["displayName"]) for l in body["learners"]] == [(1, "Priya"), (2, "Sam"), (3, "Alex")]  # tie: higher belt first
    assert body["beltCounts"]["WHITE"] == 2 and body["beltCounts"]["YELLOW"] == 1


def test_filters_ready_to_grade_falling_behind_and_failed_last_grading():
    body = json.loads(setup().leaderboard("aws", NOW)["body"])
    learners = {l["displayName"]: l for l in body["learners"]}
    assert learners["Sam"]["readyToGrade"] and not learners["Sam"]["fallingBehind"]
    assert not learners["Priya"]["readyToGrade"] and learners["Priya"]["failedLastGrading"]       # 3-day wait
    assert learners["Alex"]["fallingBehind"] and learners["Alex"]["overdueReviews"] == 1
    assert (learners["Alex"]["practised"], learners["Alex"]["dojoPoints"]) == (1, 0)
    assert body["counts"] == {"learners": 3, "readyToGrade": 1, "fallingBehind": 1, "failedLastGrading": 1}


def test_reads_partitions_with_queries_only_and_404s_an_unknown_course():
    m = setup()
    m.leaderboard("aws", NOW)
    assert m._clients["table:app"].queried == ["TOPIC#aws", "VIDEO#v1", "VIDEO#v2", "VIDEO#draft"]   # no scans
    assert setup().leaderboard("nope", NOW)["statusCode"] == 404


def test_backfill_counts_points_belts_and_the_latest_grading_per_course():
    spec = importlib.util.spec_from_file_location("backfill", ROOT / "scripts" / "backfill_leaderboard.py")
    backfill = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(backfill)
    items = [
        {"PK": "USER#sam", "SK": "ATTEMPT#v1#o1#2026-11-20T10:00:00Z", "topicId": "aws", "dojoPoints": Decimal(3), "displayName": "Sam"},
        {"PK": "USER#sam", "SK": "ATTEMPT#v2#o2#2026-11-22T10:00:00Z", "topicId": "aws", "dojoPoints": Decimal(6), "displayName": "Sam"},
        {"PK": "USER#sam", "SK": "ATTEMPT#v0#o0#2026-11-01T10:00:00Z", "topicId": "aws", "displayName": "Sam"},   # before points existed
        {"PK": "USER#sam", "SK": "BELT#aws", "belt": "YELLOW"},
        {"PK": "USER#sam", "SK": "GRADING#aws#2026-11-23", "topicId": "aws", "status": "COMPLETE", "score": Decimal(80),
         "passed": True, "targetBelt": "YELLOW", "completedAt": "2026-11-23T11:00:00Z"},
        {"PK": "USER#sam", "SK": "GRADING#aws#2026-11-24", "topicId": "aws", "status": "IN_PROGRESS"},   # not finished: ignored
    ]
    (row,) = backfill.build_rows(items).values()
    assert (row["dojoPoints"], row["belt"], row["gradings"], row["learnerName"]) == (9, "YELLOW", 1, "Sam")
    assert row["lastGrading"]["score"] == 80 and row["lastActiveAt"] == "2026-11-23T11:00:00Z"
