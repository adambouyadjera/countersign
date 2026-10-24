import { forwardRef, type ButtonHTMLAttributes } from 'react'
import { cn } from '@/lib/cn'

export type ButtonVariant = 'primary' | 'quiet'

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  size?: 'sm' | 'md'
}

// From the mockups: cobalt fill with white ink, or a transparent outline on the paper.
const variants: Record<ButtonVariant, string> = {
  primary: 'border border-accent bg-accent text-on-accent hover:bg-[color-mix(in_srgb,var(--accent)_88%,black)]',
  quiet: 'border border-line bg-transparent text-fg hover:bg-panel',
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = 'primary', size = 'md', className, type = 'button', ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      className={cn(
        'inline-flex items-center justify-center gap-2 font-[inherit] transition-colors duration-150',
        'disabled:cursor-not-allowed disabled:opacity-50',
        size === 'sm' ? 'min-h-9 rounded-[6px] px-3.5 font-bold' : 'min-h-11 rounded-[8px] px-3.5 font-semibold',
        variants[variant],
        className,
      )}
      {...rest}
    />
  )
})
