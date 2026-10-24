import { useCallback, useEffect, useState } from 'react'

export interface ReplayPlayer {
  /** How many events are shown: `total` when not playing (the final state). */
  shown: number
  playing: boolean
  play: () => void
  /** Stop and show the final state. */
  finish: () => void
}

/**
 * Steps through a recorded event list: `delays[i]` is the wait before event i appears.
 * Starts on the final state; play() rewinds to nothing and steps forward.
 * `resetKey` (e.g. the run id) returns to the final state when it changes.
 */
export function useReplayPlayer(delays: readonly number[], resetKey: string): ReplayPlayer {
  const total = delays.length
  const [state, setState] = useState({ key: resetKey, shown: total, playing: false })
  if (state.key !== resetKey) setState({ key: resetKey, shown: total, playing: false })

  const shown = state.key === resetKey ? Math.min(state.shown, total) : total
  const playing = state.key === resetKey && state.playing

  useEffect(() => {
    if (!playing || shown >= total) return
    const t = setTimeout(
      () => setState((s) => ({ ...s, shown: s.shown + 1, playing: s.shown + 1 < total })),
      delays[shown],
    )
    return () => clearTimeout(t)
  }, [playing, shown, total, delays])

  const play = useCallback(() => setState({ key: resetKey, shown: 0, playing: total > 0 }), [resetKey, total])
  const finish = useCallback(() => setState({ key: resetKey, shown: total, playing: false }), [resetKey, total])
  return { shown: playing ? shown : total, playing, play, finish }
}
