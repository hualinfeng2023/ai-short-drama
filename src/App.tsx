import { lazy, type ComponentType } from 'react'
import { Navigate, Route, Routes } from 'react-router'
import { AppShell } from './components/AppShell'

const ROUTE_LOAD_TIMEOUT_MS = 12_000

function lazyRoute<T extends ComponentType>(loader: () => Promise<T>) {
  return lazy(async () => {
    let timeoutId = 0
    const timeout = new Promise<never>((_, reject) => {
      timeoutId = window.setTimeout(() => {
        reject(new Error('页面模块加载超时，请刷新后重试。'))
      }, ROUTE_LOAD_TIMEOUT_MS)
    })

    try {
      return { default: await Promise.race([loader(), timeout]) }
    } finally {
      window.clearTimeout(timeoutId)
    }
  })
}

const CharactersPage = lazyRoute(() => import('./pages/CharactersPage').then((module) => module.CharactersPage))
const EpisodePage = lazyRoute(() => import('./pages/EpisodePage').then((module) => module.EpisodePage))
const NewProjectPage = lazyRoute(() => import('./pages/NewProjectPage').then((module) => module.NewProjectPage))
const PreviewPage = lazyRoute(() => import('./pages/PreviewPage').then((module) => module.PreviewPage))
const PreproductionPage = lazyRoute(() => import('./pages/PreproductionPage').then((module) => module.PreproductionPage))
const ProductionPage = lazyRoute(() => import('./pages/ProductionPage').then((module) => module.ProductionPage))
const ProjectBriefPage = lazyRoute(() => import('./pages/ProjectBriefPage').then((module) => module.ProjectBriefPage))
const ProjectsPage = lazyRoute(() => import('./pages/ProjectsPage').then((module) => module.ProjectsPage))
const ReviewsPage = lazyRoute(() => import('./pages/ReviewsPage').then((module) => module.ReviewsPage))
const SettingsPage = lazyRoute(() => import('./pages/SettingsPage').then((module) => module.SettingsPage))
const StoryPage = lazyRoute(() => import('./pages/StoryPage').then((module) => module.StoryPage))
const StoryboardPage = lazyRoute(() => import('./pages/StoryboardPage').then((module) => module.StoryboardPage))
const ShotWorkspacePage = lazyRoute(() => import('./pages/ShotWorkspacePage').then((module) => module.ShotWorkspacePage))
const TasksPage = lazyRoute(() => import('./pages/TasksPage').then((module) => module.TasksPage))

export function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<Navigate replace to="/projects" />} />
        <Route path="projects" element={<ProjectsPage />} />
        <Route path="projects/new" element={<NewProjectPage />} />
        <Route path="projects/:projectId" element={<ProjectBriefPage />} />
        <Route path="projects/:projectId/story" element={<StoryPage />} />
        <Route path="projects/:projectId/preproduction" element={<PreproductionPage />} />
        <Route path="projects/:projectId/storyboard" element={<StoryboardPage />} />
        <Route path="projects/:projectId/production" element={<ProductionPage />} />
        <Route path="projects/:projectId/characters" element={<CharactersPage />} />
        <Route path="projects/:projectId/episodes/:episodeId" element={<EpisodePage />} />
        <Route
          path="projects/:projectId/episodes/:episodeId/scenes/:sceneId"
          element={<ShotWorkspacePage />}
        />
        <Route
          path="projects/:projectId/episodes/:episodeId/preview"
          element={<PreviewPage />}
        />
        <Route path="tasks" element={<TasksPage />} />
        <Route path="reviews" element={<ReviewsPage />} />
        <Route path="settings" element={<SettingsPage />} />
        <Route path="*" element={<Navigate replace to="/projects" />} />
      </Route>
    </Routes>
  )
}
