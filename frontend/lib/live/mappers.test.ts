import { describe, expect, it } from "vitest";
import { toDomainActivityEvent, toDomainAgent, toDomainApproval, toDomainBrainEntry, toDomainProject, toDomainTask } from "@/lib/live/mappers";
import type { ApiActivityEvent, ApiAgent, ApiApproval, ApiBrainEntry, ApiProject, ApiTask } from "@/lib/api";

const WIRE_TASK: ApiTask = {
  id: "t_1",
  businessId: "biz_test",
  projectId: "proj_1",
  title: "T",
  ownerAgentId: "ag_1",
  status: "working",
  progress: 42,
  costAccrued: "2.20",
  etaMinutes: 9,
  summary: "S",
  attempts: 1,
  approvalGateId: "ap_1",
  gateAt: 80,
  costRate: "0.07",
  contract: "v1",
};

describe("wire to domain mappers", () => {
  it("parses money strings and keeps nullable fields", () => {
    const task = toDomainTask(WIRE_TASK);
    expect(task.costAccrued).toBeCloseTo(2.2);
    expect(task.costRate).toBeCloseTo(0.07);
    expect(task.attempts).toBe(1);
    expect(task.approvalGateId).toBe("ap_1");
    expect(task.gateAt).toBe(80);
    expect(task.status).toBe("working");
  });

  it("maps nulls to undefined and never invents dependencies", () => {
    const task = toDomainTask({ ...WIRE_TASK, attempts: null, approvalGateId: null, gateAt: null, costRate: null });
    expect(task.attempts).toBeUndefined();
    expect(task.approvalGateId).toBeUndefined();
    expect(task.gateAt).toBeUndefined();
    expect(task.costRate).toBeUndefined();
    // Backend exposes no dependency endpoint yet: honest empty list.
    expect(task.dependsOn).toEqual([]);
  });

  it("accepts numeric money too", () => {
    const task = toDomainTask({ ...WIRE_TASK, costAccrued: 3.5 });
    expect(task.costAccrued).toBe(3.5);
  });

  it("maps projects with parsed spend", () => {
    const project = toDomainProject({
      id: "proj_1",
      businessId: "biz_test",
      title: "P",
      goal: "G",
      status: "in_progress",
      progress: 30,
      budget: "1000.00",
      aiBudget: "50.00",
      aiSpent: "2.20",
      deadline: "30 May 2026",
      teamIds: ["ag_1"],
      milestones: [{ label: "M", done: true }],
      contract: "v1",
    } satisfies ApiProject);
    expect(project.budget).toBe(1000);
    expect(project.aiSpent).toBeCloseTo(2.2);
    expect(project.teamIds).toEqual(["ag_1"]);
    expect(project.milestones).toEqual([{ label: "M", done: true }]);
  });

  it("maps agents with safe defaults", () => {
    const agent = toDomainAgent({
      id: "ag_1",
      businessId: "biz_test",
      name: "Atlas",
      role: "Lead",
      responsibilities: ["x"],
      tools: [],
      permissions: {},
      status: "available",
      currentTask: null,
      costPerMonth: 9,
      performance: {},
      hired: true,
      avatarHue: 200,
      contract: "v1",
    } satisfies ApiAgent);
    expect(agent.permissions).toEqual({ read: [], write: [], actions: [], denied: [] });
    expect(agent.performance).toEqual({
      completionRate: 0,
      avgMinutes: 0,
      approvalRate: 0,
      rating: 0,
    });
    expect(agent.currentTask).toBeUndefined();
  });

  it("maps approvals field-for-field, gatesTaskId optional", () => {
    const wire: ApiApproval = {
      id: "ap_1",
      businessId: "biz_test",
      projectId: "proj_1",
      title: "Ship?",
      requestedBy: "Atlas",
      requestedByAgentId: "ag_1",
      ageLabel: "2h",
      status: "pending",
      what: "W",
      expectedResult: "E",
      cost: "$10",
      affects: "A",
      rationale: "R",
      gatesTaskId: "t_1",
      contract: "v1",
    };
    const approval = toDomainApproval(wire);
    expect(approval.status).toBe("pending");
    expect(approval.gatesTaskId).toBe("t_1");
    expect(toDomainApproval({ ...wire, gatesTaskId: null }).gatesTaskId).toBeUndefined();
  });

  it("maps activity events, parsing nullable cost, keeping the ISO timestamp", () => {
    const event = toDomainActivityEvent({
      id: "ev_1",
      at: "2026-10-11T06:00:00Z",
      agentName: "Atlas",
      text: "did a thing",
      kind: "result",
      cost: "1.25",
      contract: "v1",
    } satisfies ApiActivityEvent);
    expect(event.at).toBe("2026-10-11T06:00:00Z");
    expect(event.cost).toBeCloseTo(1.25);
    expect(toDomainActivityEvent({ id: "e", at: "", agentName: "", text: "", kind: "work", cost: null, contract: "v1" }).cost).toBeUndefined();
  });

  it("maps brain entries field-for-field", () => {
    const entry = toDomainBrainEntry({
      id: "br_1",
      businessId: "biz_test",
      category: "goals",
      title: "T",
      body: "B",
      updatedLabel: "2h ago",
      contract: "v1",
    } satisfies ApiBrainEntry);
    expect(entry).toEqual({
      id: "br_1",
      businessId: "biz_test",
      category: "goals",
      title: "T",
      body: "B",
      updatedLabel: "2h ago",
    });
  });
});
