import { createContext, useContext } from 'react'
import type { ProjectReadiness } from '../types'

export interface ProjectReadinessContextValue {
  error: boolean
  loading: boolean
  readiness: ProjectReadiness | null
}

export const ProjectReadinessContext = createContext<ProjectReadinessContextValue>({
  error: false,
  loading: false,
  readiness: null,
})

export function useProjectReadiness(): ProjectReadinessContextValue {
  return useContext(ProjectReadinessContext)
}
