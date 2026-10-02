import {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";

/**
 * In-app replacements for the OS-native <select> and <input type="date"> pickers, so
 * every dropdown looks the same on every platform and follows the light/dark tokens.
 * Popovers render in a portal (never clipped by overflow-auto tables/cards) and flip
 * upward when there is no room below.
 */

/* ── shared popover ─────────────────────────────────────────────────────── */

type Rect = { top: number; left: number; width: number; maxH: number };

function Popover({
  anchor,
  onClose,
  minWidth,
  children,
  popId,
}: {
  anchor: HTMLElement;
  onClose: () => void;
  minWidth?: number;
  children: ReactNode;
  popId: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [rect, setRect] = useState<Rect | null>(null);

  const place = useCallback(() => {
    const a = anchor.getBoundingClientRect();
    const h = ref.current?.scrollHeight ?? 0;
    const below = window.innerHeight - a.bottom - 8;
    const above = a.top - 8;
    const up = h > below && above > below;
    const maxH = Math.max(120, up ? above : below);
    const width = Math.max(a.width, minWidth ?? 0);
    setRect({
      top: up ? Math.max(8, a.top - Math.min(h, maxH) - 4) : a.bottom + 4,
      left: Math.min(Math.max(8, a.left), Math.max(8, window.innerWidth - width - 8)),
      width,
      maxH,
    });
  }, [anchor, minWidth]);

  useLayoutEffect(place, [place]);

  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node;
      if (!ref.current?.contains(t) && !anchor.contains(t)) onClose();
    };
    const onScroll = (e: Event) => {
      // Scrolling *inside* the popover (long option list) must not close it.
      if (!ref.current?.contains(e.target as Node)) onClose();
    };
    document.addEventListener("mousedown", onDown);
    window.addEventListener("scroll", onScroll, true);
    window.addEventListener("resize", onClose);
    return () => {
      document.removeEventListener("mousedown", onDown);
      window.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", onClose);
    };
  }, [anchor, onClose]);

  return createPortal(
    <div
      ref={ref}
      id={popId}
      style={{
        position: "fixed",
        top: rect?.top ?? 0,
        left: rect?.left ?? 0,
        width: rect?.width,
        maxHeight: rect?.maxH,
        visibility: rect ? "visible" : "hidden",
        zIndex: 1000,
      }}
      className="overflow-auto rounded-md border border-line bg-panel shadow-lg"
    >
      {children}
    </div>,
    document.body,
  );
}

const Chevron = ({ open }: { open: boolean }) => (
  <svg
    width="12"
    height="12"
    viewBox="0 0 12 12"
    aria-hidden="true"
    className={`shrink-0 text-muted transition-transform ${open ? "rotate-180" : ""}`}
  >
    <path d="M2.5 4.5 6 8l3.5-3.5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
);

/* ── Select ─────────────────────────────────────────────────────────────── */

export type SelectOption = { value: string; label: ReactNode; disabled?: boolean };

export function Select({
  value,
  onChange,
  options,
  className = "input w-auto",
  ariaLabel,
  placeholder,
}: {
  value: string;
  onChange: (v: string) => void;
  options: SelectOption[];
  /** Trigger styling; defaults to the standard `.input` look. */
  className?: string;
  ariaLabel?: string;
  placeholder?: string;
}) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const btn = useRef<HTMLButtonElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const popId = useId();
  const close = useCallback(() => setOpen(false), []);

  const selected = options.find((o) => o.value === value);

  const openMenu = () => {
    const i = options.findIndex((o) => o.value === value && !o.disabled);
    setActive(i >= 0 ? i : options.findIndex((o) => !o.disabled));
    setOpen(true);
  };

  const pick = (o: SelectOption) => {
    if (o.disabled) return;
    onChange(o.value);
    setOpen(false);
    btn.current?.focus();
  };

  /** Next enabled option index from `from` in direction `dir`; stays put at the ends. */
  const step = (dir: 1 | -1, from: number) => {
    for (let i = from + dir; i >= 0 && i < options.length; i += dir) {
      if (!options[i].disabled) return i;
    }
    return from;
  };

  const onKey = (e: ReactKeyboardEvent) => {
    if (!open) {
      if (["ArrowDown", "ArrowUp", "Enter", " "].includes(e.key)) {
        e.preventDefault();
        openMenu();
      }
      return;
    }
    switch (e.key) {
      case "ArrowDown":
        e.preventDefault();
        setActive((a) => step(1, a));
        break;
      case "ArrowUp":
        e.preventDefault();
        setActive((a) => step(-1, a));
        break;
      case "Home":
        e.preventDefault();
        setActive(step(1, -1));
        break;
      case "End":
        e.preventDefault();
        setActive(step(-1, options.length));
        break;
      case "Enter":
      case " ":
        e.preventDefault();
        if (options[active]) pick(options[active]);
        break;
      case "Escape":
      case "Tab":
        setOpen(false);
        break;
    }
  };

  // Keep the keyboard-highlighted option visible in long lists.
  useEffect(() => {
    if (open) list.current?.querySelector<HTMLElement>(`[data-i="${active}"]`)?.scrollIntoView({ block: "nearest" });
  }, [open, active]);

  return (
    <>
      <button
        ref={btn}
        type="button"
        role="combobox"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? popId : undefined}
        aria-label={ariaLabel}
        className={`${className} flex items-center justify-between gap-2 text-left cursor-pointer`}
        onClick={() => (open ? close() : openMenu())}
        onKeyDown={onKey}
      >
        <span className={`truncate ${selected ? "" : "text-muted"}`}>
          {selected ? selected.label : (placeholder ?? "Select…")}
        </span>
        <Chevron open={open} />
      </button>
      {open && btn.current && (
        <Popover anchor={btn.current} onClose={close} minWidth={140} popId={popId}>
          <div ref={list} role="listbox" className="py-1">
            {options.map((o, i) => (
              <div
                key={o.value}
                data-i={i}
                role="option"
                aria-selected={o.value === value}
                aria-disabled={o.disabled || undefined}
                onMouseEnter={() => !o.disabled && setActive(i)}
                onClick={() => pick(o)}
                className={`flex items-center justify-between gap-3 px-3 py-1.5 text-sm ${
                  o.disabled ? "text-muted cursor-not-allowed opacity-60" : "cursor-pointer text-ink"
                } ${i === active ? "bg-panel-2" : ""} ${o.value === value ? "font-medium text-accent" : ""}`}
              >
                <span className="truncate">{o.label}</span>
                {o.value === value && <span aria-hidden="true">✓</span>}
              </div>
            ))}
          </div>
        </Popover>
      )}
    </>
  );
}

/* ── DateField ──────────────────────────────────────────────────────────── */

const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
const DOW = ["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"];

const iso = (y: number, m: number, d: number) =>
  `${y}-${String(m + 1).padStart(2, "0")}-${String(d).padStart(2, "0")}`;

/** Parse "YYYY-MM-DD" → [y, m0, d]; null when empty/invalid. */
function parseIso(v: string): [number, number, number] | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(v);
  return m ? [Number(m[1]), Number(m[2]) - 1, Number(m[3])] : null;
}

export function DateField({
  value,
  onChange,
  className = "input w-auto",
  ariaLabel,
  placeholder = "Select date",
}: {
  /** "YYYY-MM-DD" or "" (same contract as <input type="date">). */
  value: string;
  onChange: (v: string) => void;
  className?: string;
  ariaLabel?: string;
  placeholder?: string;
}) {
  const [open, setOpen] = useState(false);
  const btn = useRef<HTMLButtonElement>(null);
  const popId = useId();
  const close = useCallback(() => setOpen(false), []);

  const parsed = parseIso(value);
  const today = useMemo(() => new Date(), []);
  const [view, setView] = useState<[number, number]>([today.getFullYear(), today.getMonth()]);

  const openCal = () => {
    setView(parsed ? [parsed[0], parsed[1]] : [today.getFullYear(), today.getMonth()]);
    setOpen(true);
  };

  const shift = (delta: number) =>
    setView(([y, m]) => {
      const t = m + delta;
      return [y + Math.floor(t / 12), ((t % 12) + 12) % 12];
    });

  const [vy, vm] = view;
  const lead = new Date(vy, vm, 1).getDay();
  const days = new Date(vy, vm + 1, 0).getDate();
  const cells: (number | null)[] = [
    ...Array<null>(lead).fill(null),
    ...Array.from({ length: days }, (_, i) => i + 1),
  ];

  const pick = (v: string) => {
    onChange(v);
    setOpen(false);
    btn.current?.focus();
  };

  const todayIso = iso(today.getFullYear(), today.getMonth(), today.getDate());
  const nav = "rounded px-2 py-1 text-muted hover:bg-panel-2 hover:text-ink";

  return (
    <>
      <button
        ref={btn}
        type="button"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={ariaLabel}
        className={`${className} flex items-center justify-between gap-2 text-left cursor-pointer`}
        onClick={() => (open ? close() : openCal())}
        onKeyDown={(e) => e.key === "Escape" && close()}
      >
        <span className={parsed ? "" : "text-muted"}>
          {parsed ? `${String(parsed[2]).padStart(2, "0")} ${MONTHS[parsed[1]].slice(0, 3)} ${parsed[0]}` : placeholder}
        </span>
        <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true" className="shrink-0 text-muted">
          <rect x="1.5" y="2.5" width="11" height="10" rx="1.5" fill="none" stroke="currentColor" strokeWidth="1.3" />
          <path d="M1.5 5.5h11M4.5 1v3M9.5 1v3" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
        </svg>
      </button>
      {open && btn.current && (
        <Popover anchor={btn.current} onClose={close} minWidth={248} popId={popId}>
          <div role="dialog" aria-label="Choose date" className="p-2 text-sm">
            <div className="flex items-center justify-between mb-1">
              <button type="button" className={nav} aria-label="Previous month" onClick={() => shift(-1)}>‹</button>
              <span className="font-medium text-ink">{MONTHS[vm]} {vy}</span>
              <button type="button" className={nav} aria-label="Next month" onClick={() => shift(1)}>›</button>
            </div>
            <div className="grid grid-cols-7 text-center text-[11px] text-muted mb-1">
              {DOW.map((d) => <span key={d} className="py-1">{d}</span>)}
            </div>
            <div className="grid grid-cols-7 gap-0.5">
              {cells.map((d, i) => {
                if (d === null) return <span key={`b${i}`} />;
                const v = iso(vy, vm, d);
                return (
                  <button
                    key={v}
                    type="button"
                    onClick={() => pick(v)}
                    className={`rounded py-1 text-xs ${
                      v === value
                        ? "bg-accent text-white font-medium"
                        : v === todayIso
                          ? "border border-accent text-ink hover:bg-panel-2"
                          : "text-ink hover:bg-panel-2"
                    }`}
                  >
                    {d}
                  </button>
                );
              })}
            </div>
            <div className="flex justify-between mt-2 pt-2 border-t border-line text-xs">
              <button type="button" className="text-accent hover:underline" onClick={() => pick(todayIso)}>Today</button>
              <button type="button" className="text-muted hover:text-ink disabled:opacity-40" disabled={!value} onClick={() => pick("")}>Clear</button>
            </div>
          </div>
        </Popover>
      )}
    </>
  );
}
