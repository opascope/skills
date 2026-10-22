# Stop and ask

Who decides what. When in doubt, take the higher level.

## The planning gate

Every milestone, epic and Beads item is `planned`, `needs-planning` or `not-planned`. Agents pick up
only planned work. An untagged item counts as needs-planning, except a bug, which counts as planned
(a clear repro and a bounded fix may be actioned at once). Moving anything to planned is level 3.

## Level 1: the agent decides

Change it, then say what changed.

- Writing or splitting the tasks inside an epic (Beads items).
- Filling in an epic's research, inputs and outputs.
- Triaging the Inbox into an existing epic or a new Beads task, or into the needs-planning list when
  it is new work nobody has planned.
- Splitting a planned epic into tasks, and labelling them planned.

## Level 2: the agent presents options

Write two or three options with a recommendation, then wait for a human pick.

- The scope of a milestone (which epics belong to it).
- Splitting or merging epics.
- Changing dependencies between epics.

## Level 3: human only

Never change these without the owner confirming in chat. The change adds a dated DECISIONS.md entry
with "confirmed by <who> in chat: <where>" (or "via <card>" for a sign-off card). A routine (unattended) run files the idea to the Inbox instead.

- VISION.md.
- The order of milestones in the roadmap, README.md (Now, Next, Later).
- A milestone's success criteria.
- Moving any milestone, epic or feature to planned.
- Anything legal, anything that spends money, any new stack or vendor.
