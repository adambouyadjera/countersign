import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { Card, InkButton } from '@/components/ui'

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
    <Card
      as="form"
      foldedCorner={false}
      onSubmit={submit}
      aria-label="Challenge Sig"
      className="flex shrink-0 items-center gap-2.5 px-3.5 py-2.5"
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
      <InkButton type="submit" size="sm">
        send
      </InkButton>
    </Card>
  )
}
