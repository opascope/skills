---
name: opascope-define-done
description: Turn a sprawling problem into one falsifiable end-state sentence. Use when asked to define done, state the objective, say what solved looks like, or specify a completion condition. Produces the objective, not the implementation.
allowed-tools: Read Glob Grep Bash Write Edit AskUserQuestion
metadata:
  portability:
    claude: full; file verification and questions or text
    codex: full; shell verification and questions or text
---

# Define done

Read [shared.md](shared.md) first. Save the result with kind `objective`.

The deliverable is the sentence that means the user's pain is gone. Do not jump
from the problem to a feature or implementation plan.

1. Mirror the problem in the user's terms, including its tension. Gather only
   missing context that would change the objective.
2. Write one sentence: "This is solved when ..." Describe the observable world
   after the work, not "we build", "we create" or "we implement". Split distinct
   problems into separate objectives rather than joining unrelated goals.
3. Try to falsify each clause. Replace words such as "better" or "streamlined"
   with observable behavior. Ask what evidence would yield a clear yes or no.
4. Test the proxy trap: could every clause be true while the original pain
   remains? If so, put the missing pain into the objective.
5. Stop at the requested altitude. Default: an alignment sentence, saved with
   a short explanation of the pain and the falsifiability test. On a request
   for measurable verification, add artifact pointers and a proof per clause.
   Read [references/proofs.md](references/proofs.md) for that path.

Example: "Create a searchable folder" describes a solution. "This is solved
when a reader can find each note from its topic, and every original note is
preserved byte-for-byte" describes the end state. Counting an index file alone
would miss both retrieval and preservation.

An objective can name a future output. Mark it as expected, verify the parent
and inputs now, and identify the later command that proves the output. Never
claim a future pointer already resolves.

If the user asks for steps, pass the objective to opascope-planning. If they ask
for unattended execution, pass it to opascope-loop-builder. Alignment alone
does not trigger either workflow. Return the sentence and its artifact path.
