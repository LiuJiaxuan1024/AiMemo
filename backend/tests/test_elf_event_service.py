from app.schemas.elf import ElfEventCreate
from app.services.elf_event_service import ElfEventService


def test_publish_event_increments_id() -> None:
    service = ElfEventService()

    first = service.publish(ElfEventCreate(source="chat", mood="thinking", message="开始思考。"))
    second = service.publish(ElfEventCreate(source="chat", mood="success", message="回答完成。"))

    assert first is not None
    assert second is not None
    assert first.id == 1
    assert second.id == 2
    assert service.latest_id() == 2


def test_list_after_returns_only_newer_events() -> None:
    service = ElfEventService()
    service.publish(ElfEventCreate(source="system", mood="idle", message="一"))
    service.publish(ElfEventCreate(source="system", mood="idle", message="二"))
    service.publish(ElfEventCreate(source="system", mood="idle", message="三"))

    events = service.list_after(1)

    assert [event.message for event in events] == ["二", "三"]


def test_retention_keeps_latest_events() -> None:
    service = ElfEventService(max_events=2)

    service.publish(ElfEventCreate(source="system", mood="idle", message="一"))
    service.publish(ElfEventCreate(source="system", mood="idle", message="二"))
    service.publish(ElfEventCreate(source="system", mood="idle", message="三"))

    assert [event.message for event in service.list_after(0)] == ["二", "三"]


def test_dedupe_key_suppresses_recent_duplicate() -> None:
    service = ElfEventService(dedupe_window_seconds=60)

    first = service.publish(
        ElfEventCreate(source="jobs", mood="success", message="任务完成。", dedupe_key="job:1:completed")
    )
    duplicate = service.publish(
        ElfEventCreate(source="jobs", mood="success", message="任务完成。", dedupe_key="job:1:completed")
    )

    assert first is not None
    assert duplicate is None
    assert len(service.list_after(0)) == 1
