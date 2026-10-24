import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'

/** The console's ">" prompt: opens the Attack Lab with the typed text as the draft. */
export function ChallengeInput() {
  const [text, setText] = useState('')
  const navigate = useNavigate()
  const submit = (e: FormEvent) => {
    e.preventDefault()
    const draft = text.trim()
    navigate(draft ? `/attack?draft=${encodeURIComponent(draft)}` : '/attack')
  }
  return (
    <form
      onSubmit={submit}
      aria-label="Challenge Sig"
      className="flex shrink-0 items-center gap-2.5 rounded-[8px] border border-line px-3.5 py-2.5"
    >
      <label htmlFor="challenge" className="text-accent">
        &gt;<span className="sr-only">Write your own invoice to challenge Sig</span>
      </label>
      <input
        id="challenge"
        type="text"
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="Write your own invoice to challenge Sig"
        className="min-h-7 min-w-0 flex-1 border-0 bg-transparent font-[inherit] text-fg outline-none placeholder:text-muted"
      />
      <button
        type="submit"
        className="min-h-9 cursor-pointer rounded-[6px] border-0 bg-accent px-3.5 font-[inherit] font-bold text-on-accent"
      >
        send
      </button>
    </form>
  )
}
