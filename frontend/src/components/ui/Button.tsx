import { forwardRef, type ButtonHTMLAttributes } from 'react'
import { cn } from '@/lib/cn'

export type ButtonVariant = 'primary' | 'quiet' | 'danger'

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  size?: 'sm' | 'md'
}

const variants: Record<ButtonVariant, string> = {
  primary:
    'bg-ink text-paper border border-ink shadow-[inset_0_-2px_0_rgba(0,0,0,0.35)] hover:bg-[#1d2a42]',
  quiet:
    'bg-paper text-ink border border-rule-strong hover:border-ink hover:bg-paper-2',
  danger:
    'bg-paper text-taint border border-taint hover:bg-taint hover:text-paper',
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
        'inline-flex items-center justify-center gap-2 rounded-[2px] font-sans font-medium tracking-[0.01em]',
        'transition-colors duration-150 disabled:cursor-not-allowed disabled:opacity-45',
        size === 'sm' ? 'h-7 px-2.5 text-[0.8125rem]' : 'h-9 px-4 text-sm',
        variants[variant],
        className,
      )}
      {...rest}
    />
  )
})
