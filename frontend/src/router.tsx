import { createBrowserRouter } from 'react-router-dom'
import { AppShell } from '@/components/shell/AppShell'
import { AuditPage } from '@/routes/AuditPage'
import { ConsolePage } from '@/routes/ConsolePage'
import { KitPage } from '@/routes/KitPage'
import { NotFoundPage } from '@/routes/NotFoundPage'
import { RedTeamPage } from '@/routes/RedTeamPage'

export const router = createBrowserRouter([
  {
    element: <AppShell />,
    children: [
      { path: '/', element: <ConsolePage /> },
      { path: '/redteam', element: <RedTeamPage /> },
      { path: '/audit', element: <AuditPage /> },
      { path: '/kit', element: <KitPage /> },
      { path: '*', element: <NotFoundPage /> },
    ],
  },
])
