"""Notifications API - what the bell in the header reads.

GET  /notifications         your latest 20 notifications, newest first, plus how many are unread
POST /notifications/read    mark them all as read (called when you open the bell)

Every signed-in user, but only ever their OWN items: the partition key comes from the verified
token (sub), never from the request.
"""
import json
import os

import boto3
from boto3.dynamodb.conditions import Key

TABLE_NAME = os.environ.get("TABLE_NAME", "")
ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "*")
SHOW = 20
_tables = {}


def table():
    if "app" not in _tables:
        _tables["app"] = boto3.resource("dynamodb").Table(TABLE_NAME)
    return _tables["app"]


def response(status, body):
    return {"statusCode": status,
            "headers": {"Content-Type": "application/json", "Access-Control-Allow-Origin": ALLOWED_ORIGIN},
            "body": json.dumps(body, ensure_ascii=False)}


def latest(user_id):
    return table().query(
        KeyConditionExpression=Key("PK").eq(f"USER#{user_id}") & Key("SK").begins_with("NOTIF#"),
        ScanIndexForward=False, Limit=SHOW)["Items"]                  # SK starts with the time: newest first


def handler(event, context):
    user_id = event["requestContext"]["authorizer"]["claims"]["sub"]
    route = f"{event['httpMethod']} {event['resource']}"
    items = latest(user_id)
    if route == "GET /notifications":
        return response(200, {
            "unread": sum(1 for i in items if not i.get("read")),
            "notifications": [{"id": i["SK"], "type": i.get("type"), "message": i.get("message"), "link": i.get("link"),
                               "read": bool(i.get("read")), "createdAt": i.get("createdAt")} for i in items]})
    if route == "POST /notifications/read":
        unread = [i for i in items if not i.get("read")]
        for item in unread:
            table().update_item(Key={"PK": item["PK"], "SK": item["SK"]}, UpdateExpression="SET #r = :t",
                                ExpressionAttributeNames={"#r": "read"}, ExpressionAttributeValues={":t": True})
        return response(200, {"marked": len(unread)})
    return response(404, {"message": "Unknown route"})
