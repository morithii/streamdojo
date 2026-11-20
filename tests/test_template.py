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
