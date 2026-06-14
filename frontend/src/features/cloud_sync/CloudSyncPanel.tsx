import { Archive, Cloud, DownloadCloud, RefreshCw, ShieldAlert, UploadCloud } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import {
  getCloudSyncStatus,
  createCloudSyncBackup,
  listCloudSyncBackups,
  listCloudSyncConflicts,
  pullCloudSync,
  pushCloudSync,
  resolveCloudSyncConflict,
  runCloudSync,
  syncCloudSyncDomain,
} from "./cloudSyncApi";
import type { CloudSyncBackup, CloudSyncConflict, CloudSyncDomainStatus, CloudSyncRunResult, CloudSyncStatus } from "./types";
import { Badge, Button, EmptyState, PanelHeader } from "../../shared/ui";

type SyncAction = "pull" | "push" | "sync";
type DomainAction = { type: "domain"; domain: string };

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
  const conflictsQuery = useQuery({
    queryKey: ["cloud-sync", "conflicts"],
    queryFn: listCloudSyncConflicts,
    refetchInterval: 20000,
  });
  const backupsQuery = useQuery({
    queryKey: ["cloud-sync", "backups"],
    queryFn: listCloudSyncBackups,
    refetchInterval: 60000,
  });

  const syncMutation = useMutation({
    mutationFn: (action: SyncAction | DomainAction) => {
      if (typeof action === "object") {
        return syncCloudSyncDomain(action.domain);
      }
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
      await queryClient.invalidateQueries({ queryKey: ["cloud-sync", "conflicts"] });
      await queryClient.invalidateQueries({ queryKey: ["notes"] });
    },
    onError: (caught) => {
      setActionError(caught instanceof Error ? caught.message : "同步操作失败");
    },
  });
  const resolveConflictMutation = useMutation({
    mutationFn: resolveCloudSyncConflict,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["cloud-sync", "status"] });
      await queryClient.invalidateQueries({ queryKey: ["cloud-sync", "conflicts"] });
    },
    onError: (caught) => {
      setActionError(caught instanceof Error ? caught.message : "处理冲突失败");
    },
  });
  const backupMutation = useMutation({
    mutationFn: createCloudSyncBackup,
    onSuccess: async (result) => {
      setActionError(result.status === "ok" ? "" : result.message || "备份未启用");
      await queryClient.invalidateQueries({ queryKey: ["cloud-sync", "backups"] });
    },
    onError: (caught) => {
      setActionError(caught instanceof Error ? caught.message : "创建备份失败");
    },
  });

  const isBusy = syncMutation.isPending || statusQuery.isFetching || resolveConflictMutation.isPending || backupMutation.isPending;

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
      {status ? (
        <CloudSyncDomains
          domains={status.domains}
          isBusy={isBusy}
          onSyncDomain={(domain) => syncMutation.mutate({ type: "domain", domain })}
        />
      ) : null}

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
      <CloudSyncConflicts conflicts={conflictsQuery.data ?? []} isBusy={isBusy} onResolve={(id) => resolveConflictMutation.mutate(id)} />
      <CloudSyncBackups
        backups={backupsQuery.data ?? []}
        isBusy={isBusy}
        onCreate={() => {
          if (window.confirm("创建云端数据库备份会上传加密快照，继续吗？")) {
            backupMutation.mutate();
          }
        }}
      />
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
      {result.domains.length > 0 ? (
        <div>
          {result.domains.map((domain) => (
            <span key={domain.domain}>
              {domainLabel(domain.domain)} +{domain.uploaded_count}/-{domain.downloaded_count}
            </span>
          ))}
        </div>
      ) : null}
      {result.message ? <p>{result.message}</p> : null}
    </section>
  );
}

function CloudSyncDomains({
  domains,
  isBusy,
  onSyncDomain,
}: {
  domains: CloudSyncDomainStatus[];
  isBusy: boolean;
  onSyncDomain: (domain: string) => void;
}) {
  if (!domains.length) {
    return null;
  }
  return (
    <section className="cloud-sync-section">
      <h3>同步领域</h3>
      <div className="cloud-sync-domain-list">
        {domains.map((domain) => (
          <article className="cloud-sync-domain-card" key={domain.domain}>
            <div>
              <strong>{domainLabel(domain.domain)}</strong>
              <span>{domain.manifest_key}</span>
            </div>
            <div>
              <Badge tone={domain.dirty_count > 0 ? "warning" : "success"}>待上传 {domain.dirty_count}</Badge>
              <Badge tone={domain.conflict_count > 0 ? "danger" : "success"}>冲突 {domain.conflict_count}</Badge>
              <small>{formatDateTime(domain.last_synced_at)}</small>
            </div>
            <Button disabled={isBusy} onClick={() => onSyncDomain(domain.domain)} size="sm">
              <RefreshCw aria-hidden="true" size={14} />
              同步
            </Button>
          </article>
        ))}
      </div>
    </section>
  );
}

function CloudSyncConflicts({
  conflicts,
  isBusy,
  onResolve,
}: {
  conflicts: CloudSyncConflict[];
  isBusy: boolean;
  onResolve: (id: number) => void;
}) {
  return (
    <section className="cloud-sync-section">
      <h3>
        <ShieldAlert aria-hidden="true" size={16} />
        冲突
      </h3>
      {conflicts.length === 0 ? (
        <EmptyState>当前没有云同步冲突。</EmptyState>
      ) : (
        <div className="cloud-sync-conflict-list">
          {conflicts.map((conflict) => (
            <article className="cloud-sync-conflict-card" key={conflict.id}>
              <div>
                <strong>{domainLabel(conflict.domain)} #{conflict.entity_id}</strong>
                <span>
                  本地 v{conflict.local_revision} / 远端 v{conflict.remote_revision}
                </span>
              </div>
              <p>{conflict.remote_summary || conflict.local_summary || "远端和本地都有修改。"}</p>
              <Button disabled={isBusy} onClick={() => onResolve(conflict.id)} size="sm">
                保留两份
              </Button>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}

function CloudSyncBackups({
  backups,
  isBusy,
  onCreate,
}: {
  backups: CloudSyncBackup[];
  isBusy: boolean;
  onCreate: () => void;
}) {
  return (
    <section className="cloud-sync-section">
      <h3>
        <Archive aria-hidden="true" size={16} />
        数据库备份
      </h3>
      <Button disabled={isBusy} onClick={onCreate} size="sm">
        创建加密备份
      </Button>
      {backups.length === 0 ? (
        <EmptyState>暂无云端备份。</EmptyState>
      ) : (
        <div className="cloud-sync-backup-list">
          {backups.map((backup) => (
            <article className="cloud-sync-backup-card" key={backup.key}>
              <strong>{backup.name}</strong>
              <span>{formatBytes(backup.size_bytes)}</span>
              <small>{formatDateTime(backup.last_modified)}</small>
            </article>
          ))}
        </div>
      )}
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

function domainLabel(domain: string): string {
  const labels: Record<string, string> = {
    notes: "笔记",
    conversations: "对话",
    memories: "长期记忆",
    config: "配置",
    knowledge: "知识库",
  };
  return labels[domain] ?? domain;
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

function formatBytes(value: number): string {
  if (value < 1024) {
    return `${value} B`;
  }
  if (value < 1024 * 1024) {
    return `${(value / 1024).toFixed(1)} KB`;
  }
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}
