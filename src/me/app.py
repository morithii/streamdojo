"""GET /me - returns who the caller is and which StreamDojo groups they belong to.

API Gateway has already checked the Cognito ID token before this runs, and
passes the token's claims in requestContext.authorizer.claims.
"""
import json
import os

ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "*")
KNOWN_GROUPS = {"creators", "educators", "learners"}


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
    }
    return {
        "statusCode": 200,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": ALLOWED_ORIGIN,        # CORS for the browser app
        },
        "body": json.dumps(body),
    }
