export type Project = {
  id: string;
  name: string;
  title: string;
  artist: string;
  status: string;
  created_at: string;
  updated_at: string;
  revision: number;
};

export type LyricUnit = {
  id: string;
  surface: string;
  start_ms: number | null;
  end_ms: number | null;
  timing_source: string;
  timing_confidence: number | null;
  ruby: string | null;
  ruby_2?: string | null;
  ruby_span?: number;
  ruby_source: string;
  roles: string[];
  locked: boolean;
};

export type LyricLine = {
  id: string;
  order: number;
  start_ms: number | null;
  end_ms: number | null;
  anchor_ms: number | null;
  timing_source: string;
  timing_precision: string;
  units: LyricUnit[];
};

export type ProjectDocument = {
  schema_version: number;
  project: { id: string; name: string; title: string; artist: string; revision: number };
  media: Record<string, unknown>;
  lyrics: { source_type: string; detected_type?: string; original_filename?: string | null; lines: LyricLine[] };
  styles: Record<string, unknown>;
  layout?: Record<string, unknown>;
  export_presets?: unknown[];
};

export type Waveform = { version: number; sample_rate: number; duration_ms: number; peaks: [number, number][] };

export type AnalysisJob = {
  id: string;
  project_id: string;
  type: "VOCAL_SEPARATION" | "TRANSCRIPTION" | "PRONUNCIATION" | "STABLE_GLOBAL_ALIGNMENT" | "STABLE_ALIGNMENT" | "FULL_ANALYSIS" | "EXPORT";
  status: "QUEUED" | "PREPARING" | "RUNNING" | "SUCCEEDED" | "FAILED" | "CANCELED";
  progress: number;
  steps: { key: string; label: string; status: "pending" | "running" | "completed"; progress: number; message?: string }[];
  stage: string;
  message: string;
  input_revision: number;
  request?: Record<string, unknown>;
  output_revision: number | null;
  error_code: string | null;
  error_message: string | null;
  cancel_requested: boolean;
  result: Record<string, unknown> | null;
  created_at: string;
};

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
  });
  if (!response.ok) {
    const error = new Error(await response.text()) as Error & { status?: number };
    error.status = response.status;
    throw error;
  }
  return (response.status === 204 ? null : await response.json()) as T;
}

export function formatTime(ms: number | null): string {
  if (ms === null || !Number.isFinite(ms)) return "--:--.---";
  const safe = Math.max(0, Math.round(ms));
  const minutes = Math.floor(safe / 60000);
  const seconds = Math.floor((safe % 60000) / 1000);
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}.${String(safe % 1000).padStart(3, "0")}`;
}

export function parseTime(value: string): number | null {
  const text = value.trim();
  if (/^\d+$/.test(text)) return Number(text);
  const match = text.match(/^(?:(\d+):)?([0-5]?\d)(?:[\.:](\d{1,3}))$/);
  if (!match) return null;
  const fraction = match[3].padEnd(3, "0").slice(0, 3);
  return (Number(match[1] || 0) * 60 + Number(match[2])) * 1000 + Number(fraction);
}
