import type { ServiceHealth } from '@/api/types'

/** "live", "offline", or what is wrong: the word after each service name in the header. */
export function serviceWord(s: ServiceHealth): string {
  switch (s.status) {
    case 'ok':
      return s.mode
    case 'offline':
      return 'offline'
    case 'missing_key':
      return 'missing key'
    case 'error':
      return 'error'
  }
}
