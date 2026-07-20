import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router'
import { App } from './App'
import { StudioProvider } from './store/StudioContext'
import { ToastProvider } from './store/ToastContext'
import './design-system/tokens.css'
import './styles.css'
import './apple-design.css'
import './design-system/components.css'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <StudioProvider>
        <ToastProvider>
          <App />
        </ToastProvider>
      </StudioProvider>
    </BrowserRouter>
  </StrictMode>,
)
