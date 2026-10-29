export const THEMES = [
  { id: "studio", label: "Studio", description: "Cyan accents · modern sans" },
  { id: "orchid", label: "Orchid", description: "Violet accents · rounded sans" },
  { id: "ember", label: "Ember", description: "Warm amber accents · humanist sans" },
  { id: "terminal", label: "Terminal", description: "Green accents · monospace" },
] as const;
export type Theme = typeof THEMES[number]["id"];
const KEY = "om_studio_theme";
export function readTheme(): Theme {
  try {
    const saved = localStorage.getItem(KEY);
    return THEMES.find(t => t.id === saved)?.id || "studio";
  } catch { return "studio"; }
}
export function applyTheme(theme: Theme) {
  document.documentElement.dataset.theme = theme;
  try { localStorage.setItem(KEY, theme); } catch { /* storage can be disabled */ }
}
