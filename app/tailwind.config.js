/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Forensic "case file" palette — warm steel neutrals + a signal-amber accent.
        ink: "#e9ebe6",
        panel: "#161a1f",
        "panel-2": "#1c2127",
        line: "#2b323a",
        muted: "#8a939d",
        accent: "#d8823c",
        // Confidence tiers (must stay distinct from the accent).
        live: "#4fb477",
        recovered: "#5b9bd5",
        carved: "#d8a53c",
        deletion: "#d3625f",
        critical: "#d3625f",
        warn: "#d8a53c",
        info: "#5b9bd5",
      },
      fontFamily: {
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
    },
  },
  plugins: [],
};
