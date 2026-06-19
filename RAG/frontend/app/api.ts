const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8010";
const API_KEY = process.env.NEXT_PUBLIC_API_KEY || "change-this-admin-api-key";
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

async function request<T>(path: string, init?: RequestInit, retry = true): Promise<T> {
  const headers = new Headers(init?.headers);
  const token = getAccessToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  headers.set("X-API-Key", API_KEY);
  const response = await fetch(`${API_BASE}/api${path}`, {
    ...init,
    headers,
    cache: "no-store"
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
    headers: { "Content-Type": "application/json", "X-API-Key": API_KEY },
    body: JSON.stringify(payload),
    cache: "no-store"
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
  login: async (payload: { email: string; password: string }) => {
    const tokens = await authRequest<import("./types").TokenPair>("/auth/login", payload);
    saveTokens(tokens);
    return tokens;
  },
  register: async (payload: { email: string; name: string; password: string }) => {
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
  listDocuments: (collectionId: string) => request<import("./types").DocumentItem[]>(`/collections/${collectionId}/documents`),
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
  chat: (payload: { collection_id: string; question: string }) =>
    request<import("./types").ChatResponse>("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    })
};
