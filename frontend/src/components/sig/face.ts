export type SigExpression = 'default' | 'serious' | 'cheeky' | 'pleased' | 'honest' | 'calm'

/** Eyes per expression, from docs/design/README.md. */
export const SIG_EYES: Record<SigExpression, string> = {
  default: '◉ ◉',
  serious: '▬ ▬', // held, case file
  cheeky: '◕ ◕', // Attack Lab
  pleased: '^ ^', // books balanced
  honest: '• •', // proof
  calm: '- -', // audit
}

export function sigFaceLines(expression: SigExpression = 'default', size: 'small' | 'large' = 'small'): string[] {
  const eyes = SIG_EYES[expression]
  return size === 'large'
    ? [' ╭─────╮', ` │ ${eyes} │`, ' ╰─┬─┬─╯', '  ═╧═╧═']
    : ['╭───╮', `│${eyes}│`, '╰┬─┬╯']
}
