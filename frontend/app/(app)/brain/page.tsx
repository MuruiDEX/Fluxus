"use client";

import { useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Plus, Search, Upload, X } from "lucide-react";
import { Card, GhostButton, PageHeader, PrimaryButton } from "@/components/system/ui";
import { useBrain, useBusiness } from "@/lib/store/app";
import type { BrainEntry } from "@/lib/domain/types";
import { cn } from "@/lib/cn";

const cats = ["all", "company", "audience", "model", "brand", "goals", "decisions", "knowledge", "results"] as const;

let noteSeq = 0;
const nextNoteId = () => {
  noteSeq += 1;
  return `brain_note_${noteSeq}`;
};

export default function BrainPage() {
  const entries = useBrain((s) => s.entries);
  const addEntry = useBrain((s) => s.addEntry);
  const { currentBusinessId } = useBusiness();
  const [cat, setCat] = useState<(typeof cats)[number]>("all");
  const [q, setQ] = useState("");
  const [modal, setModal] = useState<"note" | "import" | null>(null);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [noteCat, setNoteCat] = useState<BrainEntry["category"]>("knowledge");
  const fileRef = useRef<HTMLInputElement>(null);
  const visible = entries.filter(
    (e) => (cat === "all" || e.category === cat) && (e.title + e.body).toLowerCase().includes(q.toLowerCase()),
  );

  const saveNote = () => {
    if (!title.trim() || !body.trim()) return;
    addEntry({ id: nextNoteId(), businessId: currentBusinessId, category: noteCat, title: title.trim(), body: body.trim(), updatedLabel: "just now" });
    setTitle("");
    setBody("");
    setModal(null);
  };

  const importFile = (name: string) => {
    addEntry({
      id: nextNoteId(),
      businessId: currentBusinessId,
      category: "knowledge",
      title: `Imported: ${name} (simulated)`,
      body: `Source registered from “${name}”. Content parsing is simulated in this demo — no file leaves your browser.`,
      updatedLabel: "just now",
    });
    setModal(null);
  };

  return (
    <>
      <PageHeader
        title="Business Brain"
        sub="Persistent company memory. No one has to explain the business twice."
        actions={
          <>
            <GhostButton onClick={() => setModal("import")}><Upload className="h-4 w-4" /> Import</GhostButton>
            <GhostButton onClick={() => setModal("note")}><Plus className="h-4 w-4" /> Add note</GhostButton>
          </>
        }
      />
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex h-9 max-w-xs flex-1 items-center gap-2 rounded-[10px] border border-mist-200 bg-white px-3 dark:border-white/10 dark:bg-night-900">
          <Search className="h-4 w-4 text-ink-400" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search memory…" className="w-full bg-transparent text-[13px] outline-none" />
        </div>
        <div className="flex flex-wrap gap-1.5">
          {cats.map((c) => (
            <button
              key={c}
              onClick={() => setCat(c)}
              className={cn(
                "rounded-full px-2.5 py-1 text-[12.5px] font-medium capitalize",
                cat === c ? "bg-brand-500 text-white" : "border border-mist-200 bg-white text-ink-500 dark:border-white/10 dark:bg-night-900",
              )}
            >
              {c}
            </button>
          ))}
        </div>
      </div>
      <div className="grid gap-2.5 md:grid-cols-2 xl:grid-cols-3">
        {visible.map((e) => (
          <Card key={e.id} className="p-4">
            <div className="flex items-center justify-between">
              <span className="rounded-full bg-brand-50 px-2 py-0.5 text-[11px] font-bold capitalize text-brand-600 dark:bg-brand-500/15 dark:text-sky-300">
                {e.category}
              </span>
              <span className="text-[11px] text-ink-400">{e.updatedLabel}</span>
            </div>
            <p className="mt-2 text-[14px] font-bold">{e.title}</p>
            <p className="mt-1 text-[13px] leading-5 text-ink-700 dark:text-slate-300">{e.body}</p>
          </Card>
        ))}
      </div>

      <AnimatePresence>
        {modal && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-50 flex items-center justify-center bg-night-950/50 px-4"
            onClick={() => setModal(null)}
          >
            <motion.div
              initial={{ y: 12, scale: 0.98 }}
              animate={{ y: 0, scale: 1 }}
              exit={{ y: 8, scale: 0.98 }}
              onClick={(e) => e.stopPropagation()}
              className="w-full max-w-md rounded-2xl border border-mist-200 bg-white p-5 shadow-pop dark:border-white/10 dark:bg-night-900"
            >
              <div className="mb-3 flex items-center justify-between">
                <h3 className="text-[15px] font-bold">{modal === "note" ? "Add memory" : "Import knowledge"}</h3>
                <button onClick={() => setModal(null)} aria-label="Close"><X className="h-4 w-4 text-ink-400" /></button>
              </div>
              {modal === "note" ? (
                <div className="flex flex-col gap-2.5">
                  <div className="flex flex-wrap gap-1.5">
                    {(["company", "audience", "model", "brand", "goals", "decisions", "knowledge", "results"] as const).map((c) => (
                      <button
                        key={c}
                        onClick={() => setNoteCat(c)}
                        className={cn("rounded-full px-2.5 py-1 text-[12px] font-medium capitalize", noteCat === c ? "bg-brand-500 text-white" : "border border-mist-200 text-ink-500 dark:border-white/10")}
                      >
                        {c}
                      </button>
                    ))}
                  </div>
                  <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Title" className="h-10 rounded-[10px] border border-mist-200 bg-transparent px-3 text-[13.5px] outline-none focus:border-brand-500 dark:border-white/10" />
                  <textarea value={body} onChange={(e) => setBody(e.target.value)} placeholder="What should the team remember?" rows={4} className="rounded-[10px] border border-mist-200 bg-transparent p-3 text-[13.5px] outline-none focus:border-brand-500 dark:border-white/10" />
                  <PrimaryButton onClick={saveNote}>Save to Business Brain</PrimaryButton>
                </div>
              ) : (
                <div className="flex flex-col gap-2.5">
                  <p className="text-[13px] leading-5 text-ink-500">
                    Attach PDFs, docs, sheets or catalogs. Parsing is <b>simulated</b> in this demo —
                    the file never leaves your browser; only its name is registered.
                  </p>
                  <input
                    ref={fileRef}
                    type="file"
                    multiple
                    accept=".pdf,.doc,.docx,.xls,.xlsx,.csv,.txt"
                    onChange={(e) => {
                      const files = e.target.files ? Array.from(e.target.files).map((f) => f.name) : [];
                      if (files.length > 0) importFile(files.join(", "));
                    }}
                    className="text-[13px] file:mr-3 file:rounded-lg file:border-0 file:bg-brand-50 file:px-3 file:py-2 file:text-[12.5px] file:font-semibold file:text-brand-600 dark:file:bg-brand-500/15 dark:file:text-sky-300"
                  />
                  <GhostButton onClick={() => fileRef.current?.click()}>Choose files</GhostButton>
                </div>
              )}
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  );
}
