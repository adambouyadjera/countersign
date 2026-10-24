export type Currency = 'USD'

/** U+2212 MINUS SIGN: same advance width as digits in tabular figures. */
export const MINUS = '−'

const dollarsFormatter = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 })

/**
 * Format integer cents as a currency string without going through floating point,
 * so large values (up to Number.MAX_SAFE_INTEGER cents) stay exact.
 */
export function formatCents(cents: number, currency: Currency = 'USD'): string {
  if (!Number.isFinite(cents)) return '—'
  const rounded = Math.round(cents)
  const negative = rounded < 0
  const abs = Math.abs(rounded)
  const whole = Math.floor(abs / 100)
  const frac = abs % 100
  const symbol = currency === 'USD' ? '$' : ''
  const body = `${symbol}${dollarsFormatter.format(whole)}.${frac.toString().padStart(2, '0')}`
  return negative ? `${MINUS}${body}` : body
}

/** The mockups' short form: "$1,250" for whole dollars, full cents otherwise ("$12.50"). */
export function formatCentsShort(cents: number, currency: Currency = 'USD'): string {
  const full = formatCents(cents, currency)
  return Math.round(cents) % 100 === 0 ? full.replace(/\.00$/, '') : full
}
