import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Zap } from 'lucide-react'
import { DeviceTable } from './DeviceTable'
import { ModelTable } from './ModelTable'
import { AddModelPanel } from './AddModelPanel'

export function ModelsTab() {
  const { t } = useTranslation('models')
  const [selectedDevice, setSelectedDevice] = useState('') // address or ''

  const handleDeviceSelect = (addr: string, _name?: string) => {
    setSelectedDevice(addr)
  }

  return (
    <div className="space-y-6">
      {/* Device selector table */}
      <DeviceTable selected={selectedDevice} onSelect={handleDeviceSelect} />

      {/* Models for selected device */}
      <ModelTable selectedDevice={selectedDevice} />

      {/* Add model panel */}
      <AddModelPanel selectedDevice={selectedDevice} />

      {/* Fleet-managed indicator */}
      {selectedDevice && (
        <div className="border border-ledger/20 bg-ledger/5 rounded-xl p-4 text-center">
          <div className="flex items-center justify-center space-x-2 mb-1">
            <Zap className="w-3.5 h-3.5 text-ledger" />
            <p className="text-sm text-ledger font-medium">
              {t('fleet.managedTitle', 'Remote Device')}
            </p>
          </div>
          <p className="text-xs text-gray-500">
            {t(
              'fleet.managedDesc',
              'Commands are relayed to this node. Some operations may not be available remotely.'
            )}
          </p>
        </div>
      )}
    </div>
  )
}

export default ModelsTab
