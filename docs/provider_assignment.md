# Provider Assignment

## Lifecycle

Assignments use the following transitions:

```text
candidate -> pilot -> active
candidate -> terminated
pilot -> paused | terminated
active -> paused | terminated | replaced
paused -> active | terminated | replaced
```

Terminal assignments cannot be reactivated. Termination and replacement
require a reason. The service permits one active assignment per property and
rejects activation while another provider is active.

An assignment records provider-candidate provenance when available, coverage,
destinations, response expectation, lead-acceptance requirement, agreement
dates, active dates, termination reason, and replacement history.

## Replacement

`ProviderOperationsService.replace_assignment()`:

1. Verifies both assignments belong to the same property.
2. Marks the current active assignment `replaced`.
3. Links the replacement to the prior assignment.
4. Promotes a candidate replacement through `pilot` to `active`.

The property routing profile is not changed. Its public tracking number,
public contact email, opportunity identity, analytics identity, and lead
history survive replacement. Only the destination assignment changes.

## Property compatibility

Workstream I's authoritative `Property` table is not present in the baseline.
J/K therefore use an opaque, unique `property_id` integration key. The routing
profile ties that key to an existing opportunity. When Workstream I lands, its
property identifier can become the foreign-key target without changing the
provider or lead lifecycle APIs.
