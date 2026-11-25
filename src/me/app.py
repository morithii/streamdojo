"""GET /me - returns who the caller is, which StreamDojo groups they belong to, and their dojo points.

API Gateway has already checked the Cognito ID token before this runs, and
passes the token's claims in requestContext.authorizer.claims.
"""
import json
import os

import boto3

ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "*")
KNOWN_GROUPS = {"creators", "educators", "learners"}
TABLE_NAME = os.environ.get("TABLE_NAME", "")
_table = None


def table():
    global _table
    if _table is None:
        _table = boto3.resource("dynamodb").Table(TABLE_NAME)
    return _table


def dojo_points(user_id):
    """The learner's running total (marking ADDs to it). 0 if they haven't scored yet; never breaks /me."""
    if not TABLE_NAME:
        return 0
    try:
        item = table().get_item(Key={"PK": f"USER#{user_id}", "SK": "PROFILE"}, ProjectionExpression="dojoPoints").get("Item") or {}
        return int(item.get("dojoPoints", 0))
    except Exception as error:                     # noqa: BLE001 - points are a bonus, sign-in must still work
        print(json.dumps({"level": "WARNING", "message": "could not read dojo points", "error": str(error)}))
        return 0


def parse_groups(raw):
    """API Gateway passes cognito:groups as a string, e.g. "learners" or "[creators, educators]"."""
    if not raw:
        return []
    if isinstance(raw, list):
        items = raw
    else:
        items = raw.strip("[]").replace(",", " ").split()
    # only report groups the app knows about, in a stable order
    return sorted(g for g in items if g in KNOWN_GROUPS)


def handler(event, context):
    claims = event["requestContext"]["authorizer"]["claims"]
    body = {
        "userId": claims["sub"],                                  # stable Cognito user ID
        "displayName": claims.get("name") or "Learner",           # never return the email to other users
        "groups": parse_groups(claims.get("cognito:groups")),
        "dojoPoints": dojo_points(claims["sub"]),
    }
    return {
        "statusCode": 200,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": ALLOWED_ORIGIN,        # CORS for the browser app
        },
        "body": json.dumps(body),
    }
