import { Moon, Sun } from "lucide-react";
import { useEffect, useState } from "react";
import { applyTheme, getInitialTheme, persistTheme, type Theme } from "../lib/theme";

/** Header toggle. Theme is already applied pre-paint by the inline script in
 * index.html (avoids a flash of the wrong theme); this just mirrors that state
 * into React and flips it on click. */
export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(getInitialTheme);

  useEffect(() => {
    applyTheme(theme);
    persistTheme(theme);
  }, [theme]);

  const isDark = theme === "dark";

  return (
    <button
      type="button"
      onClick={() => setTheme(isDark ? "light" : "dark")}
      className="btn-ghost !px-2.5 !py-1.5 text-xs flex items-center gap-1.5"
      title={isDark ? "Switch to light mode" : "Switch to dark mode"}
      aria-label={isDark ? "Switch to light mode" : "Switch to dark mode"}
    >
      {isDark ? <Sun className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden /> : <Moon className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden />}
      {isDark ? "Light" : "Dark"}
    </button>
  );
}
