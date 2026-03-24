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
  owned_by: string
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
  timestamp: number
}

export interface RoutingOptions {
  min_tier: string
  required_tags: string[]
  routing: 'best' | 'fastest'
  fallback: 'downgrade' | 'reject'
}

export type Tab = 'overview' | 'network' | 'models' | 'chat' | 'credits' | 'logs' | 'settings'
