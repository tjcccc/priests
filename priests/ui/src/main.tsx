import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import './index.css'
import App from './App'
import ConfigPage from './ConfigPage'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Navigate to="/ui" replace />} />
        <Route path="/ui" element={<App />} />
        <Route path="/ui/session/:sessionId" element={<App />} />
        <Route path="/ui/config" element={<ConfigPage />} />
      </Routes>
    </BrowserRouter>
  </StrictMode>,
)
