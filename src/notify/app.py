"""Notifier - turns StreamDojo business events into in-app bell notifications.

Triggered by EventBridge (custom bus streamdojo-<env>-events). Publishers don't know or care who
listens: they just announce what happened. This function decides WHO should hear about it.

  LessonDraftReady   -> every educator      "'AWS' is ready for review" (+ a warning if the safety check flagged it)
  LessonPublished    -> the lesson's creator "'AWS' was approved and published"
  LessonRejected     -> the lesson's creator "'AWS' was not approved: <reason>"
  PracticeCompleted  -> every educator      "Alex completed 'AWS': 2 of 3 ideas recalled"

Educators ALSO get an email (Amazon SES) for LessonDraftReady - the one event that needs them to act.
Email is optional: it's on only when the stack has a verified sender address (EMAIL_FROM).
It's sent only after that educator's bell item was newly written, so a duplicate delivery never
sends a second email. A failed email is logged, never retried: the bell notification is already there.

Each notification is one DynamoDB item:  PK USER#<sub>  SK NOTIF#<time>#<event id>
EventBridge delivers "at least once", so the event id is in the key and the write is conditional:
a duplicate delivery can never create a duplicate notification. Items expire after 30 days (TTL).
"""
import html
import json
import logging
import os
import time

import boto3
from botocore.exceptions import ClientError

TABLE_NAME = os.environ.get("TABLE_NAME", "")
USER_POOL_ID = os.environ.get("USER_POOL_ID", "")
EMAIL_FROM = os.environ.get("EMAIL_FROM", "")          # verified SES sender; empty = no emails
SITE_URL = os.environ.get("SITE_URL", "").rstrip("/")   # https://<cloudfront domain>, for links in emails
EMAIL_EVENTS = {"LessonDraftReady"}                     # only events an educator must act on
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


_emails = {}                                            # sub -> email, filled by educators() for this event


def educators():
    """Everyone in the Cognito 'educators' group (paginated). Also notes each one's email address."""
    subs, kwargs = [], {"UserPoolId": USER_POOL_ID, "GroupName": "educators"}
    while True:
        page = client("cognito-idp").list_users_in_group(**kwargs)
        for user in page.get("Users", []):
            attributes = {a["Name"]: a["Value"] for a in user.get("Attributes", [])}
            sub = attributes.get("sub")
            if sub and user.get("Enabled", True):
                subs.append(sub)
                if attributes.get("email"):
                    _emails[sub] = attributes["email"]
        if not page.get("NextToken"):
            return subs
        kwargs["NextToken"] = page["NextToken"]


def send_email(to, detail_type, d, message, link):
    """One email per educator (nobody sees anyone else's address). Best effort: failures are logged."""
    title = d.get("title") or "A lesson"
    flagged = d.get("moderationStatus") == "FLAGGED"
    subject = f"{'⚠ Check before publishing' if flagged else 'Ready for review'}: {title}"[:150]
    url = f"{SITE_URL}/{link}" if SITE_URL else ""
    text = f"{message}\n\nReview it here: {url}\n\n- ClipSensei, StreamDojo" if url else f"{message}\n\n- ClipSensei, StreamDojo"
    button = (f'<p><a href="{html.escape(url)}" style="background:#2ec4b6;color:#0b1a2b;padding:10px 18px;'
              f'border-radius:999px;text-decoration:none;font-weight:700">Review the lesson</a></p>') if url else ""
    body = (f'<div style="font-family:Arial,sans-serif;font-size:15px;color:#13283d;max-width:520px">'
            f'<p style="font-size:20px;font-weight:800">Stream<span style="color:#2ec4b6">Dojo</span></p>'
            f'<p>{html.escape(message)}</p>{button}'                      # titles are user text: always escaped
            f'<p style="color:#5b6b7b;font-size:13px">You get this because you are an educator on StreamDojo.</p></div>')
    try:
        client("sesv2").send_email(
            FromEmailAddress=f"StreamDojo <{EMAIL_FROM}>",
            Destination={"ToAddresses": [to]},
            Content={"Simple": {"Subject": {"Data": subject, "Charset": "UTF-8"},
                                "Body": {"Text": {"Data": text, "Charset": "UTF-8"},
                                         "Html": {"Data": body, "Charset": "UTF-8"}}}})
        return True
    except Exception as error:                                   # noqa: BLE001 - e.g. SES sandbox, unverified address
        logger.warning(json.dumps({"event": "email_failed", "type": detail_type, "error": str(error)[:300]}))
        return False


def describe(detail_type, d):
    """(recipients, message, link) for each kind of event. Unknown events notify nobody."""
    title = d.get("title") or "A lesson"
    if detail_type == "BeltAwarded":
        belt, course = str(d.get("belt", "")).title(), d.get("topicName") or "a course"
        learner = d.get("learnerName") or "A learner"
        return ([d.get("learnerSub")] + educators(), f"{learner} earned a {belt} belt in {course} ({d.get('score')}%).",
                f"#/course/{d.get('topicId', '')}")
    if detail_type == "LessonDeleted":
        # Only worth telling the creator when SOMEONE ELSE (an educator) removed their video
        if not d.get("byEducator") or not d.get("creatorSub"):
            return [], "", ""
        reason = f" Reason: {d['reason']}" if d.get("reason") else ""
        return [d["creatorSub"]], f"An educator removed “{title}”.{reason}", "#/create"
    if detail_type == "LessonDraftReady":
        message = f"“{title}” is ready for review - ClipSensei drafted {d.get('objectiveCount', 0)} question(s)."
        if d.get("moderationStatus") == "FLAGGED":
            message = f"⚠ {message} The safety check flagged {d.get('flagCount', 0)} moment(s) - watch them before publishing."
        elif d.get("moderationStatus") == "UNCHECKED":
            message += " The safety check couldn't run - please watch it all before publishing."
        return educators(), message, f"#/teach/review/{d['videoId']}"
    if detail_type == "LessonPublished":
        return [d["creatorSub"]], f"“{title}” was approved and published in {d.get('topicName', 'a course')}.", "#/create"
    if detail_type == "LessonRejected":
        return [d["creatorSub"]], f"“{title}” was not approved: {d.get('reason', '')}", "#/create"
    if detail_type == "PracticeCompleted":
        return educators(), (f"{d.get('learnerName', 'A learner')} completed “{title}”: "
                             f"{d.get('recalled', 0)} of {d.get('total', 0)} ideas recalled unaided."), f"#/teach/insights/{d['videoId']}"
    return [], "", ""


def personal(detail_type, d, sub, message):
    """The same event, worded for the person it's about: 'You earned...' instead of 'Sam earned...'."""
    if detail_type == "BeltAwarded" and sub == d.get("learnerSub"):
        return (f"🥋 You earned your {str(d.get('belt', '')).title()} belt in {d.get('topicName') or 'your course'} "
                f"({d.get('score')}%). ClipSensei bows.")
    return message


def handler(event, context):
    detail_type, detail = event["detail-type"], event.get("detail", {})
    _emails.clear()
    recipients, message, link = describe(detail_type, detail)
    created = event.get("time")                                  # when it happened, per EventBridge
    written, emailed = 0, 0
    for sub in dict.fromkeys(r for r in recipients if r):        # de-duplicate, keep order
        text = personal(detail_type, detail, sub, message)
        try:
            table().put_item(
                Item={"PK": f"USER#{sub}", "SK": f"NOTIF#{created}#{event['id']}", "type": detail_type,
                      "message": text[:300], "link": link, "read": False, "createdAt": created,
                      "expiresAt": int(time.time()) + KEEP_DAYS * 86400},        # DynamoDB TTL deletes it later
                ConditionExpression="attribute_not_exists(PK)")
            written += 1
            # Email only on the FIRST delivery (this write just succeeded), so retries never double-send
            if EMAIL_FROM and detail_type in EMAIL_EVENTS and sub in _emails:
                emailed += int(send_email(_emails[sub], detail_type, detail, text, link))
        except ClientError as error:
            if error.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise                                            # a real error: let EventBridge retry
    logger.info(json.dumps({"event": detail_type, "recipients": len(recipients), "written": written, "emailed": emailed}))
    return {"written": written, "emailed": emailed}
