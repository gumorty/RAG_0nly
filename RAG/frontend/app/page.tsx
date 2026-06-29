"use client";

import { ChangeEvent, FormEvent, KeyboardEvent, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  Archive,
  BookOpenCheck,
  Bot,
  BrainCircuit,
  ChevronRight,
  Cpu,
  Database,
  FileText,
  FileUp,
  Globe2,
  KeyRound,
  Layers3,
  Loader2,
  LogOut,
  MessageSquareText,
  PanelRightOpen,
  PlugZap,
  RefreshCw,
  Send,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  Trash2,
  UploadCloud,
  UserRound,
  Workflow
} from "lucide-react";
import { api, auth } from "./api";
import type {
  AdminMetrics,
  ChatResponse,
  ChatSession,
  ChatTurn,
  Citation,
  Collection,
  CollectionQuality,
  DocumentItem,
  EvaluationDataset,
  EvaluationRun,
  ImportBatch,
  KnowledgeGap,
  MeetingSummary,
  ModelConfig,
  User
} from "./types";

type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  citations?: Citation[];
  evidenceScore?: number;
  model?: string;
  answerId?: string;
};

const statusLabels: Record<string, string> = {
  uploaded: "已上传",
  parsing: "解析中",
  parsed: "已解析",
  indexing: "索引中",
  ready: "可检索",
  failed: "失败"
};

const pipelineSteps = [
  { title: "接入", detail: "文件、网页、ZIP 批量进入统一入口" },
  { title: "解析", detail: "抽取正文与结构化元数据" },
  { title: "清洗", detail: "规整编码、换行、空白与噪声" },
  { title: "分块", detail: "按 token 预算生成可追溯片段" },
  { title: "检索", detail: "混合检索、重排与证据扩展" },
  { title: "回答", detail: "带引用、评分和缺口沉淀" }
];

const gapReasonLabels: Record<string, string> = {
  low_evidence: "证据不足",
  missing_citations: "缺少引用",
  "feedback:negative": "用户反馈：负面",
  "feedback:incorrect": "用户反馈：不正确",
  "feedback:missing_source": "用户反馈：缺少来源"
};

const quickQuestions = [
  "这个知识库里最近的项目进展是什么？",
  "当前有哪些风险、阻塞和下一步计划？",
  "请按资料来源列出关键结论和证据。",
  "哪些问题没有足够证据，需要补充资料？"
];

const modelPresets = [
  {
    name: "阿里云百炼 Qwen",
    provider: "openai_compatible",
    model_name: "qwen3.7-plus",
    base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1",
    temperature: "0.2",
    max_tokens: "2000"
  },
  {
    name: "DeepSeek Chat",
    provider: "openai_compatible",
    model_name: "deepseek-chat",
    base_url: "https://api.deepseek.com/v1",
    temperature: "0.2",
    max_tokens: "2000"
  },
  {
    name: "OpenAI Compatible",
    provider: "openai_compatible",
    model_name: "gpt-4.1-mini",
    base_url: "https://api.openai.com/v1",
    temperature: "0.2",
    max_tokens: "2000"
  }
];

export default function HomePage() {
  const [user, setUser] = useState<User | null>(null);
  const [authReady, setAuthReady] = useState(false);
  const [collections, setCollections] = useState<Collection[]>([]);
  const [metrics, setMetrics] = useState<AdminMetrics | null>(null);
  const [gaps, setGaps] = useState<KnowledgeGap[]>([]);
  const [models, setModels] = useState<ModelConfig[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [batches, setBatches] = useState<ImportBatch[]>([]);
  const [quality, setQuality] = useState<CollectionQuality | null>(null);
  const [summary, setSummary] = useState<MeetingSummary | null>(null);
  const [evaluationDatasets, setEvaluationDatasets] = useState<EvaluationDataset[]>([]);
  const [activeEvaluationDatasetId, setActiveEvaluationDatasetId] = useState("");
  const [evaluationRun, setEvaluationRun] = useState<EvaluationRun | null>(null);
  const [evaluationBusy, setEvaluationBusy] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [activeSessionId, setActiveSessionId] = useState("");
  const [name, setName] = useState("企业知识库");
  const [description, setDescription] = useState("项目资料、会议纪要、周报、制度文档和网页资料");
  const [uploadMeta, setUploadMeta] = useState({ author: "", project: "", meeting_date: "", tags: "" });
  const [urlInput, setUrlInput] = useState("");
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [selectedZip, setSelectedZip] = useState<File | null>(null);
  const [question, setQuestion] = useState("");
  const [modelForm, setModelForm] = useState({
    name: "阿里云百炼 Qwen",
    provider: "openai_compatible",
    model_name: "qwen3.7-plus",
    base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1",
    api_key: "",
    temperature: "0.2",
    max_tokens: "1600"
  });
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const streamEndRef = useRef<HTMLDivElement | null>(null);

  const selectedCollection = useMemo(
    () => collections.find((collection) => collection.id === selectedId),
    [collections, selectedId]
  );
  const selectedCollectionName = safeDisplayText(selectedCollection?.name, "选择一个知识库开始问答");
  const activeModel = models.find((model) => model.active) || models[0];
  const selectedGaps = gaps.filter((gap) => !selectedId || gap.collection_id === selectedId);
  const readyRatio = quality && quality.document_count > 0
    ? Math.round((quality.ready_count / quality.document_count) * 100)
    : 0;

  useEffect(() => {
    if (!auth.getAccessToken()) {
      setAuthReady(true);
      setLoading(false);
      return;
    }
    auth.me()
      .then((nextUser) => {
        setUser(nextUser);
        return loadCollections();
      })
      .catch(() => auth.clearTokens())
      .finally(() => {
        setAuthReady(true);
        setLoading(false);
      });
  }, []);

  useEffect(() => {
    if (!user) return;
    setMessages([]);
    setSessions([]);
    setActiveSessionId("");
    loadSelectedCollection(selectedId).catch((err) => setError(err instanceof Error ? err.message : "加载知识库详情失败"));
  }, [selectedId, user]);

  useEffect(() => {
    streamEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, busy]);

  useEffect(() => {
    if (!user || !selectedId) return;
    const hasActiveDocuments = documents.some((document) => ["uploaded", "parsing"].includes(document.status));
    if (!hasActiveDocuments) return;
    const timer = window.setInterval(() => {
      loadWorkspace(selectedId).catch(() => undefined);
    }, 3000);
    return () => window.clearInterval(timer);
  }, [documents, selectedId, user]);

  async function loadCollections() {
    const [items, nextMetrics, nextGaps, nextModels] = await Promise.all([
      api.listCollections(),
      api.adminMetrics().catch(() => null),
      api.knowledgeGaps().catch(() => []),
      api.listModels().catch(() => [])
    ]);
    setCollections(items);
    setMetrics(nextMetrics);
    setGaps(nextGaps);
    setModels(nextModels);
    if (!selectedId && items[0]) setSelectedId(items[0].id);
  }

  async function loadWorkspace(collectionId = selectedId) {
    if (!collectionId) {
      setDocuments([]);
      setBatches([]);
      setQuality(null);
      setSummary(null);
      setEvaluationDatasets([]);
      setActiveEvaluationDatasetId("");
      setEvaluationRun(null);
      return;
    }
    const [nextDocuments, nextQuality, nextSummary, nextBatches, nextEvaluationDatasets] = await Promise.all([
      api.listDocuments(collectionId),
      api.collectionQuality(collectionId).catch(() => null),
      api.meetingSummary(collectionId).catch(() => null),
      api.listImportBatches(collectionId).catch(() => []),
      api.listEvaluationDatasets(collectionId).then((res) => res.items).catch(() => [])
    ]);
    setDocuments(nextDocuments);
    setQuality(nextQuality);
    setSummary(nextSummary);
    setBatches(nextBatches);
    setEvaluationDatasets(nextEvaluationDatasets);
    setActiveEvaluationDatasetId((current) => current && nextEvaluationDatasets.some((item) => item.id === current)
      ? current
      : nextEvaluationDatasets[0]?.id || "");
  }

  async function loadSelectedCollection(collectionId = selectedId) {
    if (!collectionId) {
      setMessages([]);
      setSessions([]);
      setActiveSessionId("");
      return;
    }
    await loadWorkspace(collectionId);
    const nextSessions = await api.listChatSessions(collectionId).catch(() => []);
    const active = nextSessions[0] || await api.createChatSession(collectionId, "新对话");
    setSessions(nextSessions.length ? nextSessions : [active]);
    setActiveSessionId(active.session_id);
    await loadConversation(collectionId, active.session_id);
  }

  async function loadConversation(collectionId = selectedId, sessionId = activeSessionId) {
    if (!collectionId || !sessionId) {
      setMessages([]);
      return;
    }
    const answers = await api.listAnswers(collectionId, 1000, sessionId).catch(() => []);
    const restored: ChatMessage[] = [];
    for (const answer of answers) {
      restored.push({ id: `${answer.id}-question`, role: "user", content: answer.question });
      restored.push({
        id: answer.id,
        role: "assistant",
        content: answer.answer,
        citations: answer.citations,
        evidenceScore: answer.evidence_score,
        model: answer.model,
        answerId: answer.id
      });
    }
    setMessages(restored);
  }

  async function onAuthenticated(nextUser: User) {
    setUser(nextUser);
    setLoading(true);
    await loadCollections().finally(() => setLoading(false));
  }

  async function logout() {
    await auth.logout();
    setUser(null);
    setCollections([]);
    setSelectedId("");
    setMessages([]);
  }

  async function refreshAll() {
    await runAction(async () => {
      await loadCollections();
      await loadWorkspace();
    }, "刷新失败");
  }

  async function createCollection(event: FormEvent) {
    event.preventDefault();
    await runAction(async () => {
      const created = await api.createCollection({ name: name.trim(), description: description.trim() });
      await loadCollections();
      setSelectedId(created.id);
    }, "创建知识库失败");
  }

  async function deleteCurrentCollection() {
    if (!selectedId || !selectedCollection) return;
    const ok = window.confirm(`确认删除知识库「${selectedCollection.name}」？这会删除本地文档、会话、问答记录，并同步删除 RAGFlow dataset。`);
    if (!ok) return;
    await runAction(async () => {
      await api.deleteCollection(selectedId);
      setMessages([]);
      setSessions([]);
      setActiveSessionId("");
      setSelectedId("");
      await loadCollections();
      setNotice("知识库已删除，关联会话、问答记录和索引已清理。");
    }, "删除知识库失败");
  }

  async function createNewSession() {
    if (!selectedId) return;
    await runAction(async () => {
      const session = await api.createChatSession(selectedId, "新对话");
      const nextSessions = [session, ...sessions.filter((item) => item.session_id !== session.session_id)];
      setSessions(nextSessions);
      setActiveSessionId(session.session_id);
      setMessages([]);
    }, "新建对话失败");
  }

  async function switchSession(sessionId: string) {
    if (!selectedId || sessionId === activeSessionId) return;
    setActiveSessionId(sessionId);
    setMessages([]);
    await loadConversation(selectedId, sessionId);
  }

  async function deleteCurrentSession() {
    if (!selectedId || !activeSessionId) return;
    const ok = window.confirm("确认删除当前对话？该对话中的问答历史和检索 trace 会从数据库中删除。");
    if (!ok) return;
    await runAction(async () => {
      await api.deleteChatSession(selectedId, activeSessionId);
      const remaining = sessions.filter((item) => item.session_id !== activeSessionId);
      if (remaining.length > 0) {
        setSessions(remaining);
        setActiveSessionId(remaining[0].session_id);
        await loadConversation(selectedId, remaining[0].session_id);
      } else {
        const session = await api.createChatSession(selectedId, "新对话");
        setSessions([session]);
        setActiveSessionId(session.session_id);
        setMessages([]);
      }
      setNotice("当前对话已删除，记忆和检索 trace 已清理。");
    }, "删除对话失败");
  }

  async function upload(event: FormEvent) {
    event.preventDefault();
    if (!selectedId || !selectedFile) return;
    const form = buildUploadForm(selectedFile);
    await runAction(async () => {
      const document = await api.uploadDocument(selectedId, form);
      setSelectedFile(null);
      setNotice(document.status_message || `文档 ${document.filename} 已进入 RAGFlow 解析、Embedding 和索引流程。`);
      await refreshAfterIngest();
    }, "上传并索引失败");
  }

  async function ingestUrl(event: FormEvent) {
    event.preventDefault();
    if (!selectedId || !urlInput.trim()) return;
    await runAction(async () => {
      const document = await api.ingestUrl(selectedId, {
        url: urlInput.trim(),
        author: uploadMeta.author || null,
        project: uploadMeta.project || null,
        meeting_date: uploadMeta.meeting_date || null,
        tags: parseTags(uploadMeta.tags)
      });
      setUrlInput("");
      setNotice(document.status_message || "网页资料已进入 RAGFlow 解析、Embedding 和索引流程。");
      await refreshAfterIngest();
    }, "网页导入失败");
  }

  async function uploadZip(event: FormEvent) {
    event.preventDefault();
    if (!selectedId || !selectedZip) return;
    const form = buildUploadForm(selectedZip);
    await runAction(async () => {
      await api.uploadZipBatch(selectedId, form);
      setSelectedZip(null);
      setNotice("ZIP 批量导入已提交，文档会进入 RAGFlow 解析、Embedding 和索引流程。");
      await refreshAfterIngest();
    }, "ZIP 批量导入失败");
  }

  async function ask(event?: FormEvent, preset?: string) {
    event?.preventDefault();
    const text = (preset || question).trim();
    if (!selectedId || !text) return;
    let sessionId = activeSessionId;
    if (!sessionId) {
      const session = await api.createChatSession(selectedId, "新对话");
      sessionId = session.session_id;
      setActiveSessionId(sessionId);
      setSessions((items) => [session, ...items.filter((item) => item.session_id !== session.session_id)]);
    }
    const history = toChatHistory(messages);
    const userMessage: ChatMessage = { id: crypto.randomUUID(), role: "user", content: text };
    const assistantId = crypto.randomUUID();
    setMessages((items) => [...items, userMessage]);
    setQuestion("");
    setBusy(true);
    setError("");

    try {
      const controller = api.chatStream(
        { collection_id: selectedId, question: text, session_id: sessionId, history },
        (event) => {
          if (event.error && !event.answer) {
            setMessages((items) => ensureAssistantMessage(items, assistantId, event.error || "检索链路异常，本次问题没有生成回答。"));
            setError(event.error);
          }
          if (event.answer) {
            const answerText = event.answer;
            setMessages((items) => {
              const updated = [...items];
              const last = updated[updated.length - 1];
              if (last && last.role === "assistant") {
                last.content = mergeAssistantContent(last.content, answerText);
              } else {
                updated.push({ id: assistantId, role: "assistant", content: answerText });
              }
              return updated;
            });
          }
          if (event.final && !event.answer && !event.reference?.chunks?.length && !event.error) {
            setMessages((items) => ensureAssistantMessage(items, assistantId, "本次检索结束，但没有生成可展示的回答。请查看后台日志或稍后重试。"));
          }
          if (event.final && event.reference?.chunks) {
            const ragCitations = event.reference.chunks.map(normalizeCitation);
            setMessages((items) => {
              const updated = [...items];
              const last = updated[updated.length - 1];
              if (last && last.role === "assistant") {
                last.citations = ragCitations;
                last.evidenceScore = ragCitations.length > 0
                  ? Math.max(...ragCitations.map((c: { score: number }) => c.score))
                  : 0;
              } else {
                updated.push({
                  id: assistantId,
                  role: "assistant",
                  content: event.answer || "已完成检索，但没有生成可展示的回答。",
                  citations: ragCitations,
                  evidenceScore: ragCitations.length > 0 ? Math.max(...ragCitations.map((c: { score: number }) => c.score)) : 0
                });
              }
              return updated;
            });
          }
        },
        () => {
          setBusy(false);
          refreshAfterChat();
        },
        (errMsg) => {
          setMessages((items) => {
            const updated = [...items];
            const last = updated[updated.length - 1];
            if (last && last.role === "assistant") {
              last.content = errMsg;
            } else {
              updated.push({ id: assistantId, role: "assistant", content: errMsg });
            }
            return updated;
          });
          setBusy(false);
        }
      );
      // Store controller for abort
      (window as unknown as Record<string, unknown>).__chatAbort = controller;
    } catch {
      // fallback to non-streaming
      try {
        const response = await api.chat({ collection_id: selectedId, question: text, session_id: sessionId, history });
        setMessages((items) => [...items, toAssistantMessage(response)]);
        await refreshAfterChat();
      } catch (err) {
        setError(err instanceof Error ? err.message : "问答检索失败");
      } finally {
        setBusy(false);
      }
    }
  }

  async function refreshAfterChat() {
    await Promise.all([
      loadCollections(),
      loadWorkspace(),
      selectedId && activeSessionId ? loadConversation(selectedId, activeSessionId) : Promise.resolve(),
      api.knowledgeGaps(selectedId).then((items) => {
        setGaps((all) => [...all.filter((gap) => gap.collection_id !== selectedId), ...items]);
      }).catch(() => undefined)
    ]);
  }

  async function createModel(event: FormEvent) {
    event.preventDefault();
    await runAction(async () => {
      await api.createModel({
        name: modelForm.name.trim(),
        provider: modelForm.provider.trim(),
        model_name: modelForm.model_name.trim(),
        base_url: modelForm.base_url.trim(),
        api_key: modelForm.api_key,
        temperature: Number(modelForm.temperature || 0.2),
        max_tokens: Number(modelForm.max_tokens || 1600),
        active: true
      });
      setModels(await api.listModels());
      setModelForm({ ...modelForm, api_key: "" });
      setNotice("模型配置已保存并启用。后续问答会使用新的 Chat 模型，RAGFlow 继续负责解析、向量化和检索。");
    }, "保存模型配置失败");
  }

  async function activateModel(modelId: string) {
    await runAction(async () => {
      await api.activateModel(modelId);
      setModels(await api.listModels());
      setNotice("模型路由已切换。下一次问答会使用新的 Chat 模型。");
    }, "切换模型失败");
  }

  async function createEvalCase(answerId: string) {
    await runAction(async () => {
      await api.answerToEvalCase(answerId);
      setGaps(await api.knowledgeGaps(selectedId));
    }, "加入评估集失败");
  }

  async function createDefaultEvaluationDataset() {
    if (!selectedId) return;
    await runAction(async () => {
      const dataset = await api.createEvaluationDataset({
        collection_id: selectedId,
        name: `${selectedCollectionName} 测评集`,
        description: "用于检验召回、排序、引用可读率和低证据失败样例。"
      });
      setEvaluationDatasets((items) => [dataset, ...items.filter((item) => item.id !== dataset.id)]);
      setActiveEvaluationDatasetId(dataset.id);
      setNotice("测评集已创建，可以继续从低证据问题沉淀用例。");
    }, "创建测评集失败");
  }

  async function addGapToEvaluation(gap: KnowledgeGap) {
    const datasetId = activeEvaluationDatasetId || evaluationDatasets[0]?.id;
    if (!datasetId) {
      setError("请先创建测评集。");
      return;
    }
    await runAction(async () => {
      await api.createEvaluationCase(datasetId, {
        question: gap.question,
        expected_chunk_ids: gap.citations?.map((citation) => citation.chunk_id).filter(Boolean) || [],
        metadata: { source: "knowledge_gap", answer_id: gap.id, reason: gap.reason }
      });
      const next = await api.listEvaluationDatasets(selectedId);
      setEvaluationDatasets(next.items);
      setNotice("失败样例已加入测评集。");
    }, "加入测评集失败");
  }

  async function runEvaluationCenter() {
    const datasetId = activeEvaluationDatasetId || evaluationDatasets[0]?.id;
    if (!datasetId) {
      setError("请先创建或选择一个测评集。");
      return;
    }
    setEvaluationBusy(true);
    setError("");
    try {
      const run = await api.runEvaluation(datasetId, { strategy: { retriever: "local_bm25_cache", top_k: 10 } });
      setEvaluationRun(run);
      setNotice("测评运行完成。");
    } catch (err) {
      setError(err instanceof Error ? err.message : "测评运行失败");
    } finally {
      setEvaluationBusy(false);
    }
  }

  async function deleteDocument(documentId: string) {
    await runAction(async () => {
      await api.deleteDocument(documentId);
      await refreshAfterIngest();
    }, "删除文档失败");
  }

  async function refreshAfterIngest() {
    await Promise.all([loadWorkspace(), loadCollections()]);
  }

  async function runAction(action: () => Promise<void>, fallback: string) {
    setBusy(true);
    setError("");
    try {
      await action();
    } catch (err) {
      setError(err instanceof Error ? err.message : fallback);
    } finally {
      setBusy(false);
    }
  }

  function buildUploadForm(file: File) {
    const form = new FormData();
    form.append("file", file);
    Object.entries(uploadMeta).forEach(([key, value]) => {
      if (value) form.append(key, value);
    });
    return form;
  }

  function applyModelPreset(preset: (typeof modelPresets)[number]) {
    setModelForm({ ...modelForm, ...preset });
  }

  function submitOnEnter(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing) return;
    event.preventDefault();
    void ask();
  }

  if (!authReady || loading) {
    return <main className="authShell"><div className="loadingLine"><Loader2 size={16} /> 正在加载...</div></main>;
  }

  if (!user) {
    return <AuthPage onAuthenticated={onAuthenticated} />;
  }

  return (
    <main className="appShell">
      <aside className="leftSidebar" aria-label="知识库导航">
        <div className="brandBlock">
          <img src="/rag.png" alt="RAG 知识中枢 Logo" className="brandLogo" />
          <div>
            <strong>RAG 知识中枢</strong>
            <span>RAGFlow 驱动的知识库问答</span>
          </div>
        </div>

        <div className="userPanel">
          <UserRound size={16} />
          <div><strong>{user.name}</strong><span>{user.username || user.email} · {user.role}</span></div>
          <button type="button" onClick={logout} aria-label="退出登录"><LogOut size={15} /></button>
        </div>

        <form className="createPanel" onSubmit={createCollection}>
          <SectionLabel>新建知识库</SectionLabel>
          <label><span>名称</span><input value={name} onChange={(event) => setName(event.target.value)} /></label>
          <label><span>说明</span><textarea value={description} onChange={(event) => setDescription(event.target.value)} rows={3} /></label>
          <button className="primaryButton" disabled={busy || !name.trim()} type="submit"><Sparkles size={16} /> 创建知识库</button>
        </form>

        <nav className="collectionList" aria-label="知识库列表">
          <SectionLabel>知识库</SectionLabel>
          {collections.length === 0 && <div className="emptyHint">暂无知识库，请先创建。</div>}
          {collections.map((collection) => (
            <button className={collection.id === selectedId ? "collectionButton active" : "collectionButton"} key={collection.id} onClick={() => setSelectedId(collection.id)} type="button">
              <Database size={16} /><span>{safeDisplayText(collection.name, "未命名知识库")}</span><ChevronRight size={14} aria-hidden="true" />
            </button>
          ))}
        </nav>

        <section className="modelSidebar">
          <SectionLabel>模型路由</SectionLabel>
          <div className="activeModel">
            <Cpu size={16} />
            <div><strong>{activeModel?.name || "Mock 兜底模型"}</strong><span>{activeModel ? `${activeModel.provider}/${activeModel.model_name}` : "未配置第三方模型"}</span></div>
          </div>
          <details>
            <summary><SlidersHorizontal size={15} /> 管理模型配置</summary>
            <form className="modelForm compact" onSubmit={createModel}>
              <div className="presetStrip">
                {modelPresets.map((preset) => <button key={preset.name} type="button" onClick={() => applyModelPreset(preset)}>{preset.name}</button>)}
              </div>
              <label><span>配置名称</span><input value={modelForm.name} onChange={(e) => setModelForm({ ...modelForm, name: e.target.value })} /></label>
              <label><span>供应商</span><input value={modelForm.provider} onChange={(e) => setModelForm({ ...modelForm, provider: e.target.value })} /></label>
              <label><span>模型名称</span><input value={modelForm.model_name} onChange={(e) => setModelForm({ ...modelForm, model_name: e.target.value })} /></label>
              <label><span>Base URL</span><input value={modelForm.base_url} onChange={(e) => setModelForm({ ...modelForm, base_url: e.target.value })} /></label>
              <label><span>API Key</span><input type="password" value={modelForm.api_key} onChange={(e) => setModelForm({ ...modelForm, api_key: e.target.value })} /></label>
              <label><span>温度</span><input value={modelForm.temperature} onChange={(e) => setModelForm({ ...modelForm, temperature: e.target.value })} /></label>
              <label><span>最大 tokens</span><input value={modelForm.max_tokens} onChange={(e) => setModelForm({ ...modelForm, max_tokens: e.target.value })} /></label>
              <button className="primaryButton" type="submit" disabled={busy}><KeyRound size={16} /> 保存并启用</button>
              <p className="modelHint">切换只影响 Chat 生成模型；文档解析、Embedding、索引和检索继续由 RAGFlow 负责。</p>
            </form>
            <div className="modelList">
              {models.map((model) => (
                <button className={model.active ? "modelItem active" : "modelItem"} key={model.id} onClick={() => activateModel(model.id)} disabled={busy || model.active} type="button">
                  <PlugZap size={15} /><span>{model.name}</span><strong>{model.provider}/{model.model_name}</strong>
                </button>
              ))}
            </div>
          </details>
        </section>

        <section className="inspectorPanel">
          <PanelTitle icon={<BrainCircuit size={18} />} title="测评中心" meta="召回、排序、引用质量回归" />
          <div className="evalToolbar">
            <select
              value={activeEvaluationDatasetId}
              onChange={(event) => setActiveEvaluationDatasetId(event.target.value)}
              disabled={!evaluationDatasets.length || evaluationBusy}
            >
              {evaluationDatasets.length === 0 && <option value="">暂无测评集</option>}
              {evaluationDatasets.map((dataset) => (
                <option key={dataset.id} value={dataset.id}>{dataset.name} · {dataset.case_count} 例</option>
              ))}
            </select>
            <button type="button" onClick={createDefaultEvaluationDataset} disabled={!selectedId || evaluationBusy}>创建</button>
            <button type="button" onClick={runEvaluationCenter} disabled={!activeEvaluationDatasetId || evaluationBusy}>
              {evaluationBusy ? "运行中..." : "运行"}
            </button>
          </div>
          {evaluationRun ? (
            <>
              <div className="qualityGrid">
                <Metric label="Recall" value={formatMetric(evaluationRun.metrics.recall_at_k)} />
                <Metric label="MRR" value={formatMetric(evaluationRun.metrics.mrr)} />
                <Metric label="NDCG" value={formatMetric(evaluationRun.metrics.ndcg_at_k)} />
                <Metric label="引用可读" value={formatMetric(evaluationRun.metrics.citation_readability)} />
              </div>
              <div className="evalFailureList">
                {evaluationRun.results.filter((item) => !item.passed).slice(0, 4).map((item) => (
                  <article className="gapItem" key={item.id}>
                    <div><strong>失败样例</strong><span>{formatMetric(item.metrics?.recall_at_k)}</span></div>
                    <p>{item.question}</p>
                    {item.error_message && <small>{item.error_message}</small>}
                  </article>
                ))}
                {evaluationRun.results.length > 0 && evaluationRun.results.every((item) => item.passed) && <div className="emptyHint">本轮测评未发现失败样例。</div>}
              </div>
            </>
          ) : (
            <div className="emptyHint">选择测评集后运行，查看 recall/MRR/NDCG/faithfulness/引用可读率。</div>
          )}
          {selectedGaps.length > 0 && (
            <button className="textButton" type="button" onClick={() => addGapToEvaluation(selectedGaps[0])} disabled={!activeEvaluationDatasetId || evaluationBusy}>
              将最新低证据问题加入测评集
            </button>
          )}
        </section>
      </aside>

      <section className="chatWorkspace" aria-label="知识库问答区">
        <header className="chatHeader">
          <div><p>知识库对话</p><h1>{selectedCollectionName}</h1></div>
          <div className="headerActions">
            <StatusPill icon={<ShieldCheck size={14} />} label={`可检索率 ${readyRatio}%`} />
            <StatusPill icon={<Layers3 size={14} />} label={`${quality?.total_chunks ?? 0} 个分块`} />
            <button className="iconButton" onClick={createNewSession} disabled={busy || !selectedId} type="button" aria-label="新建对话"><MessageSquareText size={18} /></button>
            <button className="iconButton danger" onClick={deleteCurrentSession} disabled={busy || !activeSessionId} type="button" aria-label="删除当前对话"><Trash2 size={18} /></button>
            <button className="iconButton danger" onClick={deleteCurrentCollection} disabled={busy || !selectedId} type="button" aria-label="删除当前知识库"><Database size={18} /></button>
            <button className="iconButton" onClick={refreshAll} disabled={busy} type="button" aria-label="刷新数据"><RefreshCw size={18} /></button>
          </div>
        </header>

        {error && <div className="errorBanner" role="alert">{error}</div>}
        {notice && <div className="noticeBanner" role="status"><ShieldCheck size={16} /><span>{notice}</span><button type="button" onClick={() => setNotice("")}>知道了</button></div>}

        <div className="sessionBar" aria-label="对话列表">
          {sessions.map((session) => (
            <button
              key={session.session_id}
              className={session.session_id === activeSessionId ? "sessionTab active" : "sessionTab"}
              type="button"
              disabled={busy}
              onClick={() => switchSession(session.session_id)}
            >
              <span>{safeDisplayText(session.title || `对话 ${session.session_id.slice(0, 6)}`, "新对话")}</span>
              <small>{session.turn_count} 轮</small>
            </button>
          ))}
          {sessions.length === 0 && <span className="emptyHint">暂无对话。</span>}
        </div>

        <div className="chatStream" aria-live="polite">
          {messages.length === 0 && (
            <EmptyConversation
              title={selectedCollection ? "开始向知识库提问" : "先创建或选择知识库"}
              description={selectedCollection ? "系统会执行混合检索、证据评分、引用溯源，并把低证据问题沉淀为知识缺口。" : "导入资料后，就可以在这里进行带证据引用的企业知识问答。"}
            />
          )}
          {messages.map((message) => (
            <article className={`message ${message.role}`} key={message.id}>
              <div className="messageAvatar" aria-hidden="true">{message.role === "user" ? <UserRound size={18} /> : <Bot size={18} />}</div>
              <div className="messageBody">
                <div className="messageMeta">
                  <strong>{message.role === "user" ? "你" : "知识库助手"}</strong>
                  {message.model && <span>{message.model}</span>}
                  {typeof message.evidenceScore === "number" && <span>证据分 {message.evidenceScore.toFixed(3)}</span>}
                </div>
                <FormattedAnswer content={message.content} />
                {message.citations?.length ? <CitationList citations={message.citations} messageId={message.id} /> : null}
                {message.answerId && <button className="textButton" type="button" onClick={() => createEvalCase(message.answerId!)} disabled={busy}>加入评估集</button>}
              </div>
            </article>
          ))}
          {busy && <div className="loadingLine"><Loader2 size={16} /> 正在处理...</div>}
          <div ref={streamEndRef} />
        </div>

        <div className="quickPrompts" aria-label="快捷问题">
          {quickQuestions.map((item) => <button key={item} type="button" disabled={!selectedId || busy} onClick={() => ask(undefined, item)}>{item}</button>)}
        </div>

        <form className="composer" onSubmit={(event) => ask(event)}>
          <textarea value={question} onKeyDown={submitOnEnter} onChange={(event) => setQuestion(event.target.value)} placeholder="输入问题，例如：本周完成了什么？风险有哪些？下一步计划是什么？" aria-label="向当前知识库提问" rows={3} />
          <button className="sendButton" disabled={busy || !selectedId || !question.trim()} type="submit" aria-label="发送问题"><Send size={18} /></button>
        </form>
      </section>

      <aside className="rightInspector" aria-label="知识库管理区">
        <section className="inspectorPanel">
          <PanelTitle icon={<UploadCloud size={18} />} title="资料导入" meta="本机文件、网页、ZIP 批量进入同一处理链路" />
          <div className="metaGrid">
            <label><span>作者</span><input value={uploadMeta.author} onChange={(e) => setUploadMeta({ ...uploadMeta, author: e.target.value })} /></label>
            <label><span>项目</span><input value={uploadMeta.project} onChange={(e) => setUploadMeta({ ...uploadMeta, project: e.target.value })} /></label>
            <label><span>会议日期</span><input value={uploadMeta.meeting_date} onChange={(e) => setUploadMeta({ ...uploadMeta, meeting_date: e.target.value })} placeholder="2026-06-17" /></label>
            <label><span>标签</span><input value={uploadMeta.tags} onChange={(e) => setUploadMeta({ ...uploadMeta, tags: e.target.value })} placeholder="用英文逗号分隔" /></label>
          </div>
          <div className="ingestTabs">
            <form className="ingestCard" onSubmit={upload}><FileUp size={18} /><strong>本地资料</strong><input type="file" onChange={(event: ChangeEvent<HTMLInputElement>) => setSelectedFile(event.target.files?.[0] || null)} /><button type="submit" disabled={busy || !selectedId || !selectedFile}>上传并索引</button></form>
            <form className="ingestCard" onSubmit={ingestUrl}><Globe2 size={18} /><strong>网页资料</strong><input value={urlInput} onChange={(event) => setUrlInput(event.target.value)} placeholder="https://example.com/page" /><button type="submit" disabled={busy || !selectedId || !urlInput.trim()}>导入网页</button></form>
            <form className="ingestCard" onSubmit={uploadZip}><Archive size={18} /><strong>批量 ZIP</strong><input type="file" accept=".zip" onChange={(event: ChangeEvent<HTMLInputElement>) => setSelectedZip(event.target.files?.[0] || null)} /><button type="submit" disabled={busy || !selectedId || !selectedZip}>批量导入</button></form>
          </div>
        </section>

        <section className="inspectorPanel">
          <PanelTitle icon={<Workflow size={18} />} title="处理流程" meta="企业级 RAG 的治理闭环" />
          <div className="pipelineList">{pipelineSteps.map((step, index) => <div className="pipelineItem" key={step.title}><span>{String(index + 1).padStart(2, "0")}</span><div><strong>{step.title}</strong><p>{step.detail}</p></div></div>)}</div>
        </section>

        <section className="inspectorPanel">
          <PanelTitle icon={<BookOpenCheck size={18} />} title="文档治理" meta={`${documents.length} 份资料`} />
          <div className="qualityGrid"><Metric label="文档" value={quality?.document_count ?? documents.length} /><Metric label="可检索" value={quality?.ready_count ?? 0} /><Metric label="失败" value={quality?.failed_count ?? 0} tone="warn" /></div>
          <div className="documentList">
            {documents.length === 0 && <div className="emptyHint">当前知识库还没有资料。</div>}
            {documents.slice(0, 8).map((document) => <DocumentRow document={document} busy={busy} onDelete={deleteDocument} key={document.id} />)}
          </div>
        </section>

        <section className="inspectorPanel">
          <PanelTitle icon={<AlertTriangle size={18} />} title="缺口与运营" meta="低证据回答会进入评估闭环" />
          <div className="qualityGrid"><Metric label="回答" value={metrics?.answer_count ?? 0} /><Metric label="低证据" value={metrics?.low_evidence_answer_count ?? 0} tone="warn" /><Metric label="缺口" value={selectedGaps.length} tone="warn" /></div>
          <div className="gapList">
            {selectedGaps.slice(0, 4).map((gap) => <article className="gapItem" key={gap.id}><div><strong>{gapReasonLabels[gap.reason] || safeDisplayText(gap.reason, "未知原因")}</strong><span>{gap.evidence_score.toFixed(3)}</span></div><p>{safeDisplayText(gap.question, "问题文本不可读")}</p></article>)}
            {selectedGaps.length === 0 && <div className="emptyHint">暂无低证据缺口。</div>}
          </div>
          {batches.length > 0 && <div className="batchLog"><strong>最近导入</strong>{batches.slice(0, 3).map((batch) => <p key={batch.id}>{batch.source_name}: 成功 {batch.imported_items}，跳过 {batch.skipped_items}，失败 {batch.failed_items}</p>)}</div>}
        </section>

        <section className="inspectorPanel">
          <PanelTitle icon={<PanelRightOpen size={18} />} title="资料摘要" meta={summary ? `${summary.document_count} 份可分析资料，全部来自文档抽取信号` : "等待文档分析"} />
          {!summary && <div className="emptyHint">暂无摘要。</div>}
          {summary && <>
            <SummaryColumn title="资料要点" items={summary.key_points || []} />
            <SummaryColumn title="进展" items={summary.progress} />
            <SummaryColumn title="风险" items={summary.risks} />
            <SummaryColumn title="下一步" items={summary.next_steps} />
            <SummaryColumn title="决策" items={summary.decisions} />
          </>}
        </section>
      </aside>
    </main>
  );
}

function AuthPage({ onAuthenticated }: { onAuthenticated: (user: User) => Promise<void> }) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [username, setUsername] = useState("");
  const [name, setName] = useState("RAG Admin");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const result = mode === "login"
        ? await auth.login({ username, password })
        : await auth.register({ username, name, password });
      await onAuthenticated(result.user);
    } catch (err) {
      setError(err instanceof Error ? err.message : "认证失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="authShell">
      <section className="authCard">
        <div className="authLogo"><img src="/rag.png" alt="RAG 知识中枢 Logo" /><div><strong>RAG 知识中枢</strong><span>RAGFlow 驱动的企业知识库</span></div></div>
        <div><h1>{mode === "login" ? "登录系统" : "注册账号"}</h1><p>使用用户名和密码登录，双 token 会话保护知识库和导入数据。</p></div>
        <form className="authForm" onSubmit={submit}>
          {mode === "register" && <label><span>姓名</span><input value={name} onChange={(event) => setName(event.target.value)} required /></label>}
          <label><span>用户名</span><input value={username} onChange={(event) => setUsername(event.target.value)} placeholder="admin" autoComplete="username" required /></label>
          <label><span>密码</span><input type="password" value={password} onChange={(event) => setPassword(event.target.value)} placeholder="请输入登录密码" required /></label>
          <p className="authHint">密码至少 10 位，包含大小写字母、数字和特殊字符。</p>
          {error && <div className="errorBanner">{error}</div>}
          <button className="primaryButton" type="submit" disabled={busy}>{busy ? "处理中..." : mode === "login" ? "登录系统" : "注册并登录"}</button>
        </form>
        <button className="textButton" type="button" onClick={() => setMode(mode === "login" ? "register" : "login")}>{mode === "login" ? "没有账号？去注册" : "已有账号？去登录"}</button>
      </section>
    </main>
  );
}

function toAssistantMessage(response: ChatResponse): ChatMessage {
  return {
    id: response.answer_id,
    role: "assistant",
    content: response.answer,
    citations: response.citations,
    evidenceScore: response.evidence_score,
    model: response.model,
    answerId: response.answer_id
  };
}

function ensureAssistantMessage(items: ChatMessage[], assistantId: string, content: string) {
  const updated = [...items];
  const last = updated[updated.length - 1];
  if (last && last.role === "assistant") {
    last.content = content;
  } else {
    updated.push({ id: assistantId, role: "assistant", content });
  }
  return updated;
}

function parseTags(value: string) {
  return value.split(",").map((tag) => tag.trim()).filter(Boolean);
}

function safeDisplayText(value: string | null | undefined, fallback: string) {
  const text = (value || "").trim();
  if (!text) return fallback;
  if (/\?{2,}/.test(text)) return fallback;
  return text;
}

function formatMetric(value: unknown) {
  if (typeof value !== "number" || Number.isNaN(value)) return "0.000";
  return value.toFixed(3);
}

function mergeAssistantContent(current: string, incoming: string) {
  const next = incoming || "";
  if (!next) return current;
  if (!current) return next;
  if (next.startsWith(current)) return next;
  if (current.endsWith(next)) return current;
  return `${current}${next}`;
}

function normalizeCitation(chunk: Citation | Record<string, unknown>): Citation {
  const item = chunk as Record<string, unknown>;
  const metadata = (item.metadata as Record<string, unknown> | undefined) || {};
  const raw = (metadata.ragflow_chunk as Record<string, unknown> | undefined) || item;
  const title = String(
    item.title
    || raw.docnm_kwd
    || raw.document_keyword
    || raw.document_name
    || raw.filename
    || "RAGFlow document"
  );
  const content = String(
    item.content
    || item.preview
    || raw.content_with_weight
    || raw.content
    || raw.text
    || ""
  ).replace(/\s+/g, " ").trim();
  const titlePath = Array.isArray(item.title_path) && item.title_path.length
    ? item.title_path.map(String)
    : [title];
  return {
    chunk_id: String(item.chunk_id || raw.chunk_id || raw.id || ""),
    document_id: String(item.document_id || raw.doc_id || raw.document_id || ""),
    title,
    title_path: titlePath,
    score: Number(item.score || raw.similarity || 0),
    source_uri: (item.source_uri as string | null | undefined) || null,
    metadata,
    preview: String(item.preview || content).slice(0, 520),
    content,
    page: item.page as string | number | null | undefined
  };
}

function citationText(citation: Citation) {
  const raw = (citation.metadata?.ragflow_chunk || {}) as Record<string, unknown>;
  const text = String(
    citation.preview
    || citation.content
    || raw.content_with_weight
    || raw.content
    || raw.text
    || ""
  ).replace(/\s+/g, " ").trim();
  return safeDisplayText(text, "未获得可读引用片段，请重新解析文档或检查 RAGFlow 返回字段。");
}

function toChatHistory(messages: ChatMessage[]): ChatTurn[] {
  return messages
    .filter((message) => message.content.trim())
    .slice(-8)
    .map((message) => ({ role: message.role, content: message.content.slice(0, 1800) }));
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return <div className="sectionLabel">{children}</div>;
}

function PanelTitle({ icon, title, meta }: { icon: React.ReactNode; title: string; meta?: string }) {
  return <div className="panelTitle"><div className="panelIcon" aria-hidden="true">{icon}</div><div><h2>{title}</h2>{meta && <span>{meta}</span>}</div></div>;
}

function StatusPill({ icon, label }: { icon: React.ReactNode; label: string }) {
  return <span className="statusPill">{icon}{label}</span>;
}

function Metric({ label, value, tone }: { label: string; value: number | string; tone?: "warn" }) {
  return <div className={`miniMetric ${tone || ""}`}><span>{label}</span><strong>{value}</strong></div>;
}

function EmptyConversation({ title, description }: { title: string; description: string }) {
  return <div className="emptyConversation"><div aria-hidden="true"><MessageSquareText size={28} /></div><h2>{title}</h2><p>{description}</p></div>;
}

function FormattedAnswer({ content }: { content: string }) {
  const blocks = content.split(/\n{2,}/).map((block) => block.trim()).filter(Boolean);
  if (!blocks.length) return <p className="answerParagraph">暂无回答内容。</p>;
  return <div className="answerBlock">{blocks.map((block, blockIndex) => renderAnswerBlock(block, blockIndex))}</div>;
}

function renderAnswerBlock(block: string, blockIndex: number) {
  const lines = block.split("\n").map((line) => line.trim()).filter(Boolean);
  if (lines.length === 1 && /^#{1,4}\s+/.test(lines[0])) {
    return <h3 className="answerHeading" key={blockIndex}>{renderInline(lines[0].replace(/^#{1,4}\s+/, ""))}</h3>;
  }
  if (lines.every((line) => /^([-*]|\d+[.)])\s+/.test(line))) {
    return <ul className="answerList" key={blockIndex}>{lines.map((line, index) => <li key={index}>{renderInline(line.replace(/^([-*]|\d+[.)])\s+/, ""))}</li>)}</ul>;
  }
  return <p className="answerParagraph" key={blockIndex}>{renderInline(lines.map((line) => line.replace(/^#{1,4}\s+/, "")).join("\n"))}</p>;
}

function renderInline(text: string) {
  return text.split(/(\*\*[^*]+\*\*)/g).map((part, index) => {
    if (part.startsWith("**") && part.endsWith("**")) return <strong key={index}>{part.slice(2, -2)}</strong>;
    return <span key={index}>{part}</span>;
  });
}

function CitationList({ citations, messageId }: { citations: Citation[]; messageId: string }) {
  const [expanded, setExpanded] = useState(false);
  const visible = expanded ? citations : citations.slice(0, 3);
  return <div className="citationStack"><div className="citationToolbar"><span>证据来源，默认显示前 {Math.min(3, citations.length)} 条</span>{citations.length > 3 && <button type="button" className="citationToggle" onClick={() => setExpanded(!expanded)}>{expanded ? "收起" : `展开全部 ${citations.length} 条`}</button>}</div>{visible.map((citation, index) => <CitationCard citation={citation} index={index} key={`${messageId}-${citation.chunk_id}-${index}`} />)}</div>;
}

function CitationCard({ citation, index }: { citation: Citation; index: number }) {
  return <article className="citationCard"><div><strong>[{index + 1}] {safeDisplayText(citation.title, "未命名来源")}</strong><span>证据匹配度 {citation.score.toFixed(3)}</span></div><p>{citationText(citation)}</p>{citation.title_path.length > 0 && <small>{citation.title_path.map((item) => safeDisplayText(item, "未命名层级")).join(" / ")}{citation.page ? ` · 位置 ${citation.page}` : ""}</small>}</article>;
}

function DocumentRow({ document, busy, onDelete }: { document: DocumentItem; busy: boolean; onDelete: (id: string) => void }) {
  const progress = Math.max(0, Math.min(100, Math.round((document.ragflow_progress || 0) * 100)));
  const active = ["uploaded", "parsing"].includes(document.status);
  return (
    <div className="documentRow">
      <FileText size={15} />
      <div>
        <strong>{safeDisplayText(document.title, "未命名文档")}</strong>
        <span>{safeDisplayText(document.project, "未归属项目")}，{statusLabels[document.status] || document.status}{document.chunk_count ? `，${document.chunk_count} 个分块` : ""}</span>
        {active && <div className="progressTrack" aria-label={`解析进度 ${progress}%`}><i style={{ width: `${progress}%` }} /></div>}
        {document.status_message && <small>{safeDisplayText(document.status_message, "RAGFlow 状态不可读，请刷新。")}</small>}
        {document.error_message && <small className="errorText">{safeDisplayText(document.error_message, "解析失败，错误信息不可读。")}</small>}
      </div>
      <button type="button" onClick={() => onDelete(document.id)} disabled={busy} aria-label={`删除 ${safeDisplayText(document.title, "未命名文档")}`}><Trash2 size={15} /></button>
    </div>
  );
}

function SummaryColumn({ title, items }: { title: string; items: string[] }) {
  return <article className="summaryColumn"><strong>{title}</strong>{items.length ? items.slice(0, 8).map((item) => <p key={item}>{safeDisplayText(item, "该条摘要不可读，请重新解析文档。")}</p>) : <p>暂无从文档中抽取到的内容。</p>}</article>;
}
