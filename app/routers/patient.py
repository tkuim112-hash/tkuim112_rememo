from datetime import date

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from audit import log_access
from auth import get_current_therapist_id
from db.deps import get_db
from db.models import Patient, Therapist

router = APIRouter(prefix="/patients", tags=["patients"])


class PatientSummary(BaseModel):
    id: int
    name: str
    age: int
    avatar: str | None = None


class PatientListResponse(BaseModel):
    patients: list[PatientSummary]


@router.get(
    "",
    response_model=PatientListResponse,
    summary="取得目前治療師所屬機構的病患清單（供 Unity UserSelectScene 用）",
)
async def list_patients(
    therapist_id: int = Depends(get_current_therapist_id),
    db: AsyncSession = Depends(get_db),
):
    therapist = (
        await db.execute(select(Therapist).where(Therapist.id == therapist_id))
    ).scalar_one_or_none()
    if therapist is None or therapist.organization_id is None:
        return PatientListResponse(patients=[])

    result = await db.execute(
        select(Patient)
        .where(Patient.organization_id == therapist.organization_id)
        .order_by(Patient.id)
    )
    patients = result.scalars().all()

    await log_access(
        therapist_id=therapist_id,
        patient_id=None,
        action="list_patients",
        resource=f"organization:{therapist.organization_id}",
    )

    current_year = date.today().year
    return PatientListResponse(
        patients=[
            PatientSummary(
                id=p.id,
                name=p.name,
                age=current_year - p.birth_year,
                avatar=p.avatar,
            )
            for p in patients
        ]
    )
