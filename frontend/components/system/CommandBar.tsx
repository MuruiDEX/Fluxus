"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { AnimatePresence, motion } from "framer-motion";
import { ArrowRight, Bot, Globe, Search, Sparkles, X, Zap } from "lucide-react";
import { useUI } from "@/lib/store/app";
import { transition } from "@/lib/motion";
import { goalHref } from "@/lib/intake-goal";
import { useModalSurface } from "@/lib/use-modal-surface";
import { cn } from "@/lib/cn";

const suggestions = [
  { icon: Bot, label: "Launch a fitness studio in Krakow", tab: "Projects" },
  { icon: Search, label: "Create a project to enter the German market", tab: "Projects" },
  { icon: Globe, label: "Build me a website", tab: "Projects" },
  { icon: Search, label: "Find why sales dropped", tab: "Analysis" },
  { icon: Search, label: "Find ways to reduce costs", tab: "Analysis" },
  { icon: Search, label: "Analyze competitors", tab: "Research" },
  { icon: Sparkles, label: "Create a marketing campaign", tab: "Marketing" },
  { icon: Zap, label: "Automate lead follow-up", tab: "Automation" },
  { icon: Bot, label: "Hire an AI sales manager", tab: "Team" },
];

const tabs = ["Popular", "Projects", "Research", "Marketing", "Automation", "Analysis", "Team"];

const OPTION_ID = (index: number) => `command-option-${index}`;

export function CommandBar() {
  const { commandOpen, setCommandOpen } = useUI();
  const [value, setValue] = useState("");
  const [tab, setTab] = useState("Popular");
  // Index of the suggestion keyboard focus is parked on. Mouse hover follows it
  // too, so the highlight always means "Enter would pick this one".
  const [active, setActive] = useState(0);
  // Enter submits typed text until the user arrows into the list; after that it
  // picks the highlighted row, which is what the highlight promises.
  const [navigated, setNavigated] = useState(false);
  const router = useRouter();
  const surfaceRef = useRef<HTMLDivElement | null>(null);

  useModalSurface(surfaceRef, commandOpen, {
    onEscape: () => setCommandOpen(false),
  });

  const close = () => {
    setCommandOpen(false);
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setCommandOpen(!useUI.getState().commandOpen);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [setCommandOpen]);

  const results = useMemo(
    () =>
      suggestions.filter(
        (s) =>
          (tab === "Popular" || s.tab === tab) &&
          s.label.toLowerCase().includes(value.toLowerCase()),
      ),
    [tab, value],
  );
  // The list can shrink under the stored index (typing or a tab change); clamp
  // during render instead of resetting in an effect that would re-render.
  const activeIndex = results.length === 0 ? 0 : Math.min(active, results.length - 1);
  const activeResult = navigated ? results[activeIndex] : undefined;

  // Keep the arrowing highlight inside the scroll area without moving the page.
  useEffect(() => {
    if (!navigated) return;
    document.getElementById(OPTION_ID(activeIndex))?.scrollIntoView({ block: "nearest" });
  }, [activeIndex, navigated]);

  const go = (goal: string) => {
    close();
    setValue("");
    setNavigated(false);
    router.push(goalHref(goal));
  };

  const onInputKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      if (results.length === 0) return;
      event.preventDefault();
      setNavigated(true);
      setActive(
        event.key === "ArrowDown"
          ? (activeIndex + 1) % results.length
          : (activeIndex - 1 + results.length) % results.length,
      );
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      if (activeResult) {
        go(activeResult.label);
        return;
      }
      if (value.trim()) go(value.trim());
    }
  };

  return (
    <AnimatePresence>
      {commandOpen && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={transition.fast}
          className="fixed inset-0 z-50 flex items-start justify-center bg-overlay px-4 pb-6 pt-[12vh] backdrop-blur-sm"
          onClick={close}
        >
          <motion.div
            ref={surfaceRef}
            initial={{ opacity: 0, y: 12, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 8, scale: 0.98 }}
            transition={transition.base}
            onClick={(e) => e.stopPropagation()}
            role="dialog"
            aria-modal="true"
            aria-label="Command bar"
            className="bg-command-glow w-full max-w-xl overflow-hidden rounded-2xl border border-white/10 shadow-pop"
          >
            <div className="flex items-center gap-2 border-b border-white/10 px-4 focus-within:ring-1 focus-within:ring-inset focus-within:ring-sky-400/40">
              <Search aria-hidden className="h-4 w-4 shrink-0 text-slate-400" />
              <input
                autoFocus
                value={value}
                onChange={(e) => {
                  setValue(e.target.value);
                  // A new query is a new list: back to its first row.
                  setActive(0);
                  setNavigated(false);
                }}
                onKeyDown={onInputKeyDown}
                placeholder="What do you want to accomplish?"
                role="combobox"
                aria-label="What do you want to accomplish?"
                aria-expanded={results.length > 0}
                aria-haspopup="listbox"
                aria-controls="command-results"
                aria-autocomplete="list"
                aria-activedescendant={activeResult ? OPTION_ID(activeIndex) : undefined}
                className="h-12 min-w-0 flex-1 bg-transparent text-body text-white outline-none placeholder:text-slate-400"
              />
              <kbd className="hidden rounded border border-white/15 px-1.5 py-0.5 text-micro font-semibold text-slate-300 sm:block">
                Ctrl K
              </kbd>
              <button
                type="button"
                onClick={close}
                aria-label="Close"
                className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-control text-slate-400 transition-colors hover:bg-white/10 hover:text-white focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-300"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
            <div className="flex gap-1.5 overflow-x-auto px-4 pt-3">
              {tabs.map((t) => (
                <button
                  key={t}
                  type="button"
                  onClick={() => {
                    setTab(t);
                    setActive(0);
                    setNavigated(false);
                  }}
                  aria-pressed={tab === t}
                  className={cn(
                    "whitespace-nowrap rounded-full px-2.5 py-1 text-caption font-medium transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-300",
                    tab === t
                      ? "bg-sky-400/20 text-sky-200"
                      : "text-slate-400 hover:text-slate-200",
                  )}
                >
                  {t}
                </button>
              ))}
            </div>
            <div
              id="command-results"
              role="listbox"
              aria-label="Suggested goals"
              className="flex max-h-[60vh] flex-col gap-1 overflow-y-auto overscroll-contain p-3"
            >
              {results.map((s, i) => (
                <div
                  key={s.label}
                  id={OPTION_ID(i)}
                  role="option"
                  aria-selected={navigated && i === activeIndex}
                  onClick={() => go(s.label)}
                  onMouseEnter={() => {
                    setActive(i);
                    setNavigated(true);
                  }}
                  className={cn(
                    "group flex cursor-pointer items-center gap-3 rounded-xl px-3 py-2.5 text-left transition-colors",
                    navigated && i === activeIndex
                      ? "bg-brand-500/90 text-white"
                      : "text-slate-200 hover:bg-white/5",
                  )}
                >
                  <s.icon aria-hidden className="h-4 w-4 shrink-0 opacity-80" />
                  <span className="flex-1 text-body-sm font-medium">{s.label}</span>
                  <ArrowRight
                    aria-hidden
                    className={cn(
                      "h-4 w-4 transition-opacity",
                      navigated && i === activeIndex
                        ? "opacity-80"
                        : "opacity-0 group-hover:opacity-70",
                    )}
                  />
                </div>
              ))}
              {results.length === 0 && (
                <p className="px-3 py-2 text-body-sm text-slate-400">
                  No suggestions in this tab — press Enter to start a Team Lead flow with what you
                  typed.
                </p>
              )}
              {value.trim() && (
                <button
                  type="button"
                  onClick={() => go(value.trim())}
                  className="mt-1 flex items-center gap-3 rounded-xl border border-dashed border-white/20 px-3 py-2.5 text-left text-sky-200 transition-colors hover:bg-white/5 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-300"
                >
                  <Sparkles aria-hidden className="h-4 w-4" />
                  <span className="text-body-sm">Start Team Lead flow for “{value.trim()}”</span>
                </button>
              )}
            </div>
            <div className="flex items-center justify-between gap-3 border-t border-white/10 px-4 py-2 text-micro text-slate-400">
              <span className="hidden sm:inline">↑ ↓ to browse · Enter to choose · Esc to close</span>
              <span className="sm:hidden">Tap a suggestion to start</span>
              <span className="shrink-0">{results.length} suggestions</span>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
