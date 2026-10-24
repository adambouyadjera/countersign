import { useEffect, useId, useRef, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion'
import { cn } from '@/lib/cn'

export interface DrawerProps {
  open: boolean
  onClose: () => void
  title: string
  eyebrow?: string
  children: ReactNode
  footer?: ReactNode
  /** Width as a CSS length; clamped to the viewport. */
  width?: string
}

export function Drawer({ open, onClose, title, eyebrow, children, footer, width = '34rem' }: DrawerProps) {
  const titleId = useId()
  const panelRef = useRef<HTMLDivElement>(null)
  const reduce = useReducedMotion()

  useEffect(() => {
    if (!open) return
    const opener = document.activeElement as HTMLElement | null
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    panelRef.current?.focus()
    return () => {
      document.removeEventListener('keydown', onKey)
      opener?.focus?.()
    }
  }, [open, onClose])

  return createPortal(
    <AnimatePresence>
      {open && (
        <div className="fixed inset-0 z-50">
          <motion.div
            aria-hidden
            className="absolute inset-0 bg-ink/30"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: reduce ? 0 : 0.18 }}
            onClick={onClose}
          />
          <motion.div
            ref={panelRef}
            role="dialog"
            aria-modal="true"
            aria-labelledby={titleId}
            tabIndex={-1}
            className={cn(
              'absolute top-0 right-0 flex h-full max-w-[92vw] flex-col border-l border-ink bg-paper outline-none',
              'shadow-[-24px_0_48px_-24px_rgba(17,26,43,0.45)]',
            )}
            style={{ width }}
            initial={reduce ? { opacity: 0 } : { x: '100%' }}
            animate={reduce ? { opacity: 1 } : { x: 0 }}
            exit={reduce ? { opacity: 0 } : { x: '100%' }}
            transition={reduce ? { duration: 0 } : { type: 'tween', duration: 0.26, ease: [0.2, 0.7, 0.2, 1] }}
          >
            {/* Binding edge: a double rule down the left, like a case-file spine. */}
            <span aria-hidden className="absolute inset-y-0 left-1.5 w-[3px] border-x border-rule" />
            <header className="flex items-start justify-between gap-4 border-b border-rule py-4 pr-4 pl-7">
              <div className="min-w-0">
                {eyebrow && <p className="smallcaps m-0 text-ink-soft">{eyebrow}</p>}
                <h2 id={titleId} className="m-0 font-display text-[1.75rem] leading-tight font-normal">
                  {title}
                </h2>
              </div>
              <button
                type="button"
                onClick={onClose}
                aria-label="Close"
                className="mt-1 inline-flex size-8 items-center justify-center rounded-[2px] border border-rule text-ink-soft hover:border-ink hover:text-ink"
              >
                <svg aria-hidden viewBox="0 0 12 12" className="size-3">
                  <path d="M2 2l8 8M10 2l-8 8" stroke="currentColor" strokeWidth="1.5" />
                </svg>
              </button>
            </header>
            <div className="min-h-0 flex-1 overflow-y-auto py-5 pr-5 pl-7">{children}</div>
            {footer && <footer className="border-t border-rule py-3 pr-5 pl-7">{footer}</footer>}
          </motion.div>
        </div>
      )}
    </AnimatePresence>,
    document.body,
  )
}
