import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'
import { reportClientError } from './api'

// Anything that escapes a component lands here. A crash in the render tree
// leaves a blank screen with nothing in the backend log — the request that
// served the bundle was a clean 200 — so without this the only record is a
// console the phone never shows. Reports are capped in reportClientError, so a
// render loop firing thousands of these can't flood the buffer.
window.addEventListener('error', e => {
  reportClientError(e?.message || 'Uncaught error', {
    detail: [e?.filename, e?.lineno && `line ${e.lineno}`, e?.error?.stack]
      .filter(Boolean).join('\n'),
  })
})
window.addEventListener('unhandledrejection', e => {
  const r = e?.reason
  reportClientError(
    `Unhandled promise rejection: ${r?.message || r || 'unknown'}`,
    { status: r?.status, request: r?.path, detail: r?.stack },
  )
})

if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {})
  })
}

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
