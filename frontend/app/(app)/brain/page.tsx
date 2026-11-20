"use client";

import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Plus, Search, Upload, X } from "lucide-react";
import { Card, GhostButton, LiveBadge, PageHeader, PrimaryButton } from "@/components/system/ui";
import { Badge } from "@/components/system/badges";
import { ChoiceChip, IconButton, Input, Textarea } from "@/components/system/controls";
import { useModalSurface } from "@/lib/use-modal-surface";
import { transition } from "@/lib/motion";
import { BrainCreateButton } from "@/components/system/BrainCreate";
import { useSession } from "@/lib/store/auth";
import { isLiveContext, useLiveBrain } from "@/lib/store/live";
import { useBrain, useBusiness } from "@/lib/store/app";
import type { BrainEntry } from "@/lib/domain/types";
import type { BrainCategory } from "@/lib/api";
import { cn } from "@/lib/cn";

const cats = ["all", "company", "audience", "model", "brand", "goals", "decisions", "knowledge", "results"] as const;

type Cat = (typeof cats)[number];

function EntryGrid({ entries }: { entries: BrainEntry[] }) {
  return (
    <div className="grid gap-2.5 md:grid-cols-2 xl:grid-cols-3">
      {entries.map((e) => (
        <Card key={e.id} className="p-4">
          <div className="flex items-center justify-between gap-2">
            <Badge tone="brand" size="xs">
              {e.category}
            </Badge>
            <span className="text-label text-fg-subtle">{e.updatedLabel}</span>
          </div>
          <p className="mt-2 text-body font-bold">{e.title}</p>
          <p className="mt-1 text-body-sm leading-5 text-fg-muted">{e.body}</p>
        </Card>
      ))}
    </div>
  );
}

let noteSeq = 0;
const nextNoteId = () => {
  noteSeq += 1;
  return `brain_note_${noteSeq}`;
};

export default function BrainPage() {
  const { status, currentBusinessId } = useSession();
  if (isLiveContext(status, currentBusinessId)) {
    return <LiveBrainView businessId={currentBusinessId} />;
  }
  return <DemoBrainView />;
}

function DemoBrainView() {
  const entries = useBrain((s) => s.entries);
  const addEntry = useBrain((s) => s.addEntry);
  const { currentBusinessId } = useBusiness();
  const [cat, setCat] = useState<Cat>("all");
  const [q, setQ] = useState("");
  const [modal, setModal] = useState<"note" | "import" | null>(null);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [noteCat, setNoteCat] = useState<BrainEntry["category"]>("knowledge");
  const fileRef = useRef<HTMLInputElement>(null);
  const modalRef = useRef<HTMLDivElement | null>(null);
  // The demo modal shares the same scroll lock, focus trap and Escape contract
  // as the server-backed dialogs instead of being a second, weaker variant.
  useModalSurface(modalRef, modal !== null, { onEscape: () => setModal(null) });
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
      <EntryGrid entries={visible} />

      <AnimatePresence>
        {modal && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={transition.fast}
            className="fixed inset-0 z-50 flex overflow-y-auto overscroll-contain bg-overlay px-4 py-6 sm:px-6"
            onClick={() => setModal(null)}
          >
            <motion.div
              ref={modalRef}
              initial={{ y: 12, scale: 0.98, opacity: 0 }}
              animate={{ y: 0, scale: 1, opacity: 1 }}
              exit={{ y: 8, scale: 0.98, opacity: 0 }}
              transition={transition.overlay}
              onClick={(e) => e.stopPropagation()}
              role="dialog"
              aria-modal="true"
              aria-label={modal === "note" ? "Add memory" : "Import knowledge"}
              className="m-auto w-full max-w-md rounded-card border border-line bg-surface p-5 text-fg shadow-pop"
            >
              <div className="mb-3 flex items-center justify-between gap-3 border-b border-line pb-2.5">
                <h3 className="text-heading-sm font-bold tracking-tight">
                  {modal === "note" ? "Add memory" : "Import knowledge"}
                </h3>
                <IconButton
                  variant="ghost"
                  size="sm"
                  label="Close"
                  onClick={() => setModal(null)}
                  className="-mr-1"
                >
                  <X className="h-4 w-4" />
                </IconButton>
              </div>
              {modal === "note" ? (
                <div className="flex flex-col gap-2.5">
                  <div role="group" aria-label="Category" className="flex flex-wrap gap-1.5">
                    {(["company", "audience", "model", "brand", "goals", "decisions", "knowledge", "results"] as const).map((c) => (
                      <ChoiceChip key={c} pressed={noteCat === c} onClick={() => setNoteCat(c)}>
                        {c}
                      </ChoiceChip>
                    ))}
                  </div>
                  <Input
                    value={title}
                    onChange={(e) => setTitle(e.target.value)}
                    placeholder="Title"
                    aria-label="Memory title"
                  />
                  <Textarea
                    value={body}
                    onChange={(e) => setBody(e.target.value)}
                    placeholder="What should the team remember?"
                    aria-label="Memory content"
                    rows={4}
                  />
                  <PrimaryButton
                    onClick={saveNote}
                    disabled={!title.trim() || !body.trim()}
                    className="justify-center"
                  >
                    Save to Business Brain
                  </PrimaryButton>
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

function LiveBrainView({ businessId }: { businessId: string }) {
  const { status, entries, total, error, load, reset } = useLiveBrain();
  const [cat, setCat] = useState<Cat>("all");
  const [q, setQ] = useState("");

  const category: BrainCategory | undefined = cat === "all" ? undefined : cat;

  useEffect(() => {
    void load({ businessId, category });
    return () => reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [businessId, cat]);

  const visible = entries.filter((e) =>
    (e.title + e.body).toLowerCase().includes(q.toLowerCase()),
  );

  return (
    <>
      <PageHeader
        title="Business Brain"
        sub="Server memory for your business. New notes are saved to the backend."
        badge={<LiveBadge />}
        actions={<BrainCreateButton businessId={businessId} />}
      />
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex h-9 max-w-xs flex-1 items-center gap-2 rounded-[10px] border border-mist-200 bg-white px-3 dark:border-white/10 dark:bg-night-900">
          <Search className="h-4 w-4 text-ink-400" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search loaded memory…" className="w-full bg-transparent text-[13px] outline-none" />
        </div>
        <div className="flex flex-wrap gap-1.5">
          {cats.map((c) => (
            <button
              key={c}
              onClick={() => setCat(c)}
              aria-pressed={cat === c}
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
      {status === "loading" || status === "idle" ? (
        <Card className="p-8 text-center text-[13px] text-ink-400">Loading memory…</Card>
      ) : status === "error" ? (
        <Card className="p-8 text-center">
          <p className="text-[14px] font-semibold">Couldn&apos;t load memory</p>
          <p className="mx-auto mt-1 max-w-md text-[13px] text-ink-500">{error}</p>
          <div className="mt-3 flex justify-center">
            <PrimaryButton className="justify-center" onClick={() => void load({ businessId, category })}>
              Retry
            </PrimaryButton>
          </div>
        </Card>
      ) : visible.length === 0 ? (
        <Card className="p-8 text-center text-[13px] text-ink-400">
          {entries.length === 0
            ? "No memory stored for this business yet."
            : "No entries match this search in the loaded page."}
        </Card>
      ) : (
        <>
          <EntryGrid entries={visible} />
          <p className="text-[11.5px] text-ink-400">
            Showing {visible.length} of {total} stored entries{q ? " matching this search" : ""}.
          </p>
        </>
      )}
    </>
  );
}
