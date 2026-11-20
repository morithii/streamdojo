"""Educator API - review ClipSensei's draft objectives, edit them, and publish the lesson.

GET  /lessons?status=DRAFT                            lessons waiting for review (oldest first)
GET  /lessons/{videoId}/objectives                    objectives with prompts AND answer keys
PUT  /lessons/{videoId}                 {"title": ...}  rename the lesson
PUT  /lessons/{videoId}/objectives/{objectiveId}      save the educator's edits
POST /lessons/{videoId}/objectives                    add an objective ClipSensei didn't pick (max 5)
DELETE /lessons/{videoId}/objectives/{objectiveId}    remove a weak draft objective (drafts only, keep at least 1)
POST /lessons/{videoId}/publish   {"topic": "..."}    publish: DRAFTS queue -> CATALOGUE

Educators only. Nothing is shown to learners until an educator publishes it.
"""
import json
import os
import re
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Key

TABLE_NAME = os.environ.get("TABLE_NAME", "")
KEYS_TABLE_NAME = os.environ.get("KEYS_TABLE_NAME", "")
ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "*")
TIMESTAMP = re.compile(r"^\d{1,3}:\d{2}$")
MAX_OBJECTIVES = 5

_tables = {}


def table(name):
    if name not in _tables:
        _tables[name] = boto3.resource("dynamodb").Table(name)
    return _tables[name]


def response(status, body):
    return {"statusCode": status,
            "headers": {"Content-Type": "application/json", "Access-Control-Allow-Origin": ALLOWED_ORIGIN},
            "body": json.dumps(body, default=lambda v: int(v) if v == int(v) else float(v))}   # DynamoDB Decimals


def caller(event):
    claims = event["requestContext"]["authorizer"]["claims"]
    raw = claims.get("cognito:groups") or ""
    groups = raw if isinstance(raw, list) else raw.strip("[]").replace(",", " ").split()
    return claims["sub"], set(groups)


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60]


def list_drafts():
    result = table(TABLE_NAME).query(IndexName="GSI1", KeyConditionExpression=Key("GSI1PK").eq("DRAFTS"),
                                     ScanIndexForward=True, Limit=50)          # oldest first: a fair queue
    fields = ["videoId", "title", "createdAt", "captionStatus", "objectivesStatus", "objectiveCount"]
    return response(200, {"lessons": [{f: i.get(f) for f in fields} for i in result.get("Items", [])]})


def get_objectives(video_id):
    lesson = table(TABLE_NAME).get_item(Key={"PK": f"VIDEO#{video_id}", "SK": "META"}).get("Item")
    if not lesson:
        return response(404, {"message": "Lesson not found"})
    public = [i for i in table(TABLE_NAME).query(KeyConditionExpression=Key("PK").eq(f"VIDEO#{video_id}") & Key("SK").begins_with("OBJ#"))["Items"]
              if i["SK"].startswith("OBJ#")]
    keys = {k["objectiveId"]: k for k in table(KEYS_TABLE_NAME).query(KeyConditionExpression=Key("PK").eq(f"VIDEO#{video_id}"))["Items"]}
    objectives = []
    for p in sorted(public, key=lambda item: item["objectiveId"]):
        k = keys.get(p["objectiveId"], {})
        objectives.append({"objectiveId": p["objectiveId"], "objective": p["objective"], "prompts": p["prompts"],
                           "timestamp": p["timestamp"], "status": p["status"],
                           "essentialPoints": k.get("essentialPoints", []), "modelAnswer": k.get("modelAnswer"),
                           "hint": k.get("hint")})
    return response(200, {"videoId": video_id, "title": lesson.get("title"), "suggestedTitle": lesson.get("suggestedTitle"),
                          "objectivesStatus": lesson.get("objectivesStatus"), "objectives": objectives})


def validate_edit(body):
    for field in ("objective", "modelAnswer", "hint"):
        if not isinstance(body.get(field), str) or not body[field].strip():
            return f"{field} is required"
    points, prompts = body.get("essentialPoints"), body.get("prompts")
    if not isinstance(points, list) or not 2 <= len(points) <= 4 or not all(isinstance(p, str) and p.strip() for p in points):
        return "essentialPoints must be a list of 2-4 non-empty points"
    if not isinstance(prompts, list) or len(prompts) != 3 or not all(isinstance(p, str) and p.strip() for p in prompts):
        return "prompts must be a list of 3 questions"
    if not TIMESTAMP.match(str(body.get("timestamp", ""))):
        return "timestamp must look like mm:ss"
    return None


def update_objective(video_id, objective_id, body, user_id):
    error = validate_edit(body)
    if error:
        return response(400, {"message": error})
    key = {"PK": f"VIDEO#{video_id}", "SK": f"OBJ#{objective_id}"}
    if "Item" not in table(TABLE_NAME).get_item(Key=key):
        return response(404, {"message": "Objective not found"})
    table(TABLE_NAME).update_item(
        Key=key, UpdateExpression="SET objective = :o, prompts = :p, #ts = :t, editedBy = :u, editedAt = :at",
        ExpressionAttributeNames={"#ts": "timestamp"},
        ExpressionAttributeValues={":o": body["objective"].strip(), ":p": [p.strip() for p in body["prompts"]],
                                   ":t": body["timestamp"], ":u": user_id, ":at": now()})
    table(KEYS_TABLE_NAME).update_item(
        Key=key, UpdateExpression="SET essentialPoints = :e, modelAnswer = :m, hint = :h",
        ExpressionAttributeValues={":e": [{"pointId": f"p{i}", "text": p.strip()} for i, p in enumerate(body["essentialPoints"], 1)],
                                   ":m": body["modelAnswer"].strip(), ":h": body["hint"].strip()})
    return response(200, {"message": "Saved", "objectiveId": objective_id})


def rename_lesson(video_id, body, user_id):
    title = str(body.get("title", "")).strip()
    if not 3 <= len(title) <= 120:
        return response(400, {"message": "title must be 3-120 characters"})
    lesson_key = {"PK": f"VIDEO#{video_id}", "SK": "META"}
    lesson = table(TABLE_NAME).get_item(Key=lesson_key).get("Item")
    if not lesson:
        return response(404, {"message": "Lesson not found"})
    table(TABLE_NAME).update_item(Key=lesson_key, UpdateExpression="SET title = :t, editedBy = :u, editedAt = :at",
                                  ExpressionAttributeValues={":t": title, ":u": user_id, ":at": now()})
    if lesson.get("topicId"):                   # the topic's lesson list keeps a copy of the title too
        table(TABLE_NAME).update_item(Key={"PK": f"TOPIC#{lesson['topicId']}", "SK": f"LESSON#{video_id}"},
                                      UpdateExpression="SET title = :t", ExpressionAttributeValues={":t": title})
    return response(200, {"message": "Renamed", "title": title})


def lesson_objectives(video_id):
    query = Key("PK").eq(f"VIDEO#{video_id}") & Key("SK").begins_with("OBJ#")
    return [i for i in table(TABLE_NAME).query(KeyConditionExpression=query)["Items"] if i["SK"].startswith("OBJ#")]


def add_objective(video_id, body, user_id):
    error = validate_edit(body)
    if error:
        return response(400, {"message": error})
    lesson_key = {"PK": f"VIDEO#{video_id}", "SK": "META"}
    lesson = table(TABLE_NAME).get_item(Key=lesson_key).get("Item")
    if not lesson or lesson.get("objectivesStatus") not in ("DRAFT_READY", "PUBLISHED"):
        return response(404, {"message": "Lesson not found or ClipSensei hasn't drafted it yet"})
    existing = lesson_objectives(video_id)
    if len(existing) >= MAX_OBJECTIVES:
        return response(409, {"message": f"A lesson can have at most {MAX_OBJECTIVES} objectives"})
    number = max([int(o["objectiveId"][3:]) for o in existing] + [0]) + 1     # obj1, obj2... never reuse an id
    objective_id = f"obj{number}"
    # On a published lesson the new objective goes live straight away, under the lesson's topic
    status = "PUBLISHED" if lesson["objectivesStatus"] == "PUBLISHED" else "DRAFT"
    extra = {"topicId": lesson["topicId"]} if status == "PUBLISHED" else {}
    key = {"PK": f"VIDEO#{video_id}", "SK": f"OBJ#{objective_id}"}
    table(TABLE_NAME).put_item(Item={**key, **extra, "objectiveId": objective_id, "objective": body["objective"].strip(),
                                     "prompts": [p.strip() for p in body["prompts"]], "timestamp": body["timestamp"],
                                     "status": status, "createdBy": user_id, "editedAt": now()},
                               ConditionExpression="attribute_not_exists(PK)")
    table(KEYS_TABLE_NAME).put_item(Item={**key, **extra, "objectiveId": objective_id, "status": status,
        "essentialPoints": [{"pointId": f"p{i}", "text": p.strip()} for i, p in enumerate(body["essentialPoints"], 1)],
        "modelAnswer": body["modelAnswer"].strip(), "hint": body["hint"].strip()})
    table(TABLE_NAME).update_item(Key=lesson_key, UpdateExpression="ADD objectiveCount :one",
                                  ExpressionAttributeValues={":one": 1})
    return response(201, {"message": "Added", "objectiveId": objective_id, "status": status})


def delete_objective(video_id, objective_id):
    lesson_key = {"PK": f"VIDEO#{video_id}", "SK": "META"}
    lesson = table(TABLE_NAME).get_item(Key=lesson_key).get("Item")
    if not lesson:
        return response(404, {"message": "Lesson not found"})
    if lesson.get("objectivesStatus") != "DRAFT_READY":
        # Learners may already have reviews scheduled for a published objective: edit it instead
        return response(409, {"message": "Only draft objectives can be removed - edit a published one instead"})
    existing = [o["objectiveId"] for o in lesson_objectives(video_id)]
    if objective_id not in existing:
        return response(404, {"message": "Objective not found"})
    if len(existing) == 1:
        return response(409, {"message": "A lesson needs at least one objective"})
    key = {"PK": f"VIDEO#{video_id}", "SK": f"OBJ#{objective_id}"}
    table(TABLE_NAME).delete_item(Key=key)
    table(KEYS_TABLE_NAME).delete_item(Key=key)
    table(TABLE_NAME).update_item(Key=lesson_key, UpdateExpression="ADD objectiveCount :minus",
                                  ExpressionAttributeValues={":minus": -1})
    return response(200, {"message": "Removed", "objectiveId": objective_id})


def publish(video_id, body, user_id):
    topic = str(body.get("topic", "")).strip()
    if not 2 <= len(topic) <= 60 or not slug(topic):
        return response(400, {"message": "topic must be 2-60 characters, e.g. 'CloudFront & S3'"})
    lesson_key = {"PK": f"VIDEO#{video_id}", "SK": "META"}
    lesson = table(TABLE_NAME).get_item(Key=lesson_key).get("Item")
    if not lesson:
        return response(404, {"message": "Lesson not found"})
    if lesson.get("processingStatus") != "READY" or lesson.get("objectivesStatus") != "DRAFT_READY":
        return response(409, {"message": "Only a READY lesson with draft objectives can be published"})

    topic_id, published_at = slug(topic), now()
    objectives = [i for i in table(TABLE_NAME).query(KeyConditionExpression=Key("PK").eq(f"VIDEO#{video_id}") & Key("SK").begins_with("OBJ#"))["Items"]
                  if i["SK"].startswith("OBJ#")]
    for obj in objectives:                      # objectives first...
        for name in (TABLE_NAME, KEYS_TABLE_NAME):
            table(name).update_item(Key={"PK": obj["PK"], "SK": obj["SK"]}, UpdateExpression="SET #s = :p, topicId = :t",
                                    ExpressionAttributeNames={"#s": "status"},
                                    ExpressionAttributeValues={":p": "PUBLISHED", ":t": topic_id})
    table(TABLE_NAME).put_item(Item={"PK": f"TOPIC#{topic_id}", "SK": "META", "topicId": topic_id, "name": topic})
    table(TABLE_NAME).put_item(Item={"PK": f"TOPIC#{topic_id}", "SK": f"LESSON#{video_id}",
                                     "videoId": video_id, "title": lesson.get("title"), "publishedAt": published_at})
    # ...the lesson last: this single conditional write is what makes it visible (DRAFTS -> CATALOGUE)
    table(TABLE_NAME).update_item(
        Key=lesson_key,
        UpdateExpression=("SET objectivesStatus = :pub, topicId = :t, topicName = :tn, publishedBy = :u, "
                          "publishedAt = :at, GSI1PK = :cat, GSI1SK = :sk"),
        ConditionExpression="objectivesStatus = :draft",
        ExpressionAttributeValues={":pub": "PUBLISHED", ":t": topic_id, ":tn": topic, ":u": user_id, ":at": published_at,
                                   ":cat": "CATALOGUE", ":sk": f"{published_at}#{video_id}", ":draft": "DRAFT_READY"})
    return response(200, {"message": "Published", "videoId": video_id, "topicId": topic_id})


def handler(event, context):
    user_id, groups = caller(event)
    if "educators" not in groups:
        return response(403, {"message": "Only educators can review and publish lessons"})
    route, params = f"{event['httpMethod']} {event['resource']}", event.get("pathParameters") or {}
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return response(400, {"message": "Body must be valid JSON"})

    if route == "GET /lessons":
        return list_drafts()
    if route == "GET /lessons/{videoId}/objectives":
        return get_objectives(params["videoId"])
    if route == "PUT /lessons/{videoId}/objectives/{objectiveId}":
        return update_objective(params["videoId"], params["objectiveId"], body, user_id)
    if route == "PUT /lessons/{videoId}":
        return rename_lesson(params["videoId"], body, user_id)
    if route == "POST /lessons/{videoId}/objectives":
        return add_objective(params["videoId"], body, user_id)
    if route == "DELETE /lessons/{videoId}/objectives/{objectiveId}":
        return delete_objective(params["videoId"], params["objectiveId"])
    if route == "POST /lessons/{videoId}/publish":
        return publish(params["videoId"], body, user_id)
    return response(404, {"message": "Unknown route"})
