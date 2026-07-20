# ADR: Property Boundary for Lead and Outcome Records

## Status

Accepted for Workstreams J and K.

## Context

The production master spec makes lead routing depend on Workstream I's
property workflow. The current integration baseline does not contain the
authoritative `Property` model, and parallel workstreams must avoid defining
each other's schema.

## Decision

J/K use an opaque, constrained `property_id` as their integration boundary.
`PropertyRoutingProfile` and `PropertyDecision` each require a unique property
ID and link it to an existing opportunity. The outcome decision also pins the
existing full score and evidence artifact with database foreign keys.

The routing profile owns the public tracking number. Provider assignments own
only replaceable destinations.

## Consequences

- Lead routing and outcome feedback can be tested independently.
- Provider replacement preserves public property identity.
- Historical outcome joins are enforced for opportunity, score, and evidence.
- Workstream I can later make `property_id` a foreign key to its authoritative
  table without changing J/K service inputs.
- Until that integration migration lands, existence of a Workstream I property
  is enforced by orchestration rather than a database foreign key.
