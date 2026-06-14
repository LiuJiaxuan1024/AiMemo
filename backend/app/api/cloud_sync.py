from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.core.database import get_session
from app.schemas.cloud_sync import (
    CloudSyncBackupCreateResult,
    CloudSyncBackupRead,
    CloudSyncConflictRead,
    CloudSyncConflictResolveRequest,
    CloudSyncDomainStatus,
    CloudSyncRunResult,
    CloudSyncStatusRead,
)
from app.services.cloud_sync_service import (
    create_backup,
    get_sync_status,
    list_backups,
    list_conflicts,
    list_domain_statuses,
    pull_once,
    push_once,
    resolve_conflict,
    sync_domain_once,
    sync_once,
)


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


@router.get("/domains", response_model=list[CloudSyncDomainStatus])
def list_cloud_sync_domains_api(session: Session = Depends(get_session)) -> list[CloudSyncDomainStatus]:
    return list_domain_statuses(session)


@router.post("/domains/{domain}/sync", response_model=CloudSyncRunResult)
def run_cloud_sync_domain_api(domain: str, session: Session = Depends(get_session)) -> CloudSyncRunResult:
    return sync_domain_once(session, domain)


@router.get("/conflicts", response_model=list[CloudSyncConflictRead])
def list_cloud_sync_conflicts_api(session: Session = Depends(get_session)) -> list[CloudSyncConflictRead]:
    return list_conflicts(session)


@router.post("/conflicts/{conflict_id}/resolve", response_model=CloudSyncConflictRead)
def resolve_cloud_sync_conflict_api(
    conflict_id: int,
    payload: CloudSyncConflictResolveRequest,
    session: Session = Depends(get_session),
) -> CloudSyncConflictRead:
    return resolve_conflict(session, conflict_id, resolution=payload.resolution)


@router.get("/backups", response_model=list[CloudSyncBackupRead])
def list_cloud_sync_backups_api() -> list[CloudSyncBackupRead]:
    return list_backups()


@router.post("/backups", response_model=CloudSyncBackupCreateResult)
def create_cloud_sync_backup_api() -> CloudSyncBackupCreateResult:
    return create_backup()
