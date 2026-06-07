import { useEffect, useState } from "react";

import { getElfRuntimeStatus } from "../../features/elf/elfRuntimeApi";
import type { ElfRuntimeStatusRead } from "../../features/elf/types";

const STATUS_LABELS: Record<string, string> = {
  idle: "空闲",
  thinking: "整理上下文",
  tool_running: "执行工具",
  streaming_answer: "生成回复",
  speaking: "播放语音",
  waiting_user_input: "等待选择",
  completed: "已完成",
  failed: "已失败",
  recovering: "恢复中",
};

export function WorkshopElfPage() {
  const [status, setStatus] = useState<ElfRuntimeStatusRead | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;

    async function refresh() {
      try {
        const nextStatus = await getElfRuntimeStatus();
        if (!cancelled) {
          setStatus(nextStatus);
          setError("");
        }
      } catch (currentError) {
        if (!cancelled) {
          setError(currentError instanceof Error ? currentError.message : "读取精灵状态失败。");
        }
      }
    }

    void refresh();
    const intervalId = window.setInterval(refresh, 2500);
    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
    };
  }, []);

  if (error && !status) {
    return <div className="workshop-error-slot">{error}</div>;
  }

  return (
    <div className="elf-runtime-panel">
      <section className="elf-runtime-card elf-runtime-card--primary">
        <div>
          <span className="elf-runtime-kicker">Memo Elf Runtime</span>
          <h2>精灵状态</h2>
          <p>{status?.message || "当前没有需要恢复的精灵对话。"}</p>
        </div>
        <span className={`elf-runtime-status elf-runtime-status--${status?.status ?? "idle"}`}>
          {STATUS_LABELS[status?.status ?? "idle"] ?? status?.status ?? "空闲"}
        </span>
      </section>

      <section className="elf-runtime-grid">
        <RuntimeField label="是否占用" value={status?.busy ? "是" : "否"} />
        <RuntimeField label="Conversation" value={formatNullable(status?.conversation_id)} />
        <RuntimeField label="Turn" value={formatNullable(status?.turn_id)} />
        <RuntimeField label="更新时间" value={status ? new Date(status.updated_at).toLocaleString() : "-"} />
      </section>

      {status?.pending_interrupt ? (
        <section className="elf-runtime-card">
          <h3>等待用户选择</h3>
          <pre>{JSON.stringify(status.pending_interrupt, null, 2)}</pre>
        </section>
      ) : null}

      {status?.last_message ? (
        <section className="elf-runtime-card">
          <h3>最后消息</h3>
          <p>{status.last_message}</p>
        </section>
      ) : null}

      {status?.last_error ? (
        <section className="elf-runtime-card elf-runtime-card--error">
          <h3>最后错误</h3>
          <p>{status.last_error}</p>
        </section>
      ) : null}
    </div>
  );
}

function RuntimeField({ label, value }: { label: string; value: string }) {
  return (
    <div className="elf-runtime-field">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function formatNullable(value: number | null | undefined) {
  return typeof value === "number" ? `#${value}` : "-";
}
