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

Workstream I's authoritative `properties` table is integrated. Property creation uses the same
stable string ID for the property and its routing profile, preserving the original J/K service
contract. Provider assignments retain their foreign key to
`property_routing_profiles.property_id`; the property workflow verifies the shared property
identity and approval boundary before creating or changing assignments. Provider replacement does
not alter the property, domain, SiteConfig, build history, or analytics lineage.
