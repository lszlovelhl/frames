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
  cover_url: string | null;
  duration_ms: number | null;
  publish_time: string | null;
  tags: string[];
  stats_snapshot: Record<string, number>;
  category_guess: string | null;
  created_at: string;
  subtitle_source: string;
  media?: VideoMedia | null;
  latest_analysis?: { id: string; status: string; current_layer: number; summary: Record<string, unknown> } | null;
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

export const api = {
  health: () => request<{ database: string }>("/api/health/db"),
  models: () => request<Array<{ alias: string; model: string }>>("/api/ai/models"),
  createVideo: (payload: VideoCreatePayload) => request<VideoItem & { has_subtitle?: boolean }>("/api/videos", { method: "POST", body: JSON.stringify(payload) }),
  listVideos: () => request<{ videos: VideoItem[] }>("/api/videos"),
  runAnalysis: (videoId: string, model = "pro", targetLayers = 5) =>
    request<AnalysisResult>(`/api/videos/${videoId}/analyse`, { method: "POST", body: JSON.stringify({ model, target_layers: targetLayers }) }),
  getAnalysis: (analysisId: string) => request<AnalysisResult>(`/api/analyses/${analysisId}`),
  getVideoDetail: (videoId: string) => request<VideoItem & { detail: VideoDetail }>(`/api/videos/${videoId}/detail`),
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
};
