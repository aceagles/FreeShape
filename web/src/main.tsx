import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { App } from './App'
import { ConnectionStore } from './net/connection'
import './style.css'

const root = document.getElementById('root')
if (!root) throw new Error('#root is missing from index.html')
const store = new ConnectionStore()
store.start()
createRoot(root).render(
  <StrictMode>
    <App store={store} />
  </StrictMode>,
)
