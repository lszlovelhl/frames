// 帧间前端 API 封装
const BASE = "http://127.0.0.1:8000";

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
  has_transcript: boolean;
  transcript_segments: number;
  frames: number;
  bpm: number | null;
  bgm_ok: boolean;
  bgm_path: string | null;
  media_status: string;
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

export const api = {
  health: () => request<{ database: string }>("/api/health/db"),
  models: () => request<Array<{ alias: string; model: string }>>("/api/ai/models"),
  createVideo: (payload: VideoCreatePayload) => request<VideoItem & { has_subtitle?: boolean }>("/api/videos", { method: "POST", body: JSON.stringify(payload) }),
  listVideos: () => request<{ videos: VideoItem[] }>("/api/videos"),
  runAnalysis: (videoId: string, model = "pro", targetLayers = 5) =>
    request<AnalysisResult>(`/api/videos/${videoId}/analyse`, { method: "POST", body: JSON.stringify({ model, target_layers: targetLayers }) }),
  getAnalysis: (analysisId: string) => request<AnalysisResult>(`/api/analyses/${analysisId}`),
};
