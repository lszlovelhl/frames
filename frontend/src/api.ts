// 帧间前端 API 封装
export const BASE = "http://127.0.0.1:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!resp.ok) {
    const body = await resp.json().catch(() => null);
    const detail = body?.detail;
    const message =
      typeof detail === "string"
        ? detail
        : (detail?.message as string | undefined) || `请求失败 ${resp.status}`;
    const err = new Error(message) as Error & { code?: string; balance_points?: number; required_points?: number };
    if (detail && typeof detail === "object") {
      err.code = detail.code;
      err.balance_points = detail.balance_points;
      err.required_points = detail.required_points;
    }
    throw err;
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
  /** A+B 素材保留策略：auto / keep_preview / keep_full */
  media_policy?: "auto" | "keep_preview" | "keep_full" | null;
  /** 原片是否已完成清理（auto 删原片留音轨 / keep_preview 删原片留 preview.mp4） */
  cleaned?: boolean;
  cleaned_at?: string | null;
  /** 清理时抽取的 128k aac 音轨相对 /media 地址（供将来 demucs 等使用） */
  audio_track_url?: string | null;
  has_transcript: boolean;
  transcript_segments: number;
  frames: number;
  bpm: number | null;
  bgm_ok: boolean;
  bgm_path: string | null;
  bgm_url: string | null;
  media_status: string;
  truncated?: boolean;
  truncated_to_duration_ms?: number | null;
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
  author_id: string | null;
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

export interface VideoStatsHistoryPoint {
  fetched_at: string;
  view_count: number | null;
  like_count: number | null;
  collect_count: number | null;
  share_count: number | null;
  comment_count: number | null;
  danmaku_count: number | null;
  coin_count: number | null;
  /** baseline=与基准时刻对齐的首点；latest=末点；其余为 null */
  phase: "baseline" | "latest" | null;
}

export interface VideoStatsHistoryView {
  video_id: string;
  platform: string;
  baseline: Record<string, number>;
  baseline_at: string | null;
  latest: Record<string, number>;
  updated_at: string | null;
  /** 可绘制时序维度（平台不支持的指标不会出现，如抖音 view_count） */
  series_keys: string[];
  points: VideoStatsHistoryPoint[];
}

export interface LayerInfo {
  layer: number;
  role_view: string;
  content: Record<string, unknown>;
  model: string | null;
  prompt_version: string | null;
  confidence: number | null;
  raw_response: string | null;
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
    category_guess?: string | null;
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

export interface ProductInfo {
  id: string;
  industry: string;
  category_tags: string[];
  brand: string;
  name: string;
  series: string | null;
  headline: string;
  price_range: string | null;
  specs: Array<{ k: string; v: string }>;
  selling_points: Array<{ title: string; detail: string }>;
  source?: string;
  updated_at?: string | null;
}

export interface ProductAutofill {
  series: string;
  headline: string;
  price_range: string;
  category_tags: string[];
  specs: Array<{ k: string; v: string }>;
  selling_points: Array<{ title: string; detail: string }>;
}

export interface UsedProductRef {
  id: string;
  brand: string;
  name: string;
  industry: string;
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

export interface GuideTrace {
  kind: "element" | "product";
  name: string;
  label?: string;
  time?: string;
}

/** 创作指南「参考依据」章的聚合条目：跨章 trace 去重 + 标注来源章节 */
export interface CreationSourceItem {
  kind: "element" | "product";
  name: string;
  label?: string;
  time?: string;
  chapters?: string[];
}

export interface GuideChapter {
  asset: "brief" | "strategy" | "script" | "shooting" | "editing" | "publish" | "checklist" | "sources";
  title: string;
  text?: string;
  trace?: GuideTrace[];
}

export interface GuidePreviewResult {
  title: string;
  chapters: GuideChapter[];
  model?: string;
  usage?: unknown;
}

export interface CreationAssetView {
  id: string;
  asset_type: string;
  role_view: string;
  content: {
    text?: string;
    title?: string;
    trace?: GuideTrace[];
    items?: CreationSourceItem[];
  };
  parent_id?: string | null;
  version?: number;
  created_at: string | null;
}

export interface CreationDetail extends CreationItem {
  assets: CreationAssetView[];
}

/** 发布回流：创作项目上线平台的登记 + 可选手填回流数据 */
export interface CreationPublishItem {
  id: string;
  creation_id: string;
  publish_platform: string;
  publish_url: string | null;
  published_at: string | null;
  stats: Record<string, unknown>;
  stats_fetched_at: string | null;
  created_at: string | null;
  updated_at: string | null;
}

/** 元素演化版本：母版派生/换壳/组合等一次变更 */
export interface ElementVersionEntry {
  id: string;
  version_seq: number;
  change_kind: string;
  new_formula: string | null;
  note: string | null;
  used_by_creation_id: string | null;
  used_by_creation_title: string | null;
  created_at: string | null;
}

export interface AnalysisResult {
  id: string;
  video_id: string;
  status: string;
  current_layer: number;
  ai_confidence: number | null;
  reviewed_by_user: boolean | null;
  summary: Record<string, unknown>;
  meta: Record<string, unknown>;
  layers: LayerInfo[];
  video?: VideoItem;
}

/** 拆解任务进度（常驻进度面板数据源，来自 /api/analyses/active） */
export interface AnalysisJobItem {
  analysis_id: string;
  video_id: string;
  title: string;
  platform: string;
  cover_url: string | null;
  duration_ms: number | null;
  status: string;
  current_layer: number;
  progress: {
    stage: string | null;
    message: string;
    layer: number | null;
    pct: number;
    updated_at: string | null;
  };
  model: string | null;
  failed_layers: number | null;
  created_at: string | null;
  finished_at: string | null;
}

export interface BatchAnalyseResult {
  queued: Array<{ url: string; video_id: string; analysis_id: string; title: string }>;
  skipped: Array<{ url: string; reason: string }>;
  state: AnalysisJobItem[];
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

// ---- 提示词模板 ----
export interface PromptCodeSummary {
  code: string;
  name: string;
  layer: number | null;
  role_scope: string[];
  platform_scope: string[];
  version_count: number;
  active_version: number | null;
  active_status: string;
  updated_at: string | null;
}

export interface PromptVersionItem {
  id: string;
  version: number;
  status: string;
  name: string;
  content: string;
  updated_at: string | null;
}

export interface PromptDetail {
  code: string;
  name: string;
  layer: number | null;
  versions: PromptVersionItem[];
}

// ---- 点数计费 ----
export interface BillingAccountResp {
  /** 计费模式：enforced=强制计费；free=免计费模式（放开点数校验，仅记用量） */
  billing?: {
    enforced: boolean;
    mode: "free" | "enforced";
    free_used_points: number;
    note: string;
  };
  account: {
    id: string;
    balance_points: number;
    total_recharged_points: number;
    total_consumed_points: number;
    free_claimed: boolean;
    today_consumed: number;
    daily_limit: number;
  };
  packages: Array<{ key: string; name: string; points: number; amount_cny: number }>;
  action_points: Array<{ action: string; points: number }>;
  recent_transactions: Array<{
    id: string;
    type: string;
    action: string;
    points: number;
    amount_cny: number | null;
    ref_type: string | null;
    note: string | null;
    created_at: string | null;
  }>;
}

export const api = {
  health: () => request<{ database: string }>("/api/health/db"),
  models: () => request<{ items: AiModelInfo[]; configured: boolean }>("/api/ai/models"),
  createVideo: (payload: VideoCreatePayload) => request<VideoItem & { has_subtitle?: boolean }>("/api/videos", { method: "POST", body: JSON.stringify(payload) }),
  listVideos: () => request<{ videos: VideoItem[] }>("/api/videos"),
  runAnalysis: (videoId: string, model = "pro", targetLayers = 5) =>
    request<AnalysisResult>(`/api/videos/${videoId}/analyse`, { method: "POST", body: JSON.stringify({ model, target_layers: targetLayers }) }),
  /** 批量提交拆解：提交即返回，进度由 activeAnalyses 轮询（切视图/切应用不丢） */
  batchAnalyse: (urls: string[], targetLayers = 5, withBgm = true) =>
    request<BatchAnalyseResult>("/api/videos/batch-analyse", {
      method: "POST",
      body: JSON.stringify({ urls, target_layers: targetLayers, with_bgm: withBgm }),
    }),
  /** 进行中 + 最近完成的拆解任务（常驻进度面板） */
  activeAnalyses: () => request<{ items: AnalysisJobItem[] }>("/api/analyses/active"),

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
  getElementVersions: (elementId: string) =>
    request<{ element_id: string; element_name: string; items: ElementVersionEntry[] }>(`/api/elements/${elementId}/versions`),
  // ---- 产品库 ----
  listProducts: (params?: { industry?: string; category?: string; q?: string }) => {
    const sp = new URLSearchParams();
    if (params?.industry && params.industry !== "all") sp.set("industry", params.industry);
    if (params?.category && params.category !== "all") sp.set("category", params.category);
    if (params?.q) sp.set("q", params.q);
    const qs = sp.toString();
    return request<{ items: ProductInfo[]; industries: string[] }>(`/api/products${qs ? `?${qs}` : ""}`);
  },
  createProduct: (payload: { industry: string; category_tags: string[]; brand: string; name: string; series?: string | null; headline: string; price_range?: string | null; specs?: Array<{ k: string; v: string }>; selling_points?: Array<{ title: string; detail: string }> }) =>
    request<ProductInfo>("/api/products", { method: "POST", body: JSON.stringify(payload) }),
  autofillProduct: (payload: { industry: string; brand: string; name: string; series?: string | null; price_range?: string | null }) =>
    request<ProductAutofill>(`/api/products/autofill`, { method: "POST", body: JSON.stringify(payload) }),
  listPlatforms: () => request<PlatformStatus[]>("/api/platforms"),
  savePlatformCookies: (platform: string, cookiesText: string) =>
    request<PlatformStatus>(`/api/platforms/${platform}/cookies`, { method: "POST", body: JSON.stringify({ cookies_text: cookiesText }) }),
  detectPlatform: (platform: string) => request<PlatformStatus & { hint?: string }>(`/api/platforms/${platform}/detect`, { method: "POST" }),
  clearPlatformCookies: (platform: string) =>
    request<{ ok: boolean } & PlatformStatus>(`/api/platforms/${platform}/cookies`, { method: "DELETE" }),
  platformLoginUrl: (platform: string) => request<{ platform: string; login_url: string; label: string }>(`/api/platforms/${platform}/login-url`),
  creationsChat: (messages: Array<{ role: "user" | "assistant"; content: string }>, elementIds: string[], productIds: string[] = [], platform?: string, intent?: string) =>
    request<{ reply: string; model?: string; used_elements: UsedElementRef[]; used_products?: UsedProductRef[] }>("/api/creations/chat", {
      method: "POST",
      body: JSON.stringify({ messages, element_ids: elementIds, product_ids: productIds, platform, intent }),
    }),
  guidePreview: (payload: { messages: Array<{ role: "user" | "assistant"; content: string }>; element_ids: string[]; product_ids?: string[]; platform?: string; intent?: string }) =>
    request<GuidePreviewResult>("/api/creations/guide/preview", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  guideGenerate: (payload: { messages: Array<{ role: "user" | "assistant"; content: string }>; element_ids: string[]; product_ids?: string[]; platform?: string; intent?: string; title: string; preview: GuideChapter[] }) =>
    request<CreationDetail>("/api/creations/guide/generate", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  saveCreation: (payload: { title: string; content: string; element_ids: string[]; platform?: string | null; intent?: string | null }) =>
    request<CreationItem & { assets: number }>("/api/creations", { method: "POST", body: JSON.stringify(payload) }),
  creationsContinueChat: (creationId: string, messages: Array<{ role: "user" | "assistant"; content: string }>, elementIds: string[], productIds: string[] = [], baseAssetId?: string | null) =>
    request<{ reply: string; model?: string; current_version: number; base_asset_id: string | null; used_elements: UsedElementRef[]; used_products?: UsedProductRef[] }>(
      `/api/creations/${creationId}/chat`,
      { method: "POST", body: JSON.stringify({ messages, element_ids: elementIds, product_ids: productIds, base_asset_id: baseAssetId ?? null }) }
    ),
  saveCreationVersion: (creationId: string, payload: { content: string; title?: string; element_ids: string[]; parent_asset_id?: string | null }) =>
    request<{ creation_id: string; title: string; asset: CreationAssetView }>(`/api/creations/${creationId}/versions`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  listCreations: () => request<{ creations: CreationItem[] }>("/api/creations"),
  getCreation: (creationId: string) => request<CreationDetail>(`/api/creations/${creationId}`),
  removeCreation: (creationId: string) => request<{ ok: boolean }>(`/api/creations/${creationId}`, { method: "DELETE" }),
  listCreationPublishes: (creationId: string) =>
    request<{ creation_id: string; items: CreationPublishItem[] }>(`/api/creations/${creationId}/publishes`),
  registerCreationPublish: (creationId: string, payload: { publish_platform: string; publish_url?: string | null; published_at?: string | null; stats?: Record<string, unknown> | null }) =>
    request<CreationPublishItem>(`/api/creations/${creationId}/publishes`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  removeCreationPublish: (creationId: string, publishId: string) =>
    request<{ ok: boolean }>(`/api/creations/${creationId}/publishes/${publishId}`, { method: "DELETE" }),
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
  getVideoStatsHistory: (videoId: string) => request<VideoStatsHistoryView>(`/api/videos/${videoId}/stats-history`),
  // ---- 提示词模板管理 ----
  listPrompts: () => request<{ items: PromptCodeSummary[] }>("/api/prompts"),
  getPromptDetail: (code: string) => request<PromptDetail>(`/api/prompts/${code}`),
  savePromptVersion: (code: string, payload: { content: string; name?: string; activate?: boolean }) =>
    request<{ id: string; code: string; version: number; status: string; name: string }>(
      `/api/prompts/${code}/versions`,
      { method: "POST", body: JSON.stringify(payload) }
    ),
  activatePromptVersion: (code: string, version: number) =>
    request<{ code: string; version: number; status: string }>(`/api/prompts/${code}/activate`, {
      method: "POST",
      body: JSON.stringify({ version }),
    }),
  // ---- 点数计费 ----
  billingAccount: () => request<BillingAccountResp>("/api/billing/account"),
  billingRecharge: (packageKey: string) =>
    request<BillingAccountResp>("/api/billing/recharge", { method: "POST", body: JSON.stringify({ package: packageKey }) }),
  billingFreeClaim: () =>
    request<BillingAccountResp>("/api/billing/free-claim", { method: "POST" }),
};
