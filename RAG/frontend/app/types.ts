export type Collection = {
  id: string;
  name: string;
  description?: string | null;
  metadata: Record<string, unknown>;
};

export type DocumentItem = {
  id: string;
  collection_id: string;
  title: string;
  filename: string;
  status: string;
  error_message?: string | null;
  author?: string | null;
  project?: string | null;
  meeting_date?: string | null;
  tags: string[];
  status_message?: string | null;
  ragflow_progress?: number | null;
  chunk_count?: number | null;
  token_count?: number | null;
};

export type ImportBatch = {
  id: string;
  collection_id: string;
  source_type: string;
  source_name: string;
  status: string;
  total_items: number;
  imported_items: number;
  skipped_items: number;
  failed_items: number;
  report: Record<string, unknown>;
};

export type AdminMetrics = {
  collection_count: number;
  document_count: number;
  document_status_counts: Record<string, number>;
  failed_documents: Array<Record<string, unknown>>;
  chunk_count: number;
  answer_count: number;
  feedback_counts: Record<string, number>;
  low_evidence_answer_count: number;
  import_batch_count: number;
  audit_action_counts: Record<string, number>;
  recent_questions: Array<Record<string, unknown>>;
};

export type KnowledgeGap = {
  id: string;
  collection_id: string;
  question: string;
  reason: string;
  evidence_score: number;
  feedback?: string | null;
  citations: Citation[];
  created_at: string;
};

export type Citation = {
  chunk_id: string;
  document_id: string;
  title: string;
  title_path: string[];
  score: number;
  source_uri?: string | null;
  metadata: Record<string, unknown>;
  preview: string;
  content?: string;
  page?: string | number | null;
};

export type ChatResponse = {
  answer_id: string;
  trace_id: string;
  session_id?: string | null;
  answer: string;
  citations: Citation[];
  evidence_score: number;
  model: string;
};

export type AnswerItem = {
  id: string;
  trace_id: string;
  collection_id: string;
  user_id?: string | null;
  session_id?: string | null;
  question: string;
  answer: string;
  citations: Citation[];
  evidence_score: number;
  model: string;
  feedback?: string | null;
  created_at: string;
};

export type ChatTurn = {
  role: "user" | "assistant";
  content: string;
};

export type ChatPayload = {
  collection_id: string;
  question: string;
  session_id?: string;
  history?: ChatTurn[];
};

export type StreamEvent = {
  answer: string;
  reference?: { chunks?: Citation[]; doc_aggs?: Array<Record<string, unknown>> };
  final: boolean;
  error?: string;
};

export type CollectionQuality = {
  collection_id: string;
  document_count: number;
  ready_count: number;
  failed_count: number;
  total_chunks: number;
  avg_chunks_per_ready_document: number;
  warning_counts: Record<string, number>;
  top_terms: [string, number][];
};

export type MeetingSummary = {
  collection_id: string;
  meeting_date?: string | null;
  document_count: number;
  authors: string[];
  projects: string[];
  progress: string[];
  risks: string[];
  next_steps: string[];
  decisions: string[];
  key_points: string[];
};

export type StrategyCompareResult = {
  collection_id: string;
  results: Array<Record<string, unknown>>;
  winner?: Record<string, unknown> | null;
};

export type ModelConfig = {
  id: string;
  name: string;
  provider: string;
  model_name: string;
  base_url?: string | null;
  api_key_masked?: string | null;
  temperature: number;
  max_tokens: number;
  active: boolean;
};

export type User = {
  id: string;
  email: string;
  name: string;
  role: string;
  is_active: boolean;
};

export type TokenPair = {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
  user: User;
};
