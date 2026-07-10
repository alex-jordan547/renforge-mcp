export type StoryNodeType = "label" | "menu" | "choice" | "jump" | "call" | "unknown";

export interface StoryMapNodeData {
  label: string;
  type: StoryNodeType;
  covered: boolean;
  file?: string;
  line?: number;
  name: string;
}

export interface StoryMapNode {
  id: string;
  label: string;
  x?: number;
  y?: number;
  data: StoryMapNodeData;
}

export interface StoryMapEdge {
  id: string;
  source: string;
  target: string;
  label?: string;
  type?: "jump" | "call" | "menu" | "fallback" | "unknown";
}

export interface StoryMapResponse {
  nodes: StoryMapNode[];
  edges: StoryMapEdge[];
}

export interface LiveState {
  current_label: string;
  menu: boolean;
  showing_tags: string[];
  variables: Record<string, unknown>;
}

export interface LiveChoice {
  index: number;
  text: string;
  screen?: string;
}

export interface DebugBridgeEvent {
  seq?: number;
  type?: string;
  label?: string;
  what?: string;
  short?: string;
  full?: string;
  [key: string]: unknown;
}

export interface DebugEventsResponse {
  ok: boolean;
  cursor?: number;
  events: DebugBridgeEvent[];
  error?: string;
}

export interface LiveScreenshot {
  format: "png" | "jpeg";
  base64: string;
  width?: number;
  height?: number;
}

export interface TimelineItem {
  id: string;
  source: "bridge" | "activity" | "ui";
  timestamp: string;
  type: string;
  title: string;
  details: string;
  payload?: unknown;
  level?: "info" | "warn" | "error";
}

export interface SocketBridgeMessage {
  seq?: number;
  type?: string;
  cursor?: number;
  label?: string;
  what?: string;
  short?: string;
  full?: string;
  command?: string;
  event?: string;
  data?: Record<string, unknown>;
  payload?: Record<string, unknown>;
  source?: string;
  timestamp?: string | number;
}

export interface SocketActivityMessage {
  tool?: string;
  name?: string;
  category?: string;
  ts?: number;
  ok?: boolean;
  duration_ms?: number;
  params?: Record<string, unknown>;
  result?: unknown;
  files?: string[];
  files_touched?: string[];
  timestamp?: string | number;
}

export interface SocketProjectMessage {
  project?: string;
  generation?: number;
}

export interface SocketEnvelope {
  kind?: "bridge" | "activity" | "project";
  source?: string;
  type?: string;
  event?: SocketBridgeMessage | SocketActivityMessage | SocketProjectMessage;
  payload?: SocketBridgeMessage | SocketActivityMessage | SocketProjectMessage;
  timestamp?: string | number;
}

export interface CoverageResponse {
  summary?: Record<string, unknown>;
  covered?: number;
  total?: number;
  percent?: number;
  labels?: Array<{
    label: string;
    covered: boolean;
    file?: string;
    line?: number;
  }>;
  [key: string]: unknown;
}

export interface AssetsResponse {
  summary?: Record<string, unknown> | string;
  asset_files?: string[];
  orphans?: string[];
  missing_files?: string[];
  undefined_images?: string[];
  [key: string]: unknown;
}

export interface LintDiagnostic {
  file?: string;
  line?: number;
  severity?: string;
  message?: string;
  details?: string;
  [key: string]: unknown;
}

export interface LintResponse {
  diagnostics: LintDiagnostic[];
  total?: number;
  raw?: string;
  [key: string]: unknown;
}

export interface TranslationStats {
  language?: string;
  coverage?: number;
  done?: number;
  total?: number;
  total_files?: number;
  translated_files?: number;
  missing_files?: number;
  missing_translations?: number;
  missing_dialogue?: number;
  missing_dialogues?: number;
  missing_strings?: number;
  translated_lines?: number;
  total_lines?: number;
  percent?: number;
  files?: string[];
  summary?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface FileContent {
  path: string;
  content: string;
  size?: number;
  [key: string]: unknown;
}

export interface ProjectBrowserRoot {
  id: string;
  label: string;
  path: string;
}

export interface ProjectBrowserEntry {
  name: string;
  path: string;
  project: boolean;
  markers: string[];
}

export interface ProjectBrowserResponse {
  ok: boolean;
  roots: ProjectBrowserRoot[];
  root_id: string;
  path: string;
  parent_path: string;
  project: boolean;
  markers: string[];
  entries: ProjectBrowserEntry[];
  truncated: boolean;
  error?: string;
}
