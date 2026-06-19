"use client";

import { ChangeEvent, FormEvent, useEffect, useMemo, useState } from "react";
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
  Citation,
  Collection,
  CollectionQuality,
  DocumentItem,
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
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [name, setName] = useState("企业知识库");
  const [description, setDescription] = useState("项目资料、会议纪要、周报、制度文档和网页资料");
  const [uploadMeta, setUploadMeta] = useState({ author: "", project: "", meeting_date: "", tags: "" });
  const [urlInput, setUrlInput] = useState("");
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [selectedZip, setSelectedZip] = useState<File | null>(null);
  const [question, setQuestion] = useState("");
  const [modelForm, setModelForm] = useState({
    name: "DeepSeek Chat",
    provider: "openai_compatible",
    model_name: "deepseek-chat",
    base_url: "https://api.deepseek.com/v1",
    api_key: "",
    temperature: "0.2",
    max_tokens: "1600"
  });
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const selectedCollection = useMemo(
    () => collections.find((collection) => collection.id === selectedId),
    [collections, selectedId]
  );
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
    loadWorkspace().catch((err) => setError(err instanceof Error ? err.message : "加载知识库详情失败"));
  }, [selectedId, user]);

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
      return;
    }
    const [nextDocuments, nextQuality, nextSummary, nextBatches] = await Promise.all([
      api.listDocuments(collectionId),
      api.collectionQuality(collectionId).catch(() => null),
      api.meetingSummary(collectionId).catch(() => null),
      api.listImportBatches(collectionId).catch(() => [])
    ]);
    setDocuments(nextDocuments);
    setQuality(nextQuality);
    setSummary(nextSummary);
    setBatches(nextBatches);
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

  async function upload(event: FormEvent) {
    event.preventDefault();
    if (!selectedId || !selectedFile) return;
    const form = buildUploadForm(selectedFile);
    await runAction(async () => {
      await api.uploadDocument(selectedId, form);
      setSelectedFile(null);
      await refreshAfterIngest();
    }, "上传并索引失败");
  }

  async function ingestUrl(event: FormEvent) {
    event.preventDefault();
    if (!selectedId || !urlInput.trim()) return;
    await runAction(async () => {
      await api.ingestUrl(selectedId, {
        url: urlInput.trim(),
        author: uploadMeta.author || null,
        project: uploadMeta.project || null,
        meeting_date: uploadMeta.meeting_date || null,
        tags: parseTags(uploadMeta.tags)
      });
      setUrlInput("");
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
      await refreshAfterIngest();
    }, "ZIP 批量导入失败");
  }

  async function ask(event?: FormEvent, preset?: string) {
    event?.preventDefault();
    const text = (preset || question).trim();
    if (!selectedId || !text) return;
    setMessages((items) => [...items, { id: crypto.randomUUID(), role: "user", content: text }]);
    setQuestion("");
    await runAction(async () => {
      const response = await api.chat({ collection_id: selectedId, question: text });
      setMessages((items) => [...items, toAssistantMessage(response)]);
      await Promise.all([
        loadCollections(),
        api.knowledgeGaps(selectedId).then((items) => {
          setGaps((all) => [...all.filter((gap) => gap.collection_id !== selectedId), ...items]);
        }).catch(() => undefined)
      ]);
    }, "问答检索失败");
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
    }, "保存模型配置失败");
  }

  async function activateModel(modelId: string) {
    await runAction(async () => {
      await api.activateModel(modelId);
      setModels(await api.listModels());
    }, "切换模型失败");
  }

  async function createEvalCase(answerId: string) {
    await runAction(async () => {
      await api.answerToEvalCase(answerId);
      setGaps(await api.knowledgeGaps(selectedId));
    }, "加入评估集失败");
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
            <span>企业知识库问答工作台</span>
          </div>
        </div>

        <div className="userPanel">
          <UserRound size={16} />
          <div><strong>{user.name}</strong><span>{user.email} · {user.role}</span></div>
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
              <Database size={16} /><span>{collection.name}</span><ChevronRight size={14} aria-hidden="true" />
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
              <label><span>配置名称</span><input value={modelForm.name} onChange={(e) => setModelForm({ ...modelForm, name: e.target.value })} /></label>
              <label><span>供应商</span><input value={modelForm.provider} onChange={(e) => setModelForm({ ...modelForm, provider: e.target.value })} /></label>
              <label><span>模型名称</span><input value={modelForm.model_name} onChange={(e) => setModelForm({ ...modelForm, model_name: e.target.value })} /></label>
              <label><span>Base URL</span><input value={modelForm.base_url} onChange={(e) => setModelForm({ ...modelForm, base_url: e.target.value })} /></label>
              <label><span>API Key</span><input type="password" value={modelForm.api_key} onChange={(e) => setModelForm({ ...modelForm, api_key: e.target.value })} /></label>
              <label><span>温度</span><input value={modelForm.temperature} onChange={(e) => setModelForm({ ...modelForm, temperature: e.target.value })} /></label>
              <label><span>最大 tokens</span><input value={modelForm.max_tokens} onChange={(e) => setModelForm({ ...modelForm, max_tokens: e.target.value })} /></label>
              <button className="primaryButton" type="submit" disabled={busy}><KeyRound size={16} /> 保存并启用</button>
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
      </aside>

      <section className="chatWorkspace" aria-label="知识库问答区">
        <header className="chatHeader">
          <div><p>知识库对话</p><h1>{selectedCollection?.name || "选择一个知识库开始问答"}</h1></div>
          <div className="headerActions">
            <StatusPill icon={<ShieldCheck size={14} />} label={`可检索率 ${readyRatio}%`} />
            <StatusPill icon={<Layers3 size={14} />} label={`${quality?.total_chunks ?? 0} 个分块`} />
            <button className="iconButton" onClick={refreshAll} disabled={busy} type="button" aria-label="刷新数据"><RefreshCw size={18} /></button>
          </div>
        </header>

        {error && <div className="errorBanner" role="alert">{error}</div>}

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
                <p>{message.content}</p>
                {message.citations?.length ? <div className="citationStack">{message.citations.map((citation, index) => <CitationCard citation={citation} index={index} key={`${message.id}-${citation.chunk_id}`} />)}</div> : null}
                {message.answerId && <button className="textButton" type="button" onClick={() => createEvalCase(message.answerId!)} disabled={busy}>加入评估集</button>}
              </div>
            </article>
          ))}
          {busy && <div className="loadingLine"><Loader2 size={16} /> 正在处理...</div>}
        </div>

        <div className="quickPrompts" aria-label="快捷问题">
          {quickQuestions.map((item) => <button key={item} type="button" disabled={!selectedId || busy} onClick={() => ask(undefined, item)}>{item}</button>)}
        </div>

        <form className="composer" onSubmit={(event) => ask(event)}>
          <textarea value={question} onChange={(event) => setQuestion(event.target.value)} placeholder="输入问题，例如：本周完成了什么？风险有哪些？下一步计划是什么？" aria-label="向当前知识库提问" rows={3} />
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
            {documents.slice(0, 8).map((document) => <div className="documentRow" key={document.id}><FileText size={15} /><div><strong>{document.title}</strong><span>{document.project || "未归属项目"} · {statusLabels[document.status] || document.status}</span></div><button type="button" onClick={() => deleteDocument(document.id)} disabled={busy} aria-label={`删除 ${document.title}`}><Trash2 size={15} /></button></div>)}
          </div>
        </section>

        <section className="inspectorPanel">
          <PanelTitle icon={<AlertTriangle size={18} />} title="缺口与运营" meta="低证据回答会进入评估闭环" />
          <div className="qualityGrid"><Metric label="回答" value={metrics?.answer_count ?? 0} /><Metric label="低证据" value={metrics?.low_evidence_answer_count ?? 0} tone="warn" /><Metric label="缺口" value={selectedGaps.length} tone="warn" /></div>
          <div className="gapList">
            {selectedGaps.slice(0, 4).map((gap) => <article className="gapItem" key={gap.id}><div><strong>{gapReasonLabels[gap.reason] || gap.reason}</strong><span>{gap.evidence_score.toFixed(3)}</span></div><p>{gap.question}</p></article>)}
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
  const [email, setEmail] = useState("admin@example.com");
  const [name, setName] = useState("RAG Admin");
  const [password, setPassword] = useState("Admin@123456");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const result = mode === "login"
        ? await auth.login({ email, password })
        : await auth.register({ email, name, password });
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
        <div className="authLogo"><img src="/rag.png" alt="RAG 知识中枢 Logo" /><div><strong>RAG 知识中枢</strong><span>企业知识库问答工作台</span></div></div>
        <div><h1>{mode === "login" ? "登录系统" : "注册账号"}</h1><p>使用双 token 会话保护知识库、模型配置和导入数据。</p></div>
        <form className="authForm" onSubmit={submit}>
          {mode === "register" && <label><span>姓名</span><input value={name} onChange={(event) => setName(event.target.value)} required /></label>}
          <label><span>邮箱</span><input type="email" value={email} onChange={(event) => setEmail(event.target.value)} required /></label>
          <label><span>密码</span><input type="password" value={password} onChange={(event) => setPassword(event.target.value)} required /></label>
          <p className="authHint">密码至少 10 位，包含大小写字母、数字和特殊字符。</p>
          {error && <div className="errorBanner">{error}</div>}
          <button className="primaryButton" type="submit" disabled={busy}>{busy ? "处理中..." : mode === "login" ? "登录" : "注册并登录"}</button>
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

function parseTags(value: string) {
  return value.split(",").map((tag) => tag.trim()).filter(Boolean);
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

function Metric({ label, value, tone }: { label: string; value: number; tone?: "warn" }) {
  return <div className={`miniMetric ${tone || ""}`}><span>{label}</span><strong>{value}</strong></div>;
}

function EmptyConversation({ title, description }: { title: string; description: string }) {
  return <div className="emptyConversation"><div aria-hidden="true"><MessageSquareText size={28} /></div><h2>{title}</h2><p>{description}</p></div>;
}

function CitationCard({ citation, index }: { citation: Citation; index: number }) {
  return <article className="citationCard"><div><strong>[{index + 1}] {citation.title}</strong><span>相关度 {citation.score.toFixed(3)}</span></div><p>{citation.preview}</p>{citation.title_path.length > 0 && <small>{citation.title_path.join(" / ")}</small>}</article>;
}

function SummaryColumn({ title, items }: { title: string; items: string[] }) {
  return <article className="summaryColumn"><strong>{title}</strong>{items.length ? items.slice(0, 8).map((item) => <p key={item}>{item}</p>) : <p>暂无从文档中抽取到的内容。</p>}</article>;
}
