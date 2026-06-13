from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.core.database import get_session
from app.schemas.cloud_sync import CloudSyncRunResult, CloudSyncStatusRead
from app.services.cloud_sync_service import get_sync_status, pull_once, push_once, sync_once


router = APIRouter(prefix="/cloud-sync", tags=["cloud-sync"])


@router.get("/status", response_model=CloudSyncStatusRead)
def get_cloud_sync_status_api(session: Session = Depends(get_session)) -> CloudSyncStatusRead:
    return get_sync_status(session)


@router.post("/pull", response_model=CloudSyncRunResult)
def pull_cloud_sync_api(session: Session = Depends(get_session)) -> CloudSyncRunResult:
    return pull_once(session)


@router.post("/push", response_model=CloudSyncRunResult)
def push_cloud_sync_api(session: Session = Depends(get_session)) -> CloudSyncRunResult:
    return push_once(session)


@router.post("/sync", response_model=CloudSyncRunResult)
def run_cloud_sync_api(session: Session = Depends(get_session)) -> CloudSyncRunResult:
    return sync_once(session)
