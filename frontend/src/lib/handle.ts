/** Splits "$INV-003.remit_account" at the last dot: ["$INV-003.", "remit_account"]. */
export function splitHandle(name: string): [namespace: string, field: string] {
  const dot = name.lastIndexOf('.')
  if (dot <= 0) return [name, '']
  return [name.slice(0, dot + 1), name.slice(dot + 1)]
}

/**
 * Maps each handle's namespace to the invoice it was extracted from, using only
 * what fields.extracted events reported (payload.invoice_id + payload.handles).
 * A handle whose namespace was never reported stays unmapped.
 */
export function handleNamespaces(
  extracted: Iterable<{ invoice_id: string; handles: { name: string }[] }>,
): Map<string, string> {
  const byNs = new Map<string, string>()
  for (const { invoice_id, handles } of extracted) {
    for (const h of handles) byNs.set(splitHandle(h.name)[0], invoice_id)
  }
  return byNs
}
