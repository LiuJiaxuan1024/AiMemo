from fastapi import APIRouter, Depends, Response, status
from sqlmodel import Session

from app.core.database import get_session
from app.schemas.note import NoteCategoryCreate, NoteCategoryRead, NoteCategoryUpdate
from app.services.note_service import (
    create_note_category,
    delete_note_category,
    list_note_categories,
    update_note_category,
)


router = APIRouter(prefix="/note-categories", tags=["note-categories"])


@router.get("", response_model=list[NoteCategoryRead])
def list_note_categories_api(session: Session = Depends(get_session)) -> list[NoteCategoryRead]:
    return list_note_categories(session)


@router.post("", response_model=NoteCategoryRead, status_code=status.HTTP_201_CREATED)
def create_note_category_api(
    payload: NoteCategoryCreate,
    session: Session = Depends(get_session),
) -> NoteCategoryRead:
    return create_note_category(
        session,
        name=payload.name,
        description=payload.description,
        color=payload.color,
    )


@router.patch("/{category_id}", response_model=NoteCategoryRead)
def update_note_category_api(
    category_id: int,
    payload: NoteCategoryUpdate,
    session: Session = Depends(get_session),
) -> NoteCategoryRead:
    return update_note_category(
        session,
        category_id,
        name=payload.name,
        description=payload.description,
        color=payload.color,
        sort_order=payload.sort_order,
    )


@router.delete("/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_note_category_api(
    category_id: int,
    session: Session = Depends(get_session),
) -> Response:
    delete_note_category(session, category_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
