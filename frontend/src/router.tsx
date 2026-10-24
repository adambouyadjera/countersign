import { createBrowserRouter } from 'react-router-dom'
import { AppShell } from '@/components/shell/AppShell'
import { CaseFilePage } from '@/routes/CaseFilePage'
import { ConsolePage } from '@/routes/ConsolePage'
import { AttackLabPage } from '@/routes/AttackLabPage'
import { AuditPage } from '@/routes/AuditPage'
import { BankChangePage } from '@/routes/BankChangePage'
import { BooksPage } from '@/routes/BooksPage'
import { NotFoundPage } from '@/routes/placeholders'
import { ProofPage } from '@/routes/ProofPage'

export const router = createBrowserRouter([
  {
    element: <AppShell />,
    children: [
      { path: '/', element: <ConsolePage /> },
      { path: '/runs/:runId/invoices/:invoiceId', element: <CaseFilePage /> },
      { path: '/bank-change', element: <BankChangePage /> },
      { path: '/attack', element: <AttackLabPage /> },
      { path: '/books', element: <BooksPage /> },
      { path: '/proof', element: <ProofPage /> },
      { path: '/audit', element: <AuditPage /> },
      { path: '*', element: <NotFoundPage /> },
    ],
  },
])
