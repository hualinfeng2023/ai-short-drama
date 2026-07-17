import { createContext, useContext } from 'react'
import type { ProjectReadiness } from '../types'

export interface ProjectReadinessContextValue {
  loading: boolean
  readiness: ProjectReadiness | null
}

export const ProjectReadinessContext = createContext<ProjectReadinessContextValue>({
  loading: false,
  readiness: null,
})

export function useProjectReadiness(): ProjectReadinessContextValue {
  return useContext(ProjectReadinessContext)
}
