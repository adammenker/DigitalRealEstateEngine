from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import event, select, text
from sqlalchemy.orm import Session

from rank_rent.db.orm import AuditEventORM
from rank_rent.security.auth import Principal


class ImmutableAuditRecordError(RuntimeError):
    pass


def _canonical_payload(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def append_audit_event(
    session: Session,
    *,
    event_type: str,
    actor: Principal,
    target_type: str,
    target_id: str | None,
    request_id: str | None,
    metadata: dict[str, Any] | None = None,
) -> AuditEventORM:
    if session.get_bind().dialect.name == "postgresql":
        # Serialize chain-head selection even when the table is still empty.
        session.execute(text("SELECT pg_advisory_xact_lock(74291731)"))
    previous = session.scalar(select(AuditEventORM).order_by(AuditEventORM.id.desc()).limit(1))
    previous_hash = previous.event_hash if previous is not None else "GENESIS"
    occurred_at = datetime.now(UTC)
    content: dict[str, Any] = {
        "event_type": event_type,
        "actor_user_id": actor.user_id,
        "actor_role": actor.role.value,
        "target_type": target_type,
        "target_id": target_id,
        "request_id": request_id,
        "metadata_payload": metadata or {},
        "occurred_at": occurred_at,
        "previous_hash": previous_hash,
    }
    hash_content = {**content, "occurred_at": occurred_at.isoformat()}
    row = AuditEventORM(
        **content,
        event_hash=hashlib.sha256(_canonical_payload(hash_content).encode()).hexdigest(),
    )
    session.add(row)
    return row


@event.listens_for(AuditEventORM, "before_update")
@event.listens_for(AuditEventORM, "before_delete")
def _prevent_audit_mutation(*_: object) -> None:
    raise ImmutableAuditRecordError("Audit records are append-only.")
