/**
 * SNAGR mark: a rounded hexagon (the evidence "container") holding an S drawn as one continuous
 * stroke, with a bright node on the upper vertex — the point where an artifact is snagged. It is
 * drawn with the theme's accent so it follows light/dark; public/favicon.svg is the same shape in
 * fixed colours for the browser tab.
 */
export function LogoMark({ size = 28 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" fill="none" aria-hidden>
      <path
        d="M16 2.6 27.6 9.3a2.4 2.4 0 0 1 1.2 2.1v9.2a2.4 2.4 0 0 1-1.2 2.1L16 29.4 4.4 22.7a2.4 2.4 0 0 1-1.2-2.1v-9.2a2.4 2.4 0 0 1 1.2-2.1L16 2.6Z"
        fill="rgb(var(--color-accent) / 0.14)"
        stroke="rgb(var(--color-accent))"
        strokeWidth="1.6"
        strokeLinejoin="round"
      />
      <path
        d="M20.6 12.2c-.9-1.9-3-2.7-5-2.4-2.4.4-3.6 2.2-3 4 .6 1.9 2.7 2.4 4.4 2.9 2 .6 3.9 1.3 4 3.3.1 2-1.8 3.4-4.2 3.3-2-.1-3.7-1-4.6-2.6"
        stroke="rgb(var(--color-ink))"
        strokeWidth="2.1"
        strokeLinecap="round"
      />
      <circle cx="16" cy="2.6" r="1.9" fill="rgb(var(--color-accent))" />
    </svg>
  );
}

/** Mark plus wordmark. `size` is the mark's pixel size; the wordmark scales with it. */
export function Logo({ size = 28 }: { size?: number }) {
  return (
    <span className="inline-flex items-center gap-2.5 select-none">
      <LogoMark size={size} />
      <span className="font-semibold leading-none tracking-[0.2em]" style={{ fontSize: size * 0.58 }}>
        SNAG<span className="text-accent">R</span>
      </span>
    </span>
  );
}
