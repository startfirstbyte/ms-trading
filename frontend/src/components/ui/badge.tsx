import * as React from 'react'
import { cva, type VariantProps } from 'class-variance-authority'
import { cn } from '@/lib/utils'

const badgeVariants = cva(
  'inline-flex items-center rounded-md border px-2 py-0.5 text-[10px] font-semibold tracking-wide transition-colors',
  {
    variants: {
      variant: {
        default:   'border-transparent bg-[var(--accent)] text-white',
        secondary: 'border-transparent bg-[var(--surface)] text-[var(--muted)]',
        outline:   'border-[var(--border)] text-[var(--muted)]',
        wait:      'border-[var(--border)] text-[var(--muted)] bg-transparent',
        buy:       'border-[#3fb950]/40 text-[#3fb950] bg-[#3fb950]/10',
        sell:      'border-[#f85149]/40 text-[#f85149] bg-[#f85149]/10',
        pattern:   'border-[var(--border)] text-[var(--muted)] bg-transparent font-normal',
        convHigh:  'border-[#3fb950]/40 text-[#3fb950] bg-transparent',
        convMed:   'border-[var(--accent)]/40 text-[var(--accent)] bg-transparent',
        convLow:   'border-[var(--border)] text-[var(--muted)] bg-transparent',
      },
    },
    defaultVariants: { variant: 'default' },
  }
)

export interface BadgeProps
  extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return <div className={cn(badgeVariants({ variant }), className)} {...props} />
}

export { Badge, badgeVariants }
