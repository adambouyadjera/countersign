import { useId } from 'react'
import { motion, useReducedMotion } from 'framer-motion'
import { cn } from '@/lib/cn'

export type SealLabel = 'HELD' | 'COUNTERSIGNED'

export interface SealProps {
  label: SealLabel
  /** Rendered width/height in px. */
  size?: number
  /** Play the stamp-landing animation on mount. Change `stampKey` to replay. */
  stamp?: boolean
  stampKey?: string | number
  /** Resting tilt in degrees; real stamps never land square. */
  tilt?: number
  className?: string
}

const RING_TEXT: Record<SealLabel, string> = {
  HELD: 'COUNTERSIGN • PAYMENT HELD FOR REVIEW • ',
  COUNTERSIGNED: 'COUNTERSIGN • PROVENANCE VERIFIED • ',
}

/** Serrated rim: alternating radii around the circle, like an embossed foil seal. */
function rimPoints(teeth: number, outer: number, inner: number, c = 100): string {
  const pts: string[] = []
  for (let i = 0; i < teeth * 2; i++) {
    const r = i % 2 === 0 ? outer : inner
    const a = (Math.PI * i) / teeth - Math.PI / 2
    pts.push(`${(c + r * Math.cos(a)).toFixed(2)},${(c + r * Math.sin(a)).toFixed(2)}`)
  }
  return pts.join(' ')
}

const RIM = rimPoints(54, 97, 92)

export function Seal({ label, size = 160, stamp = true, stampKey, tilt = -9, className }: SealProps) {
  const uid = useId().replace(/:/g, '')
  const reduce = useReducedMotion()
  const animateIn = stamp && !reduce
  const ring = `seal-ring-${uid}`
  const ink = `seal-ink-${uid}`
  const long = label === 'COUNTERSIGNED'

  return (
    <motion.div
      key={stampKey}
      role="img"
      aria-label={label === 'HELD' ? 'Payment held seal' : 'Countersigned seal'}
      data-seal={label}
      className={cn('relative inline-block shrink-0 text-hold', className)}
      style={{ width: size, height: size }}
      initial={animateIn ? { scale: 1.85, opacity: 0, rotate: tilt - 16, y: -18 } : false}
      animate={{ scale: 1, opacity: 1, rotate: tilt, y: 0 }}
      transition={
        animateIn
          ? { type: 'spring', stiffness: 520, damping: 26, mass: 0.9, opacity: { duration: 0.12 } }
          : { duration: 0 }
      }
    >
      {animateIn && (
        // Ink pressed outward on impact: a single ring that spreads and fades.
        <motion.span
          aria-hidden
          className="pointer-events-none absolute inset-0 rounded-full border-2 border-hold"
          initial={{ scale: 0.9, opacity: 0 }}
          animate={{ scale: [0.9, 1.18], opacity: [0, 0.45, 0] }}
          transition={{ duration: 0.55, delay: 0.12, ease: 'easeOut' }}
        />
      )}
      <svg viewBox="0 0 200 200" width={size} height={size} className="block overflow-visible">
        <defs>
          <path id={ring} d="M100,100 m-73,0 a73,73 0 1,1 146,0 a73,73 0 1,1 -146,0" />
          {/* Uneven ink: displace edges slightly and knock out a few specks. */}
          <filter id={ink} x="-5%" y="-5%" width="110%" height="110%">
            <feTurbulence type="fractalNoise" baseFrequency="0.7" numOctaves="2" seed="7" result="n" />
            <feDisplacementMap in="SourceGraphic" in2="n" scale="1.4" result="d" />
            <feTurbulence type="fractalNoise" baseFrequency="0.06" numOctaves="3" seed="3" result="blot" />
            <feColorMatrix
              in="blot"
              type="matrix"
              values="0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 -1.5 1.75"
              result="mask"
            />
            <feComposite in="d" in2="mask" operator="in" />
          </filter>
        </defs>
        <g filter={`url(#${ink})`} fill="none" stroke="currentColor">
          <polygon points={RIM} strokeWidth="2.2" strokeLinejoin="round" />
          <circle cx="100" cy="100" r="86" strokeWidth="3" />
          <circle cx="100" cy="100" r="60" strokeWidth="1.4" />
          <circle cx="100" cy="100" r="56.5" strokeWidth="0.8" />
          <text
            fill="currentColor"
            stroke="none"
            fontFamily="'IBM Plex Mono', monospace"
            fontWeight={500}
            fontSize="11.2"
          >
            {/* Stretched to exactly one circumference (2π·73) so the band closes. */}
            <textPath
              href={`#${ring}`}
              startOffset="0"
              dominantBaseline="middle"
              textLength={458}
              lengthAdjust="spacing"
            >
              {RING_TEXT[label]}
            </textPath>
          </text>
          {/* Centre: a ledger rule above and below the word, with ticks at the ends. */}
          <path d="M60 80h80M60 120h80M60 76v8M140 76v8M60 116v8M140 116v8" strokeWidth="1.3" />
          <text
            x="100"
            y="101"
            fill="currentColor"
            stroke="none"
            textAnchor="middle"
            dominantBaseline="central"
            fontFamily="'Instrument Serif', Georgia, serif"
            fontSize={long ? 15.5 : 38}
            letterSpacing={long ? 0.2 : 3}
          >
            {label}
          </text>
          <text
            x="100"
            y="136"
            fill="currentColor"
            stroke="none"
            textAnchor="middle"
            fontFamily="'IBM Plex Mono', monospace"
            fontSize="7.5"
            letterSpacing="1.6"
          >
            {long ? 'TRACED TO RECORD' : 'AWAITING HUMAN'}
          </text>
          <text
            x="100"
            y="69"
            fill="currentColor"
            stroke="none"
            textAnchor="middle"
            fontFamily="'IBM Plex Mono', monospace"
            fontSize="7.5"
            letterSpacing="1.6"
          >
            {long ? 'CLEARED' : 'NOT PAID'}
          </text>
        </g>
      </svg>
    </motion.div>
  )
}
