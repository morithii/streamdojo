# StreamDojo data model

StreamDojo stores its data in **two DynamoDB tables**, both on-demand capacity with point-in-time recovery:

| Table | What it holds | Who can read it |
|---|---|---|
| `StreamDojo` | Everything: lessons, recall prompts, attempts, review schedules, belts, topics | All app functions (each limited to what it needs) |
| `StreamDojoAnswerKeys` | Essential points, model answers and hints for each learning objective | **Only** the educator, feedback and grading functions |

Large content never goes in DynamoDB. Videos, transcripts and subtitles live in S3, and items store their S3 paths.

## Why the answer keys get their own table

DynamoDB IAM conditions can limit a function to certain **partition keys**, but not to certain sort keys. If answer keys sat in the same partition as the public prompts, the learner-facing functions would need read access to that partition, and only our code would stop them returning answers. With a separate table, **IAM enforces it**: learner functions have no permission on `StreamDojoAnswerKeys` at all, so even a bug can't leak an answer.

## Keys and indexes

Single-table design: every item has a generic partition key `PK` and sort key `SK`, and the **prefix** says what the item is (`VIDEO#`, `USER#`...). Related items share a `PK`, so one query fetches them together.

| Index | Partition key | Sort key | Used for |
|---|---|---|---|
| Table | `PK` | `SK` | Reading an item and its related items |
| `GSI1` (sparse) | `GSI1PK` = `DRAFTS` or `CATALOGUE` | `GSI1SK` = timestamp`#videoId` | Lessons waiting for educator review; then the Netflix-style catalogue, newest first |
| `GSI2` (overloaded) | `GSI2PK` = `CREATOR#sub` or `REVIEWQUEUE` | `GSI2SK` = timestamp | A creator's uploads; answers flagged for the educator |
| `GSI3` (sparse) | `GSI3PK` = `DUE#learnerSub` | `GSI3SK` = `nextReviewAt` | A learner's reviews that are due |

- **Sparse index:** an item only appears in a GSI if it has that GSI's key attributes. A lesson gets `GSI1PK = DRAFTS` when ClipSensei's draft objectives are ready, and publishing **changes it** to `CATALOGUE`, so a lesson is in exactly one list at a time and drafts can never show up in the catalogue. Only flagged attempts get `GSI2PK = REVIEWQUEUE`.
- **Overloaded index:** one GSI serves several kinds of item, told apart by the key prefix. That keeps us at 3 GSIs instead of 5.
- **All timestamps are ISO 8601 in UTC** (`2026-11-20T09:15:00Z`), which sort correctly as text.

## Entities

### `StreamDojo` table

| Entity | PK | SK | Key attributes | GSI keys |
|---|---|---|---|---|
| Lesson (video) | `VIDEO#videoId` | `META` | `title`, `creatorSub`, `createdAt`, `processingStatus`, `captionStatus`, `objectivesStatus`, `topicId`, `durationSec`, `hlsPath`, `thumbnailPath`, `subtitleLanguages`, `inputTokens`, `outputTokens`, `publishedBy`, `publishedAt` | GSI2: `CREATOR#creatorSub` / `createdAt`. GSI1: `DRAFTS` / `createdAt#videoId` while awaiting review, then `CATALOGUE` / `publishedAt#videoId` once published |
| Learning objective (public) | `VIDEO#videoId` | `OBJ#objectiveId` | `objective`, `prompts` (3 versions), `timestamp`, `topicId`, `status` (`DRAFT` / `PUBLISHED`) | none |
| Recall stats (per objective) | `VIDEO#videoId` | `STATS#objectiveId` | `firstAttempts`, `firstUnaided`, `reviewAttempts`, `reviewUnaided`, `hintsUsed`, `dontRemember`, `missedPointCounts` (map) | none |
| Review schedule (per learner, per objective) | `VIDEO#videoId` | `LEARNER#learnerSub#OBJ#objectiveId` | `learnerSub`, `displayName`, `topicId`, `intervalDays`, `nextReviewAt`, `lastPromptIndex`, `lastResult`, `longestPassedIntervalDays` | GSI3: `DUE#learnerSub` / `nextReviewAt` |
| Attempt | `USER#learnerSub` | `ATTEMPT#objectiveId#timestamp` | `videoId`, `topicId`, `kind` (`PRACTICE` / `REVIEW` / `GRADING`), `promptIndex`, `answer`, `pointResults`, `firstUnaided`, `usedHint`, `dontRemember`, `needsReview`, `overriddenBy` | GSI2 only if flagged: `REVIEWQUEUE` / `timestamp` |
| Learner profile | `USER#learnerSub` | `PROFILE` | `displayName` (no email: Cognito keeps that) | none |
| Grading | `USER#learnerSub` | `GRADING#topicId#timestamp` | `percentRecalled`, `belt`, `verdict`, `objectiveResults` | none |
| Current belt | `USER#learnerSub` | `BELT#topicId` | `belt`, `percentRecalled`, `gradedAt` | none |
| Topic | `TOPIC#topicId` | `META` | `name` | none |
| Lesson in a topic | `TOPIC#topicId` | `LESSON#videoId` | `title`, `publishedAt` | none |

### `StreamDojoAnswerKeys` table

| Entity | PK | SK | Key attributes |
|---|---|---|---|
| Answer key | `VIDEO#videoId` | `OBJ#objectiveId` | `essentialPoints` (list of `{pointId, text}`), `modelAnswer`, `hint`, `status` |

## Access patterns

Every read is a `GetItem` or a `Query` on a key. **No scans.**

| # | Access pattern | Who | Operation |
|---|---|---|---|
| 1 | Get a lesson | everyone | `GetItem` PK=`VIDEO#id`, SK=`META` |
| 2 | List my uploads, newest first | creator | `Query` GSI2: `CREATOR#sub`, descending |
| 3 | Move a lesson UPLOADING → PROCESSING only once | workflow | `UpdateItem` with condition `processingStatus = UPLOADING` |
| 4 | Browse the published catalogue, newest first | everyone | `Query` GSI1: `CATALOGUE`, descending, paginated |
| 5 | Get a lesson's public objectives and prompts | learner, educator | `Query` PK=`VIDEO#id`, SK begins_with `OBJ#` |
| 6 | Get a lesson's answer keys | educator, feedback, grading | `Query` AnswerKeys PK=`VIDEO#id` |
| 7 | Get one answer key | feedback function | `GetItem` AnswerKeys PK=`VIDEO#id`, SK=`OBJ#objectiveId` |
| 8 | List lessons waiting for review, oldest first | educator | `Query` GSI1: `DRAFTS`, ascending |
| 9 | Save an attempt | learner | `PutItem` PK=`USER#sub`, SK=`ATTEMPT#objectiveId#timestamp` |
| 10 | My attempts on one objective | learner, scheduler | `Query` PK=`USER#sub`, SK begins_with `ATTEMPT#objectiveId#` |
| 11 | My reviews due now | learner | `Query` GSI3: `DUE#sub`, `GSI3SK <= now` |
| 12 | Update my review schedule | scheduler | `UpdateItem` PK=`VIDEO#id`, SK=`LEARNER#sub#OBJ#objectiveId` |
| 13 | Recall stats for a lesson | educator | `Query` PK=`VIDEO#id`, SK begins_with `STATS#` |
| 14 | Every learner's progress on a lesson | educator | `Query` PK=`VIDEO#id`, SK begins_with `LEARNER#` |
| 15 | Answers flagged for checking | educator | `Query` GSI2: `REVIEWQUEUE`, descending |
| 16 | Lessons in a topic | grading, catalogue | `Query` PK=`TOPIC#id`, SK begins_with `LESSON#` |
| 17 | My belt in a topic | learner, educator | `GetItem` PK=`USER#sub`, SK=`BELT#topicId` |
| 18 | My grading history in a topic | learner | `Query` PK=`USER#sub`, SK begins_with `GRADING#topicId#` |

**Recall stats are counted when an answer is saved** (an atomic `ADD` on the `STATS#` item), not calculated when the educator opens the dashboard. Insights then cost one small query, however many learners there are.

## Status lifecycles

Each lesson has **three separate statuses**, so a failure in one part never marks the whole lesson as failed. If translation breaks, the video still plays with English subtitles.

```
processingStatus:  UPLOADING ─▶ PROCESSING ─▶ READY
                                     └─▶ FAILED

captionStatus:     PENDING ─▶ TRANSCRIBED ─▶ READY      (all 4 translations worked)
                                   │      └─▶ PARTIAL    (some translations failed)
                                   └─▶ FAILED            (transcription failed)

objectivesStatus:  PENDING ─▶ DRAFT_READY ─▶ PUBLISHED
                       └─▶ FAILED
```

A lesson appears in the catalogue only when `processingStatus = READY` **and** it has been published.

## Example items

A published lesson:

```json
{
  "PK": "VIDEO#9f2c1e4a",
  "SK": "META",
  "title": "CloudFront and private S3 buckets",
  "creatorSub": "c1a2-creator",
  "createdAt": "2026-11-20T09:00:00Z",
  "processingStatus": "READY",
  "captionStatus": "READY",
  "objectivesStatus": "PUBLISHED",
  "topicId": "cloudfront-s3",
  "durationSec": 612,
  "hlsPath": "processed/9f2c1e4a/index.m3u8",
  "thumbnailPath": "processed/9f2c1e4a/thumb.jpg",
  "subtitleLanguages": ["en", "fr", "es", "it", "el"],
  "publishedAt": "2026-11-20T10:30:00Z",
  "GSI1PK": "CATALOGUE",
  "GSI1SK": "2026-11-20T10:30:00Z#9f2c1e4a",
  "GSI2PK": "CREATOR#c1a2-creator",
  "GSI2SK": "2026-11-20T09:00:00Z"
}
```

A public objective (what learners can see) and its answer key (in the other table):

```json
{
  "PK": "VIDEO#9f2c1e4a",
  "SK": "OBJ#oac",
  "objective": "Explain why Origin Access Control keeps an S3 origin private",
  "prompts": [
    "Why use Origin Access Control when serving files from a private S3 bucket through CloudFront?",
    "A user opens the direct S3 URL for a file served by CloudFront. What should happen, and why?",
    "Explain to a colleague how CloudFront can read a bucket that the public can't."
  ],
  "timestamp": "04:12",
  "topicId": "cloudfront-s3",
  "status": "PUBLISHED"
}
```

```json
{
  "PK": "VIDEO#9f2c1e4a",
  "SK": "OBJ#oac",
  "essentialPoints": [
    {"pointId": "p1", "text": "The bucket stays private and blocks public access"},
    {"pointId": "p2", "text": "CloudFront signs its requests to S3"},
    {"pointId": "p3", "text": "The bucket policy only allows that CloudFront distribution to read objects"}
  ],
  "modelAnswer": "OAC lets CloudFront sign its requests to S3, and the bucket policy only allows that distribution to read, so the bucket can stay fully private while CloudFront serves the files.",
  "hint": "Think about how S3 knows a request really came from your CloudFront distribution.",
  "status": "PUBLISHED"
}
```

A learner's review schedule for that objective:

```json
{
  "PK": "VIDEO#9f2c1e4a",
  "SK": "LEARNER#l9b8-learner#OBJ#oac",
  "learnerSub": "l9b8-learner",
  "displayName": "Sam",
  "topicId": "cloudfront-s3",
  "intervalDays": 3,
  "nextReviewAt": "2026-11-23T09:00:00Z",
  "lastPromptIndex": 1,
  "lastResult": "UNAIDED_FULL",
  "longestPassedIntervalDays": 1,
  "GSI3PK": "DUE#l9b8-learner",
  "GSI3SK": "2026-11-23T09:00:00Z"
}
```

## Known trade-offs

- **`CATALOGUE` and `DRAFTS` are single GSI partitions.** That's fine at demo scale and for thousands of lessons. At very large scale we'd shard it (`CATALOGUE#0`–`CATALOGUE#9`) or split it by topic.
- **Each lesson's partition holds every learner's review schedule.** That's fine for course-sized groups. A lesson with hundreds of thousands of learners would need the schedules moved into their own partitions.