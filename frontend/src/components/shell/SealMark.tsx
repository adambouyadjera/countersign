/** The Countersign mark: two interlocking rings (two signatures) inside a notched rim. */
export function SealMark({ size = 28, className }: { size?: number; className?: string }) {
  return (
    <svg
      aria-hidden
      viewBox="0 0 32 32"
      width={size}
      height={size}
      className={className}
      fill="none"
      stroke="currentColor"
    >
      <circle cx="16" cy="16" r="14.5" strokeWidth="1.4" strokeDasharray="2.2 1.3" />
      <circle cx="16" cy="16" r="11.5" strokeWidth="1" />
      <circle cx="13" cy="16" r="5.2" strokeWidth="1.5" />
      <circle cx="19" cy="16" r="5.2" strokeWidth="1.5" stroke="var(--hold)" />
      <path d="M8.5 23.5h15" strokeWidth="1" />
    </svg>
  )
}
