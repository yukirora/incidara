/**
 * Shared model color system — badge, chart bars, and legend all use the same base hue.
 * Unknown models auto-cycle through the palette by hash, so new models always get a color.
 */

/** Base palette hues — each has 3 shades: light (badge bg), mid (chart bar), text */
const HUES = [
  { light: "bg-emerald-100", mid: "bg-emerald-300", hover: "hover:bg-emerald-400", text: "text-emerald-700" },
  { light: "bg-violet-100", mid: "bg-violet-300",  hover: "hover:bg-violet-400",  text: "text-violet-700" },
  { light: "bg-sky-100",    mid: "bg-sky-300",     hover: "hover:bg-sky-400",     text: "text-sky-700" },
  { light: "bg-amber-100",  mid: "bg-amber-300",   hover: "hover:bg-amber-400",   text: "text-amber-700" },
  { light: "bg-rose-100",   mid: "bg-rose-300",    hover: "hover:bg-rose-400",    text: "text-rose-700" },
  { light: "bg-teal-100",   mid: "bg-teal-300",    hover: "hover:bg-teal-400",    text: "text-teal-700" },
  { light: "bg-orange-100", mid: "bg-orange-300",  hover: "hover:bg-orange-400",  text: "text-orange-700" },
  { light: "bg-indigo-100", mid: "bg-indigo-300",  hover: "hover:bg-indigo-400",  text: "text-indigo-700" },
];

/** Known model → palette index for consistent ordering */
const KNOWN_MODELS: Record<string, number> = {
  "claude-sonnet-4-6": 0,          // emerald
  "claude-opus-4-6": 1,            // violet
  "claude-opus-4-7": 7,            // indigo (different from opus-4-6)
  "claude-haiku-4-5-20251001": 2,  // sky
};

/** Deterministic hash for unknown models */
function hashModel(model: string): number {
  let hash = 0;
  for (let i = 0; i < model.length; i++) hash = model.charCodeAt(i) + ((hash << 5) - hash);
  return Math.abs(hash);
}

function getHue(model: string) {
  const idx = KNOWN_MODELS[model] ?? hashModel(model);
  return HUES[idx % HUES.length];
}

/** Light badge: e.g. "bg-emerald-100 text-emerald-700" */
export function modelBadgeClass(model: string): string {
  const h = getHue(model);
  return `${h.light} ${h.text}`;
}

/** Chart bar: e.g. "bg-emerald-500" */
export function chartModelColor(model: string): string {
  return getHue(model).mid;
}

/** Chart bar hover: e.g. "hover:bg-emerald-600" */
export function chartModelHover(model: string): string {
  return getHue(model).hover;
}

/** Legend dot uses same mid color as chart bar */
export function legendDotClass(model: string): string {
  return `w-2.5 h-2.5 rounded-sm ${chartModelColor(model)}`;
}
