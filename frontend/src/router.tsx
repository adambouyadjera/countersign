import { createBrowserRouter, Navigate } from 'react-router-dom'
import { AppShell } from '@/components/shell/AppShell'
import { CaseFilePage } from '@/routes/CaseFilePage'
import { ConsolePage } from '@/routes/ConsolePage'
import { AttackLabPage } from '@/routes/AttackLabPage'
import { AuditPage } from '@/routes/AuditPage'
import { BankChangePage } from '@/routes/BankChangePage'
import { BooksPage } from '@/routes/BooksPage'
import { NotFoundPage } from '@/routes/placeholders'
import { ProofPage } from '@/routes/ProofPage'
import { VendorPage } from '@/routes/VendorPage'

export const router = createBrowserRouter([
  {
    element: <AppShell />,
    children: [
      // The demo opens on the bank-change screen; the console lives at /console.
      { path: '/', element: <Navigate to="/bank-change" replace /> },
      { path: '/console', element: <ConsolePage /> },
      { path: '/runs/:runId/invoices/:invoiceId', element: <CaseFilePage /> },
      { path: '/bank-change', element: <BankChangePage /> },
      { path: '/attack', element: <AttackLabPage /> },
      { path: '/books', element: <BooksPage /> },
      { path: '/vendors/:vendorId', element: <VendorPage /> },
      { path: '/eval', element: <ProofPage /> },
      { path: '/proof', element: <Navigate to="/eval" replace /> },
      { path: '/audit', element: <AuditPage /> },
      { path: '*', element: <NotFoundPage /> },
    ],
  },
])
