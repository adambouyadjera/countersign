/**
 * Attack invoices carry a hidden CS-CANARY-XXXX marker. The backend masks it with spaces in
 * every UI-facing payload; this is a second guard so the UI never shows one even if a
 * payload slips through. Equal-length spaces keep character offsets (span highlights) intact.
 */
const CANARY = /CS-CANARY-[A-Za-z0-9]+/g

export function maskCanary(text: string): string {
  return text.replace(CANARY, (m) => ' '.repeat(m.length))
}
