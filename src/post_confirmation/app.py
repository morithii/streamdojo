"""Cognito Post Confirmation trigger: every new self-signed-up user becomes a learner.

Creators and educators are added by an admin, so nobody can promote themselves.
"""
import logging

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

DEFAULT_GROUP = "learners"
_client = None


def cognito():
    """Create the boto3 client on first use (keeps unit tests free of AWS calls)."""
    global _client
    if _client is None:
        _client = boto3.client("cognito-idp")
    return _client


def handler(event, context):
    # This trigger also fires after a password reset; only act on a new sign-up
    if event.get("triggerSource") == "PostConfirmation_ConfirmSignUp":
        cognito().admin_add_user_to_group(
            UserPoolId=event["userPoolId"],
            Username=event["userName"],
            GroupName=DEFAULT_GROUP,
        )
        logger.info("Added new user to %s", DEFAULT_GROUP)   # no email in logs
    # Cognito triggers must return the event unchanged, or sign-up fails
    return event
