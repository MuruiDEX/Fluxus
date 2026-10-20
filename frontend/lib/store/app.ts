"use client";

import { create } from "zustand";
import type {
  ActivityEvent,
  Agent,
  Approval,
  ApprovalStatus,
  AutopilotLevel,
  BrainEntry,
  Business,
  MarketplaceAgent,
  Project,
  Task,
} from "@/lib/domain/types";
import {
  BUSINESS_ID,
  activity as seedActivity,
  agents as seedAgents,
  approvals as seedApprovals,
  brain as seedBrain,
  business as seedBusiness,
  marketplace as seedMarketplace,
  project as seedProject,
  tasks as seedTasks,
} from "@/lib/data/seed-northstar";

interface BusinessState {
  currentBusinessId: string;
  business: Business;
}

export const useBusiness = create<BusinessState>(() => ({
  currentBusinessId: BUSINESS_ID,
  business: seedBusiness,
}));

interface ProjectState {
  projects: Project[];
  tasks: Task[];
  activeProjectId: string;
  setActiveProject: (id: string) => void;
  updateTask: (id: string, patch: Partial<Task>) => void;
  setProjectProgress: (id: string, progress: number) => void;
  addProject: (p: Project, t: Task[]) => void;
  /** Engine-only bulk application (single source of truth is the orchestrator). */
  replaceTasks: (tasks: Task[]) => void;
  updateProject: (id: string, patch: Partial<Project>) => void;
}

export const useProjects = create<ProjectState>((set) => ({
  projects: [seedProject],
  tasks: seedTasks,
  activeProjectId: seedProject.id,
  setActiveProject: (id) => set({ activeProjectId: id }),
  updateTask: (id, patch) =>
    set((s) => ({ tasks: s.tasks.map((t) => (t.id === id ? { ...t, ...patch } : t)) })),
  setProjectProgress: (id, progress) =>
    set((s) => ({ projects: s.projects.map((p) => (p.id === id ? { ...p, progress } : p)) })),
  addProject: (p, t) => set((s) => ({ projects: [...s.projects, p], tasks: [...s.tasks, ...t], activeProjectId: p.id })),
  replaceTasks: (tasks) => set({ tasks }),
  updateProject: (id, patch) =>
    set((s) => ({ projects: s.projects.map((p) => (p.id === id ? { ...p, ...patch } : p)) })),
}));

interface AgentState {
  agents: Agent[];
  setStatus: (id: string, status: Agent["status"], currentTask?: string) => void;
  hire: (agentId: string) => void;
  addAgent: (a: Agent) => void;
  /** Engine-only bulk application. */
  replaceAgents: (agents: Agent[]) => void;
}

export const useAgents = create<AgentState>((set) => ({
  agents: seedAgents,
  setStatus: (id, status, currentTask) =>
    set((s) => ({
      agents: s.agents.map((a) =>
        a.id === id ? { ...a, status, currentTask: currentTask ?? a.currentTask } : a,
      ),
    })),
  hire: (agentId) =>
    set((s) => ({ agents: s.agents.map((a) => (a.id === agentId ? { ...a, hired: true, status: "available" } : a)) })),
  addAgent: (a) => set((s) => ({ agents: [...s.agents, a] })),
  replaceAgents: (agents) => set({ agents }),
}));

interface ApprovalState {
  approvals: Approval[];
  decide: (id: string, status: ApprovalStatus) => void;
  updateApproval: (id: string, patch: Partial<Approval>) => void;
  /** Engine-only bulk application (reset). */
  replaceApprovals: (approvals: Approval[]) => void;
}

export const useApprovals = create<ApprovalState>((set) => ({
  approvals: seedApprovals,
  decide: (id, status) =>
    set((s) => ({ approvals: s.approvals.map((a) => (a.id === id ? { ...a, status } : a)) })),
  updateApproval: (id, patch) =>
    set((s) => ({ approvals: s.approvals.map((a) => (a.id === id ? { ...a, ...patch } : a)) })),
  replaceApprovals: (approvals) => set({ approvals }),
}));

interface BrainState {
  entries: BrainEntry[];
  addEntry: (e: BrainEntry) => void;
}

export const useBrain = create<BrainState>((set) => ({
  entries: seedBrain,
  addEntry: (e) => set((s) => ({ entries: [e, ...s.entries] })),
}));

interface MarketState {
  listings: MarketplaceAgent[];
  hiredSlugs: string[];
  installedSlugs: string[];
  hireSlug: (slug: string) => void;
  installSlug: (slug: string) => void;
}

export const useMarketplace = create<MarketState>((set) => ({
  listings: seedMarketplace,
  hiredSlugs: ["sales-manager", "marketing-manager"],
  installedSlugs: [],
  hireSlug: (slug) => set((s) => (s.hiredSlugs.includes(slug) ? s : { hiredSlugs: [...s.hiredSlugs, slug] })),
  installSlug: (slug) =>
    set((s) => (s.installedSlugs.includes(slug) ? s : { installedSlugs: [...s.installedSlugs, slug] })),
}));

interface ActivityState {
  events: ActivityEvent[];
  push: (e: ActivityEvent) => void;
  log: (e: Omit<ActivityEvent, "id">) => void;
}

let eventSeq = 100;

export const useActivity = create<ActivityState>((set) => ({
  events: seedActivity,
  push: (e) => set((s) => ({ events: [e, ...s.events].slice(0, 60) })),
  log: (e) => {
    eventSeq += 1;
    const full: ActivityEvent = { ...e, id: `ev_${eventSeq}` };
    set((s) => ({ events: [full, ...s.events].slice(0, 60) }));
  },
}));

interface UIState {
  commandOpen: boolean;
  autopilot: AutopilotLevel;
  setCommandOpen: (open: boolean) => void;
  setAutopilot: (level: AutopilotLevel) => void;
}

export const useUI = create<UIState>((set) => ({
  commandOpen: false,
  autopilot: "assist",
  setCommandOpen: (open) => set({ commandOpen: open }),
  setAutopilot: (level) => set({ autopilot: level }),
}));
