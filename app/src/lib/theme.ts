/**
 * Light/dark theme toggle. Plain CSS custom properties, not Tailwind's `dark:`
 * variant — every component already styles itself with the semantic tokens
 * (bg-panel, text-ink, text-muted, text-live, …) defined in tailwind.config.js,
 * and those tokens read their RGB triplets from `--color-*` vars set in
 * index.css. Flipping the `.dark` class on <html> repaints the whole app
 * without touching a single view file.
 */

export type Theme = "light" | "dark";

const STORAGE_KEY = "snagr-theme";

function systemPrefersDark(): boolean {
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false;
}

/** Stored choice if the user has picked one; otherwise the OS preference. */
export function getInitialTheme(): Theme {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === "light" || stored === "dark") return stored;
  } catch {
    // localStorage unavailable (e.g. private mode) — fall through to system pref.
  }
  return systemPrefersDark() ? "dark" : "light";
}

export function applyTheme(theme: Theme): void {
  document.documentElement.classList.toggle("dark", theme === "dark");
  document.documentElement.style.colorScheme = theme;
}

export function persistTheme(theme: Theme): void {
  try {
    localStorage.setItem(STORAGE_KEY, theme);
  } catch {
    // Ignore — theme still applies for this session, just won't survive a reload.
  }
}
