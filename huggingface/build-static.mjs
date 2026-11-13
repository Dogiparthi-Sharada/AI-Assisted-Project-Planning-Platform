// The three values below are public: Vite puts them into browser JavaScript.
// Use a dedicated demo project. Never use server credentials here.
import { readFileSync } from 'node:fs'
import { spawnSync } from 'node:child_process'

const names = ['VITE_SUPABASE_URL', 'VITE_SUPABASE_PUBLISHABLE_KEY', 'VITE_API_URL']
let publicConfig = {}
try {
  publicConfig = JSON.parse(readFileSync('huggingface/public-config.json', 'utf8'))
} catch (error) {
  if (error.code !== 'ENOENT') throw error
}
const env = { ...process.env }
for (const name of names) {
  const value = env[name] || publicConfig[name]
  if (typeof value !== 'string' || !value.trim() || /replace_me|your-project-ref|your-api-host/.test(value)) {
    console.error(`Missing or placeholder public config: ${name}`)
    process.exit(1)
  }
  env[name] = value.trim()
}
for (const name of ['VITE_SUPABASE_URL', 'VITE_API_URL']) {
  try {
    if (new URL(env[name]).protocol !== 'https:') throw new Error('https required')
  } catch {
    console.error(`${name} must be an absolute HTTPS URL`)
    process.exit(1)
  }
}
if (env.VITE_SUPABASE_PUBLISHABLE_KEY.startsWith('sb_secret_')) {
  console.error('A Supabase secret key must never be in the browser bundle')
  process.exit(1)
}
const result = spawnSync('pnpm', ['build'], { env, stdio: 'inherit' })
if (result.error) {
  console.error('Unable to start pnpm:', result.error.message)
  process.exit(1)
}
process.exit(result.status ?? 1)
