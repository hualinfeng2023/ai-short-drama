import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router'
import { App } from './App'
import { StudioProvider } from './store/StudioContext'
import './styles.css'
import './apple-design.css'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <StudioProvider>
        <App />
      </StudioProvider>
    </BrowserRouter>
  </StrictMode>,
)
