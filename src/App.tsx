import { lazy } from 'react'
import { Navigate, Route, Routes } from 'react-router'
import { AppShell } from './components/AppShell'

const CharactersPage = lazy(() => import('./pages/CharactersPage').then((module) => ({ default: module.CharactersPage })))
const EpisodePage = lazy(() => import('./pages/EpisodePage').then((module) => ({ default: module.EpisodePage })))
const NewProjectPage = lazy(() => import('./pages/NewProjectPage').then((module) => ({ default: module.NewProjectPage })))
const PreviewPage = lazy(() => import('./pages/PreviewPage').then((module) => ({ default: module.PreviewPage })))
const PreproductionPage = lazy(() => import('./pages/PreproductionPage').then((module) => ({ default: module.PreproductionPage })))
const ProductionPage = lazy(() => import('./pages/ProductionPage').then((module) => ({ default: module.ProductionPage })))
const ProjectBriefPage = lazy(() => import('./pages/ProjectBriefPage').then((module) => ({ default: module.ProjectBriefPage })))
const ProjectsPage = lazy(() => import('./pages/ProjectsPage').then((module) => ({ default: module.ProjectsPage })))
const ReviewsPage = lazy(() => import('./pages/ReviewsPage').then((module) => ({ default: module.ReviewsPage })))
const SettingsPage = lazy(() => import('./pages/SettingsPage').then((module) => ({ default: module.SettingsPage })))
const StoryPage = lazy(() => import('./pages/StoryPage').then((module) => ({ default: module.StoryPage })))
const StoryboardPage = lazy(() => import('./pages/StoryboardPage').then((module) => ({ default: module.StoryboardPage })))
const ShotWorkspacePage = lazy(() => import('./pages/ShotWorkspacePage').then((module) => ({ default: module.ShotWorkspacePage })))
const TasksPage = lazy(() => import('./pages/TasksPage').then((module) => ({ default: module.TasksPage })))

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
