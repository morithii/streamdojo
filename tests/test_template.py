"""Infrastructure tests: check template.yaml keeps the rules we agreed on."""
from pathlib import Path

import yaml

TEMPLATE = Path(__file__).resolve().parent.parent / "template.yaml"


class CfnLoader(yaml.SafeLoader):
    """YAML loader that understands CloudFormation short tags like !Ref and !Sub."""


def _cfn_tag(loader, tag_suffix, node):
    # Turn "!Ref X" into {"Ref": "X"}, "!Sub ..." into {"Fn::Sub": ...}, etc.
    name = "Ref" if tag_suffix == "Ref" else f"Fn::{tag_suffix}"
    if isinstance(node, yaml.ScalarNode):
        return {name: loader.construct_scalar(node)}
    if isinstance(node, yaml.SequenceNode):
        return {name: loader.construct_sequence(node, deep=True)}
    return {name: loader.construct_mapping(node, deep=True)}


CfnLoader.add_multi_constructor("!", _cfn_tag)


def load_template():
    return yaml.load(TEMPLATE.read_text(), Loader=CfnLoader)


def test_functions_default_to_arm64_python312_and_tracing():
    fn = load_template()["Globals"]["Function"]
    assert fn["Runtime"] == "python3.12"
    assert fn["Architectures"] == ["arm64"]
    assert fn["Tracing"] == "Active"


def test_alerts_topic_is_encrypted():
    topic = load_template()["Resources"]["AlertsTopic"]["Properties"]
    assert "KmsMasterKeyId" in topic


def test_alert_email_is_not_hardcoded():
    # The email must come in as a parameter (from a secret), never be committed
    params = load_template()["Parameters"]
    assert params["AlertEmail"]["Default"] == ""


def test_api_requires_cognito_by_default():
    auth = load_template()["Resources"]["StreamDojoApi"]["Properties"]["Auth"]
    assert auth["DefaultAuthorizer"] == "CognitoAuthorizer"


def test_web_client_has_no_secret_and_uses_code_flow():
    client = load_template()["Resources"]["UserPoolClient"]["Properties"]
    assert client["GenerateSecret"] is False
    assert client["AllowedOAuthFlows"] == ["code"]


def test_three_groups_exist():
    resources = load_template()["Resources"]
    names = {r["Properties"]["GroupName"] for r in resources.values() if r["Type"] == "AWS::Cognito::UserPoolGroup"}
    assert names == {"creators", "educators", "learners"}



def test_upload_bucket_is_private_encrypted_and_https_only():
    resources = load_template()["Resources"]
    bucket = resources["UploadBucket"]["Properties"]
    assert all(bucket["PublicAccessBlockConfiguration"].values())
    assert "BucketEncryption" in bucket
    statement = resources["UploadBucketPolicy"]["Properties"]["PolicyDocument"]["Statement"][0]
    assert statement["Effect"] == "Deny" and statement["Condition"]["Bool"]["aws:SecureTransport"] == "false"


def test_tables_have_point_in_time_recovery():
    resources = load_template()["Resources"]
    for name in ("AppTable", "AnswerKeysTable"):
        assert resources[name]["Properties"]["PointInTimeRecoverySpecification"]["PointInTimeRecoveryEnabled"]



def test_upload_event_rule_only_watches_raw_prefix_and_has_dead_letter_queue():
    event = load_template()["Resources"]["ProcessVideoStateMachine"]["Properties"]["Events"]["VideoUploaded"]["Properties"]
    assert event["Pattern"]["detail"]["object"]["key"] == [{"prefix": "raw/"}]
    assert event["DeadLetterConfig"]["Type"] == "SQS"


def test_processed_bucket_is_private():
    bucket = load_template()["Resources"]["ProcessedBucket"]["Properties"]
    assert all(bucket["PublicAccessBlockConfiguration"].values())



def test_caption_functions_exist_with_least_privilege_translate():
    resources = load_template()["Resources"]
    translate = resources["TranslateSubtitlesFunction"]["Properties"]
    actions = [a for s in translate["Policies"][0]["Statement"] for a in ([s["Action"]] if isinstance(s["Action"], str) else s["Action"])]
    assert "translate:TranslateText" in actions
    assert "dynamodb:Scan" not in actions
    assert "PrepareTranscriptFunction" in resources



def test_bedrock_access_is_limited_to_the_chosen_model():
    resources = load_template()["Resources"]
    statements = resources["GenerateObjectivesFunction"]["Properties"]["Policies"][0]["Statement"]
    bedrock = [s for s in statements if s["Sid"] == "InvokeClipSenseiModel"][0]
    assert bedrock["Action"] == "bedrock:InvokeModel"
    assert "*" != bedrock["Resource"]
    assert "GenerationModelId" in load_template()["Parameters"]


def test_learner_facing_code_cannot_read_answer_keys():
    # Only these back-office functions (and marking, read-only) reference the answer-keys table
    resources = load_template()["Resources"]
    readers = [name for name, r in resources.items()
               if r["Type"] == "AWS::Serverless::Function" and "AnswerKeysTable" in str(r["Properties"].get("Policies", ""))]
    assert sorted(readers) == ["EducatorFunction", "GenerateObjectivesFunction", "InsightsFunction", "MarkingFunction"]



def test_learn_api_has_no_answer_key_access_but_marking_reads_only():
    resources = load_template()["Resources"]
    assert "AnswerKeysTable" not in str(resources["LearnFunction"]["Properties"]["Policies"])
    marking = resources["MarkingFunction"]["Properties"]["Policies"][0]["Statement"]
    keys = [s for s in marking if s["Sid"] == "ReadAnswerKeysOnly"][0]
    assert keys["Action"] == "dynamodb:GetItem"

def test_insights_is_read_only():
    statements = load_template()["Resources"]["InsightsFunction"]["Properties"]["Policies"][0]["Statement"]
    dynamo = [s["Action"] for s in statements if "dynamodb" in str(s["Action"])]
    assert dynamo == ["dynamodb:Query", "dynamodb:Query"]       # no Put/Update/Delete anywhere

def test_site_and_media_are_private_and_served_through_cloudfront_oac():
    resources = load_template()["Resources"]
    assert all(resources["SiteBucket"]["Properties"]["PublicAccessBlockConfiguration"].values())
    origins = {o["Id"]: o for o in resources["SiteDistribution"]["Properties"]["DistributionConfig"]["Origins"]}
    assert "OriginAccessControlId" in origins["site"] and "OriginAccessControlId" in origins["media"]
    assert origins["api"]["CustomOriginConfig"]["OriginProtocolPolicy"] == "https-only"


def test_cloudfront_can_only_read_player_files_from_the_media_bucket():
    statements = load_template()["Resources"]["ProcessedBucketPolicy"]["Properties"]["PolicyDocument"]["Statement"]
    allow = [s for s in statements if s["Sid"] == "AllowCloudFrontPlayerFilesOnly"][0]
    resources = str(allow["Resource"])
    assert "/hls/" in resources and "thumb" in resources and "subtitles." in resources
    assert "audio" not in resources and "clean.json" not in resources and "/transcript/*" not in resources
    assert "AWS:SourceArn" in allow["Condition"]["StringEquals"]


def test_api_responses_are_never_cached_by_cloudfront():
    behaviours = load_template()["Resources"]["SiteDistribution"]["Properties"]["DistributionConfig"]["CacheBehaviors"]
    api = [b for b in behaviours if b["PathPattern"] == "/api/*"][0]
    assert api["CachePolicyId"] == "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"     # Managed-CachingDisabled

def test_browser_uploads_allowed_only_from_our_site():
    cors = load_template()["Resources"]["UploadBucket"]["Properties"]["CorsConfiguration"]["CorsRules"][0]
    assert cors["AllowedMethods"] == ["POST"] and len(cors["AllowedOrigins"]) == 2
    assert "SiteDistribution.DomainName" in str(cors["AllowedOrigins"][1])
    assert "*" not in str(cors["AllowedOrigins"])                     # never "any website"

def test_every_alarm_emails_the_alerts_topic():
    resources = load_template()["Resources"]
    alarms = {name: r for name, r in resources.items() if r["Type"] == "AWS::CloudWatch::Alarm"}
    assert len(alarms) >= 5
    for name, alarm in alarms.items():
        assert "AlertsTopic" in str(alarm["Properties"]["AlarmActions"]), name
        assert "SEARCH(" not in str(alarm["Properties"]), name          # alarms can't use SEARCH() - deploy would fail


def test_alerts_topic_uses_a_key_cloudwatch_can_use():
    resources = load_template()["Resources"]
    assert "alias/aws/sns" not in str(resources["AlertsTopic"]["Properties"])       # AWS-managed key blocks CloudWatch
    statements = resources["AlertsKey"]["Properties"]["KeyPolicy"]["Statement"]
    assert any(s.get("Principal", {}).get("Service") == "cloudwatch.amazonaws.com" for s in statements)
    assert resources["AlertsKey"]["Properties"]["EnableKeyRotation"] is True


def test_budget_warns_before_the_limit():
    budget = load_template()["Resources"]["MonthlyBudget"]["Properties"]
    notes = {(n["Notification"]["NotificationType"], n["Notification"]["Threshold"]) for n in budget["NotificationsWithSubscribers"]}
    assert notes == {("ACTUAL", 80), ("FORECASTED", 100)}


def test_dashboard_body_is_valid_json():
    import json
    import re
    body = load_template()["Resources"]["OperationsDashboard"]["Properties"]["DashboardBody"]["Fn::Sub"]
    assert len(json.loads(re.sub(r"\$\{[^}]+\}", "X", body))["widgets"]) >= 10


def test_only_the_three_publishers_can_announce_events_and_only_on_our_bus():
    resources = load_template()["Resources"]
    publishers = sorted(n for n, r in resources.items() if "events:PutEvents" in str(r.get("Properties", {}).get("Policies", "")))
    assert publishers == ["EducatorFunction", "GenerateObjectivesFunction", "MarkingFunction"]
    for name in publishers:
        statement = [s for s in resources[name]["Properties"]["Policies"][0]["Statement"] if s["Sid"] == "AnnounceBusinessEvents"][0]
        assert "EventBus" in str(statement["Resource"])                      # never "*"


def test_notifier_can_only_write_user_notification_items():
    statements = load_template()["Resources"]["NotifyFunction"]["Properties"]["Policies"][0]["Statement"]
    write = [s for s in statements if s["Sid"] == "WriteNotificationsOnly"][0]
    assert write["Action"] == "dynamodb:PutItem"
    assert write["Condition"]["ForAllValues:StringLike"]["dynamodb:LeadingKeys"] == ["USER#*"]
