"use client";

import { useState } from "react";
import Link from "next/link";
import { motion } from "framer-motion";
import { ArrowRight, Cpu } from "lucide-react";

const steps = ["What are you building?", "Business details", "Location", "Goals", "Connect tools", "Import knowledge", "Build your AI team"];

export default function OnboardingPage() {
  const [goal, setGoal] = useState("I want to launch a fitness studio in Krakow.");

  return (
    <div className="flex min-h-screen bg-mist-50 dark:bg-night-950">
      <aside className="hidden w-64 shrink-0 flex-col gap-1 border-r border-mist-200 bg-white p-4 md:flex dark:border-white/10 dark:bg-night-900">
        <p className="mb-3 flex items-center gap-2 text-[13px] font-bold"><Cpu className="h-4 w-4 text-brand-500" /> MUSE SPARK</p>
        {steps.map((s, i) => (
          <p key={s} className={`flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-[13px] ${i === 0 ? "bg-brand-50 font-semibold text-brand-600 dark:bg-brand-500/15" : "text-ink-500"}`}>
            <span className={`flex h-5 w-5 items-center justify-center rounded-full text-[11px] font-bold ${i === 0 ? "bg-brand-500 text-white" : "bg-mist-100 text-ink-400 dark:bg-white/10"}`}>{i + 1}</span>
            {s}
          </p>
        ))}
      </aside>
      <main className="flex flex-1 items-center justify-center p-6">
        <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} className="w-full max-w-xl text-center">
          <h1 className="text-[26px] font-bold">What do you want to build?</h1>
          <p className="mt-1 text-[13.5px] text-ink-500">Your AI company will handle the rest.</p>
          <div className="mx-auto mt-5 flex items-center gap-2 rounded-2xl border border-mist-200 bg-white p-2 pl-4 text-left shadow-card focus-within:border-brand-500 dark:border-white/10 dark:bg-night-900">
            <input value={goal} onChange={(e) => setGoal(e.target.value)} className="h-11 flex-1 bg-transparent text-[14px] outline-none" />
            <Link href={`/projects/new?goal=${encodeURIComponent(goal)}`} className="flex h-11 w-11 items-center justify-center rounded-xl bg-gradient-to-r from-brand-500 to-sky-400 text-white" aria-label="Start">
              <ArrowRight className="h-4 w-4" />
            </Link>
          </div>
          <p className="mt-5 text-[12.5px] text-ink-400">Or choose a mission:</p>
          <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-3">
            {["Launch Business", "Get More Customers", "Build Website", "Automate Business", "Analyze Competitors", "Explore Opportunities"].map((m) => (
              <Link key={m} href={`/projects/new?goal=${encodeURIComponent(m)}`} className="rounded-xl border border-mist-200 bg-white p-3 text-[12.5px] font-medium hover:border-brand-200 hover:bg-brand-50 dark:border-white/10 dark:bg-night-900">
                {m}
              </Link>
            ))}
          </div>
        </motion.div>
      </main>
    </div>
  );
}
