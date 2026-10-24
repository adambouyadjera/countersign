import { createBrowserRouter } from 'react-router-dom'
import { AppShell } from '@/components/shell/AppShell'
import { ConsolePage } from '@/routes/ConsolePage'
import {
  AttackLabPage,
  AuditPage,
  BooksPage,
  CaseFilePage,
  NotFoundPage,
  ProofPage,
} from '@/routes/placeholders'

export const router = createBrowserRouter([
  {
    element: <AppShell />,
    children: [
      { path: '/', element: <ConsolePage /> },
      { path: '/runs/:runId/invoices/:invoiceId', element: <CaseFilePage /> },
      { path: '/attack', element: <AttackLabPage /> },
      { path: '/books', element: <BooksPage /> },
      { path: '/proof', element: <ProofPage /> },
      { path: '/audit', element: <AuditPage /> },
      { path: '*', element: <NotFoundPage /> },
    ],
  },
])
