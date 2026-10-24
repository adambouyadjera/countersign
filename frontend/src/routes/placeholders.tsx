import { Link } from 'react-router-dom'
import { SigPlaceholder } from '@/components/sig'

export function NotFoundPage() {
  return (
    <SigPlaceholder title="Not found" expression="honest" what="There's no page at this address.">
      <Link to="/console" className="text-accent">
        back to the console
      </Link>
    </SigPlaceholder>
  )
}
