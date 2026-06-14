import { ChangeEvent, FormEvent, useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Archive,
  MoreHorizontal,
  Trash2,
  FileText,
  FolderPlus,
  Image as ImageIcon,
  Layers3,
  RefreshCw,
  Search,
  Upload,
} from "lucide-react";

import {
  archiveKnowledgeSpace,
  createKnowledgeSpace,
  deleteKnowledgeDocument,
  getKnowledgeOcrStatus,
  installKnowledgeOcr,
  listKnowledgeChunks,
  listKnowledgeDocuments,
  listKnowledgeImageAssets,
  listKnowledgeSpaces,
  retryKnowledgeDocumentFailedImages,
  retryKnowledgeDocumentProcessing,
  retryKnowledgeImageAsset,
  searchKnowledge,
  uploadKnowledgeDocument,
} from "../../features/knowledge/knowledgeApi";
import type {
  KnowledgeChunk,
  KnowledgeDocument,
  KnowledgeImageAsset,
  KnowledgeOcrStatus,
  KnowledgeSearchResultItem,
  KnowledgeSpace,
} from "../../features/knowledge/types";
import { Badge, Button, EmptyState, PanelHeader } from "../../shared/ui";

const PROCESSING_DOCUMENT_STATUSES = new Set(["pending", "parsing", "chunking", "embedding", "indexing"]);
const PROCESSING_IMAGE_ASSET_STATUSES = new Set(["pending", "processing"]);

export function KnowledgePage() {
  const queryClient = useQueryClient();
  const [selectedSpaceId, setSelectedSpaceId] = useState<number | null>(null);
  const [selectedDocumentId, setSelectedDocumentId] = useState<number | null>(null);
  const [spaceName, setSpaceName] = useState("");
  const [spaceDescription, setSpaceDescription] = useState("");
  const [uploadTitle, setUploadTitle] = useState("");
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchMode, setSearchMode] = useState<"hybrid" | "vector" | "keyword">("hybrid");
  const [searchResults, setSearchResults] = useState<KnowledgeSearchResultItem[]>([]);
  const [error, setError] = useState("");

  const spacesQuery = useQuery({
    queryKey: ["knowledge", "spaces"],
    queryFn: () => listKnowledgeSpaces(false),
  });
  const spaces = spacesQuery.data ?? [];
  const selectedSpace = spaces.find((space) => space.id === selectedSpaceId) ?? spaces[0] ?? null;

  const documentsQuery = useQuery({
    enabled: Boolean(selectedSpace?.id),
    queryKey: ["knowledge", "documents", selectedSpace?.id],
    queryFn: () => listKnowledgeDocuments(Number(selectedSpace?.id)),
    refetchInterval: (query) => {
      const documents = query.state.data ?? [];
      return documents.some((document) => PROCESSING_DOCUMENT_STATUSES.has(document.status)) ? 2500 : false;
    },
  });
  const documents = documentsQuery.data ?? [];
  const selectedDocument = documents.find((document) => document.id === selectedDocumentId) ?? documents[0] ?? null;

  const ocrStatusQuery = useQuery({
    queryKey: ["knowledge", "ocr", "status"],
    queryFn: getKnowledgeOcrStatus,
    staleTime: 60_000,
  });
  const ocrStatus = ocrStatusQuery.data ?? null;

  const chunksQuery = useQuery({
    enabled: Boolean(selectedDocument?.id),
    queryKey: ["knowledge", "chunks", selectedDocument?.id],
    queryFn: () => listKnowledgeChunks(Number(selectedDocument?.id)),
  });
  const chunks = chunksQuery.data ?? [];

  const imageAssetsQuery = useQuery({
    enabled: Boolean(selectedDocument?.id && selectedDocument.image_asset_count > 0),
    queryKey: ["knowledge", "image-assets", selectedDocument?.id],
    queryFn: () => listKnowledgeImageAssets(Number(selectedDocument?.id)),
    refetchInterval: (query) => {
      const imageAssets = query.state.data ?? [];
      return imageAssets.some((asset) => PROCESSING_IMAGE_ASSET_STATUSES.has(asset.status)) ? 2500 : false;
    },
  });
  const imageAssets = imageAssetsQuery.data ?? [];

  const stats = useMemo(() => {
    const ready = spaces.reduce((sum, space) => sum + space.ready_document_count, 0);
    const documentsTotal = spaces.reduce((sum, space) => sum + space.document_count, 0);
    return { spaces: spaces.length, documents: documentsTotal, ready };
  }, [spaces]);

  useEffect(() => {
    if (!selectedSpaceId && spaces.length > 0) {
      setSelectedSpaceId(spaces[0].id);
    }
    if (selectedSpaceId && !spaces.some((space) => space.id === selectedSpaceId)) {
      setSelectedSpaceId(spaces[0]?.id ?? null);
    }
  }, [selectedSpaceId, spaces]);

  useEffect(() => {
    if (documents.length === 0) {
      setSelectedDocumentId(null);
      return;
    }
    if (!selectedDocumentId || !documents.some((document) => document.id === selectedDocumentId)) {
      setSelectedDocumentId(documents[0].id);
    }
  }, [documents, selectedDocumentId]);

  const createSpaceMutation = useMutation({
    mutationFn: createKnowledgeSpace,
    onSuccess: async (space) => {
      setSpaceName("");
      setSpaceDescription("");
      setSelectedSpaceId(space.id);
      setError("");
      await queryClient.invalidateQueries({ queryKey: ["knowledge", "spaces"] });
    },
    onError: (caught) => setError(errorMessage(caught, "创建知识空间失败")),
  });

  const archiveSpaceMutation = useMutation({
    mutationFn: archiveKnowledgeSpace,
    onSuccess: async () => {
      setSelectedSpaceId(null);
      setSelectedDocumentId(null);
      setError("");
      await queryClient.invalidateQueries({ queryKey: ["knowledge"] });
    },
    onError: (caught) => setError(errorMessage(caught, "归档知识空间失败")),
  });

  const uploadMutation = useMutation({
    mutationFn: ({ spaceId, file, title }: { spaceId: number; file: File; title?: string }) =>
      uploadKnowledgeDocument(spaceId, file, title),
    onSuccess: async (response) => {
      setUploadTitle("");
      setUploadFile(null);
      setSelectedDocumentId(response.document.id);
      setError("");
      await queryClient.invalidateQueries({ queryKey: ["knowledge"] });
    },
    onError: (caught) => setError(errorMessage(caught, "上传文档失败")),
  });

  const installOcrMutation = useMutation({
    mutationFn: installKnowledgeOcr,
    onSuccess: async (result) => {
      await queryClient.invalidateQueries({ queryKey: ["knowledge", "ocr", "status"] });
      await queryClient.invalidateQueries({ queryKey: ["background_tasks"] });
      setError(result.after_status.ready ? "" : result.message);
    },
    onError: (caught) => setError(errorMessage(caught, "安装 OCR 失败")),
  });

  const searchMutation = useMutation({
    mutationFn: searchKnowledge,
    onSuccess: (response) => {
      setSearchResults(response.results);
      setError("");
    },
    onError: (caught) => setError(errorMessage(caught, "知库搜索失败")),
  });

  const deleteDocumentMutation = useMutation({
    mutationFn: deleteKnowledgeDocument,
    onSuccess: async () => {
      setSelectedDocumentId(null);
      setSearchResults([]);
      setError("");
      await queryClient.invalidateQueries({ queryKey: ["knowledge"] });
    },
    onError: (caught) => setError(errorMessage(caught, "删除文档失败")),
  });

  const retryDocumentProcessingMutation = useMutation({
    mutationFn: retryKnowledgeDocumentProcessing,
    onSuccess: async (response) => {
      setSelectedDocumentId(response.document.id);
      setSearchResults([]);
      setError("");
      await queryClient.invalidateQueries({ queryKey: ["knowledge"] });
      await queryClient.invalidateQueries({ queryKey: ["background_tasks"] });
    },
    onError: (caught) => setError(errorMessage(caught, "重新处理文档失败")),
  });

  const retryFailedImageAssetsMutation = useMutation({
    mutationFn: ({ documentId, onlyRetryable }: { documentId: number; onlyRetryable: boolean }) =>
      retryKnowledgeDocumentFailedImages(documentId, { onlyRetryable, maxAssets: 20 }),
    onSuccess: async (response) => {
      setSelectedDocumentId(response.document.id);
      setSearchResults([]);
      setError("");
      await queryClient.invalidateQueries({ queryKey: ["knowledge"] });
      await queryClient.invalidateQueries({ queryKey: ["knowledge", "image-assets", response.document.id] });
      await queryClient.invalidateQueries({ queryKey: ["background_tasks"] });
    },
    onError: async (caught, variables) => {
      await queryClient.invalidateQueries({ queryKey: ["knowledge"] });
      await queryClient.invalidateQueries({ queryKey: ["knowledge", "image-assets", variables.documentId] });
      const parsed = apiErrorDetail(caught);
      setError(
        parsed?.code === "KNOWLEDGE_IMAGE_ASSET_NO_RETRYABLE_FAILURES"
          ? "当前没有可自动重试的失败图片，可能刚才的重试已经成功，或剩余失败项属于不可自动重试。已刷新图片明细。"
          : errorMessage(caught, "重试失败图片失败"),
      );
    },
  });

  const retrySingleImageAssetMutation = useMutation({
    mutationFn: ({ imageAssetId, onlyRetryable }: { imageAssetId: number; onlyRetryable: boolean }) =>
      retryKnowledgeImageAsset(imageAssetId, { onlyRetryable }),
    onSuccess: async (response) => {
      setSelectedDocumentId(response.document.id);
      setSearchResults([]);
      setError("");
      await queryClient.invalidateQueries({ queryKey: ["knowledge"] });
      await queryClient.invalidateQueries({ queryKey: ["knowledge", "image-assets", response.document.id] });
      await queryClient.invalidateQueries({ queryKey: ["background_tasks"] });
    },
    onError: (caught) => setError(errorMessage(caught, "重试图片失败")),
  });

  function handleCreateSpace(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const name = spaceName.trim();
    if (!name) {
      setError("空间名称不能为空");
      return;
    }
    createSpaceMutation.mutate({
      name,
      description: spaceDescription.trim(),
      icon: "library",
    });
  }

  async function handleUpload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedSpace) {
      setError("请先创建知识空间");
      return;
    }
    if (!uploadFile) {
      setError("请选择要上传的文档");
      return;
    }
    if (documentMayContainImages(uploadFile)) {
      let currentOcrStatus = ocrStatus;
      try {
        currentOcrStatus = await queryClient.fetchQuery({
          queryKey: ["knowledge", "ocr", "status"],
          queryFn: getKnowledgeOcrStatus,
          staleTime: 60_000,
        });
      } catch {
        currentOcrStatus = null;
      }
      if (!currentOcrStatus?.ready) {
        if (currentOcrStatus?.install_running) {
          const shouldContinue = window.confirm(buildOcrUploadWarning(currentOcrStatus));
          if (!shouldContinue) {
            return;
          }
        } else if (currentOcrStatus?.status === "provider_not_configured") {
          const shouldContinue = window.confirm(buildOcrUploadWarning(currentOcrStatus));
          if (!shouldContinue) {
            return;
          }
        } else {
          const shouldInstall = window.confirm(buildOcrInstallPrompt(currentOcrStatus));
          if (shouldInstall) {
            const installedStatus = await runOcrInstall();
            if (installedStatus?.install_running) {
              setError("OCR 安装已启动，请等待安装完成后再上传需要图片 OCR 的文档。");
              return;
            }
            if (!installedStatus?.ready) {
              const shouldContinue = window.confirm(buildOcrUploadWarning(installedStatus));
              if (!shouldContinue) {
                return;
              }
            }
          } else {
            const shouldContinue = window.confirm(buildOcrUploadWarning(currentOcrStatus));
            if (!shouldContinue) {
              return;
            }
          }
        }
      }
    }
    uploadMutation.mutate({
      spaceId: selectedSpace.id,
      file: uploadFile,
      title: uploadTitle.trim() || undefined,
    });
  }

  function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    setUploadFile(event.target.files?.[0] ?? null);
  }

  async function runOcrInstall(): Promise<KnowledgeOcrStatus | null> {
    try {
      const result = await installOcrMutation.mutateAsync();
      await queryClient.invalidateQueries({ queryKey: ["knowledge", "ocr", "status"] });
      await queryClient.invalidateQueries({ queryKey: ["background_tasks"] });
      return result.after_status;
    } catch {
      return null;
    }
  }

  async function handleInstallOcr() {
    const confirmed = window.confirm(
      "一键安装 OCR 会调用系统包管理器下载并安装 Tesseract OCR，可能修改系统 PATH 或触发系统安装提示。是否继续？"
    );
    if (!confirmed) {
      return;
    }
    await runOcrInstall();
  }

  function handleSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const query = searchQuery.trim();
    if (!query) {
      setError("请输入搜索内容");
      return;
    }
    searchMutation.mutate({
      query,
      spaceId: selectedSpace?.id ?? null,
      mode: searchMode,
      topK: 8,
    });
  }

  function handleArchiveSpace(space: KnowledgeSpace) {
    const confirmed = window.confirm(`确认归档“${space.name}”吗？归档后不会再参与挂载和检索。`);
    if (!confirmed) {
      return;
    }
    archiveSpaceMutation.mutate(space.id);
  }

  function handleDeleteDocument(document: KnowledgeDocument) {
    const confirmed = window.confirm(`确认删除“${document.title}”吗？文档 chunk 和向量索引会一起移除。`);
    if (!confirmed) {
      return;
    }
    deleteDocumentMutation.mutate(document.id);
  }

  function handleRetryDocumentProcessing(document: KnowledgeDocument) {
    const isFailed = document.status === "failed";
    const confirmed = window.confirm(
      isFailed
        ? [`确认重新处理“${document.title}”吗？`, "系统会使用已保存的原始文档重新解析并重建 chunk 和向量索引。"].join("\n")
        : [
            `确认重新处理“${document.title}”吗？`,
            "当前还不是定向重试失败图片，会重新解析整份文档并重建 chunk 和向量索引。",
          ].join("\n")
    );
    if (!confirmed) {
      return;
    }
    retryDocumentProcessingMutation.mutate(document.id);
  }

  async function handleRetryFailedImages(document: KnowledgeDocument, options: { force?: boolean } = {}) {
    let assets = selectedDocument?.id === document.id ? imageAssets : [];
    if (document.image_asset_count > 0 && assets.length === 0) {
      try {
        assets = await queryClient.fetchQuery({
          queryKey: ["knowledge", "image-assets", document.id],
          queryFn: () => listKnowledgeImageAssets(document.id),
        });
      } catch {
        assets = [];
      }
    }
    const retryableFailedCount = retryableFailedImageAssets(assets).length;
    const warningCount = nonRetryableFailedImageAssets(assets).length || document.image_asset_warning_count;
    const failedCount = assets.filter((asset) => asset.status === "failed").length || document.image_asset_failed_count + document.image_asset_warning_count;
    if (!options.force && document.image_asset_failed_count > 0 && assets.length > 0 && retryableFailedCount === 0) {
      setError("当前没有可自动重试的失败图片；剩余失败项可能需要手动检查或重新处理整份文档。");
      await queryClient.invalidateQueries({ queryKey: ["knowledge"] });
      await queryClient.invalidateQueries({ queryKey: ["knowledge", "image-assets", document.id] });
      return;
    }
    const confirmed = window.confirm(
      options.force
        ? [
            `确认强制重试“${document.title}”中剩余的失败图片吗？`,
            warningCount > 0
              ? `本次最多会重试 ${Math.min(warningCount, 20)} 张不可自动重试的警告图片。`
              : failedCount > 0
                ? `本次最多会重试 ${Math.min(failedCount, 20)} 张仍处于失败状态的图片。`
                : "系统会检查当前失败图片。",
            "这会重新调用图片转文本模型，但仍不会重跑已成功图片或整份文档正文 chunk。",
          ].join("\n")
        : [
            `确认重试“${document.title}”中的失败图片吗？`,
            retryableFailedCount > 0 ? `本次将重试 ${retryableFailedCount} 张可自动重试的失败图片。` : "系统会检查当前可自动重试的失败图片。",
            "不会重跑已成功图片，也不会重建整份文档正文 chunk。",
          ].join("\n"),
    );
    if (!confirmed) {
      return;
    }
    retryFailedImageAssetsMutation.mutate({ documentId: document.id, onlyRetryable: !options.force });
  }

  function handleRetryImageAsset(imageAsset: KnowledgeImageAsset, options: { force?: boolean } = {}) {
    const label = imageAsset.location_label || imageAsset.asset_id;
    const confirmed = window.confirm(
      [
        options.force ? "确认强制重试这张图片吗？" : "确认重试这张图片吗？",
        label,
        options.force ? "这会重新调用图片转文本模型，适合手动处理不可自动重试的失败图片。" : "系统只会删除并重建这张图片对应的图片 chunk。",
      ].join("\n"),
    );
    if (!confirmed) {
      return;
    }
    retrySingleImageAssetMutation.mutate({ imageAssetId: imageAsset.id, onlyRetryable: !options.force });
  }

  const requestError = spacesQuery.error ?? documentsQuery.error ?? chunksQuery.error ?? imageAssetsQuery.error;
  const visibleError = error || (requestError ? errorMessage(requestError, "读取知库失败") : "");

  return (
    <section className="module-page knowledge-page">
      <PanelHeader
        actions={
          <Button
            disabled={spacesQuery.isFetching || documentsQuery.isFetching}
            onClick={() => void queryClient.invalidateQueries({ queryKey: ["knowledge"] })}
            size="sm"
            variant="secondary"
          >
            <RefreshCw aria-hidden="true" size={15} />
            刷新
          </Button>
        }
        subtitle="创建知识空间、上传资料、预览 chunk，并在进入对话前确认资料是否可检索。"
        title="Memo 知库"
      />

      {visibleError ? <div className="knowledge-error">{visibleError}</div> : null}

      <div className="knowledge-stats">
        <span>
          <strong>{stats.spaces}</strong>
          <small>空间</small>
        </span>
        <span>
          <strong>{stats.documents}</strong>
          <small>文档</small>
        </span>
        <span>
          <strong>{stats.ready}</strong>
          <small>可检索</small>
        </span>
      </div>

      <div className="knowledge-layout">
        <aside className="knowledge-panel knowledge-spaces-panel">
          <div className="knowledge-panel-title">
            <span>
              <Layers3 aria-hidden="true" size={16} />
              知识空间
            </span>
          </div>
          <form className="knowledge-create-form" onSubmit={handleCreateSpace}>
            <input
              onChange={(event) => setSpaceName(event.target.value)}
              placeholder="新建空间名称"
              value={spaceName}
            />
            <textarea
              onChange={(event) => setSpaceDescription(event.target.value)}
              placeholder="描述这个空间收纳的资料"
              rows={3}
              value={spaceDescription}
            />
            <Button disabled={createSpaceMutation.isPending} size="sm" type="submit" variant="primary">
              <FolderPlus aria-hidden="true" size={15} />
              创建空间
            </Button>
          </form>
          <div className="knowledge-space-list">
            {spacesQuery.isFetching && spaces.length === 0 ? <div className="module-loading">正在加载知库...</div> : null}
            {!spacesQuery.isFetching && spaces.length === 0 ? <EmptyState>还没有知识空间。</EmptyState> : null}
            {spaces.map((space) => (
              <button
                className={`knowledge-space-card ${selectedSpace?.id === space.id ? "selected" : ""}`}
                key={space.id}
                onClick={() => {
                  setSelectedSpaceId(space.id);
                  setSelectedDocumentId(null);
                  setSearchResults([]);
                }}
                type="button"
              >
                <span className="knowledge-space-card__main">
                  <strong>{space.name}</strong>
                  <small>{space.description || "未填写描述"}</small>
                </span>
                <span className="knowledge-space-card__meta">
                  <Badge tone={space.ready_document_count > 0 ? "success" : "neutral"}>
                    {space.ready_document_count}/{space.document_count}
                  </Badge>
                </span>
              </button>
            ))}
          </div>
        </aside>

        <section className="knowledge-panel knowledge-documents-panel">
          <div className="knowledge-panel-title">
            <span>
              <FileText aria-hidden="true" size={16} />
              文档
            </span>
            {selectedSpace ? (
              <Button
                disabled={archiveSpaceMutation.isPending}
                onClick={() => handleArchiveSpace(selectedSpace)}
                size="sm"
                variant="ghost"
              >
                <Archive aria-hidden="true" size={15} />
                归档
              </Button>
            ) : null}
          </div>

          <form className="knowledge-upload-form" onSubmit={handleUpload}>
            <input
              onChange={(event) => setUploadTitle(event.target.value)}
              placeholder="文档标题（可选）"
              value={uploadTitle}
            />
            <label className="knowledge-file-picker">
              <Upload aria-hidden="true" size={15} />
              <span>{uploadFile?.name ?? "选择 TXT / MD / DOCX / PPTX / PDF"}</span>
              <input accept=".txt,.text,.md,.markdown,.docx,.pptx,.pdf" onChange={handleFileChange} type="file" />
            </label>
            <OcrStatusLine
              isInstalling={installOcrMutation.isPending}
              isLoading={ocrStatusQuery.isFetching && !ocrStatus}
              onInstall={() => void handleInstallOcr()}
              status={ocrStatus}
            />
            <Button disabled={!selectedSpace || uploadMutation.isPending || installOcrMutation.isPending} size="sm" type="submit" variant="primary">
              上传并处理
            </Button>
          </form>

          <DocumentList
            documents={documents}
            isLoading={documentsQuery.isFetching && documents.length === 0}
            isDeleting={deleteDocumentMutation.isPending}
            isRetrying={retryDocumentProcessingMutation.isPending}
            isRetryingImages={retryFailedImageAssetsMutation.isPending}
            onDelete={handleDeleteDocument}
            onRetryFailedImages={(document, options) => void handleRetryFailedImages(document, options)}
            onRetryProcessing={handleRetryDocumentProcessing}
            onSelect={setSelectedDocumentId}
            selectedDocument={selectedDocument}
          />
        </section>

        <section className="knowledge-panel knowledge-detail-panel">
          <SearchBox
            isSearching={searchMutation.isPending}
            mode={searchMode}
            onModeChange={setSearchMode}
            onQueryChange={setSearchQuery}
            onSubmit={handleSearch}
            query={searchQuery}
          />
          {searchResults.length > 0 ? (
            <SearchResults results={searchResults} />
          ) : (
            <DocumentDetail
              chunks={chunks}
              document={selectedDocument}
              imageAssets={imageAssets}
              isLoading={chunksQuery.isFetching}
              isLoadingImageAssets={imageAssetsQuery.isFetching}
              isRetryingImageAsset={retrySingleImageAssetMutation.isPending}
              isRetryingImages={retryFailedImageAssetsMutation.isPending}
              onRetryFailedImages={(document, options) => void handleRetryFailedImages(document, options)}
              onRetryImageAsset={handleRetryImageAsset}
            />
          )}
        </section>
      </div>
    </section>
  );
}

function OcrStatusLine({
  isInstalling,
  isLoading,
  onInstall,
  status,
}: {
  isInstalling: boolean;
  isLoading: boolean;
  onInstall: () => void;
  status: KnowledgeOcrStatus | null;
}) {
  if (isLoading) {
    return <div className="knowledge-ocr-status muted">正在检测图片转文本能力...</div>;
  }
  if (!status) {
    return (
      <div className="knowledge-ocr-status warning">
        <span>未完成图片转文本状态检测。</span>
        <Button disabled={isInstalling} onClick={onInstall} size="sm" variant="secondary">
          {isInstalling ? "安装中..." : "一键安装 OCR"}
        </Button>
      </div>
    );
  }
  if (status.ready) {
    return (
      <div className="knowledge-ocr-status ready">
        <span>{status.message || (status.available_languages.length > 0 ? `OCR 可用：${status.available_languages.join(", ")}` : "图片转文本可用")}</span>
      </div>
    );
  }
  if (status.install_running) {
    const taskLabel = status.install_task_ids.length > 0 ? `后台任务 ${status.install_task_ids[0]}` : "后台任务";
    return (
      <div className="knowledge-ocr-status muted">
        <span>OCR 安装正在运行：{taskLabel}，可在后台任务面板查看进度。</span>
        <Button disabled size="sm" variant="secondary">
          安装中...
        </Button>
      </div>
    );
  }
  return (
    <div className="knowledge-ocr-status warning">
      <span>{status.message}</span>
      {status.status === "provider_not_configured" ? null : (
        <Button disabled={isInstalling} onClick={onInstall} size="sm" variant="secondary">
          {isInstalling ? "安装中..." : status.status === "missing_languages" ? "一键安装语言包" : "一键安装 OCR"}
        </Button>
      )}
    </div>
  );
}

function DocumentList({
  documents,
  isLoading,
  isDeleting,
  isRetrying,
  isRetryingImages,
  onDelete,
  onRetryFailedImages,
  onRetryProcessing,
  onSelect,
  selectedDocument,
}: {
  documents: KnowledgeDocument[];
  isLoading: boolean;
  isDeleting: boolean;
  isRetrying: boolean;
  isRetryingImages: boolean;
  onDelete: (document: KnowledgeDocument) => void;
  onRetryFailedImages: (document: KnowledgeDocument, options?: { force?: boolean }) => void;
  onRetryProcessing: (document: KnowledgeDocument) => void;
  onSelect: (documentId: number) => void;
  selectedDocument: KnowledgeDocument | null;
}) {
  const [openMenuDocumentId, setOpenMenuDocumentId] = useState<number | null>(null);

  if (isLoading) {
    return <div className="module-loading">正在加载文档...</div>;
  }
  if (documents.length === 0) {
    return <EmptyState>这个空间还没有文档。</EmptyState>;
  }
  return (
    <div className="knowledge-document-list">
      {documents.map((document, index) => (
        <article
          className={`knowledge-document-card ${selectedDocument?.id === document.id ? "selected" : ""}${index === 0 ? " menu-opens-down" : ""}`}
          key={document.id}
        >
          <button
            className="knowledge-document-card__select"
            onClick={() => {
              setOpenMenuDocumentId(null);
              onSelect(document.id);
            }}
            type="button"
          >
            <span className="knowledge-document-card__title">{document.title}</span>
            <span className="knowledge-document-card__file">{document.original_filename ?? document.source_type}</span>
            <span className="knowledge-document-card__footer">
              <StatusBadge status={document.status} />
              <small>{document.chunk_count} chunks</small>
            </span>
            {document.image_asset_count > 0 ? (
              <span className="knowledge-document-card__image-progress">
                <ImageIcon aria-hidden="true" size={14} />
                <small>
                  图片 {document.image_asset_processed_count}/{document.image_asset_count}
                  {document.image_text_chunk_count > 0 ? ` · ${document.image_text_chunk_count} chunks` : ""}
                  {document.image_asset_failed_count > 0 ? ` · 失败 ${document.image_asset_failed_count}` : ""}
                  {document.image_asset_warning_count > 0 ? ` · 警告 ${document.image_asset_warning_count}` : ""}
                </small>
              </span>
            ) : null}
            {document.error_message ? <span className="knowledge-document-card__error">{document.error_message}</span> : null}
          </button>
          {document.status === "failed" || document.status === "ready" ? (
            <span className="knowledge-document-card__actions">
              <span className="knowledge-document-menu">
                <button
                  aria-expanded={openMenuDocumentId === document.id}
                  aria-label="文档操作"
                  className="knowledge-document-menu__trigger"
                  onClick={(event) => {
                    event.stopPropagation();
                    setOpenMenuDocumentId((current) => (current === document.id ? null : document.id));
                  }}
                  type="button"
                >
                  <MoreHorizontal aria-hidden="true" size={16} />
                </button>
                {openMenuDocumentId === document.id ? (
                  <span className="knowledge-document-menu__popover">
                    {document.image_asset_failed_count > 0 ? (
                      <button
                        className="knowledge-inline-action"
                        disabled={isRetryingImages}
                        onClick={(event) => {
                          event.stopPropagation();
                          if (isRetryingImages) {
                            return;
                          }
                          setOpenMenuDocumentId(null);
                          onRetryFailedImages(document);
                        }}
                        type="button"
                      >
                        <RefreshCw aria-hidden="true" size={14} />
                        重试失败图片
                      </button>
                    ) : null}
                    {document.image_asset_warning_count > 0 ? (
                      <button
                        className="knowledge-inline-action"
                        disabled={isRetryingImages}
                        onClick={(event) => {
                          event.stopPropagation();
                          if (isRetryingImages) {
                            return;
                          }
                          setOpenMenuDocumentId(null);
                          onRetryFailedImages(document, { force: true });
                        }}
                        type="button"
                      >
                        <RefreshCw aria-hidden="true" size={14} />
                        强制重试警告图片
                      </button>
                    ) : null}
                    {document.status === "failed" || document.image_asset_failed_count > 0 || document.image_asset_warning_count > 0 ? (
                      <button
                        className="knowledge-inline-action"
                        disabled={isRetrying}
                        onClick={(event) => {
                          event.stopPropagation();
                          if (isRetrying) {
                            return;
                          }
                          setOpenMenuDocumentId(null);
                          onRetryProcessing(document);
                        }}
                        type="button"
                      >
                        <RefreshCw aria-hidden="true" size={14} />
                        {document.status === "failed" ? "重新处理" : "重建索引"}
                      </button>
                    ) : null}
                    <button
                      className="knowledge-inline-action danger"
                      onClick={(event) => {
                        event.stopPropagation();
                        if (isDeleting) {
                          return;
                        }
                        setOpenMenuDocumentId(null);
                        onDelete(document);
                      }}
                      disabled={isDeleting}
                      type="button"
                    >
                      <Trash2 aria-hidden="true" size={14} />
                      删除
                    </button>
                  </span>
                ) : null}
              </span>
            </span>
          ) : null}
        </article>
      ))}
    </div>
  );
}

function DocumentDetail({
  chunks,
  document,
  imageAssets,
  isLoading,
  isLoadingImageAssets,
  isRetryingImageAsset,
  isRetryingImages,
  onRetryFailedImages,
  onRetryImageAsset,
}: {
  chunks: KnowledgeChunk[];
  document: KnowledgeDocument | null;
  imageAssets: KnowledgeImageAsset[];
  isLoading: boolean;
  isLoadingImageAssets: boolean;
  isRetryingImageAsset: boolean;
  isRetryingImages: boolean;
  onRetryFailedImages: (document: KnowledgeDocument, options?: { force?: boolean }) => void;
  onRetryImageAsset: (imageAsset: KnowledgeImageAsset, options?: { force?: boolean }) => void;
}) {
  const [chunkKindFilter, setChunkKindFilter] = useState<ChunkKind | "all">("all");
  if (!document) {
    return <EmptyState>选择一个文档查看 chunk 预览。</EmptyState>;
  }
  const visibleChunks = chunks.filter((chunk) => {
    if (chunkKindFilter === "all") {
      return true;
    }
    return getChunkKind(chunk) === chunkKindFilter;
  });
  return (
    <div className="knowledge-detail">
      <div className="knowledge-detail-hero">
        <div>
          <span className="knowledge-kicker">Document</span>
          <h2>{document.title}</h2>
          <p>{document.original_filename ?? document.source_type}</p>
        </div>
        <StatusBadge status={document.status} />
      </div>
      <div className="knowledge-meta-grid">
        <span>
          <strong>{document.chunk_count}</strong>
          <small>chunks</small>
        </span>
        <span>
          <strong>{document.text_chunk_count}</strong>
          <small>text chunks</small>
        </span>
        <span>
          <strong>{document.image_asset_count ? `${document.image_asset_processed_count}/${document.image_asset_count}` : "0"}</strong>
          <small>{document.image_asset_warning_count > 0 ? `images · 警告 ${document.image_asset_warning_count}` : "images"}</small>
        </span>
        <span>
          <strong>{document.image_text_chunk_count}</strong>
          <small>image chunks</small>
        </span>
        <span>
          <strong>{document.token_count}</strong>
          <small>tokens</small>
        </span>
        <span>
          <strong>{document.parser ?? "-"}</strong>
          <small>parser</small>
        </span>
      </div>
      <ImageAssetPanel
        document={document}
        imageAssets={imageAssets}
        isLoading={isLoadingImageAssets}
        isRetryingImageAsset={isRetryingImageAsset}
        isRetryingImages={isRetryingImages}
        onRetryFailedImages={onRetryFailedImages}
        onRetryImageAsset={onRetryImageAsset}
      />
      <div className="knowledge-chunk-filter" aria-label="Chunk 来源筛选">
        {(["all", "text", "table", "image"] as const).map((kind) => (
          <button
            className={chunkKindFilter === kind ? "selected" : ""}
            key={kind}
            onClick={() => setChunkKindFilter(kind)}
            type="button"
          >
            {chunkKindLabel(kind)}
          </button>
        ))}
      </div>
      <div className="knowledge-chunk-list">
        {isLoading ? <div className="module-loading">正在读取 chunks...</div> : null}
        {!isLoading && chunks.length === 0 ? <EmptyState>文档处理完成后会在这里显示 chunk。</EmptyState> : null}
        {!isLoading && chunks.length > 0 && visibleChunks.length === 0 ? <EmptyState>没有这个来源类型的 chunk。</EmptyState> : null}
        {visibleChunks.map((chunk) => (
          <article className="knowledge-chunk-card" key={chunk.id}>
            <header>
              <span>
                <Badge tone={chunk.embedding_status === "completed" ? "success" : "warning"}>#{chunk.chunk_index}</Badge>
                <Badge tone={getChunkKind(chunk) === "image" ? "info" : "neutral"}>{chunkKindLabel(getChunkKind(chunk))}</Badge>
              </span>
              <small>{chunk.token_count} tokens</small>
            </header>
            <p>{chunk.text}</p>
            {chunk.heading_path ? <small>{chunk.heading_path}</small> : null}
          </article>
        ))}
      </div>
    </div>
  );
}

function ImageAssetPanel({
  document,
  imageAssets,
  isLoading,
  isRetryingImageAsset,
  isRetryingImages,
  onRetryFailedImages,
  onRetryImageAsset,
}: {
  document: KnowledgeDocument;
  imageAssets: KnowledgeImageAsset[];
  isLoading: boolean;
  isRetryingImageAsset: boolean;
  isRetryingImages: boolean;
  onRetryFailedImages: (document: KnowledgeDocument, options?: { force?: boolean }) => void;
  onRetryImageAsset: (imageAsset: KnowledgeImageAsset, options?: { force?: boolean }) => void;
}) {
  if (document.image_asset_count <= 0) {
    return null;
  }
  const failedAssets = imageAssets.filter((asset) => asset.status === "failed");
  const retryableFailedAssets = retryableFailedImageAssets(imageAssets);
  const nonRetryableFailedAssets = nonRetryableFailedImageAssets(imageAssets);
  return (
    <section className="knowledge-image-assets">
      <header className="knowledge-image-assets__header">
        <span>
          <ImageIcon aria-hidden="true" size={15} />
          图片明细
        </span>
        {retryableFailedAssets.length > 0 ? (
          <Button disabled={isRetryingImages} onClick={() => onRetryFailedImages(document)} size="sm" variant="secondary">
            <RefreshCw aria-hidden="true" size={14} />
            重试失败图片 · {retryableFailedAssets.length}
          </Button>
        ) : null}
        {nonRetryableFailedAssets.length > 0 ? (
          <Button disabled={isRetryingImages} onClick={() => onRetryFailedImages(document, { force: true })} size="sm" variant="secondary">
            <RefreshCw aria-hidden="true" size={14} />
            强制重试 · {nonRetryableFailedAssets.length}
          </Button>
        ) : null}
      </header>
      {isLoading && imageAssets.length === 0 ? <div className="module-loading">正在读取图片明细...</div> : null}
      {!isLoading && imageAssets.length === 0 ? <EmptyState>图片明细会在文档处理后显示。</EmptyState> : null}
      {retryableFailedAssets.length > 0 ? (
        <div className="knowledge-image-assets__notice">可重试失败图片只会定向重试，不会重跑已成功图片或整份文档正文。</div>
      ) : null}
      {failedAssets.length > 0 && retryableFailedAssets.length === 0 ? (
        <div className="knowledge-image-assets__notice muted">当前没有可自动重试的失败图片；剩余项已标为警告，通常是空图、低质量、接口参数不合法或模型未返回可检索文本。</div>
      ) : null}
      {nonRetryableFailedAssets.length > 0 && retryableFailedAssets.length > 0 ? (
        <div className="knowledge-image-assets__notice muted">另有 {nonRetryableFailedAssets.length} 张图片已标为警告，不计入可自动重试失败数。</div>
      ) : null}
      {imageAssets.length > 0 ? (
        <div className="knowledge-image-assets__list">
          {imageAssets.map((asset) => (
            <article className={`knowledge-image-asset-row ${imageAssetRowClass(asset)}`} key={asset.id}>
              <div className="knowledge-image-asset-row__main">
                <strong>{imageAssetLabel(asset)}</strong>
                <small>{imageAssetMeta(asset)}</small>
                {asset.error_message ? <span>{asset.error_code ? `${asset.error_code}: ` : ""}{asset.error_message}</span> : null}
              </div>
              <div className="knowledge-image-asset-row__side">
                <Badge tone={imageAssetStatusTone(asset)}>{imageAssetStatusLabel(asset)}</Badge>
                <small>{asset.chunk_ids.length} chunk · {asset.attempt_count} 次</small>
                {asset.status === "failed" || asset.status === "skipped" ? (
                  <button
                    className="knowledge-inline-action"
                    disabled={isRetryingImageAsset}
                    onClick={() => onRetryImageAsset(asset, { force: asset.status === "failed" && !asset.retryable })}
                    title={asset.status === "failed" && !asset.retryable ? "这张图片不是可自动重试错误，点击后会强制重新调用图片转文本模型" : undefined}
                    type="button"
                  >
                    <RefreshCw aria-hidden="true" size={14} />
                    {asset.status === "failed" && !asset.retryable ? "强制重试" : "单张重试"}
                  </button>
                ) : null}
              </div>
            </article>
          ))}
        </div>
      ) : null}
    </section>
  );
}

function imageAssetLabel(asset: KnowledgeImageAsset): string {
  if (asset.location_label) {
    return asset.location_label;
  }
  if (asset.page_number) {
    return `第 ${asset.page_number} 页图片`;
  }
  return asset.asset_id;
}

function imageAssetMeta(asset: KnowledgeImageAsset): string {
  const parts = [
    asset.parser || "parser",
    asset.mime_type ?? "unknown",
    asset.width && asset.height ? `${Math.round(asset.width)}x${Math.round(asset.height)}` : "",
    asset.byte_size > 0 ? formatBytes(asset.byte_size) : "",
  ].filter(Boolean);
  return parts.join(" · ");
}

function imageAssetRowClass(asset: KnowledgeImageAsset): string {
  if (asset.status === "failed" && !asset.retryable) {
    return "warning";
  }
  return asset.status;
}

function imageAssetStatusTone(asset: KnowledgeImageAsset): "neutral" | "info" | "success" | "warning" | "danger" {
  const status = asset.status;
  if (status === "completed") {
    return "success";
  }
  if (status === "failed") {
    return asset.retryable ? "danger" : "warning";
  }
  if (status === "pending" || status === "processing") {
    return "warning";
  }
  if (status === "skipped") {
    return "neutral";
  }
  return "info";
}

function imageAssetStatusLabel(asset: KnowledgeImageAsset): string {
  const status = asset.status;
  if (status === "failed" && !asset.retryable) {
    return "警告";
  }
  const labels: Record<string, string> = {
    completed: "完成",
    failed: "失败",
    pending: "待处理",
    processing: "处理中",
    skipped: "跳过",
    stale: "过期",
  };
  return labels[status] ?? status;
}

function retryableFailedImageAssets(assets: KnowledgeImageAsset[]): KnowledgeImageAsset[] {
  return assets.filter((asset) => asset.status === "failed" && asset.retryable);
}

function nonRetryableFailedImageAssets(assets: KnowledgeImageAsset[]): KnowledgeImageAsset[] {
  return assets.filter((asset) => asset.status === "failed" && !asset.retryable);
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

type ChunkKind = "text" | "table" | "image";

function getChunkKind(chunk: KnowledgeChunk): ChunkKind {
  const metadata = parseChunkMetadata(chunk.metadata_json);
  const modalities = arrayFrom(metadata.source_modalities);
  const blockTypes = arrayFrom(metadata.block_types);
  if (modalities.some((item) => item.startsWith("image")) || blockTypes.includes("image")) {
    return "image";
  }
  if (modalities.includes("table") || blockTypes.includes("table")) {
    return "table";
  }
  return "text";
}

function chunkKindLabel(kind: ChunkKind | "all") {
  const labels: Record<ChunkKind | "all", string> = {
    all: "全部",
    text: "正文",
    table: "表格",
    image: "图片",
  };
  return labels[kind];
}

function parseChunkMetadata(value: string | null): Record<string, unknown> {
  if (!value) {
    return {};
  }
  try {
    const parsed = JSON.parse(value);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed as Record<string, unknown> : {};
  } catch {
    return {};
  }
}

function arrayFrom(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.map((item) => String(item));
}

function documentMayContainImages(file: File): boolean {
  const name = file.name.toLowerCase();
  return [".pdf", ".docx", ".pptx", ".md", ".markdown"].some((suffix) => name.endsWith(suffix));
}

function buildOcrUploadWarning(status: KnowledgeOcrStatus | null): string {
  if (!status) {
    return [
      "当前无法检测图片转文本状态。",
      "如果文档中包含图片，图片内容可能无法转成可检索文本。",
      "是否仍然继续上传并处理正文内容？",
    ].join("\n");
  }
  const lines = [
    status.message,
    "如果文档中包含图片，图片内容可能无法转成可检索文本；正文、表格等可解析文本仍会继续处理。",
  ];
  if (status.status !== "provider_not_configured" && !status.tesseract_available) {
    lines.push("需要安装 Tesseract OCR，并确保 tesseract 命令在 PATH 中。");
  }
  if (status.missing_languages.length > 0) {
    lines.push(`缺少语言包：${status.missing_languages.join(", ")}`);
  }
  lines.push("是否仍然继续上传？");
  return lines.join("\n");
}

function buildOcrInstallPrompt(status: KnowledgeOcrStatus | null): string {
  const isMissingLanguages = status?.status === "missing_languages";
  const lines = [
    status?.message ?? "当前无法检测图片转文本状态。",
    isMissingLanguages ? "是否现在一键安装缺失的 OCR 语言包？" : "是否现在一键安装 Tesseract OCR？",
    isMissingLanguages
      ? "语言包会下载到应用数据目录，并通过后台任务显示下载进度。"
      : "安装会调用系统包管理器下载组件，可能修改系统 PATH 或触发系统安装提示。",
    "选择“确定”开始安装；选择“取消”后可继续选择是否仅上传正文内容。",
  ];
  if (status?.missing_languages.length) {
    lines.push(`当前缺少语言包：${status.missing_languages.join(", ")}`);
  }
  return lines.join("\n");
}

function SearchBox({
  isSearching,
  mode,
  onModeChange,
  onQueryChange,
  onSubmit,
  query,
}: {
  isSearching: boolean;
  mode: "hybrid" | "vector" | "keyword";
  onModeChange: (mode: "hybrid" | "vector" | "keyword") => void;
  onQueryChange: (query: string) => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  query: string;
}) {
  return (
    <form className="knowledge-search-box" onSubmit={onSubmit}>
      <label>
        <Search aria-hidden="true" size={16} />
        <input
          onChange={(event) => onQueryChange(event.target.value)}
          placeholder="搜索当前知识空间"
          value={query}
        />
      </label>
      <select onChange={(event) => onModeChange(event.target.value as "hybrid" | "vector" | "keyword")} value={mode}>
        <option value="hybrid">Hybrid</option>
        <option value="vector">Vector</option>
        <option value="keyword">Keyword</option>
      </select>
      <Button disabled={isSearching} size="sm" type="submit" variant="primary">
        搜索
      </Button>
    </form>
  );
}

function SearchResults({ results }: { results: KnowledgeSearchResultItem[] }) {
  return (
    <div className="knowledge-search-results">
      <div className="knowledge-section-title">搜索结果</div>
      {results.map((item) => (
        <article className="knowledge-result-card" key={item.chunk_id}>
          <header>
            <strong>{item.document_title}</strong>
            <Badge tone={item.score_source === "hybrid" ? "info" : "neutral"}>{item.score_source}</Badge>
          </header>
          <p>{item.text}</p>
          <footer>
            <span>{item.space_name}</span>
            {item.heading_path.length > 0 ? <span>{item.heading_path.join(" / ")}</span> : null}
            <span>{item.score.toFixed(3)}</span>
          </footer>
        </article>
      ))}
    </div>
  );
}
function StatusBadge({ status }: { status: string }) {
  const tone = status === "ready" ? "success" : status === "failed" ? "danger" : PROCESSING_DOCUMENT_STATUSES.has(status) ? "warning" : "neutral";
  return <Badge tone={tone}>{statusLabel(status)}</Badge>;
}

function statusLabel(status: string) {
  const labels: Record<string, string> = {
    pending: "排队",
    parsing: "解析",
    chunking: "分块",
    embedding: "向量",
    indexing: "索引",
    ready: "可检索",
    failed: "失败",
    deleted: "已删除",
  };
  return labels[status] ?? status;
}

function errorMessage(error: unknown, fallback: string): string {
  const detail = apiErrorDetail(error);
  if (detail?.message) {
    return detail.message;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return fallback;
}

function apiErrorDetail(error: unknown): { code?: string; message?: string } | null {
  if (!(error instanceof Error)) {
    return null;
  }
  try {
    const parsed = JSON.parse(error.message) as unknown;
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      return null;
    }
    const detail = (parsed as { detail?: unknown }).detail;
    if (!detail || typeof detail !== "object" || Array.isArray(detail)) {
      return null;
    }
    const record = detail as { code?: unknown; message?: unknown };
    return {
      code: typeof record.code === "string" ? record.code : undefined,
      message: typeof record.message === "string" ? record.message : undefined,
    };
  } catch {
    return null;
  }
}
