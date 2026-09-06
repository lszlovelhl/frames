// 帧间前端 API 封装
export const BASE = "http://127.0.0.1:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!resp.ok) {
    const body = await resp.json().catch(() => null);
    throw new Error(body?.detail || `请求失败 ${resp.status}`);
  }
  return resp.json() as Promise<T>;
}

export interface VideoCreatePayload {
  platform?: string;
  platform_video_id?: string;
  url?: string;
  title?: string;
  author_name?: string;
  tags?: string[];
  category_guess?: string;
  stats_snapshot?: Record<string, unknown>;
  subtitle_text?: string;
}

export interface VideoMedia {
  video_path: string | null;
  video_url: string | null;
  has_transcript: boolean;
  transcript_segments: number;
  frames: number;
  bpm: number | null;
  bgm_ok: boolean;
  bgm_path: string | null;
  bgm_url: string | null;
  media_status: string;
}

export interface VideoDetail {
  transcript_text: string;
  transcript_segments: Array<{ start: number; end: number; text: string }>;
  frames: FrameInfo[];
  meta: Record<string, unknown>;
  audio: Record<string, unknown>;
}

export interface FrameInfo {
  seq: number;
  time_ms: number;
  start_ms: number;
  end_ms: number;
  url: string | null;
  desc?: string;
  style?: string;
  emotion?: string;
  text_overlay?: string;
}

export interface VideoItem {
  id: string;
  platform: string;
  url: string;
  title: string;
  author_name: string | null;
  author_avatar: string | null;
  author_fans: number | null;
  author_likes: number | null;
  cover_url: string | null;
  duration_ms: number | null;
  publish_time: string | null;
  tags: string[];
  stats_snapshot: Record<string, number>;
  stats_updated_at: string | null;
  category_guess: string | null;
  created_at: string;
  subtitle_source: string;
  media?: VideoMedia | null;
  latest_analysis?: { id: string; status: string; current_layer: number; summary: Record<string, unknown> } | null;
}

export interface VideoStatsComment {
  comment_id: string;
  user_name: string;
  user_avatar: string | null;
  content: string;
  like_count: number;
  reply_count: number;
  is_top: boolean;
}

export interface VideoStatsView {
  baseline: Record<string, number>;
  baseline_at: string | null;
  latest: Record<string, number>;
  updated_at: string | null;
  diff: Record<string, number>;
  author: { name: string | null; avatar: string | null; fans: number | null; likes: number | null };
  comments: VideoStatsComment[];
  warnings: string[];
}

export interface LayerInfo {
  layer: number;
  role_view: string;
  content: Record<string, unknown>;
  model: string | null;
  segments?: SegmentInfo[];
  notes?: NoteInfo[];
  elements?: ElementInfo[];
}

export interface SegmentInfo {
  seq: number;
  type: string;
  title: string | null;
  start_ms: number;
  end_ms: number;
  hook_point: boolean;
  payoff_point: boolean;
  emotion_level: number | null;
  summary: string | null;
}

export interface NoteInfo {
  note_type: string;
  start_ms: number;
  end_ms: number;
  role_view: string;
  content: string;
  confidence: number | null;
}

export interface ElementInfo {
  id: string;
  category: string;
  name: string;
  description: string | null;
  formula: string | null;
  confidence: number | null;
  role_view: string;
  evidence: Array<{ seg?: number; quote?: string }>;
  status: string;
}

export interface ElementItem extends ElementInfo {
  analysis_id: string | null;
  source_type: string;
  usage_count?: number;
  tags: string[];
  created_at: string | null;
  updated_at: string | null;
  video?: {
    id: string;
    title: string;
    platform: string;
    author_name: string | null;
  } | null;
}

export interface ElementListResp {
  total: number;
  status_counts: Record<string, number>;
  items: ElementItem[];
}

export interface UsedElementRef {
  id: string;
  category: string;
  name: string;
  status: string;
}

export interface CreationItem {
  id: string;
  title: string;
  platform: string | null;
  intent: string | null;
  status: string;
  core_elements: Array<{ element_id: string; role?: string; order?: number }>;
  created_at: string | null;
  updated_at: string | null;
}

export interface CreationAssetView {
  id: string;
  asset_type: string;
  role_view: string;
  content: { text?: string; title?: string };
  parent_id?: string | null;
  version?: number;
  created_at: string | null;
}

export interface CreationDetail extends CreationItem {
  assets: CreationAssetView[];
}

export interface AnalysisResult {
  id: string;
  video_id: string;
  status: string;
  current_layer: number;
  summary: Record<string, unknown>;
  meta: Record<string, unknown>;
  layers: LayerInfo[];
  video?: VideoItem;
}

export type PlatformAuthStatus = "none" | "imported" | "valid" | "expired";

export interface PlatformStatus {
  platform: string;
  label: string;
  login_url: string;
  need_login: boolean;
  downloadable: boolean;
  status: PlatformAuthStatus;
  note: string;
  last_success_at: string | null;
  last_fail_at: string | null;
  updated_at: string | null;
  has_cookie: boolean;
}

export interface AiUsageAgg {
  calls: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cost_cny: number;
}

export interface AiUsageSummary {
  total: AiUsageAgg;
  today: AiUsageAgg;
  by_alias: Array<AiUsageAgg & { alias: string }>;
}

export interface AiUsageRecentItem {
  id: string;
  scene: string | null;
  provider: string | null;
  alias: string;
  model: string | null;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cost_cny: number;
  ok: boolean;
  error: string | null;
  ref_type: string | null;
  ref_id: string | null;
  created_at: string | null;
}

export interface AiModelInfo {
  id: string;
  kind: string;
  label?: string;
  provider: string;
  provider_name: string;
}

export type ProviderBalanceStatus = "ok" | "low" | "unknown" | "no_key";

export interface AiProviderCard {
  key: string;
  name: string;
  base_url: string;
  api_key_set: boolean;
  api_key_masked: string;
  models: Array<{ id: string; kind: string; label?: string }>;
  priority: number;
  enabled: boolean;
  brand_color: string | null;
  logo_url: string | null;
  topup_url: string | null;
  balance_cny: number | null;
  balance_checked_at: string | null;
  balance_manual: boolean;
  balance_warn_threshold: number;
  balance_status: ProviderBalanceStatus;
  warn?: string;
  usage: AiUsageAgg;
  today: { calls: number; cost_cny: number };
  created_at: string | null;
}

export interface ProviderListResp {
  items: AiProviderCard[];
  templates: AiProviderCard[];
}

export interface ProviderUsageDetail {
  provider: AiProviderCard;
  total: AiUsageAgg;
  today: AiUsageAgg;
  by_model: Array<{ alias: string; model: string; calls: number; total_tokens: number; cost_cny: number }>;
  recent: AiUsageRecentItem[];
}

export const api = {
  health: () => request<{ database: string }>("/api/health/db"),
  models: () => request<{ items: AiModelInfo[]; configured: boolean }>("/api/ai/models"),
  createVideo: (payload: VideoCreatePayload) => request<VideoItem & { has_subtitle?: boolean }>("/api/videos", { method: "POST", body: JSON.stringify(payload) }),
  listVideos: () => request<{ videos: VideoItem[] }>("/api/videos"),
  runAnalysis: (videoId: string, model = "pro", targetLayers = 5) =>
    request<AnalysisResult>(`/api/videos/${videoId}/analyse`, { method: "POST", body: JSON.stringify({ model, target_layers: targetLayers }) }),
  getAnalysis: (analysisId: string) => request<AnalysisResult>(`/api/analyses/${analysisId}`),
  getVideoDetail: (videoId: string) =>
    request<VideoItem & { detail: VideoDetail; stats?: VideoStatsView }>(`/api/videos/${videoId}/detail`),
  reviewElement: (elementId: string, action: "accept" | "reject" | "adjust", patch?: Partial<Pick<ElementInfo, "category" | "name" | "description" | "formula">>) =>
    request<{ id: string; status: string }>(`/api/elements/${elementId}/review`, { method: "PATCH", body: JSON.stringify({ action, patch }) }),
  listElements: (params?: { status?: string; category?: string; q?: string }) => {
    const sp = new URLSearchParams();
    if (params?.status && params.status !== "all") sp.set("status", params.status);
    if (params?.category && params.category !== "all") sp.set("category", params.category);
    if (params?.q) sp.set("q", params.q);
    const qs = sp.toString();
    return request<ElementListResp>(`/api/elements${qs ? `?${qs}` : ""}`);
  },
  listPlatforms: () => request<PlatformStatus[]>("/api/platforms"),
  savePlatformCookies: (platform: string, cookiesText: string) =>
    request<PlatformStatus>(`/api/platforms/${platform}/cookies`, { method: "POST", body: JSON.stringify({ cookies_text: cookiesText }) }),
  detectPlatform: (platform: string) => request<PlatformStatus & { hint?: string }>(`/api/platforms/${platform}/detect`, { method: "POST" }),
  clearPlatformCookies: (platform: string) =>
    request<{ ok: boolean } & PlatformStatus>(`/api/platforms/${platform}/cookies`, { method: "DELETE" }),
  platformLoginUrl: (platform: string) => request<{ platform: string; login_url: string; label: string }>(`/api/platforms/${platform}/login-url`),
  creationsChat: (messages: Array<{ role: "user" | "assistant"; content: string }>, elementIds: string[], platform?: string, intent?: string) =>
    request<{ reply: string; model?: string; used_elements: UsedElementRef[] }>("/api/creations/chat", {
      method: "POST",
      body: JSON.stringify({ messages, element_ids: elementIds, platform, intent }),
    }),
  saveCreation: (payload: { title: string; content: string; element_ids: string[]; platform?: string | null; intent?: string | null }) =>
    request<CreationItem & { assets: number }>("/api/creations", { method: "POST", body: JSON.stringify(payload) }),
  creationsContinueChat: (creationId: string, messages: Array<{ role: "user" | "assistant"; content: string }>, elementIds: string[], baseAssetId?: string | null) =>
    request<{ reply: string; model?: string; current_version: number; base_asset_id: string | null; used_elements: UsedElementRef[] }>(
      `/api/creations/${creationId}/chat`,
      { method: "POST", body: JSON.stringify({ messages, element_ids: elementIds, base_asset_id: baseAssetId ?? null }) }
    ),
  saveCreationVersion: (creationId: string, payload: { content: string; title?: string; element_ids: string[]; parent_asset_id?: string | null }) =>
    request<{ creation_id: string; title: string; asset: CreationAssetView }>(`/api/creations/${creationId}/versions`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  listCreations: () => request<{ creations: CreationItem[] }>("/api/creations"),
  getCreation: (creationId: string) => request<CreationDetail>(`/api/creations/${creationId}`),
  removeCreation: (creationId: string) => request<{ ok: boolean }>(`/api/creations/${creationId}`, { method: "DELETE" }),
  mixElements: (mode: "mix" | "vary", elementIds: string[], instruction?: string) =>
    request<{ items: ElementItem[] }>("/api/elements/mix", {
      method: "POST",
      body: JSON.stringify({ mode, element_ids: elementIds, instruction: instruction || null }),
    }),
  aiUsageSummary: () => request<AiUsageSummary>("/api/ai/usage/summary"),
  aiUsageRecent: (limit = 30) => request<{ items: AiUsageRecentItem[] }>(`/api/ai/usage/recent?limit=${limit}`),
  // ---- AI 服务商 / api-key / 余额 ----
  aiProviders: () => request<ProviderListResp>("/api/ai/providers"),
  aiProviderCreate: (payload: Partial<AiProviderCard> & { key: string; name: string; base_url: string }) =>
    request<AiProviderCard>("/api/ai/providers", { method: "POST", body: JSON.stringify(payload) }),
  aiProviderUpdate: (key: string, payload: Partial<AiProviderCard> & { key: string; name: string; base_url: string }) =>
    request<AiProviderCard>(`/api/ai/providers/${key}`, { method: "PUT", body: JSON.stringify(payload) }),
  aiProviderRemove: (key: string) => request<{ ok: boolean }>(`/api/ai/providers/${key}`, { method: "DELETE" }),
  aiProviderRefreshBalance: (key: string) =>
    request<{ ok: boolean; manual: boolean; balance_cny: number | null; checked_at?: string; status?: string; note?: string }>(
      `/api/ai/providers/${key}/refresh-balance`,
      { method: "POST" }
    ),
  aiProviderSetManualBalance: (key: string, balanceCny: number | null) =>
    request<AiProviderCard>(`/api/ai/providers/${key}/balance-manual`, {
      method: "POST",
      body: JSON.stringify({ balance_cny: balanceCny }),
    }),
  aiProviderUsage: (key: string) => request<ProviderUsageDetail>(`/api/ai/providers/${key}/usage`),
  refreshVideoStats: (videoId: string) => request<VideoStatsView>(`/api/videos/${videoId}/stats-refresh`, { method: "POST" }),
};
