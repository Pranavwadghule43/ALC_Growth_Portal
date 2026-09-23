import { PageHeader } from '../components/ui'
import { AlcDirectory } from '../components/directory'

export default function DcuAlcs() {
  return <><PageHeader title="ALCs" description="Every ALC in your DCU with its SBU, workload and partners. Filter by SBU, code, name or status." />
    <AlcDirectory showSbuFilter />
  </>
}
