import type { AppState, ProjectSummary } from '../types'

type WorkspaceState = Pick<AppState, 'project' | 'jobs'>

export async function prepareCurrentProjectRecovery({
  projectId,
  fetchCurrentWorkspace,
  fetchProjectSummaries,
  clearLocalCache,
}: {
  projectId: string
  fetchCurrentWorkspace: (projectId: string) => Promise<WorkspaceState>
  fetchProjectSummaries: () => Promise<ProjectSummary[]>
  clearLocalCache: () => void
}) {
  const [workspace, projects] = await Promise.all([
    fetchCurrentWorkspace(projectId),
    fetchProjectSummaries(),
  ])

  // Only discard the fallback cache after SQLite has returned a complete replacement.
  clearLocalCache()
  return { workspace, projects }
}
