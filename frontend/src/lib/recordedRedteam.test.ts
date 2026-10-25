import { describe, expect, it } from 'vitest'
import type { RedTeamJob } from '@/api/types'
import { redteamHeadline } from './recordedRedteam'

const job = (distinct: number) =>
  ({ variants: [], metrics: { variants: 40, unguarded_compromised: 0, countersign_unauthorized: 0, countersign_held: 2, false_holds_on_controls: 0, distinct_planner_prompts: distinct } }) as unknown as RedTeamJob

describe('redteamHeadline', () => {
  it('says the planner input was identical only when there was exactly one', () => {
    expect(redteamHeadline(job(1))).toBe("40 different attacks. The planner's input was identical.")
    expect(redteamHeadline(job(2))).toBe('40 different attacks. The planner saw 2 distinct inputs.')
  })
})
