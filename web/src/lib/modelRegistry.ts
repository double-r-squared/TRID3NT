/**
 * modelRegistry.ts — single source of truth for selectable Bedrock models.
 *
 * Mirrors the Python-side SELECTABLE_MODELS list in
 * services/agent/src/grace2_agent/bedrock_adapter.py.  Both must be kept in
 * sync: the server enforces cachePoint gating per-model; this file drives the
 * in-chat selector UI + localStorage persistence.
 *
 * Accent colors are muted to fit the dark theme.  Provider palette:
 *   Anthropic — warm terracotta / clay (#c2603c)
 *   Amazon    — muted amber (#b8860b)
 *   DeepSeek  — slate blue / indigo (#5c7fa3)
 */

export interface ModelEntry {
  /** Bedrock model id / cross-region inference profile id. */
  id: string;
  /** Short human label shown in the selector popover. */
  label: string;
  /** Provider family shown as a secondary line in the popover. */
  provider: string;
  /**
   * Muted accent color used to tint the chat input border while this model
   * is active.  Must be readable against the dark background (#1a1a20).
   */
  accentColor: string;
  /** Whether this model supports Bedrock cachePoint prompt caching. */
  supportsPromptCache: boolean;
}

export const SELECTABLE_MODELS: ModelEntry[] = [
  {
    id: "us.anthropic.claude-sonnet-4-6",
    label: "Claude Sonnet 4.6",
    provider: "Anthropic",
    accentColor: "#c2603c",
    supportsPromptCache: true,
  },
  {
    id: "us.anthropic.claude-haiku-4-5",
    label: "Claude Haiku 4.5",
    provider: "Anthropic",
    accentColor: "#c2603c",
    supportsPromptCache: true,
  },
  {
    id: "us.amazon.nova-lite-v1:0",
    label: "Nova Lite",
    provider: "Amazon",
    accentColor: "#b8860b",
    supportsPromptCache: true,
  },
  {
    id: "us.amazon.nova-pro-v1:0",
    label: "Nova Pro",
    provider: "Amazon",
    accentColor: "#b8860b",
    supportsPromptCache: true,
  },
  {
    id: "us.deepseek.r1-v1:0",
    label: "DeepSeek-R1",
    provider: "DeepSeek",
    accentColor: "#5c7fa3",
    supportsPromptCache: false,
  },
];

// SELECTABLE_MODELS is always non-empty (5 entries defined above).
// The non-null assertions below are safe: the array is module-level and
// immutable after import.
// eslint-disable-next-line @typescript-eslint/no-non-null-assertion
export const DEFAULT_MODEL_ID = SELECTABLE_MODELS[0]!.id;

export const MODEL_STORAGE_KEY = "grace2.selected_model_id";

/** Look up a model entry by id; returns the default (Sonnet) when not found. */
export function getModelById(id: string | null | undefined): ModelEntry {
  // eslint-disable-next-line @typescript-eslint/no-non-null-assertion
  if (!id) return SELECTABLE_MODELS[0]!;
  // eslint-disable-next-line @typescript-eslint/no-non-null-assertion
  return SELECTABLE_MODELS.find((m) => m.id === id) ?? SELECTABLE_MODELS[0]!;
}

/** Load the persisted model id from localStorage; null when nothing stored. */
export function loadPersistedModelId(): string | null {
  try {
    const v = window.localStorage.getItem(MODEL_STORAGE_KEY);
    return v ?? null;
  } catch {
    return null;
  }
}

/** Persist the selected model id to localStorage. */
export function persistModelId(id: string): void {
  try {
    window.localStorage.setItem(MODEL_STORAGE_KEY, id);
  } catch {
    // ignore (private browsing mode)
  }
}
