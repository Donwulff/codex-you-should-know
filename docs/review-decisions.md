# Review decisions and ideas

Recorded 2026-10-20. The confirmation policy is accepted for now. Updating
published notes remains an idea to revisit after observing actual use.

## What happens today

1. An **initial review** looks for one new concern in the conversation log.
2. A **candidate** is a possible note waiting for a second opinion.
3. A **confirmation review** checks that candidate against the original evidence
   and newer conversation. It can withdraw a concern that was fixed, explained,
   accepted, is still being worked on, or is unsupported. This review confirms
   the pending candidate rather than searching for another concern.
4. An approved candidate becomes a **published note**. Later reviews receive
   summaries of recent notes to avoid repetition; they do not update their status.

Both reviews read the supplied log; they do not independently inspect the system.
A successful finding normally needs two model calls. Time and call limits can
leave a candidate pending before confirmation starts.

## Decision: accept confirmation against the conversation it reviewed

An approved confirmation is published even if more messages or tool activity
arrive during the model call. New activity alone neither withdraws the candidate
nor causes another confirmation.

Confirmation already reads the conversation added since the initial review.
We accept the remaining delay of running confirmation and delivering its result:
a note may occasionally arrive just after its cause was fixed or explained.

Reasons for this choice:

- New activity does not establish that the concern changed.
- Waiting for a quiet conversation can repeatedly postpone notes and spend
  additional model calls on unrelated activity.
- Another change could arrive immediately after a final check anyway.

Reconsider this policy if notes arriving after resolution become a recurring
problem in actual use. The possibility alone does not justify extra calls.

## Alternatives considered

| Approach | Status | Reason |
| --- | --- | --- |
| Withdraw an approved candidate whenever activity arrives during confirmation. | Removed; upstream commit `0d5ab3f`. | Closed the candidate even when the activity was unrelated. |
| Keep confirming at later checks until no activity arrives during a call. | Implemented in `55df6cc`, replaced by `e587760`. | Avoided losing candidates, but still required a quiet interval and could waste calls. |
| Accept confirmation against the supplied conversation. | Current policy; `e587760`. | Keeps the normal confirmation while accepting an occasional note that has just become outdated. |

The original upstream test deliberately covered a fix arriving during
confirmation. That establishes intent, but not how often the problem occurs.

## Possible future feature: update existing notes during later reviews

Later reviews already receive recent note summaries. The same model call could
return a new concern and updates to existing notes, such as evidence of resolution.
This is an idea only, not an implemented or scheduled feature.

It could avoid separate follow-up calls, although more context and output would
still use tokens. Prompt and response-format changes, stored updates, and their
display would need implementation. Effort and review quality are not evaluated.

If it becomes useful, decide:

- Which notes to include and what evidence justifies an update.
- Whether to annotate a note or mark it resolved, and how to display that change.
- How updates interact with notes already marked known or dismissed.

For now, let notes accumulate. Revisit this if outdated notes cause confusion or
repeated cleanup.

## A withdrawn concern can appear again

Withdrawal closes a candidate, not its topic. A later review can create a new
candidate from the same earlier evidence if it remains in the conversation
excerpt. The withdrawn record stays in history, visible with `notes --all`.

Automatic checks skip unchanged excerpts; new activity or an explicit manual
check permits another review. Rediscovery is possible, not guaranteed.

Published notes, including known or dismissed ones, suppress repeated topics
while retained. This does not track later changes to their underlying cause.
