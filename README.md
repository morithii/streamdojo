# StreamDojo

**Watch a lesson. Practise with ClipSensei. See what stuck.**

StreamDojo is a Netflix-style video learning platform on AWS. Its AI teaching assistant, **ClipSensei**, writes the practice questions, marks the answers and helps learners remember what they watch.

StreamDojo asks learners to explain an idea from memory before showing the answer. ClipSensei gives feedback against the lesson's rubric, then brings that idea back after a delay to see what the learner can still recall without help, and shows the educator which ideas aren't sticking.

> Specialisation: **Serverless GenAI** (AWS Lambda, Step Functions, Amazon Bedrock)

## The problem

**For educators:** Writing quizzes and marking answers takes hours. That's time not spent on the thing that matters most: finding out where each learner is strong and where they're struggling. StreamDojo writes the practice questions and marks the answers against the educator's own rubric. That frees educators to focus on their learners.

**For learners:** Watching a lesson feels like learning, but most of it fades within days. StreamDojo is built on two of the best-proven techniques in learning science:

- **Active recall:** learners explain each idea from memory before seeing the answer, and that effort is what makes knowledge stick.
- **Spaced repetition:** ClipSensei brings each idea back at growing intervals, just as it's starting to fade, so it moves into long-term memory.

After every attempt, ClipSensei shows what the learner recalled, what they missed, and the exact moment in the video to rewatch. It's a teaching assistant that's there whenever they practise, helping them build skills on their own.

## Users
| Role | What they do |
|---|---|
| Creator | Uploads lesson videos |
| Educator | Reviews and publishes ClipSensei's learning objectives; checks flagged marks; reads recall insights |
| Learner | Watches, answers recall prompts from memory, does reviews when they're due |

## Architecture
_To do: diagram and request/processing flows._

## Decisions
_To do: key choices and the alternatives rejected (ADRs)._

## Cost
_To do: monthly estimate and cost per video._

## Security
_To do: Cognito groups, least-privilege IAM, encryption, Guardrails, prompt-injection tests._

## Limitations
_To do: what this version deliberately doesn't do yet._

## How to deploy
_To do: prerequisites, deploy and tear-down commands._

### Educator emails (optional, Amazon SES)
Educators always get a bell notification when a lesson is ready for review. To also email them:

1. Add the GitHub secret `EDUCATOR_EMAIL_FROM` with the sender address (e.g. an address you own).
2. Deploy. SES emails that address a verification link: click it once.
3. While the AWS account is in the **SES sandbox**, SES only delivers to verified addresses, so verify
   each educator's address too (SES console → Identities → Create identity → Email address), or request
   production access (SES console → Account dashboard → Request production access).

No secret = no emails; everything else works the same.
