from __future__ import annotations

from datetime import date
from typing import Protocol

from rank_rent.outcomes.models import PropertyOutcomeRecord


class OutcomeSourceAdapter(Protocol):
    name: str

    async def collect(
        self,
        *,
        property_id: str,
        start_date: date,
        end_date: date,
    ) -> list[PropertyOutcomeRecord]: ...
