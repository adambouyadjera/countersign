import { forwardRef, type ButtonHTMLAttributes } from 'react'
import { cn } from '@/lib/cn'

export type InkButtonVariant = 'primary' | 'secondary' | 'danger' | 'launch'

export interface InkButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: InkButtonVariant
  size?: 'sm' | 'md' | 'lg'
}

// primary: cobalt on a hard ink shadow. secondary: paper with a soft shadow.
// danger: dashed red, for actions that trust something untrusted.
const VARIANT: Record<InkButtonVariant, string> = {
  primary:
    'border-[1.5px] border-solid border-ink bg-accent text-on-accent shadow-[var(--button-shadow)] hover:bg-[color-mix(in_srgb,var(--accent)_88%,black)]',
  secondary: 'border-[1.5px] border-solid border-[var(--inner-edge)] bg-card text-fg shadow-[var(--inner-shadow)] hover:bg-inner',
  danger: 'border-[1.5px] border-dashed border-bad bg-card text-bad hover:bg-bad-bg',
  // The Attack Lab's launch button: red, light outline, hard light shadow, display type.
  launch:
    'border-2 border-solid border-ink bg-[var(--red,var(--bad))] text-[var(--bg)] shadow-[var(--button-shadow)] font-display font-extrabold hover:brightness-110',
}

const SIZE = {
  sm: 'min-h-9 rounded-[6px] px-3.5 font-bold',
  md: 'min-h-11 rounded-[8px] px-3.5 font-semibold',
  lg: 'min-h-12 rounded-[8px] px-[22px] text-face font-bold',
} as const

/** A real <button> in the refined ink style, with a visible focus ring. */
export const InkButton = forwardRef<HTMLButtonElement, InkButtonProps>(function InkButton(
  { variant = 'primary', size = 'md', className, type = 'button', ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      data-variant={variant}
      className={cn(
        'inline-flex cursor-pointer items-center justify-center gap-2 font-[inherit] transition-[transform,box-shadow,background-color] duration-100',
        'focus-visible:outline-[3px] focus-visible:outline-offset-[3px] focus-visible:outline-accent focus-visible:outline-solid',
        // Pressing pushes the button into its shadow; reduced motion keeps it still.
        'motion-safe:active:translate-x-[1px] motion-safe:active:translate-y-[1px]',
        'disabled:cursor-not-allowed disabled:opacity-50 disabled:shadow-none disabled:hover:bg-[unset]',
        VARIANT[variant],
        SIZE[size],
        className,
      )}
      {...rest}
    />
  )
})
