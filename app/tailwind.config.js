/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Forensic "case file" palette — light paper neutrals + a signal-amber accent.
        // Matches the light palette already used by the generated HTML report
        // (triage/report/html_report.py) so the report and the dashboard read as one
        // system. Confidence tiers reuse the light-background-safe tones AntiForensics.tsx
        // already defined for its badges (its own CONF_COLORS map), rather than inventing
        // a second set of "light mode" values that could drift out of sync with them.
        ink: "#1a1d21",
        panel: "#f3f4f1",
        "panel-2": "#ffffff",
        line: "#dde1de",
        muted: "#5b6570",
        accent: "#c1651f",
        // Confidence tiers (must stay distinct from the accent, and legible as plain text
        // on a white/near-white surface — not just as a tinted pill background).
        live: "#1c7d3f",
        recovered: "#2258a8",
        carved: "#a6741a",
        deletion: "#a5322f",
        critical: "#a5322f",
        warn: "#a6741a",
        info: "#2258a8",
      },
      fontFamily: {
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
    },
  },
  plugins: [],
};
