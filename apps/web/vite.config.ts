import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import { productionApiUrlProblem } from './src/lib/apiUrl'

export default defineConfig(({ command, mode }) => {
  // A production build must never point the browser at a development backend.
  if (command === 'build' && mode === 'production') {
    const problem = productionApiUrlProblem(loadEnv(mode, '.', 'VITE_').VITE_API_URL)
    if (problem) throw new Error(`${problem}. Build with VITE_API_URL=/api for the same-origin deployment.`)
  }
  return { plugins: [react()], server: { port: 5173 } }
})
