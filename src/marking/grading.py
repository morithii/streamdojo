"""Grading - the dojo exam. A learner earns karate belts in a module (course), one belt per grading.

  WHITE -> YELLOW 50% -> ORANGE 60% -> GREEN 70% -> BLUE 80% -> BROWN 90% -> BLACK 95%

Routes (served by the marking function, so the exam is marked by exactly the same ClipSensei code as practice):
  GET  /learn/grading/{topicId}          belt, next belt, whether a grading is unlocked, any exam in progress
  POST /learn/grading/{topicId}/start    starts (or resumes) an exam: one question per learning objective
  POST /learn/grading/{topicId}/answer   answers one question - marked straight away, results shown at the end

Rules
  * Unlocked when the learner has practised every idea in the module AND remembered each one after at least a
    day (their review schedule has moved past 1 day) - memory over days, not one evening.
  * One question per learning objective (max 15, sampled), in a different phrasing from the last one they saw.
    No hints, no rewatching, one answer each. Marked key idea by key idea; a partial counts as half.
  * Score = % of key ideas recalled. Pass mark depends on the belt you're grading FOR. One belt per grading.
  * 3 days between gradings, pass or fail. 60 minutes per exam; unanswered questions score 0.
  * ClipSensei writes a short sensei verdict either way. A new belt goes on the event bus (BeltAwarded).

Data (single table)
  USER#learner / GRADING#topicId#startedAt   the exam: questions, marked answers, score, verdict
  USER#learner / BELT#topicId                current belt + when they last graded
  TOPIC#topicId / BELTSTATS                  how many learners hold each belt (educator insights, atomic ADDs)
"""
import json
import random
from datetime import timedelta
from decimal import Decimal

from boto3.dynamodb.conditions import Key

BELTS = ["WHITE", "YELLOW", "ORANGE", "GREEN", "BLUE", "BROWN", "BLACK"]
PASS_MARK = {"YELLOW": 50, "ORANGE": 60, "GREEN": 70, "BLUE": 80, "BROWN": 90, "BLACK": 95}
COOLDOWN_DAYS = 3
MAX_QUESTIONS = 15
EXAM_MINUTES = 60
REMEMBERED_AFTER_DAYS = 3        # schedule interval >= 3 days means: recalled it again at least a day later

VERDICT_PROMPT = """You are ClipSensei, a warm but honest martial-arts sensei who teaches cloud computing.
A learner has just finished a grading (an exam). Write 2-3 short sentences in the second person:
name what they clearly know, then the one or two ideas to work on before their next grading.
If they passed, congratulate them on the new belt. If not, encourage them - say the belt is close.
No lists, no emojis, under 70 words. Use only the results given; never invent topics."""


class Grading:
    """Wraps the marking module's helpers (table, Bedrock marking, events) so this file stays focused on rules."""

    def __init__(self, m):
        self.m = m

    # -------------------------------------------------------------------------
    # Reading the module and the learner's position in it
    # -------------------------------------------------------------------------
    def _query(self, key_condition, **kwargs):
        items, kwargs = [], {"KeyConditionExpression": key_condition, **kwargs}
        while True:
            page = self.m.table(self.m.TABLE_NAME).query(**kwargs)
            items += page.get("Items", [])
            if "LastEvaluatedKey" not in page:
                return items
            kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]

    def module(self, topic_id):
        """[(lesson, objective)] for every published learning objective in the module, in course order."""
        links = self._query(Key("PK").eq(f"TOPIC#{topic_id}") & Key("SK").begins_with("LESSON#"))
        objectives = []
        lessons = []
        for link in links:
            video_id = link["SK"].split("#", 1)[1]
            lesson = self.m.table(self.m.TABLE_NAME).get_item(Key={"PK": f"VIDEO#{video_id}", "SK": "META"}).get("Item")
            if lesson and lesson.get("objectivesStatus") == "PUBLISHED":
                lessons.append(lesson)
        for lesson in sorted(lessons, key=lambda l: (int(l.get("position") or 999), l.get("publishedAt", ""))):
            video_id = lesson["PK"].split("#", 1)[1]
            for obj in self._query(Key("PK").eq(f"VIDEO#{video_id}") & Key("SK").begins_with("OBJ#")):
                if obj.get("status", "PUBLISHED") == "PUBLISHED" and obj.get("prompts"):
                    objectives.append((lesson, obj))
        return objectives

    def schedules(self, user_id, video_ids):
        """objectiveId -> the learner's review schedule (exists once they've practised that idea)."""
        found = {}
        for video_id in dict.fromkeys(video_ids):
            for item in self._query(Key("PK").eq(f"VIDEO#{video_id}") & Key("SK").begins_with(f"LEARNER#{user_id}#OBJ#")):
                found[item["SK"].rsplit("#", 1)[1]] = item
        return found

    def belt(self, user_id, topic_id):
        return self.m.table(self.m.TABLE_NAME).get_item(Key={"PK": f"USER#{user_id}", "SK": f"BELT#{topic_id}"}).get("Item") or {}

    def latest_session(self, user_id, topic_id):
        items = self.m.table(self.m.TABLE_NAME).query(
            KeyConditionExpression=Key("PK").eq(f"USER#{user_id}") & Key("SK").begins_with(f"GRADING#{topic_id}#"),
            ScanIndexForward=False, Limit=1).get("Items", [])
        return items[0] if items else None

    # -------------------------------------------------------------------------
    # GET /learn/grading/{topicId}
    # -------------------------------------------------------------------------
    def status(self, user_id, topic_id, now):
        objectives = self.module(topic_id)
        if not objectives:
            return self.m.response(404, {"message": "This course has no published lessons yet"})
        session = self.latest_session(user_id, topic_id)
        if session and session["status"] == "IN_PROGRESS" and self._expired(session, now):
            session = self.finish(user_id, session, now)          # time ran out: unanswered questions score 0
        schedules = self.schedules(user_id, [lesson["PK"].split("#", 1)[1] for lesson, _ in objectives])
        practised = sum(1 for _, obj in objectives if obj["SK"].split("#", 1)[1] in schedules)
        remembered = sum(1 for _, obj in objectives
                         if int(schedules.get(obj["SK"].split("#", 1)[1], {}).get("intervalDays") or 0) >= REMEMBERED_AFTER_DAYS)
        belt = self.belt(user_id, topic_id)
        current = belt.get("belt", "WHITE")
        next_belt = BELTS[BELTS.index(current) + 1] if current != "BLACK" else None
        next_grading_at = None
        if belt.get("lastGradingAt"):
            next_grading_at = self.m.iso(self._parse(belt["lastGradingAt"]) + timedelta(days=COOLDOWN_DAYS))
        total = len(objectives)
        eligible = practised == total and remembered == total
        waiting = bool(next_grading_at and next_grading_at > self.m.iso(now))
        in_progress = session if session and session["status"] == "IN_PROGRESS" else None
        return self.m.response(200, {
            "topicId": topic_id, "topicName": objectives[0][0].get("topicName"), "belt": current, "nextBelt": next_belt,
            "passMark": PASS_MARK.get(next_belt), "questionCount": min(total, MAX_QUESTIONS),
            "requirements": {"ideas": total, "practised": practised, "remembered": remembered},
            "eligible": eligible, "nextGradingAt": next_grading_at,
            "canStart": bool(next_belt) and eligible and not waiting or bool(in_progress),
            "inProgress": self._public(in_progress) if in_progress else None,
            "lastResult": self._result(session) if session and session["status"] == "COMPLETE" else None,
        })

    # -------------------------------------------------------------------------
    # POST /learn/grading/{topicId}/start
    # -------------------------------------------------------------------------
    def start(self, user_id, display_name, topic_id, now):
        current = json.loads(self.status(user_id, topic_id, now)["body"])
        if "requirements" not in current:
            return self.m.response(404, current)
        if current["inProgress"]:
            return self.m.response(200, current["inProgress"])                # resume, never a second exam
        if not current["canStart"]:
            reason = ("You already hold a black belt in this course." if not current["nextBelt"]
                      else "Practise every idea and remember each one after a day to unlock grading." if not current["eligible"]
                      else "Your next grading opens on " + current["nextGradingAt"][:10] + ".")
            return self.m.response(409, {"message": reason})

        objectives = self.module(topic_id)
        if len(objectives) > MAX_QUESTIONS:
            objectives = sorted(random.sample(objectives, MAX_QUESTIONS), key=objectives.index)
        schedules = self.schedules(user_id, [lesson["PK"].split("#", 1)[1] for lesson, _ in objectives])
        questions = []
        for lesson, obj in objectives:
            objective_id = obj["SK"].split("#", 1)[1]
            last = int(schedules.get(objective_id, {}).get("lastPromptIndex", 0))
            index = (last + 1) % len(obj["prompts"])                           # not the wording they saw last
            questions.append({"videoId": lesson["PK"].split("#", 1)[1], "objectiveId": objective_id, "promptIndex": index,
                              "prompt": obj["prompts"][index], "lessonTitle": lesson.get("title", "")})
        started = self.m.iso(now)
        session = {"PK": f"USER#{user_id}", "SK": f"GRADING#{topic_id}#{started}", "status": "IN_PROGRESS",
                   "topicId": topic_id, "topicName": current["topicName"], "learnerName": display_name,
                   "fromBelt": current["belt"], "targetBelt": current["nextBelt"], "passMark": current["passMark"],
                   "questions": questions, "answers": {}, "startedAt": started,
                   "expiresAt": self.m.iso(now + timedelta(minutes=EXAM_MINUTES))}
        self.m.table(self.m.TABLE_NAME).put_item(Item=session)
        return self.m.response(201, self._public(session))

    # -------------------------------------------------------------------------
    # POST /learn/grading/{topicId}/answer
    # -------------------------------------------------------------------------
    def answer(self, user_id, topic_id, body, now):
        sk = f"GRADING#{topic_id}#{body.get('gradingId', '')}"
        session = self.m.table(self.m.TABLE_NAME).get_item(Key={"PK": f"USER#{user_id}", "SK": sk}).get("Item")
        if not session:
            return self.m.response(404, {"message": "Grading not found"})
        if session["status"] != "IN_PROGRESS":
            return self.m.response(409, {"message": "This grading has finished", "result": self._result(session)})
        if self._expired(session, now):
            return self.m.response(200, {"finished": True, "result": self._result(self.finish(user_id, session, now))})
        try:
            index = int(body.get("index"))
            question = session["questions"][index]
        except (TypeError, ValueError, IndexError):
            return self.m.response(400, {"message": "Unknown question"})
        if str(index) in session["answers"]:
            return self.m.response(409, {"message": "One answer per question - this one is already marked"})

        answer = str(body.get("answer", ""))[:self.m.MAX_ANSWER_CHARS]
        dont_remember = bool(body.get("dontRemember"))
        if not dont_remember and len(answer.strip()) < 3:
            return self.m.response(400, {"message": "Write an answer, or choose 'I don't remember'"})
        key = self.m.table(self.m.KEYS_TABLE_NAME).get_item(
            Key={"PK": f"VIDEO#{question['videoId']}", "SK": f"OBJ#{question['objectiveId']}"}).get("Item")
        points = key["essentialPoints"] if key else []
        if dont_remember or not points:
            results = [{"pointId": p["pointId"], "point": p["text"], "verdict": "missing", "evidence": "", "confidence": "high"}
                       for p in points]
            needs_review = False
        else:
            marks, _usage = self.m.mark_with_clipsensei(question["prompt"], points, answer)   # same marking as practice
            results, needs_review = self.m.check_marks(points, marks, answer)
        share = self.share(results)
        marked = {"share": Decimal(str(share)), "needsReview": needs_review, "answer": answer,   # DynamoDB: no floats
                  "points": [{"point": r["point"], "verdict": r["verdict"]} for r in results]}
        try:
            self.m.table(self.m.TABLE_NAME).update_item(
                Key={"PK": f"USER#{user_id}", "SK": sk},
                UpdateExpression="SET answers.#i = :a",
                ConditionExpression="#s = :ip AND attribute_not_exists(answers.#i)",       # one answer, open exams only
                ExpressionAttributeNames={"#i": str(index), "#s": "status"},
                ExpressionAttributeValues={":a": marked, ":ip": "IN_PROGRESS"})
        except Exception as error:                     # noqa: BLE001
            if "ConditionalCheckFailed" in type(error).__name__ + str(error):
                return self.m.response(409, {"message": "One answer per question - this one is already marked"})
            raise
        session["answers"][str(index)] = marked
        if len(session["answers"]) == len(session["questions"]):
            return self.m.response(200, {"finished": True, "result": self._result(self.finish(user_id, session, now))})
        return self.m.response(200, {"finished": False, "answered": len(session["answers"]), "total": len(session["questions"])})

    # -------------------------------------------------------------------------
    # Scoring, the belt and the sensei verdict
    # -------------------------------------------------------------------------
    @staticmethod
    def share(results):
        """Share of key ideas recalled in one answer: covered = 1, partial = 0.5, anything else 0."""
        if not results:
            return 0
        return round(sum(1 if r["verdict"] == "covered" else 0.5 if r["verdict"] == "partial" else 0 for r in results) / len(results), 4)

    def finish(self, user_id, session, now):
        questions, answers = session["questions"], session["answers"]
        score = round(100 * sum(float(answers.get(str(i), {}).get("share", 0)) for i in range(len(questions))) / len(questions))
        passed = score >= int(session["passMark"])
        verdict = self.sensei_verdict(session, score, passed)
        finished = self.m.iso(now)
        try:
            self.m.table(self.m.TABLE_NAME).update_item(
                Key={"PK": session["PK"], "SK": session["SK"]},
                UpdateExpression="SET #s = :done, score = :sc, passed = :p, verdict = :v, completedAt = :at",
                ConditionExpression="#s = :ip",                                    # finishes exactly once
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={":done": "COMPLETE", ":sc": score, ":p": passed, ":v": verdict,
                                           ":at": finished, ":ip": "IN_PROGRESS"})
        except Exception as error:                     # noqa: BLE001 - another request finished it first
            if "ConditionalCheckFailed" not in type(error).__name__ + str(error):
                raise
            return self.m.table(self.m.TABLE_NAME).get_item(Key={"PK": session["PK"], "SK": session["SK"]})["Item"]

        topic_id, target = session["topicId"], session["targetBelt"]
        belt_update = "SET lastGradingAt = :at, lastScore = :sc, topicName = :tn"
        values = {":at": finished, ":sc": score, ":tn": session.get("topicName") or ""}
        if passed:
            belt_update += ", belt = :b, awardedAt = :at"
            values[":b"] = target
        self.m.table(self.m.TABLE_NAME).update_item(Key={"PK": f"USER#{user_id}", "SK": f"BELT#{topic_id}"},
                                                    UpdateExpression=belt_update, ExpressionAttributeValues=values)
        if passed:
            # educator insights: how many learners hold each belt in this module (one atomic update, no scans)
            counts, names = ", ".join(["#new :one"] + (["#old :minus"] if session["fromBelt"] != "WHITE" else [])), {"#new": target}
            stats_values = {":one": 1}
            if session["fromBelt"] != "WHITE":
                names["#old"], stats_values[":minus"] = session["fromBelt"], -1
            self.m.table(self.m.TABLE_NAME).update_item(Key={"PK": f"TOPIC#{topic_id}", "SK": "BELTSTATS"},
                                                        UpdateExpression=f"ADD {counts}",
                                                        ExpressionAttributeNames=names, ExpressionAttributeValues=stats_values)
            self.m.announce("BeltAwarded", {"learnerSub": user_id, "learnerName": session.get("learnerName"),
                                            "topicId": topic_id, "topicName": session.get("topicName"),
                                            "belt": target, "score": score})
        session.update({"status": "COMPLETE", "score": score, "passed": passed, "verdict": verdict, "completedAt": finished})
        return session

    def sensei_verdict(self, session, score, passed):
        """2-3 sentences from ClipSensei. If Bedrock is unavailable, a plain verdict - never blocks the result."""
        lines = []
        for i, q in enumerate(session["questions"]):
            marked = session["answers"].get(str(i))
            missed = [p["point"] for p in (marked or {}).get("points", []) if p["verdict"] != "covered"]
            lines.append(f"- {q['lessonTitle']}: {q['prompt']} -> " + ("not answered" if not marked
                         else "all key ideas" if not missed else "missed: " + "; ".join(missed)))
        outcome = (f"PASSED: earned the {session['targetBelt'].title()} belt" if passed
                   else f"NOT PASSED: needed {session['passMark']}% for the {session['targetBelt'].title()} belt")
        try:
            reply = self.m.client("bedrock-runtime").converse(
                modelId=self.m.MODEL_ID, system=[{"text": VERDICT_PROMPT}],
                messages=[{"role": "user", "content": [{"text": f"Score: {score}%. {outcome}.\nResults:\n" + "\n".join(lines)}]}],
                inferenceConfig={"maxTokens": 220, "temperature": 0.3})
            text = " ".join(b["text"] for b in reply["output"]["message"]["content"] if "text" in b).strip()
            if text:
                return text[:600]
        except Exception as error:                     # noqa: BLE001
            print(json.dumps({"level": "WARNING", "message": "sensei verdict unavailable", "error": str(error)}))
        return (f"You scored {score}% and earned your {session['targetBelt'].title()} belt. Keep reviewing to stay sharp." if passed
                else f"You scored {score}% - {session['passMark']}% earns the {session['targetBelt'].title()} belt. Review the ideas you missed and try again.")

    # -------------------------------------------------------------------------
    # What the browser sees
    # -------------------------------------------------------------------------
    @staticmethod
    def _parse(value):
        from datetime import datetime
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    def _expired(self, session, now):
        return self.m.iso(now) > session["expiresAt"]

    @staticmethod
    def _public(session):
        """An exam in progress: the questions (never the answer keys) and which are already answered."""
        return {"gradingId": session["startedAt"], "targetBelt": session["targetBelt"], "passMark": int(session["passMark"]),
                "expiresAt": session["expiresAt"], "answered": sorted(int(i) for i in session["answers"]),
                "questions": [{"index": i, "prompt": q["prompt"], "lessonTitle": q["lessonTitle"]}
                              for i, q in enumerate(session["questions"])]}

    @staticmethod
    def _result(session):
        """A finished exam: score, belt, verdict and a per-question breakdown (shown only now, after the exam)."""
        return {"gradingId": session["startedAt"], "score": int(session["score"]), "passed": bool(session["passed"]),
                "targetBelt": session["targetBelt"], "fromBelt": session["fromBelt"], "passMark": int(session["passMark"]),
                "verdict": session.get("verdict", ""), "completedAt": session.get("completedAt"),
                "questions": [{"prompt": q["prompt"], "lessonTitle": q["lessonTitle"],
                               "share": float(session["answers"].get(str(i), {}).get("share", 0)),
                               "answered": str(i) in session["answers"],
                               "points": session["answers"].get(str(i), {}).get("points", [])}
                              for i, q in enumerate(session["questions"])]}
