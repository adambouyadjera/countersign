import type { components, operations } from './schema'

type S = components['schemas']

export type Lane = S['Lane']
export type Mode = S['Mode']
export type Health = S['Health']
export type ServiceHealth = S['ServiceHealth']
export type ServiceName = keyof S['ServicesHealth']
export type Batch = S['Batch']
export type InvoiceSummary = S['InvoiceSummary']
export type Run = S['Run']
export type RunRequest = S['RunRequest']
export type LaneTally = S['LaneTally']
export type Balances = S['Balances']
export type AccountBalance = S['AccountBalance']
export type Payment = S['Payment']
export type Handle = S['Handle']
export type SourceSpan = S['SourceSpan']
export type PolicyDecision = S['PolicyDecision']
export type PlanStep = S['PlanStep']
export type ErrorCode = S['ErrorDetail']['code']
export type ErrorResponse = S['ErrorResponse']

export type RunEvent = S['RunEvent']
export type RunEventType = RunEvent['type']
/** The event variant for a given `type`, e.g. EventOf<'payment.held'>. */
export type EventOf<T extends RunEventType> = Extract<RunEvent, { type: T }>

/** Every event type in the contract. Typed so a new contract type fails to compile here. */
export const RUN_EVENT_TYPES = [
  'run.started',
  'lane.status',
  'document.loaded',
  'fields.extracted',
  'plan.proposed',
  'agent.tool_call',
  'agent.message',
  'policy.decision',
  'payment.executed',
  'payment.held',
  'approval.resolved',
  'error',
  'run.completed',
  'ledger.posted',
  'inbox.received',
] as const satisfies readonly RunEventType[]

// Compile-time check that the list above is exhaustive.
type _Missing = Exclude<RunEventType, (typeof RUN_EVENT_TYPES)[number]>
const _exhaustive: [_Missing] extends [never] ? true : _Missing = true
void _exhaustive

/** JSON body of an operation's 200 response. */
export type OkJson<Op extends keyof operations> = operations[Op]['responses'] extends {
  200: { content: { 'application/json': infer T } }
}
  ? T
  : never

/** JSON request body of an operation. */
export type BodyJson<Op extends keyof operations> = operations[Op] extends {
  requestBody: { content: { 'application/json': infer T } }
}
  ? T
  : operations[Op] extends { requestBody?: { content: { 'application/json': infer T } } }
    ? T
    : never

export type Person = S['Person']
export type VendorDetail = S['VendorDetail']
export type Approval = S['Approval']
export type ApprovalDecision = S['ApprovalDecision']
export type ProvenanceGraph = S['ProvenanceGraph']
export type GraphNode = S['GraphNode']
export type GraphEdge = S['GraphEdge']
export type DocumentText = S['DocumentText']
export type PlannerTranscript = S['PlannerTranscript']
export type AgentTranscript = S['AgentTranscript']
export type TrialBalance = S['TrialBalance']
export type Reconciliation = S['Reconciliation']
export type JournalEntry = S['JournalEntry']
export type EvalJob = S['EvalJob']
export type RedTeamJob = S['RedTeamJob']
export type AuditAnswer = S['AuditAnswer']
export type Autopilot = S['Autopilot']
export type UnguardedMode = NonNullable<S['RunRequest']['unguarded_mode']>
export type ReasonCode = S['ReasonCode']
export type BankChangeReplaySummary = S['BankChangeReplaySummary']
export type BankChangeReplay = S['BankChangeReplay']
export type ReplayLane = S['ReplayLane']
export type ReplayPayment = S['ReplayPayment']
export type ReplayHold = S['ReplayHold']
export type ReplayLetter = S['ReplayLetter']
