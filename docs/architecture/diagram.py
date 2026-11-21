"""StreamDojo architecture diagram, drawn as code with the official AWS icons.

Re-generate after changing the architecture:
    pip install diagrams          # also needs Graphviz:  brew install graphviz
    python docs/architecture/diagram.py
Writes docs/architecture/streamdojo-architecture.png (the system)
and docs/architecture/streamdojo-ship-and-operate.png (CI/CD and operations)
"""
from pathlib import Path

from diagrams import Cluster, Diagram, Edge
from diagrams.aws.compute import Lambda
from diagrams.aws.cost import Budgets
from diagrams.aws.database import Dynamodb
from diagrams.aws.integration import Eventbridge, SimpleNotificationServiceSns, SQS, StepFunctions
from diagrams.aws.management import Cloudformation, Cloudwatch, CloudwatchAlarm
from diagrams.aws.media import ElementalMediaconvert
from diagrams.aws.ml import Bedrock, Transcribe, Translate
from diagrams.aws.network import APIGateway, CloudFront
from diagrams.aws.security import Cognito, IAMRole, KMS
from diagrams.aws.storage import S3
from diagrams.onprem.ci import GithubActions
from diagrams.onprem.client import Users

OUT = Path(__file__).with_name("streamdojo-architecture")
OPS_OUT = Path(__file__).with_name("streamdojo-ship-and-operate")

graph = {"fontsize": "26", "fontname": "Helvetica", "pad": "0.6", "nodesep": "0.45", "ranksep": "1.3", "splines": "spline"}
node = {"fontsize": "13", "fontname": "Helvetica"}
cluster_style = {"fontsize": "16", "fontname": "Helvetica-Bold", "margin": "18"}
web = {"color": "#2ec4b6", "penwidth": "2"}           # browser traffic
pipe = {"color": "#e7871d", "penwidth": "2"}          # upload pipeline
ai = {"color": "#8a63d2", "penwidth": "2"}            # ClipSensei / Bedrock
ops = {"color": "#888888", "style": "dashed"}         # operations
biz = {"color": "#d6336c", "penwidth": "2"}           # business events -> notification bell

with Diagram("StreamDojo - serverless video learning with ClipSensei (eu-west-2)\n"
             "teal = browser traffic   orange = upload pipeline   purple = ClipSensei (Bedrock)   "
             "pink = business events -> bell   grey = data   dashed = failures / secrets", filename=str(OUT), show=False,
             direction="TB", graph_attr=graph, node_attr=node, outformat="png"):
    people = Users("Creators · Educators\nLearners")

    with Cluster("Edge & identity", graph_attr={**cluster_style, "bgcolor": "#eefaf8"}):
        cdn = CloudFront("CloudFront\n/  /api  /media\n(OAC, HTTPS)")
        cognito = Cognito("Cognito\nhosted sign-in (PKCE)\ngroups = roles")

    with Cluster("Storage (private, encrypted)", graph_attr={**cluster_style, "bgcolor": "#f4f8fd"}):
        site = S3("Website bucket\nHTML · CSS · JS")
        uploads = S3("Uploads bucket\nraw videos")
        media = S3("Media bucket\nHLS · thumbnails\nsubtitles ×5")

    with Cluster("API (REST, Cognito authorizer)", graph_attr={**cluster_style, "bgcolor": "#fff8ef"}):
        api = APIGateway("API Gateway")
        with Cluster("Lambda (Python 3.12, arm64)", graph_attr=cluster_style):
            videos = Lambda("videos\npresigned upload")
            catalogue = Lambda("catalogue\ncourses")
            learn = Lambda("learn\npractice · reviews")
            marking = Lambda("marking\nClipSensei marks")
            educator = Lambda("educator\nreview · publish")
            insights = Lambda("insights\nwhat's sticking")
            notifications = Lambda("notifications\nbell · mark read")

    with Cluster("Data (DynamoDB, on-demand, PITR)", graph_attr={**cluster_style, "bgcolor": "#f6f2fd"}):
        table = Dynamodb("App table\nlessons · schedules\nattempts · stats")
        keys = Dynamodb("Answer keys\n(separate table,\nIAM-isolated)")

    bedrock = Bedrock("Amazon Bedrock\nClaude Haiku 4.5\n= ClipSensei")

    with Cluster("Business events -> in-app bell (event-driven)", graph_attr={**cluster_style, "bgcolor": "#fdf0f4"}):
        bus = Eventbridge("Custom event bus\nstreamdojo-<env>-events\nDraftReady · Published\nRejected · LessonCompleted")
        notify = Lambda("notify\nwho gets told?\n(idempotent, 30-day TTL)")
        notify_dlq = SQS("dead-letter\nqueue")

    with Cluster("Upload pipeline (event-driven)", graph_attr={**cluster_style, "bgcolor": "#fff4ea"}):
        events = Eventbridge("EventBridge (default bus)\nS3 Object Created")
        dlq = SQS("dead-letter\nqueue")
        flow = StepFunctions("Step Functions\nprocess-video")
        convert = ElementalMediaconvert("MediaConvert\nHLS 360/720/1080p")
        transcribe = Transcribe("Transcribe\nEnglish")
        translate = Translate("Translate ×4\nFR · ES · IT · EL\n(parallel Map)")
        generate = Lambda("generate objectives\n(draft questions)")

    # Browser traffic
    people >> Edge(label="sign in", **web) >> cognito
    people >> Edge(label="HTTPS", **web) >> cdn
    cdn >> Edge(**web) >> site
    cdn >> Edge(label="/api", **web) >> api
    cdn >> Edge(label="/media (OAC)", **web) >> media
    api >> Edge(**web) >> [videos, catalogue, learn, marking, educator, insights, notifications]
    cognito >> Edge(style="dotted", color="#2ec4b6", label="JWT checked", constraint="false") >> api

    # Data and ClipSensei
    [videos, catalogue, learn, educator, insights, notifications] >> Edge(color="#7a7a7a") >> table
    marking >> Edge(color="#7a7a7a") >> table
    [marking, educator, insights] >> Edge(color="#7a7a7a", style="dashed") >> keys
    [marking, insights, generate] >> Edge(**ai) >> bedrock

    # Upload pipeline
    people >> Edge(label="presigned POST\n(≤ 2 GB video)", **pipe) >> uploads
    uploads >> Edge(**pipe) >> events >> Edge(**pipe) >> flow
    events >> Edge(style="dashed", color="#e7871d") >> dlq
    flow >> Edge(**pipe) >> [convert, transcribe, translate, generate]
    convert >> Edge(**pipe) >> media
    translate >> Edge(**pipe) >> media
    generate >> Edge(**pipe) >> [table, keys]

    # Business events: publishers never wait on the bell (best-effort put_events)
    generate >> Edge(label="LessonDraftReady", **biz) >> bus
    educator >> Edge(label="Published / Rejected\n(+ reason)", **biz) >> bus
    marking >> Edge(label="LessonCompleted", **biz) >> bus
    bus >> Edge(label="rule", **biz) >> notify
    bus >> Edge(style="dashed", color="#d6336c", label="delivery failed") >> notify_dlq
    notify >> Edge(style="dotted", color="#d6336c", label="who is in\neducators group?", constraint="false") >> cognito
    notify >> Edge(label="NOTIF items\nper user", **biz) >> table


with Diagram("StreamDojo - how it ships and how it is watched", filename=str(OPS_OUT), show=False,
             direction="LR", graph_attr=graph, node_attr=node, outformat="png"):
    dev = Users("Developer\ngit push main")
    with Cluster("CI/CD - GitHub Actions", graph_attr={**cluster_style, "bgcolor": "#f4f8fd"}):
        actions = GithubActions("checks job\npytest · cfn-lint")
        role = IAMRole("OIDC deploy role\n(no stored keys,\nmain branch only)")
    with Cluster("AWS account (eu-west-2)", graph_attr={**cluster_style, "bgcolor": "#fff8ef"}):
        stack = Cloudformation("SAM / CloudFormation\n(execution role)")
        site = S3("website bucket\n+ CloudFront\ninvalidation")
        with Cluster("Operations", graph_attr={**cluster_style, "bgcolor": "#f5f5f5"}):
            dashboard = Cloudwatch("CloudWatch\ndashboard · logs · X-Ray")
            alarms = CloudwatchAlarm("5 alarms\npipeline · DLQ · API 5xx\nLambda errors · Bedrock")
            kms = KMS("KMS key\n(rotated)")
            sns = SimpleNotificationServiceSns("SNS alerts")
            budget = Budgets("Budget $25 / month\n80% actual · 100% forecast")
    inbox = Users("Your email")

    dev >> Edge(color="#2ec4b6", penwidth="2") >> actions >> Edge(label="OIDC token", color="#2ec4b6", penwidth="2") >> role
    role >> Edge(label="sam deploy", color="#2ec4b6", penwidth="2") >> stack
    role >> Edge(label="sync + invalidate", color="#2ec4b6", penwidth="2") >> site
    dashboard >> Edge(**ops) >> alarms >> Edge(color="#d62728", penwidth="2") >> sns
    kms >> Edge(**ops, label="encrypts") >> sns
    sns >> Edge(color="#d62728", penwidth="2", label="alarm") >> inbox
    budget >> Edge(color="#d62728", penwidth="2", label="cost") >> inbox
