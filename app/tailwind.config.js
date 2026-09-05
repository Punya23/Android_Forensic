/** @type {import('tailwindcss').Config} */
export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Forensic "case file" palette — light paper neutrals + a signal-amber accent,
        // plus a full-black dark counterpart. Every value here is a CSS custom property
        // (index.css :root / .dark) rather than a literal hex, so toggling the `dark`
        // class on <html> (see lib/theme.ts) repaints every view that uses these token
        // classes with no per-component changes. The `<alpha-value>` placeholder keeps
        // Tailwind's opacity modifiers (bg-panel/50, etc.) working with CSS vars.
        // Light values match the palette already used by the generated HTML report
        // (triage/report/html_report.py); dark values are brightened for AA contrast
        // on pure black rather than reused as-is. Confidence tiers reuse the same
        // light-background-safe tones AntiForensics.tsx already defined for its badges
        // (its own CONF_COLORS map), rather than inventing a second set that could
        // drift out of sync with them.
        ink: "rgb(var(--color-ink) / <alpha-value>)",
        panel: "rgb(var(--color-panel) / <alpha-value>)",
        "panel-2": "rgb(var(--color-panel-2) / <alpha-value>)",
        line: "rgb(var(--color-line) / <alpha-value>)",
        muted: "rgb(var(--color-muted) / <alpha-value>)",
        accent: "rgb(var(--color-accent) / <alpha-value>)",
        // Confidence tiers (must stay distinct from the accent, and legible as plain text
        // on the panel/panel-2 surface — not just as a tinted pill background). `critical`
        // and `deletion` share a color on purpose: both flag destructive/anti-forensic
        // findings and must read as the same alarm red in both themes.
        live: "rgb(var(--color-live) / <alpha-value>)",
        recovered: "rgb(var(--color-recovered) / <alpha-value>)",
        carved: "rgb(var(--color-carved) / <alpha-value>)",
        deletion: "rgb(var(--color-deletion) / <alpha-value>)",
        critical: "rgb(var(--color-critical) / <alpha-value>)",
        warn: "rgb(var(--color-warn) / <alpha-value>)",
        info: "rgb(var(--color-info) / <alpha-value>)",
      },
      fontFamily: {
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
    },
  },
  plugins: [],
};
