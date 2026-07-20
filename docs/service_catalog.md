# Service Catalog

`config/services.yaml` is the versioned source of truth for discoverable service families.

Each configured service has:

- a stable ID, slug, display name, aliases, and description;
- authoritative keyword seeds and commercial-intent modifiers;
- negative terms and negative product terms;
- provider categories used for suitability and evidence validation;
- enabled and regulated flags.

Input may resolve by ID, slug, display name, or alias. The resolved service definition is
stored with scan inputs so planning, execution, rescoring, and promotion use the same
contract.

Unmatched text creates an explicit draft service only when the user chooses that path. Drafts
support testing scans for exploration, but full scans, rankable assessments, and promotion
require an enabled configured service. Add or revise catalog entries through a reviewed,
versioned configuration change rather than silently deriving production seeds from free text.
