"""Notifier - turns StreamDojo business events into in-app bell notifications.

Triggered by EventBridge (custom bus streamdojo-<env>-events). Publishers don't know or care who
listens: they just announce what happened. This function decides WHO should hear about it.

  LessonDraftReady   -> every educator      "'AWS' is ready for review"
  LessonPublished    -> the lesson's creator "'AWS' was approved and published"
  LessonRejected     -> the lesson's creator "'AWS' was not approved: <reason>"
  PracticeCompleted  -> every educator      "Alex completed 'AWS': 2 of 3 ideas recalled"

Each notification is one DynamoDB item:  PK USER#<sub>  SK NOTIF#<time>#<event id>
EventBridge delivers "at least once", so the event id is in the key and the write is conditional:
a duplicate delivery can never create a duplicate notification. Items expire after 30 days (TTL).
"""
import json
import logging
import os
import time

import boto3
from botocore.exceptions import ClientError

TABLE_NAME = os.environ.get("TABLE_NAME", "")
USER_POOL_ID = os.environ.get("USER_POOL_ID", "")
KEEP_DAYS = 30

logger = logging.getLogger()
logger.setLevel(logging.INFO)
_clients = {}


def client(name):
    if name not in _clients:
        _clients[name] = boto3.client(name)
    return _clients[name]


def table():
    if "table" not in _clients:
        _clients["table"] = boto3.resource("dynamodb").Table(TABLE_NAME)
    return _clients["table"]


def educators():
    """Everyone in the Cognito 'educators' group (paginated)."""
    subs, kwargs = [], {"UserPoolId": USER_POOL_ID, "GroupName": "educators"}
    while True:
        page = client("cognito-idp").list_users_in_group(**kwargs)
        for user in page.get("Users", []):
            sub = next((a["Value"] for a in user.get("Attributes", []) if a["Name"] == "sub"), None)
            if sub and user.get("Enabled", True):
                subs.append(sub)
        if not page.get("NextToken"):
            return subs
        kwargs["NextToken"] = page["NextToken"]


def describe(detail_type, d):
    """(recipients, message, link) for each kind of event. Unknown events notify nobody."""
    title = d.get("title") or "A lesson"
    if detail_type == "LessonDraftReady":
        return educators(), f"“{title}” is ready for review - ClipSensei drafted {d.get('objectiveCount', 0)} question(s).", f"#/teach/review/{d['videoId']}"
    if detail_type == "LessonPublished":
        return [d["creatorSub"]], f"“{title}” was approved and published in {d.get('topicName', 'a course')}.", "#/create"
    if detail_type == "LessonRejected":
        return [d["creatorSub"]], f"“{title}” was not approved: {d.get('reason', '')}", "#/create"
    if detail_type == "PracticeCompleted":
        return educators(), (f"{d.get('learnerName', 'A learner')} completed “{title}”: "
                             f"{d.get('recalled', 0)} of {d.get('total', 0)} ideas recalled unaided."), f"#/teach/insights/{d['videoId']}"
    return [], "", ""


def handler(event, context):
    detail_type, detail = event["detail-type"], event.get("detail", {})
    recipients, message, link = describe(detail_type, detail)
    created = event.get("time")                                  # when it happened, per EventBridge
    written = 0
    for sub in dict.fromkeys(r for r in recipients if r):        # de-duplicate, keep order
        try:
            table().put_item(
                Item={"PK": f"USER#{sub}", "SK": f"NOTIF#{created}#{event['id']}", "type": detail_type,
                      "message": message[:300], "link": link, "read": False, "createdAt": created,
                      "expiresAt": int(time.time()) + KEEP_DAYS * 86400},        # DynamoDB TTL deletes it later
                ConditionExpression="attribute_not_exists(PK)")
            written += 1
        except ClientError as error:
            if error.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise                                            # a real error: let EventBridge retry
    logger.info(json.dumps({"event": detail_type, "recipients": len(recipients), "written": written}))
    return {"written": written}
