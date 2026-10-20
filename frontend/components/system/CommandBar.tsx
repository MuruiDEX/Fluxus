"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { AnimatePresence, motion } from "framer-motion";
import { ArrowRight, Bot, Globe, Search, Sparkles, X, Zap } from "lucide-react";
import { useUI } from "@/lib/store/app";

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

export function CommandBar() {
  const { commandOpen, setCommandOpen } = useUI();
  const [value, setValue] = useState("");
  const [tab, setTab] = useState("Popular");
  const router = useRouter();

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setCommandOpen(!useUI.getState().commandOpen);
      }
      if (e.key === "Escape") setCommandOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [setCommandOpen]);

  const go = (goal: string) => {
    setCommandOpen(false);
    setValue("");
    router.push(`/projects/new?goal=${encodeURIComponent(goal)}`);
  };

  return (
    <AnimatePresence>
      {commandOpen && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          className="fixed inset-0 z-50 flex items-start justify-center bg-night-950/60 px-4 pt-[12vh] backdrop-blur-sm"
          onClick={() => setCommandOpen(false)}
        >
          <motion.div
            initial={{ opacity: 0, y: 12, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 8, scale: 0.98 }}
            transition={{ duration: 0.18 }}
            onClick={(e) => e.stopPropagation()}
            className="bg-command-glow w-full max-w-xl overflow-hidden rounded-2xl border border-white/10 shadow-pop"
          >
            <div className="flex items-center gap-2 border-b border-white/10 px-4">
              <Search className="h-4 w-4 text-slate-400" />
              <input
                autoFocus
                value={value}
                onChange={(e) => setValue(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && value.trim()) go(value.trim());
                }}
                placeholder="What do you want to accomplish?"
                className="h-12 flex-1 bg-transparent text-[14px] text-white outline-none placeholder:text-slate-400"
              />
              <kbd className="rounded border border-white/15 px-1.5 py-0.5 text-[10px] font-semibold text-slate-300">
                Ctrl K
              </kbd>
              <button onClick={() => setCommandOpen(false)} aria-label="Close">
                <X className="h-4 w-4 text-slate-400 hover:text-white" />
              </button>
            </div>
            <div className="flex gap-1.5 overflow-x-auto px-4 pt-3">
              {tabs.map((t) => (
                <button
                  key={t}
                  onClick={() => setTab(t)}
                  className={`whitespace-nowrap rounded-full px-2.5 py-1 text-[12px] font-medium transition-colors ${
                    tab === t ? "bg-sky-400/20 text-sky-200" : "text-slate-400 hover:text-slate-200"
                  }`}
                >
                  {t}
                </button>
              ))}
            </div>
            <div className="flex flex-col gap-1 p-3">
              {suggestions
                .filter((s) => (tab === "Popular" || s.tab === tab) && s.label.toLowerCase().includes(value.toLowerCase()))
                .map((s, i) => (
                  <button
                    key={s.label}
                    onClick={() => go(s.label)}
                    className={`group flex items-center gap-3 rounded-xl px-3 py-2.5 text-left transition-colors ${
                      i === 0 ? "bg-brand-500/90 text-white" : "text-slate-200 hover:bg-white/5"
                    }`}
                  >
                    <s.icon className="h-4 w-4 shrink-0 opacity-80" />
                    <span className="flex-1 text-[13.5px] font-medium">{s.label}</span>
                    <ArrowRight className="h-4 w-4 opacity-0 transition-opacity group-hover:opacity-70" />
                  </button>
                ))}
              {value.trim() && (
                <button
                  onClick={() => go(value.trim())}
                  className="flex items-center gap-3 rounded-xl border border-dashed border-white/20 px-3 py-2.5 text-left text-sky-200 hover:bg-white/5"
                >
                  <Sparkles className="h-4 w-4" />
                  <span className="text-[13px]">Start Team Lead flow for “{value.trim()}”</span>
                </button>
              )}
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
