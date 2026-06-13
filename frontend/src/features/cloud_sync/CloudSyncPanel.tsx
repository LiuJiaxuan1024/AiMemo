import { Cloud, DownloadCloud, RefreshCw, UploadCloud } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import {
  getCloudSyncStatus,
  pullCloudSync,
  pushCloudSync,
  runCloudSync,
} from "./cloudSyncApi";
import type { CloudSyncRunResult, CloudSyncStatus } from "./types";
import { Badge, Button, EmptyState, PanelHeader } from "../../shared/ui";

type SyncAction = "pull" | "push" | "sync";

export function CloudSyncPanel() {
  const queryClient = useQueryClient();
  const [lastResult, setLastResult] = useState<CloudSyncRunResult | null>(null);
  const [actionError, setActionError] = useState("");

  const statusQuery = useQuery({
    queryKey: ["cloud-sync", "status"],
    queryFn: getCloudSyncStatus,
    refetchInterval: 15000,
  });
  const status = statusQuery.data ?? null;

  const syncMutation = useMutation({
    mutationFn: (action: SyncAction) => {
      if (action === "pull") {
        return pullCloudSync();
      }
      if (action === "push") {
        return pushCloudSync();
      }
      return runCloudSync();
    },
    onSuccess: async (result) => {
      setActionError("");
      setLastResult(result);
      await queryClient.invalidateQueries({ queryKey: ["cloud-sync", "status"] });
      await queryClient.invalidateQueries({ queryKey: ["notes"] });
    },
    onError: (caught) => {
      setActionError(caught instanceof Error ? caught.message : "同步操作失败");
    },
  });

  const isBusy = syncMutation.isPending || statusQuery.isFetching;

  return (
    <section className="cloud-sync-panel">
      <PanelHeader
        actions={
          <Button disabled={isBusy} onClick={() => statusQuery.refetch()} size="sm" variant="ghost">
            <RefreshCw aria-hidden="true" size={15} />
            刷新
          </Button>
        }
        subtitle="查看云同步状态，并手动执行上传、拉取或完整同步"
        title="云同步"
      />

      {statusQuery.error ? (
        <div className="cloud-sync-error">
          {statusQuery.error instanceof Error ? statusQuery.error.message : "读取云同步状态失败"}
        </div>
      ) : null}
      {actionError ? <div className="cloud-sync-error">{actionError}</div> : null}

      {status ? <CloudSyncStatusView isBusy={isBusy} status={status} /> : <EmptyState>正在读取同步状态...</EmptyState>}

      <div className="cloud-sync-actions" aria-label="云同步操作">
        <Button disabled={isBusy} onClick={() => syncMutation.mutate("push")} variant="primary">
          <UploadCloud aria-hidden="true" size={16} />
          立即上传
        </Button>
        <Button disabled={isBusy} onClick={() => syncMutation.mutate("pull")}>
          <DownloadCloud aria-hidden="true" size={16} />
          立即拉取
        </Button>
        <Button disabled={isBusy} onClick={() => syncMutation.mutate("sync")}>
          <RefreshCw aria-hidden="true" size={16} />
          先拉取再上传
        </Button>
      </div>

      {lastResult ? <CloudSyncResult result={lastResult} /> : null}
    </section>
  );
}

function CloudSyncStatusView({ isBusy, status }: { isBusy: boolean; status: CloudSyncStatus }) {
  return (
    <div className="cloud-sync-grid">
      <section className="cloud-sync-summary">
        <div className="cloud-sync-summary-icon">
          <Cloud aria-hidden="true" size={22} />
        </div>
        <div>
          <div className="cloud-sync-summary-title">
            <strong>{providerLabel(status.provider)}</strong>
            <Badge tone={status.enabled ? "success" : "warning"}>
              {status.enabled ? "已启用" : "仅手动"}
            </Badge>
            {isBusy ? <Badge tone="info">同步中</Badge> : null}
          </div>
          <p>{status.bucket || "本地模拟存储"}</p>
        </div>
      </section>

      <Metric label="待上传笔记" tone={status.dirty_note_count > 0 ? "warning" : "success"} value={status.dirty_note_count} />
      <Metric label="冲突" tone={status.conflict_count > 0 ? "danger" : "success"} value={status.conflict_count} />
      <Metric label="远端版本" tone="info" value={status.last_remote_global_revision} />

      <Detail label="同步命名空间" value={status.user_id} />
      <Detail label="Manifest" value={status.manifest_key} />
      <Detail label="Endpoint" value={status.endpoint || "-"} />
      <Detail label="最近上传" value={formatDateTime(status.last_push_at)} />
      <Detail label="最近拉取" value={formatDateTime(status.last_pull_at)} />
      <Detail label="最近错误" value={status.last_error || "-"} />
    </div>
  );
}

function Metric({
  label,
  tone,
  value,
}: {
  label: string;
  tone: "success" | "warning" | "danger" | "info";
  value: number;
}) {
  return (
    <section className="cloud-sync-metric">
      <span>{label}</span>
      <strong>{value}</strong>
      <Badge tone={tone}>{toneLabel(tone)}</Badge>
    </section>
  );
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div className="cloud-sync-detail">
      <span>{label}</span>
      <strong title={value}>{value}</strong>
    </div>
  );
}

function CloudSyncResult({ result }: { result: CloudSyncRunResult }) {
  return (
    <section className="cloud-sync-result">
      <strong>最近操作完成</strong>
      <div>
        <span>上传 {result.uploaded_note_count}</span>
        <span>下载 {result.downloaded_note_count}</span>
        <span>跳过 {result.skipped_note_count}</span>
        <span>冲突 {result.conflict_count}</span>
      </div>
      {result.message ? <p>{result.message}</p> : null}
    </section>
  );
}

function providerLabel(provider: string): string {
  if (provider === "aliyun_oss") {
    return "阿里云 OSS";
  }
  if (provider === "local_mock") {
    return "本地模拟存储";
  }
  return provider || "未配置";
}

function toneLabel(tone: "success" | "warning" | "danger" | "info"): string {
  if (tone === "success") {
    return "正常";
  }
  if (tone === "warning") {
    return "待处理";
  }
  if (tone === "danger") {
    return "需要处理";
  }
  return "信息";
}

function formatDateTime(value: string | null): string {
  if (!value) {
    return "-";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "short",
    timeStyle: "medium",
  }).format(date);
}
