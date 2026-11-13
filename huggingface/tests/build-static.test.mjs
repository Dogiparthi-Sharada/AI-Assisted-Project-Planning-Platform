import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { test } from 'node:test'

const script = fileURLToPath(new URL('../build-static.mjs', import.meta.url))
const valid = {
  VITE_SUPABASE_URL: 'https://example.supabase.co',
  VITE_SUPABASE_PUBLISHABLE_KEY: 'sb_publishable_test',
  VITE_API_URL: 'https://api.example.test',
}

function fixture(run) {
  const dir = mkdtempSync(path.join(tmpdir(), 'hf-build-test-'))
  try {
    mkdirSync(path.join(dir, 'bin'))
    mkdirSync(path.join(dir, 'huggingface'))
    const fakePnpm = path.join(dir, 'bin', 'pnpm')
    writeFileSync(fakePnpm, '#!/bin/sh\nprintf "%s\\n%s\\n%s\\n%s\\n" "$1" "$VITE_SUPABASE_URL" "$VITE_SUPABASE_PUBLISHABLE_KEY" "$VITE_API_URL" > "$MOCK_OUTPUT"\n', { mode: 0o755 })
    const baseEnv = { ...process.env, PATH: path.join(dir, 'bin'), MOCK_OUTPUT: path.join(dir, 'output') }
    delete baseEnv.VITE_SUPABASE_URL
    delete baseEnv.VITE_SUPABASE_PUBLISHABLE_KEY
    delete baseEnv.VITE_API_URL
    function execute(values = {}) {
      return spawnSync(process.execPath, [script], {
        cwd: dir, env: { ...baseEnv, ...values }, encoding: 'utf8', timeout: 10000,
      })
    }
    run({ dir, execute })
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
}

test('fails closed when public configuration is missing', () => fixture(({ execute }) => {
  const result = execute()
  assert.equal(result.status, 1)
  assert.match(result.stderr, /Missing or placeholder public config: VITE_SUPABASE_URL/)
}))

test('rejects a non-HTTPS API URL', () => fixture(({ execute }) => {
  const result = execute({ ...valid, VITE_API_URL: 'http://api.example.test' })
  assert.equal(result.status, 1)
  assert.match(result.stderr, /VITE_API_URL must be an absolute HTTPS URL/)
}))

test('rejects a Supabase secret key', () => fixture(({ execute }) => {
  const result = execute({ ...valid, VITE_SUPABASE_PUBLISHABLE_KEY: 'sb_secret_test' })
  assert.equal(result.status, 1)
  assert.match(result.stderr, /secret key must never be in the browser bundle/)
}))

test('passes valid public configuration to pnpm build', () => fixture(({ dir, execute }) => {
  const result = execute(valid)
  assert.equal(result.status, 0, result.stderr)
  assert.equal(readFileSync(path.join(dir, 'output'), 'utf8'), [
    'build', valid.VITE_SUPABASE_URL, valid.VITE_SUPABASE_PUBLISHABLE_KEY, valid.VITE_API_URL, '',
  ].join('\n'))
}))

test('reads the public config file when environment values are absent', () => fixture(({ dir, execute }) => {
  writeFileSync(path.join(dir, 'huggingface', 'public-config.json'), JSON.stringify(valid))
  const result = execute()
  assert.equal(result.status, 0, result.stderr)
  assert.match(readFileSync(path.join(dir, 'output'), 'utf8'), /https:\/\/api\.example\.test/)
}))
