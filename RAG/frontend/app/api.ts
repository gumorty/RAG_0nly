const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:18010";
const ACCESS_TOKEN_KEY = "rag_access_token";
const REFRESH_TOKEN_KEY = "rag_refresh_token";

function getAccessToken() {
  if (typeof window === "undefined") return "";
  return window.localStorage.getItem(ACCESS_TOKEN_KEY) || "";
}

function getRefreshToken() {
  if (typeof window === "undefined") return "";
  return window.localStorage.getItem(REFRESH_TOKEN_KEY) || "";
}

function saveTokens(tokens: import("./types").TokenPair) {
  window.localStorage.setItem(ACCESS_TOKEN_KEY, tokens.access_token);
  window.localStorage.setItem(REFRESH_TOKEN_KEY, tokens.refresh_token);
}

function clearTokens() {
  if (typeof window === "undefined") return;
  window.localStorage.removeItem(ACCESS_TOKEN_KEY);
  window.localStorage.removeItem(REFRESH_TOKEN_KEY);
}

function apiConnectionError(error: unknown) {
  return new Error(
    `无法连接后端 API（${API_BASE}）。请确认 Docker 中 api 服务已启动并映射到 18010 端口。${
      error instanceof Error ? `原始错误：${error.message}` : ""
    }`
  );
}

async function request<T>(path: string, init?: RequestInit, retry = true): Promise<T> {
  const headers = new Headers(init?.headers);
  const token = getAccessToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(`${API_BASE}/api${path}`, {
    ...init,
    headers,
    cache: "no-store"
  }).catch((error) => {
    throw apiConnectionError(error);
  });
  if (response.status === 401 && retry && getRefreshToken()) {
    const refreshed = await refreshSession().catch(() => null);
    if (refreshed) return request<T>(path, init, false);
  }
  if (!response.ok) {
    const detail = await readError(response);
    throw new Error(detail || `Request failed: ${response.status}`);
  }
  return response.json() as Promise<T>;
}

async function authRequest<T>(path: string, payload: Record<string, unknown>): Promise<T> {
  const response = await fetch(`${API_BASE}/api${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    cache: "no-store"
  }).catch((error) => {
    throw apiConnectionError(error);
  });
  if (!response.ok) {
    throw new Error(await readError(response));
  }
  return response.json() as Promise<T>;
}

async function readError(response: Response) {
  const text = await response.text();
  try {
    const parsed = JSON.parse(text);
    return parsed.detail || text;
  } catch {
    return text;
  }
}

async function refreshSession() {
  const refreshToken = getRefreshToken();
  if (!refreshToken) return null;
  const tokens = await authRequest<import("./types").TokenPair>("/auth/refresh", { refresh_token: refreshToken });
  saveTokens(tokens);
  return tokens;
}

export const auth = {
  getAccessToken,
  clearTokens,
  login: async (payload: { username: string; password: string }) => {
    const tokens = await authRequest<import("./types").TokenPair>("/auth/login", payload);
    saveTokens(tokens);
    return tokens;
  },
  register: async (payload: { username: string; name?: string; password: string }) => {
    const tokens = await authRequest<import("./types").TokenPair>("/auth/register", payload);
    saveTokens(tokens);
    return tokens;
  },
  me: () => request<import("./types").User>("/users/me"),
  logout: async () => {
    try {
      await request<{ status: string }>("/auth/logout", { method: "POST" }, false);
    } finally {
      clearTokens();
    }
  }
};

export const api = {
  adminMetrics: () => request<import("./types").AdminMetrics>("/admin/metrics"),
  listModels: () => request<import("./types").ModelConfig[]>("/model-configs"),
  createModel: (payload: Record<string, unknown>) =>
    request<import("./types").ModelConfig>("/model-configs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }),
  activateModel: (modelId: string) =>
    request<import("./types").ModelConfig>(`/model-configs/${modelId}/activate`, { method: "POST" }),
  knowledgeGaps: (collectionId?: string) =>
    request<import("./types").KnowledgeGap[]>(`/knowledge-gaps${collectionId ? `?collection_id=${collectionId}` : ""}`),
  answerToEvalCase: (answerId: string) =>
    request<{ id: string; status: string }>(`/answers/${answerId}/to-eval-case`, { method: "POST" }),
  listCollections: () => request<import("./types").Collection[]>("/collections"),
  createCollection: (payload: { name: string; description?: string }) =>
    request<import("./types").Collection>("/collections", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...payload, metadata: {} })
    }),
  deleteCollection: (collectionId: string) =>
    request<{ status: string; collection_id: string }>(`/collections/${collectionId}`, { method: "DELETE" }),
  listChatSessions: (collectionId: string) =>
    request<import("./types").ChatSession[]>(`/collections/${collectionId}/sessions`),
  createChatSession: (collectionId: string, title?: string) =>
    request<import("./types").ChatSession>(`/collections/${collectionId}/sessions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title })
    }),
  deleteChatSession: (collectionId: string, sessionId: string) =>
    request<{ status: string; session_id: string }>(`/collections/${collectionId}/sessions/${encodeURIComponent(sessionId)}`, {
      method: "DELETE"
    }),
  listDocuments: (collectionId: string) => request<import("./types").DocumentItem[]>(`/collections/${collectionId}/documents`),
  listAnswers: (collectionId: string, limit = 500, sessionId?: string) =>
    request<import("./types").AnswerItem[]>(
      `/collections/${collectionId}/answers?limit=${limit}${sessionId ? `&session_id=${encodeURIComponent(sessionId)}` : ""}`
    ),
  listImportBatches: (collectionId: string) => request<import("./types").ImportBatch[]>(`/collections/${collectionId}/import-batches`),
  collectionQuality: (collectionId: string) => request<import("./types").CollectionQuality>(`/collections/${collectionId}/quality`),
  meetingSummary: (collectionId: string, meetingDate?: string) =>
    request<import("./types").MeetingSummary>(
      `/collections/${collectionId}/meeting-summary${meetingDate ? `?meeting_date=${encodeURIComponent(meetingDate)}` : ""}`
    ),
  compareStrategies: (collectionId: string, strategies: Record<string, Record<string, unknown>>) =>
    request<import("./types").StrategyCompareResult>(`/eval/${collectionId}/compare`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ strategies })
    }),
  listEvaluationDatasets: (collectionId: string) =>
    request<{ items: import("./types").EvaluationDataset[] }>(`/evaluation/datasets?collection_id=${collectionId}`),
  createEvaluationDataset: (payload: { collection_id: string; name: string; description?: string }) =>
    request<import("./types").EvaluationDataset>("/evaluation/datasets", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }),
  createEvaluationCase: (datasetId: string, payload: Record<string, unknown>) =>
    request<{ id: string }>(`/evaluation/datasets/${datasetId}/cases`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }),
  runEvaluation: (datasetId: string, payload: Record<string, unknown> = {}) =>
    request<import("./types").EvaluationRun>(`/evaluation/datasets/${datasetId}/runs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }),
  getEvaluationRun: (runId: string) => request<import("./types").EvaluationRun>(`/evaluation/runs/${runId}`),
  uploadDocument: (collectionId: string, form: FormData) =>
    request<import("./types").DocumentItem>(`/collections/${collectionId}/documents`, {
      method: "POST",
      body: form
    }),
  uploadZipBatch: (collectionId: string, form: FormData) =>
    request<import("./types").ImportBatch>(`/collections/${collectionId}/batch-zip`, {
      method: "POST",
      body: form
    }),
  ingestUrl: (collectionId: string, payload: Record<string, unknown>) =>
    request<import("./types").DocumentItem>(`/collections/${collectionId}/url-documents`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }),
  deleteDocument: (documentId: string) =>
    request<{ status: string; document_id: string }>(`/documents/${documentId}`, {
      method: "DELETE"
    }),
  chat: (payload: import("./types").ChatPayload) =>
    request<import("./types").ChatResponse>("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }),
  chatStream: function(
    payload: import("./types").ChatPayload,
    onEvent: (event: import("./types").StreamEvent) => void,
    onDone: () => void,
    onError: (error: string) => void,
  ): AbortController {
    const controller = new AbortController();
    const headers = new Headers({ "Content-Type": "application/json" });
    const token = getAccessToken();
    if (token) headers.set("Authorization", `Bearer ${token}`);

    fetch(`${API_BASE}/api/chat/stream`, {
      method: "POST",
      headers,
      body: JSON.stringify(payload),
      signal: controller.signal,
      cache: "no-store"
    }).then(async (response) => {
      if (!response.ok) {
        onError(await readError(response));
        return;
      }
      const reader = response.body?.getReader();
      if (!reader) {
        onError("无法读取响应流");
        return;
      }
      const decoder = new TextDecoder();
      let buffer = "";
      let completed = false;

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";
        for (const line of lines) {
          const trimmed = line.trim();
          if (!trimmed || trimmed === "data: [DONE]") {
            if (trimmed === "data: [DONE]" && !completed) {
              completed = true;
              onDone();
            }
            continue;
          }
          if (trimmed.startsWith("data: ")) {
            try {
              const event = JSON.parse(trimmed.slice(6));
              onEvent(event);
              if (event.final && !completed) {
                completed = true;
                onDone();
              }
            } catch {
              // Ignore malformed stream fragments.
            }
          }
        }
      }
      if (!completed) onDone();
    }).catch((err) => {
      if (err.name !== "AbortError") {
        onError(err instanceof Error ? err.message : "流式请求失败");
      }
    });

    return controller;
  }
};
