"""StreamDojo one-slide overview: the whole system in 9 boxes (the detail lives in diagram.py).

Re-generate:  python docs/architecture/overview.py   -> docs/architecture/streamdojo-overview.png
"""
from pathlib import Path

from diagrams import Cluster, Diagram, Edge
from diagrams.aws.database import Dynamodb
from diagrams.aws.integration import Eventbridge, StepFunctions
from diagrams.aws.ml import Bedrock
from diagrams.aws.network import APIGateway, CloudFront
from diagrams.aws.security import Cognito
from diagrams.aws.storage import S3
from diagrams.onprem.client import Users

OUT = Path(__file__).with_name("streamdojo-overview")

graph = {"fontsize": "28", "fontname": "Helvetica", "pad": "0.7", "nodesep": "0.9", "ranksep": "1.5",
         "splines": "spline", "labelloc": "t"}
node = {"fontsize": "14", "fontname": "Helvetica"}
box = {"fontsize": "17", "fontname": "Helvetica-Bold", "margin": "22"}
web = {"color": "#2ec4b6", "penwidth": "3"}      # browser traffic
pipe = {"color": "#e7871d", "penwidth": "3"}     # upload pipeline
ai = {"color": "#8a63d2", "penwidth": "3"}       # ClipSensei
biz = {"color": "#d6336c", "penwidth": "3"}      # business events -> bell
data = {"color": "#7a7a7a", "penwidth": "2"}     # reads / writes

with Diagram("StreamDojo - how it fits together", filename=str(OUT), show=False, direction="LR",
             graph_attr=graph, node_attr=node, outformat="png"):
    people = Users("Creators · Educators\nLearners")

    with Cluster("1. Front door", graph_attr={**box, "bgcolor": "#eef9f8"}):
        cognito = Cognito("Cognito\nsign in + roles")
        cdn = CloudFront("CloudFront\nwebsite · API · video\n(HTTPS)")

    with Cluster("2. Do things", graph_attr={**box, "bgcolor": "#fdf6ec"}):
        api = APIGateway("API Gateway\n+ 7 Lambda functions")
        pipeline = StepFunctions("Upload pipeline\nStep Functions:\nconvert · subtitles · translate")

    with Cluster("3. Think", graph_attr={**box, "bgcolor": "#f3effb"}):
        sensei = Bedrock("ClipSensei\nBedrock (Claude)\ndrafts + marks")

    with Cluster("4. Tell people", graph_attr={**box, "bgcolor": "#fdf0f4"}):
        bus = Eventbridge("EventBridge\nevents -> bell")

    with Cluster("5. Remember", graph_attr={**box, "bgcolor": "#f2f4f8"}):
        db = Dynamodb("DynamoDB\nlessons · progress\nnotifications")
        s3 = S3("S3 (private)\nvideos · subtitles\nwebsite")

    people >> Edge(label="sign in", **web) >> cognito
    people >> Edge(label="browse · watch · answer", **web) >> cdn
    cdn >> Edge(label="/api", **web) >> api
    cdn >> Edge(label="stream video", **web) >> s3
    people >> Edge(label="upload video\n(straight to S3)", **pipe) >> pipeline
    pipeline >> Edge(label="HLS + 5 languages", **pipe) >> s3
    pipeline >> Edge(label="draft questions", **ai) >> sensei
    api >> Edge(label="mark answers", **ai) >> sensei
    api >> Edge(label="read / write", **data) >> db
    api >> Edge(label="published · rejected\nquiz completed", **biz) >> bus
    pipeline >> Edge(label="draft ready", **biz) >> bus
    bus >> Edge(label="bell", **biz) >> db
