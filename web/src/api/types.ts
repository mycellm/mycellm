export interface NodeStatus {
  node_name: string
  peer_id: string
  uptime_seconds: number
  mode: string
  role: string
  models: string[]
  peers: { peer_id: string; transport: string }[]
  hardware: {
    gpu_name: string
    gpu_backend: string
    vram_gb: number
    cpu_name: string
    ram_gb: number
  }
  quic_port: number
  api_port: number
}

export interface SystemInfo {
  cpu: {
    name: string
    arch: string
    cores: number
    physical_cores: number
  }
  memory: {
    total_gb: number
    used_pct: number
    available_gb: number
  }
  disk: {
    total_gb: number
    used_pct: number
    available_gb: number
  }
  gpu: {
    name: string
    backend: string
    vram_gb: number
  }
  os: {
    distro: string
    hostname: string
    python_version: string
    mycellm_version: string
  }
}

export interface Credits {
  balance: number
  earned: number
  spent: number
}

export interface CreditTier {
  tier: string
  label: string
  access: string
  balance: number
  next_tier_at: number
  receipts: {
    total: number
    verified: number
    fleet: number
  }
}

export interface Transaction {
  direction: 'credit' | 'debit'
  amount: number
  reason: string
  counterparty_id: string
  timestamp: string
}

export interface FleetNode {
  peer_id: string
  node_name: string
  status: 'approved' | 'pending' | 'rejected'
  online: boolean
  api_addr: string
  last_seen: string
  capabilities: {
    role: string
    models: { name: string; param_count_b: number; context: number }[]
  }
  system?: {
    memory?: {
      total_gb: number
      used_pct: number
    }
  }
}

export interface HardwareNode {
  name: string
  gpu: string
  backend: string
  ram_gb: number
  ram_used_pct: number
  vram_gb: number
  tps: number
  models: string[]
  online: boolean
  type: 'self' | 'fleet'
}

export interface Model {
  id: string
  object: string
  created: number
  // 'local' | 'fleet:<node>' | 'peer:<id>' | 'mycellm' (strategy models such
  // as `auto` and `mycellm/swarm`, which select a strategy, not a model).
  owned_by: string
  tags?: string[]
  description?: string
  context_length?: number
}

export interface SavedModel {
  name: string
  backend: string
  loaded: boolean
  scope: string
  api_base?: string
  api_key?: string
  api_model?: string
  ctx_len?: number
  max_concurrent?: number
  quant?: string
  param_count_b?: number
  visible_networks?: string[]
}

export interface LogEntry {
  time: string
  level: string
  name: string
  message: string
}

export interface ActivityEvent {
  type: string
  time: string
  model?: string
  source?: string
  tokens?: number
  latency_ms?: number
  routed_to?: string
  peer_id?: string
  amount?: number
  node_name?: string
  peers_discovered?: number
  nat_type?: string
  public_ip?: string
  hole_punch?: string
  status?: string
  health?: number
  message?: string
}

export interface ActivityData {
  events: ActivityEvent[]
  stats: {
    requests_1m: number
    requests_5m: number
    tokens_1m: number
    avg_latency_ms: number
  }
  sparklines: {
    throughput: number[]
    latency: number[]
    data_size: number[]
  }
}

export interface Connection {
  peer_id: string
  state: 'routable' | 'connecting' | 'disconnected'
  transport?: string
  address?: string
  rtt_ms?: number | null
  uptime_seconds?: number
  reconnect_attempts?: number
}

export interface SearchResult {
  repo_id: string
  param_b: number
  architecture: string
  context_length: number
  est_min_size_gb: number
  downloads: number
  tags: string[]
}

export interface RepoFile {
  filename: string
  size_gb: number
  quant: string
  est_ram_gb: number
  warn_disk: boolean
  warn_ram: boolean
}

export interface DownloadStatus {
  download_id: string
  filename: string
  repo_id: string
  status: 'downloading' | 'complete' | 'failed'
  progress: number
  speed_mbs?: number
  eta_s?: number
}

export interface Relay {
  url: string
  name: string
  online: boolean
  error?: string
  model_count: number
  models: string[]
}

export interface NodeConfig {
  api_key_set: boolean
  bootstrap_peers: string[]
  announce_task_alive: boolean
  hf_token_set: boolean
  db_backend: string
  log_level: string
  telemetry: boolean
}

export interface FederationInfo {
  network_id: string
  network_name: string
  public: boolean
  bootstrap_addresses: string[]
}

export interface VersionInfo {
  current: string
  latest?: string
  update_available: boolean
}

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant' | 'system' | 'error'
  content: string
  model?: string
  routed_to?: string
  tokens?: {
    prompt: number
    completion: number
  }
  // OpenAI-o-series style: model's internal reasoning, separated from the
  // user-facing answer so the UI can render it in a collapsible panel.
  reasoning_content?: string
  /// Which node produced this reply. The gateway stamps every response with it
  /// and the UI used to drop it, so a fleet answer looked identical to a local
  /// one — the single most useful fact about a distributed inference result.
  served_by?: string
  // Execution plan returned by the node on the `mycellm` field. Present for
  // swarm answers, which are paid for per proposer — the caller is entitled
  // to see what ran, what was refused, and whether the job degraded.
  plan?: ExecutionMeta
  timestamp: number
}

// ── Execution fabric (0.8) ────────────────────────────────────────────────

export interface PlanUnit {
  unit_id: string
  role: 'direct' | 'proposer' | 'synthesizer' | 'critic' | 'verifier' | 'embed'
  target: string
  model: string
  depends_on: string[]
}

export interface PlanRejection {
  target: string
  reason: string
}

/** `ExecutionPlan.to_dict()` plus the coordinator's post-run counters. */
export interface ExecutionMeta {
  job_id: string
  strategy: 'direct' | 'replica' | 'swarm'
  reasons: string[]
  rejected: PlanRejection[]
  token_budget: number
  units: PlanUnit[]
  // Present once a job has actually run (absent on a /plan dry run).
  units_ok?: number
  units_failed?: number
  proposers_planned?: number
  completion_tokens_spent?: number
  synthesized_by?: string
  served_by?: string
  elapsed_s?: number
  degraded?: boolean
  degradation?: string
  cancelled_for_budget?: number
  failures?: { target: string; error: string }[]
}

export interface Deployment {
  deployment_id: string
  // The name the model is registered under locally (`relay:<id>`).
  model: string
  // What the gateway calls it upstream. These differ, and showing only one
  // makes a routing question unanswerable from the UI.
  upstream_model?: string
  parallelism?: { type?: string; world_size?: number }
}

export interface ServingGroup {
  group_id: string
  name: string
  // ⚠️ `endpoint`, NOT `url` — the field name the node actually emits. The
  // relay *add* API takes `url`; `GET /v1/node/groups` returns `endpoint`.
  // Guessing symmetry here rendered an empty string against a live node.
  endpoint: string
  runtime?: string
  endpoint_mode?: string
  online: boolean
  healthy: boolean
  error?: string
  deployments: Deployment[]
}

export interface GroupsResponse {
  groups: ServingGroup[]
  count: number
  healthy_count: number
  deployment_count: number
}

export interface RoutingOptions {
  min_tier: string
  required_tags: string[]
  // ⚠️ `routing` is NOT a user choice. The node implements exactly one mode
  // and returns HTTP 400 for anything else, so offering "fastest" here put a
  // button in the UI that could only fail. It stays in the type as a literal
  // so a future mode has to be added deliberately, in both places at once.
  routing: 'best'
  fallback: 'downgrade' | 'reject'
  // Route only to peers at this trust level or higher. '' = node default.
  trust: '' | 'local' | 'trusted' | 'any'
  // Swarm proposers. 0 = let the planner decide.
  fanout: number
  // Ceiling on generated tokens across the whole job. 0 = no ceiling.
  token_budget: number
  // Whether to ask thinking-capable models to surface their reasoning.
  // When false (default), the server strips <think>...</think> blocks and
  // suppresses thinking on Qwen3-family templates. When true, reasoning is
  // returned on reasoning_content and shown in a collapsible UI panel.
  show_reasoning: boolean
}

/** The synthetic model that selects the swarm strategy. */
export const SWARM_MODEL = 'mycellm/swarm'

export type Tab = 'overview' | 'network' | 'models' | 'chat' | 'credits' | 'logs' | 'settings'
