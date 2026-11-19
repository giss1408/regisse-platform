import uuid

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import AuditLog


def client_ip(request: Request) -> str:
    hops = get_settings().trusted_proxy_hops
    if hops > 0:
        parts = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
        if len(parts) >= hops:
            return parts[-hops]
    return request.client.host if request.client else "unknown"


async def audit(db: AsyncSession, *, actor_type: str, action: str, actor_id: uuid.UUID | None = None, entity: str | None = None,
                entity_id: object = None, ip: str | None = None, detail: dict | None = None) -> None:
    """Appends an audit row in the caller's transaction. Never put secrets, codes or full phone numbers in `detail`."""
    db.add(AuditLog(actor_type=actor_type, actor_id=actor_id, action=action, entity=entity,
                    entity_id=str(entity_id) if entity_id is not None else None, ip=ip, detail=detail))
