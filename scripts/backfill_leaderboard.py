"""One-off: build the educator leaderboard rows from what learners have already done.

From stage 3 on, the marking function keeps a row per learner per course up to date:
    TOPIC#<course> / POINTS#<learner>   dojoPoints, belt, lastGrading, gradings, lastActiveAt
Activity from BEFORE that deploy isn't in those rows yet. This script fills them in from:
    USER#<learner> / ATTEMPT#...         dojoPoints earned (stage 1 onwards) and when they were active
    USER#<learner> / BELT#<course>       the belt they hold
    USER#<learner> / GRADING#<course>#.. their gradings (stage 2 onwards)

Usage (dry run first - it prints what it would write and changes nothing):
    python scripts/backfill_leaderboard.py --profile streamdojo --table streamdojo-dev
    python scripts/backfill_leaderboard.py --profile streamdojo --table streamdojo-dev --write

Safe to run more than once: it SETs each row to the totals it counted (never adds on top).
Run it soon after deploying, so few new attempts land between the count and the write.
It uses a Scan - fine for a one-off admin job, never for an API request.
"""
import argparse
from collections import defaultdict

import boto3
from boto3.dynamodb.conditions import Attr


def scan_user_items(table):
    """Every attempt, belt and grading item (one paginated Scan, filtered to the USER# partitions)."""
    kwargs = {"FilterExpression": Attr("PK").begins_with("USER#") & (
        Attr("SK").begins_with("ATTEMPT#") | Attr("SK").begins_with("BELT#") | Attr("SK").begins_with("GRADING#"))}
    while True:
        page = table.scan(**kwargs)
        yield from page.get("Items", [])
        if "LastEvaluatedKey" not in page:
            return
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def build_rows(items):
    """{(course, learner): row} from the raw items. Pure function, so it's easy to test."""
    rows = defaultdict(lambda: {"dojoPoints": 0, "gradings": 0})
    for item in items:
        sub, sk = item["PK"].split("#", 1)[1], item["SK"]
        if sk.startswith("ATTEMPT#"):
            if not item.get("topicId"):
                continue
            row = rows[(item["topicId"], sub)]
            row["dojoPoints"] += int(item.get("dojoPoints", 0))                  # 0 for attempts before stage 1
            when = sk.rsplit("#", 1)[1]                                          # ATTEMPT#video#objective#<time>
            row["lastActiveAt"] = max(row.get("lastActiveAt", ""), when)
            row["learnerName"] = item.get("displayName") or row.get("learnerName")
        elif sk.startswith("BELT#") and item.get("belt"):
            rows[(sk.split("#", 1)[1], sub)]["belt"] = item["belt"]
        elif sk.startswith("GRADING#") and item.get("status") == "COMPLETE":
            row = rows[(item["topicId"], sub)]
            row["gradings"] += 1
            at = item.get("completedAt", "")
            if at >= row.get("lastGrading", {}).get("at", ""):
                row["lastGrading"] = {"score": int(item["score"]), "passed": bool(item["passed"]),
                                      "targetBelt": item["targetBelt"], "at": at}
            row["lastActiveAt"] = max(row.get("lastActiveAt", ""), at)
            row["learnerName"] = row.get("learnerName") or item.get("learnerName")
    return rows


def write_rows(table, rows):
    for (course, sub), row in rows.items():
        values = {":p": row["dojoPoints"], ":g": row["gradings"], ":u": sub, ":n": row.get("learnerName") or "Learner"}
        expression = "SET dojoPoints = :p, gradings = :g, learnerSub = :u, learnerName = :n"
        for field in ("belt", "lastGrading", "lastActiveAt"):
            if row.get(field):
                expression += f", {field} = :{field}"
                values[f":{field}"] = row[field]
        table.update_item(Key={"PK": f"TOPIC#{course}", "SK": f"POINTS#{sub}"},
                          UpdateExpression=expression, ExpressionAttributeValues=values)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--profile", default="streamdojo")
    parser.add_argument("--region", default="eu-west-2")
    parser.add_argument("--table", default="streamdojo-dev")
    parser.add_argument("--write", action="store_true", help="actually write (default: dry run)")
    args = parser.parse_args()

    table = boto3.Session(profile_name=args.profile, region_name=args.region).resource("dynamodb").Table(args.table)
    rows = build_rows(scan_user_items(table))
    for (course, sub), row in sorted(rows.items()):
        print(f"{course:20} {row.get('learnerName') or sub:20} {row['dojoPoints']:>5} pts  "
              f"{row.get('belt', 'WHITE'):7} gradings={row['gradings']}  last active {row.get('lastActiveAt', '-')}")
    if args.write:
        write_rows(table, rows)
        print(f"Wrote {len(rows)} leaderboard rows.")
    else:
        print(f"Dry run: {len(rows)} rows. Add --write to save them.")


if __name__ == "__main__":
    main()
