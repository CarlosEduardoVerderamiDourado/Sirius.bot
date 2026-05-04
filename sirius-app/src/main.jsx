import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import SiriusApp from './SiriusApp.jsx'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <SiriusApp />
  </StrictMode>,
)
