"""Grading (the dojo exam) and belts. Fake DynamoDB that understands the few expressions grading uses."""
import importlib.util
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load():
    spec = importlib.util.spec_from_file_location("marking_for_grading", ROOT / "src" / "marking" / "app.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _key_values(condition):
    """Literal values from Key('PK').eq(x) [& Key('SK').begins_with(y)]."""
    if condition.expression_operator == "AND":
        for part in condition._values:
            yield from _key_values(part)
    else:
        yield condition._values[1]


class FakeTable:
    def __init__(self, items=()):
        self.items = {(i["PK"], i["SK"]): json.loads(json.dumps(i)) for i in items}

    def get_item(self, Key, **kwargs):
        item = self.items.get((Key["PK"], Key["SK"]))
        return {"Item": json.loads(json.dumps(item, default=str))} if item else {}

    def put_item(self, Item, **kwargs):
        self.items[(Item["PK"], Item["SK"])] = json.loads(json.dumps(Item, default=str))

    def query(self, KeyConditionExpression, ScanIndexForward=True, Limit=None, **kwargs):
        values = list(_key_values(KeyConditionExpression))
        pk, prefix = values[0], values[1] if len(values) > 1 else ""
        found = sorted((i for (p, s), i in self.items.items() if p == pk and s.startswith(prefix)), key=lambda i: i["SK"],
                       reverse=not ScanIndexForward)
        return {"Items": [json.loads(json.dumps(i)) for i in found[:Limit]]}

    def update_item(self, Key, UpdateExpression, ExpressionAttributeValues, ExpressionAttributeNames=None,
                    ConditionExpression=None, **kwargs):
        names, vals = ExpressionAttributeNames or {}, json.loads(json.dumps(ExpressionAttributeValues, default=str))
        item = self.items.setdefault((Key["PK"], Key["SK"]), dict(Key))
        path = lambda p: [names.get(part, part) for part in p.strip().split(".")]   # noqa: E731
        if ConditionExpression:
            for clause in ConditionExpression.split(" AND "):
                m = re.fullmatch(r"(#\w+) = (:\w+)", clause.strip())
                if m and item.get(names[m.group(1)]) != vals[m.group(2)]:
                    raise RuntimeError("ConditionalCheckFailedException")
                m = re.fullmatch(r"attribute_not_exists\((.+)\)", clause.strip())
                if m:
                    node = item
                    for part in path(m.group(1)):
                        node = node.get(part) if isinstance(node, dict) else None
                    if node is not None:
                        raise RuntimeError("ConditionalCheckFailedException")
        action, rest = UpdateExpression.split(" ", 1)
        for assignment in rest.split(", "):
            if action == "SET":
                target, value = assignment.split(" = ")
                *parents, leaf = path(target)
                node = item
                for part in parents:
                    node = node.setdefault(part, {})
                node[leaf] = vals[value.strip()]
            else:                                                        # ADD #name :n
                target, value = assignment.split()
                (leaf,) = path(target)
                item[leaf] = item.get(leaf, 0) + vals[value]
        return {}


class FakeBedrock:
    """Marks: a tool call -> every idea covered (or as configured). A plain call -> the sensei verdict."""
    def __init__(self, verdict="covered"):
        self.verdict, self.verdict_calls = verdict, 0

    def converse(self, **kwargs):
        if "toolConfig" in kwargs:
            ids = re.findall(r"^- (\w+):", kwargs["messages"][0]["content"][0]["text"], re.M)
            quote = re.search(r"<answer>\n(.*)\n</answer>", kwargs["messages"][0]["content"][0]["text"], re.S).group(1)[:20]
            return {"output": {"message": {"content": [{"toolUse": {"input": {
                "points": [{"pointId": i, "verdict": self.verdict, "evidence": quote, "confidence": "high"} for i in ids],
                "feedback": "ok"}}}]}}, "usage": {}}
        self.verdict_calls += 1
        return {"output": {"message": {"content": [{"text": "Your recall of OAC is solid. Work on caching next."}]}}}


class FakeEvents:
    def __init__(self):
        self.sent = []

    def put_events(self, Entries):
        self.sent += Entries
        return {"FailedEntryCount": 0}


NOW = datetime(2026, 11, 25, 9, 0, tzinfo=timezone.utc)


def module_items(objectives=2, interval=3, last_prompt=0):
    items = [{"PK": "TOPIC#cloud", "SK": "LESSON#v1"},
             {"PK": "VIDEO#v1", "SK": "META", "objectivesStatus": "PUBLISHED", "title": "OAC", "topicName": "Cloud Skills"}]
    keys = []
    for n in range(1, objectives + 1):
        items.append({"PK": "VIDEO#v1", "SK": f"OBJ#o{n}", "status": "PUBLISHED", "prompts": [f"Q{n} a", f"Q{n} b", f"Q{n} c"]})
        if interval is not None:
            items.append({"PK": "VIDEO#v1", "SK": f"LEARNER#u1#OBJ#o{n}", "intervalDays": interval, "lastPromptIndex": last_prompt})
        keys.append({"PK": "VIDEO#v1", "SK": f"OBJ#o{n}", "essentialPoints": [{"pointId": "p1", "text": f"Idea {n}"}]})
    return items, keys


def setup(items_and_keys, verdict="covered"):
    m = load()
    items, keys = items_and_keys
    m.TABLE_NAME, m.KEYS_TABLE_NAME, m.EVENT_BUS_NAME = "app", "keys", "streamdojo-test-events"
    m._clients = {"table:app": FakeTable(items), "table:keys": FakeTable(keys), "bedrock-runtime": FakeBedrock(verdict),
                  "events": FakeEvents()}
    m.utcnow = lambda: NOW
    return m


def call(m, method, path, body=None):
    resource = {"": "/learn/grading/{topicId}", "/start": "/learn/grading/{topicId}/start",
                "/answer": "/learn/grading/{topicId}/answer"}[path]
    event = {"httpMethod": method, "resource": resource, "pathParameters": {"topicId": "cloud"},
             "body": json.dumps(body or {}),
             "requestContext": {"authorizer": {"claims": {"sub": "u1", "name": "Sam", "cognito:groups": "learners"}}}}
    res = m.handler(event, None)
    return res["statusCode"], json.loads(res["body"])


def test_locked_until_every_idea_is_practised_and_remembered_after_a_day():
    m = setup(module_items(interval=None))                                   # never practised
    status, body = call(m, "GET", "")
    assert status == 200 and body["eligible"] is False and body["canStart"] is False
    assert body["requirements"] == {"ideas": 2, "practised": 0, "remembered": 0} and body["belt"] == "WHITE"
    m = setup(module_items(interval=1))                                      # practised, not yet remembered a day later
    assert call(m, "GET", "")[1]["requirements"] == {"ideas": 2, "practised": 2, "remembered": 0}
    assert call(m, "POST", "/start")[0] == 409
    m = setup(module_items(interval=3))
    body = call(m, "GET", "")[1]
    assert body["eligible"] and body["canStart"] and (body["nextBelt"], body["passMark"]) == ("YELLOW", 50)


def test_exam_asks_every_objective_in_a_new_phrasing_and_resumes_instead_of_restarting():
    m = setup(module_items(last_prompt=0))
    status, exam = call(m, "POST", "/start")
    assert status == 201 and [q["prompt"] for q in exam["questions"]] == ["Q1 b", "Q2 b"]   # not the wording seen last
    assert "essentialPoints" not in json.dumps(exam)                                     # never the answer keys
    assert call(m, "POST", "/start")[1]["gradingId"] == exam["gradingId"]                # resume, not a second exam


def test_pass_awards_the_next_belt_with_a_sensei_verdict_and_an_event():
    m = setup(module_items())
    exam = call(m, "POST", "/start")[1]
    first = call(m, "POST", "/answer", {"gradingId": exam["gradingId"], "index": 0, "answer": "Idea one, clearly explained."})
    assert first == (200, {"finished": False, "answered": 1, "total": 2})                 # no marks shown mid-exam
    assert call(m, "POST", "/answer", {"gradingId": exam["gradingId"], "index": 0, "answer": "again"})[0] == 409  # one answer each
    status, done = call(m, "POST", "/answer", {"gradingId": exam["gradingId"], "index": 1, "answer": "Idea two, also explained."})
    result = done["result"]
    assert done["finished"] and result["score"] == 100 and result["passed"] and result["targetBelt"] == "YELLOW"
    assert "solid" in result["verdict"]
    app = m._clients["table:app"].items
    assert app[("USER#u1", "BELT#cloud")]["belt"] == "YELLOW"
    assert app[("TOPIC#cloud", "BELTSTATS")]["YELLOW"] == 1
    (event,) = m._clients["events"].sent
    assert event["DetailType"] == "BeltAwarded" and json.loads(event["Detail"])["belt"] == "YELLOW"


def test_a_failed_grading_keeps_the_belt_and_starts_the_three_day_wait():
    m = setup(module_items(), verdict="missing")
    exam = call(m, "POST", "/start")[1]
    for i in (0, 1):
        status, done = call(m, "POST", "/answer", {"gradingId": exam["gradingId"], "index": i, "answer": "I am not sure at all."})
    assert done["result"]["score"] == 0 and done["result"]["passed"] is False
    assert m._clients["events"].sent == []
    body = call(m, "GET", "")[1]
    assert body["belt"] == "WHITE" and body["canStart"] is False and body["nextGradingAt"].startswith("2026-11-28")
    assert body["lastResult"]["score"] == 0
    assert call(m, "POST", "/start")[0] == 409


def test_partial_counts_half_and_one_belt_per_grading():
    m = setup(module_items(), verdict="partial")
    exam = call(m, "POST", "/start")[1]
    for i in (0, 1):
        done = call(m, "POST", "/answer", {"gradingId": exam["gradingId"], "index": i, "answer": "Partly right answer."})[1]
    assert done["result"]["score"] == 50 and done["result"]["passed"] is True              # exactly the yellow pass mark
    assert m._clients["table:app"].items[("USER#u1", "BELT#cloud")]["belt"] == "YELLOW"     # never skips to green


def test_time_runs_out_unanswered_questions_score_zero():
    m = setup(module_items())
    exam = call(m, "POST", "/start")[1]
    call(m, "POST", "/answer", {"gradingId": exam["gradingId"], "index": 0, "answer": "Idea one, clearly explained."})
    m.utcnow = lambda: NOW + timedelta(minutes=61)
    status, late = call(m, "POST", "/answer", {"gradingId": exam["gradingId"], "index": 1, "answer": "Too late now."})
    assert late["finished"] and late["result"]["score"] == 50 and late["result"]["questions"][1]["answered"] is False


def test_black_belt_is_the_end_of_the_road():
    items, keys = module_items()
    items.append({"PK": "USER#u1", "SK": "BELT#cloud", "belt": "BLACK"})
    m = setup((items, keys))
    body = call(m, "GET", "")[1]
    assert body["nextBelt"] is None and body["canStart"] is False
    assert "black belt" in call(m, "POST", "/start")[1]["message"]
