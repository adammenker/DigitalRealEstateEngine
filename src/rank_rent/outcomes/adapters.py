from __future__ import annotations

from datetime import date

from rank_rent.outcomes.models import PropertyOutcomeRecord


class FixtureOutcomeAdapter:
    """Deterministic outcome source that performs no external requests."""

    def __init__(self, name: str, records: list[PropertyOutcomeRecord]) -> None:
        self.name = name
        self.records = list(records)

    async def collect(
        self,
        *,
        property_id: str,
        start_date: date,
        end_date: date,
    ) -> list[PropertyOutcomeRecord]:
        return [
            record.model_copy(deep=True)
            for record in self.records
            if record.property_id == property_id and start_date <= record.period_date <= end_date
        ]
