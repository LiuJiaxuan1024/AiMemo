import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Server, Trash2, X } from "lucide-react";

import { Button, PanelHeader } from "../../shared/ui";
import {
  getBackgroundTaskOutput,
  killBackgroundTask,
  listBackgroundTasks,
  pruneBackgroundTask,
} from "./backgroundTasksApi";
import type { BackgroundTask, BackgroundTaskStatus } from "./types";

const RUNNING_STATUSES = new Set<BackgroundTaskStatus>(["running"]);

const STATUS_LABELS: Record<BackgroundTaskStatus, string> = {
  running: "运行中",
  exited: "已结束",
  failed: "失败",
  killed: "已终止",
  orphaned: "已孤立",
  unknown: "未知",
};

function formatTime(value: string | null): string {
  if (!value) return "—";
  try {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    return date.toLocaleString();
  } catch {
    return value;
  }
}

function truncate(text: string, max = 80): string {
  if (text.length <= max) return text;
  return `${text.slice(0, max - 1)}…`;
}

export function BackgroundTasksDrawer() {
  const queryClient = useQueryClient();
  const [isOpen, setIsOpen] = useState(false);
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);

  const tasksQuery = useQuery({
    queryKey: ["background_tasks"],
    queryFn: listBackgroundTasks,
    refetchInterval: (query) => {
      const tasks = query.state.data ?? [];
      const hasRunning = tasks.some((t) => RUNNING_STATUSES.has(t.status));
      return isOpen || hasRunning ? 3000 : 10000;
    },
  });

  const tasks = tasksQuery.data ?? [];
  const runningCount = useMemo(
    () => tasks.filter((t) => RUNNING_STATUSES.has(t.status)).length,
    [tasks],
  );

  const outputQuery = useQuery({
    enabled: isOpen && selectedTaskId !== null,
    queryKey: ["background_tasks", selectedTaskId, "output"],
    queryFn: () => getBackgroundTaskOutput(selectedTaskId as string, 0, 200),
    refetchInterval: (query) => {
      const data = query.state.data;
      return data && data.status === "running" ? 2000 : false;
    },
  });

  const killMutation = useMutation({
    mutationFn: (taskId: string) => killBackgroundTask(taskId),
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ["background_tasks"] });
    },
  });

  const pruneMutation = useMutation({
    mutationFn: (taskId: string) => pruneBackgroundTask(taskId),
    onSuccess: (_, taskId) => {
      if (selectedTaskId === taskId) {
        setSelectedTaskId(null);
      }
      queryClient.invalidateQueries({ queryKey: ["background_tasks"] });
    },
  });

  useEffect(() => {
    if (selectedTaskId === null) return;
    if (!tasks.some((t) => t.task_id === selectedTaskId)) {
      setSelectedTaskId(null);
    }
  }, [tasks, selectedTaskId]);

  const selectedTask = selectedTaskId
    ? tasks.find((t) => t.task_id === selectedTaskId) ?? null
    : null;

  return (
    <aside className={isOpen ? "bg-task-drawer open" : "bg-task-drawer"}>
      <button
        aria-label={isOpen ? "收起后台任务" : "展开后台任务"}
        className="bg-task-drawer-handle"
        onClick={() => setIsOpen((v) => !v)}
        type="button"
      >
        <Server aria-hidden="true" size={18} />
        <span>后台任务</span>
        {runningCount > 0 ? <strong>{runningCount}</strong> : null}
      </button>

      <div className="bg-task-drawer-panel">
        <PanelHeader
          actions={
            <Button onClick={() => setIsOpen(false)} size="sm">
              <X aria-hidden="true" size={16} />
              收起
            </Button>
          }
          subtitle={
            runningCount > 0
              ? `${runningCount} 个任务运行中`
              : "当前没有运行中的任务"
          }
          title="后台任务"
        />

        {tasksQuery.error ? (
          <div className="bg-task-drawer-error">
            {tasksQuery.error instanceof Error
              ? tasksQuery.error.message
              : "读取后台任务失败"}
          </div>
        ) : null}

        <div className="bg-task-drawer-content">
          <BackgroundTaskList
            tasks={tasks}
            selectedTaskId={selectedTaskId}
            onSelect={setSelectedTaskId}
            onKill={(taskId) => killMutation.mutate(taskId)}
            onPrune={(taskId) => pruneMutation.mutate(taskId)}
            killingTaskId={killMutation.isPending ? killMutation.variables ?? null : null}
            pruningTaskId={pruneMutation.isPending ? pruneMutation.variables ?? null : null}
          />
          <BackgroundTaskDetail
            task={selectedTask}
            outputLines={outputQuery.data?.lines ?? []}
            isLoading={outputQuery.isFetching && !outputQuery.data}
            error={
              outputQuery.error instanceof Error ? outputQuery.error.message : null
            }
          />
        </div>
      </div>
    </aside>
  );
}

interface ListProps {
  tasks: BackgroundTask[];
  selectedTaskId: string | null;
  onSelect: (taskId: string) => void;
  onKill: (taskId: string) => void;
  onPrune: (taskId: string) => void;
  killingTaskId: string | null;
  pruningTaskId: string | null;
}

function BackgroundTaskList({
  tasks,
  selectedTaskId,
  onSelect,
  onKill,
  onPrune,
  killingTaskId,
  pruningTaskId,
}: ListProps) {
  if (tasks.length === 0) {
    return (
      <div className="bg-task-drawer-empty">
        还没有后台任务。让 agent 启动一个长跑服务后会出现在这里。
      </div>
    );
  }

  return (
    <ol className="bg-task-list">
      {tasks.map((task) => {
        const isSelected = selectedTaskId === task.task_id;
        const isRunning = task.status === "running";
        const isKilling = killingTaskId === task.task_id;
        const isPruning = pruningTaskId === task.task_id;
        return (
          <li
            className={`bg-task-list-item ${isSelected ? "selected" : ""}`}
            key={task.task_id}
          >
            <button
              className="bg-task-list-item-main"
              onClick={() => onSelect(task.task_id)}
              type="button"
            >
              <span className={`bg-task-status bg-task-status--${task.status}`}>
                {STATUS_LABELS[task.status] ?? task.status}
              </span>
              <span className="bg-task-command" title={task.command}>
                {truncate(task.command, 90)}
              </span>
              <span className="bg-task-meta">
                <span>PID {task.pid ?? "—"}</span>
                <span>{formatTime(task.started_at)}</span>
                {task.exit_code !== null && task.exit_code !== undefined ? (
                  <span>退出码 {task.exit_code}</span>
                ) : null}
              </span>
            </button>
            <div className="bg-task-list-item-actions">
              {isRunning ? (
                <Button
                  disabled={isKilling}
                  onClick={() => onKill(task.task_id)}
                  size="sm"
                  variant="ghost"
                >
                  <X aria-hidden="true" size={14} />
                  {isKilling ? "终止中" : "终止"}
                </Button>
              ) : (
                <Button
                  disabled={isPruning}
                  onClick={() => onPrune(task.task_id)}
                  size="sm"
                  variant="ghost"
                >
                  <Trash2 aria-hidden="true" size={14} />
                  {isPruning ? "移除中" : "移除"}
                </Button>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

interface DetailProps {
  task: BackgroundTask | null;
  outputLines: { line: number; stream: string; text: string }[];
  isLoading: boolean;
  error: string | null;
}

function BackgroundTaskDetail({ task, outputLines, isLoading, error }: DetailProps) {
  if (!task) {
    return (
      <div className="bg-task-detail bg-task-detail--empty">
        选择一个任务查看实时输出。
      </div>
    );
  }

  return (
    <div className="bg-task-detail">
      <div className="bg-task-detail-meta">
        <div>
          <strong>cwd:</strong> {task.cwd}
        </div>
        {task.kill_reason ? (
          <div>
            <strong>终止原因:</strong> {task.kill_reason}
          </div>
        ) : null}
        {task.finished_at ? (
          <div>
            <strong>结束于:</strong> {formatTime(task.finished_at)}
          </div>
        ) : null}
      </div>
      <pre className="bg-task-output">
        {error ? (
          <span className="bg-task-output-error">{error}</span>
        ) : isLoading ? (
          <span className="bg-task-output-empty">正在读取输出…</span>
        ) : outputLines.length === 0 ? (
          <span className="bg-task-output-empty">暂无输出</span>
        ) : (
          outputLines.map((l) => (
            <div
              className={`bg-task-output-line bg-task-output-line--${l.stream}`}
              key={`${l.stream}-${l.line}`}
            >
              {l.text || " "}
            </div>
          ))
        )}
      </pre>
    </div>
  );
}
