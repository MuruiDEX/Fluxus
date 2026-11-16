import {
  useLiveApprovals,
  useLiveProjects,
  useLiveTasks,
} from "@/lib/store/live";

type ProjectsLoad = (businessId: string) => Promise<void>;
type TasksLoad = (businessId: string, projectId: string) => Promise<void>;
type ApprovalsLoad = (businessId: string) => Promise<void>;

export function loadLiveOfficeTasksForProject(
  businessId: string,
  projectId: string | null,
  projectBusinessId: string | null,
  load: TasksLoad = useLiveTasks.getState().load,
): Promise<void> {
  if (projectId === null || projectBusinessId !== businessId) {
    return Promise.resolve();
  }
  return load(businessId, projectId);
}

export function retryLiveOfficeProjects(
  businessId: string,
  load: ProjectsLoad = useLiveProjects.getState().load,
): Promise<void> {
  return load(businessId);
}

export function retryLiveOfficeTasks(
  businessId: string,
  projectId: string,
  load: TasksLoad = useLiveTasks.getState().load,
): Promise<void> {
  return load(businessId, projectId);
}

export function retryLiveOfficeApprovals(
  businessId: string,
  load: ApprovalsLoad = useLiveApprovals.getState().load,
): Promise<void> {
  return load(businessId);
}
