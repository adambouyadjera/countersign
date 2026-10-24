import type { Lane } from '@/api/types'

export const LANE_META: Record<Lane, { letter: string; title: string; short: string }> = {
  unguarded: { letter: 'A', title: 'Unguarded agent', short: 'Unguarded' },
  countersign: { letter: 'B', title: 'Countersign', short: 'Countersign' },
}
