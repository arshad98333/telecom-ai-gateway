# Decisions

One file per decision, numbered in order, written when the decision was made. Five
headings: Context, Decision, Alternatives considered, Consequences, Status.

These are immutable. A decision that turns out to be wrong is not edited — a later
record supersedes it, and this one is marked `Superseded by NNNN`. That is the point:
the record of what was believed at the time is what stops a deliberate choice being
quietly undone by someone who never knew it was deliberate.

Scope: this directory holds decisions about the workspace and the system as a whole.
Decisions internal to one service live with that service — see
`telecom-mcp/docs/decisions/` for the tool package's own.

To add one:

    cp docs/decisions/0000-template.md docs/decisions/00NN-a-short-title.md

`make adr` prints the next number.
