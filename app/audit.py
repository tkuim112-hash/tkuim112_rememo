"""
存取稽核紀錄：誰、什麼時候、看了哪個病患的資料。
"""
from db.models import AuditLog
from db.session import AsyncSessionLocal


async def log_access(
    therapist_id: int | None,
    patient_id: int | None,
    action: str,
    resource: str | None = None,
) -> None:
    """寫入稽核紀錄；失敗只印警告，不讓稽核本身變成主功能的單點故障。"""
    try:
        async with AsyncSessionLocal() as session:
            session.add(AuditLog(
                therapist_id=therapist_id,
                patient_id=patient_id,
                action=action,
                resource=resource,
            ))
            await session.commit()
    except Exception as e:
        print(f"[AuditLog] 寫入失敗（不影響主要功能）: {e}")
