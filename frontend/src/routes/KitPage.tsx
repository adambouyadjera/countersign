import { useState, type ReactNode } from 'react'
import {
  AccountNumber,
  Button,
  Drawer,
  EmptyState,
  ErrorState,
  HandleChip,
  Kbd,
  LedgerRule,
  MockBadge,
  Money,
  Panel,
  Seal,
  StatusDot,
  TrustBadge,
} from '@/components/ui'
import { SealMark } from '@/components/shell/SealMark'
import { Placeholder } from '@/components/shell/Placeholder'

function Specimen({ name, note, children }: { name: string; note: string; children: ReactNode }) {
  return (
    <section aria-labelledby={`kit-${name}`} className="min-w-0 border-t border-ink pt-3">
      <div className="mb-4 flex items-baseline justify-between gap-4">
        <h2 id={`kit-${name}`} className="m-0 font-display text-[1.625rem] leading-none font-normal">
          {name}
        </h2>
        <p className="m-0 truncate text-[0.8125rem] text-ink-soft">{note}</p>
      </div>
      {children}
    </section>
  )
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[8.5rem_minmax(0,1fr)] items-center gap-4 border-b border-rule/70 py-2.5 last:border-b-0">
      <span className="smallcaps text-ink-soft">{label}</span>
      <div className="flex min-w-0 flex-wrap items-center gap-3">{children}</div>
    </div>
  )
}

const SWATCHES = [
  ['--paper', '#F3EFE6'],
  ['--paper-2', '#E9E3D6'],
  ['--ink', '#111A2B'],
  ['--ink-soft', '#4A5468'],
  ['--rule', '#C9C0AE'],
  ['--verified', '#2F7D6D'],
  ['--taint', '#D23B2A'],
  ['--hold', '#B7791F'],
] as const

export function KitPage() {
  const [balance, setBalance] = useState(1_284_000_00)
  const [stampKey, setStampKey] = useState(0)
  const [drawerOpen, setDrawerOpen] = useState(false)

  return (
    <div className="mx-auto w-full max-w-[100rem] py-6 pr-6 pl-8">
      <header className="mb-8 flex items-end justify-between gap-6 border-b-4 border-double border-ink pb-4">
        <div>
          <p className="smallcaps m-0 text-ink-soft">Design system · specimen sheet</p>
          <h1 className="m-0 font-display text-[3rem] leading-none font-normal">The Countersign kit</h1>
        </div>
        <p className="m-0 max-w-[48ch] text-sm text-ink-soft">
          Every component in every variant. Values on this page are specimens typed into the page for display; they do
          not come from the backend.
        </p>
      </header>

      <div className="grid grid-cols-1 gap-x-10 gap-y-10 xl:grid-cols-2">
        <Specimen name="Tokens" note="CSS variables, mapped to Tailwind colours">
          <div className="grid grid-cols-4 gap-3">
            {SWATCHES.map(([v, hex]) => (
              <div key={v} className="min-w-0">
                <div className="h-12 border border-rule" style={{ background: `var(${v})` }} />
                <p className="m-0 mt-1 font-mono text-[0.75rem]">{v}</p>
                <p className="m-0 font-mono text-[0.6875rem] text-ink-soft">{hex}</p>
              </div>
            ))}
          </div>
          <div className="mt-5 space-y-1">
            <p className="m-0 font-display text-[2rem] leading-none">Instrument Serif, for headings</p>
            <p className="m-0 font-sans">Inter Tight, for interface text and labels.</p>
            <p className="m-0 font-mono tabular">IBM Plex Mono · 0123456789 · $INV-003.remit_account</p>
          </div>
        </Specimen>

        <Specimen name="Button" note="primary ink, quiet, danger">
          <Row label="primary">
            <Button>Approve payment</Button>
            <Button size="sm">Run both lanes</Button>
            <Button disabled>Disabled</Button>
          </Row>
          <Row label="quiet">
            <Button variant="quiet">Open case file</Button>
            <Button variant="quiet" size="sm">
              View transcript
            </Button>
            <Button variant="quiet" disabled>
              Disabled
            </Button>
          </Row>
          <Row label="danger">
            <Button variant="danger">Reject payment</Button>
            <Button variant="danger" size="sm">
              Reset demo
            </Button>
            <Button variant="danger" disabled>
              Disabled
            </Button>
          </Row>
        </Specimen>

        <Specimen name="Panel" note="paper card with a hairline rule">
          <div className="grid grid-cols-2 gap-4">
            <Panel eyebrow="Case file" title="INV-003 · Harbor Lights" aside={<TrustBadge level="untrusted" />}>
              <p className="m-0 text-sm text-ink-soft">Panel with eyebrow, title and aside.</p>
            </Panel>
            <Panel dense>
              <p className="m-0 text-sm text-ink-soft">Dense panel, no header.</p>
            </Panel>
          </div>
        </Specimen>

        <Specimen name="LedgerRule" note="single, double, heavy, labelled">
          <div className="space-y-4">
            <Row label="single">
              <LedgerRule />
            </Row>
            <Row label="double">
              <LedgerRule variant="double" />
            </Row>
            <Row label="heavy">
              <LedgerRule variant="heavy" />
            </Row>
            <Row label="labelled">
              <LedgerRule label="Folio 2 · Policy checks" />
            </Row>
          </div>
        </Specimen>

        <Specimen name="HandleChip" note="trusted, untrusted, corroborated">
          <Row label="trusted">
            <HandleChip trust="trusted" name="$VENDOR-HARBOR.account_on_file" />
          </Row>
          <Row label="untrusted">
            <HandleChip trust="untrusted" name="$INV-003.remit_account" />
          </Row>
          <Row label="corroborated">
            <HandleChip trust="corroborated" name="$INV-002.amount" />
          </Row>
          <Row label="inline">
            <p className="m-0 text-sm">
              Pay <HandleChip trust="corroborated" name="$INV-002.amount" /> to{' '}
              <HandleChip trust="untrusted" name="$INV-003.remit_account" />.
            </p>
          </Row>
        </Specimen>

        <Specimen name="Money" note="cents in, tabular numerals out; flow on change">
          <Row label="positive">
            <Money cents={482_150} />
            <Money cents={7} />
            <Money cents={0} />
          </Row>
          <Row label="negative">
            <Money cents={-129_999} signed />
            <Money cents={-129_999} />
          </Row>
          <Row label="large">
            <Money cents={987_654_321_00} />
          </Row>
          <Row label="flow">
            <Money cents={balance} flow className="text-xl" />
            <Button size="sm" variant="quiet" onClick={() => setBalance((b) => b + 1_250_00)}>
              + $1,250.00
            </Button>
            <Button size="sm" variant="quiet" onClick={() => setBalance((b) => b - 430_17)}>
              − $430.17
            </Button>
          </Row>
        </Specimen>

        <Specimen name="AccountNumber" note="masked except last four; hover or focus to reveal">
          <Row label="revealable">
            <AccountNumber value="021000021 4401 9932" />
            <AccountNumber value="8890-3317" />
          </Row>
          <Row label="masked only">
            <AccountNumber value="000123456789" revealable={false} />
          </Row>
        </Specimen>

        <Specimen name="TrustBadge" note="four provenance levels">
          <Row label="levels">
            <TrustBadge level="trusted" />
            <TrustBadge level="untrusted" />
            <TrustBadge level="corroborated" />
            <TrustBadge level="human-approved" />
          </Row>
        </Specimen>

        <Specimen name="Seal" note="original SVG; stamp lands on mount">
          <div className="flex flex-wrap items-center gap-10">
            <Seal label="HELD" stampKey={`h${stampKey}`} />
            <Seal label="COUNTERSIGNED" stampKey={`c${stampKey}`} tilt={6} />
            <Seal label="HELD" size={72} stamp={false} />
            <div className="flex flex-col items-start gap-3">
              <Button variant="quiet" size="sm" onClick={() => setStampKey((k) => k + 1)}>
                Stamp again
              </Button>
              <span className="flex items-center gap-2 text-[0.8125rem] text-ink-soft">
                <SealMark size={24} /> Wordmark seal
              </span>
            </div>
          </div>
        </Specimen>

        <Specimen name="StatusDot" note="ok, hold, fault, idle, offline">
          <Row label="tones">
            <StatusDot tone="ok" label="Live" showLabel />
            <StatusDot tone="hold" label="Held" showLabel />
            <StatusDot tone="fault" label="Error" showLabel />
            <StatusDot tone="idle" label="Idle" showLabel />
            <StatusDot tone="offline" label="Offline mode" showLabel />
          </Row>
          <Row label="pulse">
            <StatusDot tone="ok" label="Running" showLabel pulse />
          </Row>
        </Specimen>

        <Specimen name="Drawer" note="right-hand case-file drawer; Esc to close">
          <Row label="demo">
            <Button variant="quiet" onClick={() => setDrawerOpen(true)}>
              Open drawer
            </Button>
            <span className="text-[0.8125rem] text-ink-soft">
              Close with <Kbd>Esc</Kbd>
            </span>
          </Row>
          <Drawer
            open={drawerOpen}
            onClose={() => setDrawerOpen(false)}
            eyebrow="Case file · specimen"
            title="INV-003 held"
            footer={
              <div className="flex justify-end gap-2">
                <Button variant="quiet" onClick={() => setDrawerOpen(false)}>
                  Close
                </Button>
              </div>
            }
          >
            <div className="flex items-start gap-5">
              <Seal label="HELD" size={110} />
              <p className="m-0 text-sm text-ink-soft">
                The drawer hosts case files. Its contents here are a specimen; the real case file is built from the
                provenance graph endpoint.
              </p>
            </div>
          </Drawer>
        </Specimen>

        <Specimen name="Kbd" note="keyboard keys">
          <Row label="keys">
            <Kbd>Esc</Kbd>
            <Kbd>R</Kbd>
            <span className="flex items-center gap-1">
              <Kbd>⌘</Kbd>
              <Kbd>K</Kbd>
            </span>
          </Row>
        </Specimen>

        <Specimen name="EmptyState" note="nothing to show yet">
          <Panel dense>
            <EmptyState
              title="No held payments"
              body="When Countersign holds a payment, its case file appears here."
              action={<Button variant="quiet" size="sm">Start a run</Button>}
            />
          </Panel>
        </Specimen>

        <Specimen name="ErrorState" note="contract error envelope">
          <div className="space-y-3">
            <ErrorState
              title="Audit agent unavailable"
              code="service_unavailable"
              message="Elastic is in offline mode, so questions cannot be answered."
              action={<Button size="sm" variant="quiet">Retry</Button>}
            />
            <ErrorState title="Confirmation digits do not match" code="validation_error" />
          </div>
        </Specimen>

        <Specimen name="MockBadge" note="shown on every screen when VITE_USE_MOCK=1">
          <Row label="enabled">
            <MockBadge enabled />
          </Row>
          <Row label="from env">
            <MockBadge />
            <span className="font-mono text-[0.75rem] text-ink-soft">
              VITE_USE_MOCK={import.meta.env.VITE_USE_MOCK ?? '(unset)'}
            </span>
          </Row>
        </Specimen>

        <Specimen name="Placeholder" note="shell-only: marks unwired regions">
          <Placeholder title="Provenance graph" source="GET /api/runs/{run_id}/invoices/{invoice_id}/graph" />
        </Specimen>
      </div>
    </div>
  )
}
