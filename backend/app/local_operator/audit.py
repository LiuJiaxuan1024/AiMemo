import json
from typing import Any

from sqlmodel import Session

from app.models.agent_operation import AgentOperation
from app.models.note import utc_now


class AgentOperationAudit:
    """封装 agent_operations 写入逻辑。

    工具执行节点只调用 start/complete/fail/block，不直接操作 SQLModel 字段。
    这样后续 write/exec 增加 approval_required 时，不会污染工具代码。
    """

    def __init__(
        self,
        session: Session,
        *,
        conversation_id: int | None,
        turn_id: int | None,
    ):
        self.session = session
        self.conversation_id = conversation_id
        self.turn_id = turn_id

    def start(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        operation_type: str = "read",
        risk_level: str = "low",
        approval_required: bool = False,
    ) -> AgentOperation:
        operation = AgentOperation(
            conversation_id=self.conversation_id,
            turn_id=self.turn_id,
            operation_type=operation_type,
            status="running",
            tool_name=tool_name,
            input_json=_to_json(arguments),
            output_json="{}",
            risk_level=risk_level,
            approval_required=approval_required,
        )
        self.session.add(operation)
        self.session.commit()
        self.session.refresh(operation)
        return operation

    def complete(self, operation: AgentOperation, *, output: dict[str, Any]) -> AgentOperation:
        operation.status = "completed"
        operation.output_json = _to_json(output)
        operation.updated_at = utc_now()
        self.session.add(operation)
        self.session.commit()
        self.session.refresh(operation)
        return operation

    def fail(self, operation: AgentOperation, *, output: dict[str, Any]) -> AgentOperation:
        operation.status = "failed"
        operation.output_json = _to_json(output)
        operation.updated_at = utc_now()
        self.session.add(operation)
        self.session.commit()
        self.session.refresh(operation)
        return operation

    def block(self, operation: AgentOperation, *, output: dict[str, Any]) -> AgentOperation:
        operation.status = "blocked"
        operation.output_json = _to_json(output)
        operation.updated_at = utc_now()
        self.session.add(operation)
        self.session.commit()
        self.session.refresh(operation)
        return operation


def _to_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)
