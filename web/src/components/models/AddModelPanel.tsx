import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Search, HardDrive, Cloud, Monitor } from 'lucide-react'
import { cn } from '@/lib/utils'
import { ModelBrowser } from './ModelBrowser'
import { LocalFileLoader } from './LocalFileLoader'
import { ApiProviderForm } from './ApiProviderForm'
import { RelayPanel } from './RelayPanel'

interface AddModelPanelProps {
  selectedDevice: string
}

type AddMode = 'browse' | 'local' | 'api' | 'relay'

interface TabDef {
  id: AddMode
  label: string
  labelKey: string
  Icon: typeof Search
}

const TABS: TabDef[] = [
  { id: 'browse', label: 'HuggingFace', labelKey: 'addModel.browse', Icon: Search },
  { id: 'local', label: 'Local File', labelKey: 'addModel.local', Icon: HardDrive },
  { id: 'api', label: 'API Provider', labelKey: 'addModel.api', Icon: Cloud },
  { id: 'relay', label: 'Device Relay', labelKey: 'addModel.relay', Icon: Monitor },
]

export function AddModelPanel({ selectedDevice }: AddModelPanelProps) {
  const { t } = useTranslation('models')
  const [addMode, setAddMode] = useState<AddMode>('browse')

  return (
    <div className="border border-white/10 bg-surface rounded-xl overflow-hidden">
      {/* Tab bar */}
      <div className="flex border-b border-white/10">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setAddMode(tab.id)}
            className={cn(
              'flex items-center space-x-2 px-4 py-3 text-xs font-medium border-b-2 transition-all',
              addMode === tab.id
                ? 'border-spore text-white bg-black/20'
                : 'border-transparent text-gray-500 hover:text-gray-300'
            )}
          >
            <tab.Icon className="w-3.5 h-3.5" />
            <span>{t(tab.labelKey, tab.label)}</span>
          </button>
        ))}
      </div>

      {/* Active sub-tab */}
      <div className="p-5">
        {addMode === 'browse' && <ModelBrowser selectedDevice={selectedDevice} />}
        {addMode === 'local' && <LocalFileLoader selectedDevice={selectedDevice} />}
        {addMode === 'api' && <ApiProviderForm selectedDevice={selectedDevice} />}
        {addMode === 'relay' && <RelayPanel selectedDevice={selectedDevice} />}
      </div>
    </div>
  )
}
