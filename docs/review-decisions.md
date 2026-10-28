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

## Trial: explain clear reviews and include smaller concrete concerns

Added 2026-10-26 after observing many clear reviews and very few candidates.
The initial reviewer now supplies a bounded category and brief decision summary
when it returns no finding. Status retains those explanations and category totals;
historical reviews stay unclassified. This helps distinguish lack of evidence,
disclosure, ongoing work, and other reasons for silence. The classification is a
model judgment, not independent verification of why no useful concern was found.

The prompts also explicitly allow modest practical consequences and tool evidence
from settings, code, or diffs with a clear causal link. An outage or severe impact
is not required. Unrelated activity is not evidence that the specific concern is
being addressed, and merely mentioning a topic does not disclose its consequence.
Finding explanations should describe the observed behavior, practical effect,
and relevant uncertainty in two to four sentences.

Exact tool-result evidence, separate confirmation, and suppression of disclosed,
resolved, accepted, repeated, or actively addressed concerns still apply.
Whether this increases useful findings or false alarms remains to be evaluated.

## Retain rejected responses for diagnosis

Added 2026-10-28 after an evidence-escaping mismatch discarded a proposed finding
before candidate creation. Existing diagnostics preserved the quotes but lost
the topic, summary, and explanation, leaving no way to inspect the proposed concern.

Assessment failures now retain up to 16,384 characters of serialized response JSON
in the check's diagnostics, including an original length and truncation flag.
The existing check retention policy applies. These are unvalidated responses;
they never become notes or bypass exact evidence validation. Transport failures
without a parsed response remain outside this capture. A replay is a new model
decision, not recovery of a historical response.

## Reinforce literal quote copying

Added 2026-10-28 after a reviewer decoded literal newline and tab escapes from
JSON embedded in tool output, despite the existing instruction to preserve them.
Both review stages now include concrete source-to-response JSON examples and
repeat the literal-copy rule after the conversation. Shorter quotes are encouraged
when they still support the concern. Exact matching remains unchanged; this is
a prompt mitigation whose effect on model behavior has not yet been measured.

## Preserve the current request in resumed conversations

Changed 2026-10-28 after reviewing a long-lived conversation resumed for a new
task. The excerpt retained the first three user messages from months earlier,
while a long sequence of tool results displaced the current request. A replay
then evaluated the recent work against requirements from the old task.

The transcript reader now reserves up to a third of the character budget for
the latest three user messages and fills the remaining space with recent work.
It merges these by their original positions, without duplication, and marks
omitted regions. The model-input byte-budget pass also reserves room for recent
user messages, including when confirmation shortens an already-shortened excerpt.
Prompts explicitly distinguish current requests from completed or superseded tasks.

This remains bounded selection, not a complete task history. Large messages can
be clipped, tiny budgets can omit older reserved messages, and requirements more
than three user messages back can still fall outside the activity window.
The algorithm does not claim to identify which older requests remain relevant.
