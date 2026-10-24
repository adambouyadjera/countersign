/** Read at call time so tests can stub the flag with vi.stubEnv. */
export function isMockMode(): boolean {
  return import.meta.env.VITE_USE_MOCK === '1'
}

export function apiBaseUrl(): string {
  return import.meta.env.VITE_API_BASE_URL ?? ''
}
