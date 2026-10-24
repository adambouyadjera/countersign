import type { Person } from '@/api/types'

export const ROLE_LABEL: Record<Person['role'], string> = {
  ap_agent: 'AP agent',
  ap_manager: 'AP manager',
  controller: 'controller',
  vendor_admin: 'vendor admin',
}
