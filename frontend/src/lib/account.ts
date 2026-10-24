export function lastFour(value: string): string {
  return value.replace(/\D/g, '').slice(-4)
}

export function maskAccount(value: string): string {
  const last = lastFour(value)
  return last ? `•••• ${last}` : '••••'
}
