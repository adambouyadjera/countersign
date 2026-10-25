// Contract fixtures, imported through the @fixtures alias (contracts/fixtures).
// Only reachable from ./index, which production builds never load.
import agentTranscriptInv003 from '@fixtures/agent_transcript_INV-003.json'
import approvals from '@fixtures/approvals.json'
import auditAnswer from '@fixtures/audit_answer.json'
import autopilot from '@fixtures/autopilot.json'
import balances from '@fixtures/balances.json'
import bankChangeReplay from '@fixtures/bank_change_replay.json'
import batch from '@fixtures/batch.json'
import demoReset from '@fixtures/demo_reset.json'
import documentInv003 from '@fixtures/document_INV-003.json'
import evalJob from '@fixtures/eval_job.json'
// The backend's saved responses for the two recorded planted-errors jobs (mock mode only).
import { job as recordedEvalBefore } from '@evidence/eval_results.json'
import { job as recordedEvalAfter } from '@evidence/eval_results_after_semantic.json'
import graphInv003 from '@fixtures/graph_INV-003.json'
import health from '@fixtures/health.json'
import inbox from '@fixtures/inbox.json'
import ledgerEntries from '@fixtures/ledger_entries.json'
import people from '@fixtures/people.json'
import plannerTranscript from '@fixtures/planner_transcript.json'
import policyDecisions from '@fixtures/policy_decisions.json'
import reconciliation from '@fixtures/reconciliation.json'
import redteamJob from '@fixtures/redteam_job.json'
import run from '@fixtures/run.json'
import runBothEvents from '@fixtures/run_both.events.jsonl?raw'
import trialBalance from '@fixtures/trial_balance.json'
import vendorDetail from '@fixtures/vendor_detail.json'
import type { RunEvent } from '../types'

export const fixtures = {
  agentTranscriptInv003,
  approvals,
  auditAnswer,
  autopilot,
  balances,
  bankChangeReplay,
  batch,
  demoReset,
  documentInv003,
  evalJob,
  recordedEvalBefore,
  recordedEvalAfter,
  graphInv003,
  health,
  inbox,
  ledgerEntries,
  people,
  plannerTranscript,
  policyDecisions,
  reconciliation,
  redteamJob,
  run,
  trialBalance,
  vendorDetail,
}

export function parseJsonl(text: string): RunEvent[] {
  return text
    .split('\n')
    .map((l) => l.trim())
    .filter(Boolean)
    .map((l) => JSON.parse(l) as RunEvent)
}

export const runBothEventsText: string = runBothEvents
