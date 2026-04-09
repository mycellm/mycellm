import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Plus, X } from 'lucide-react'
import { useFleetStore } from '@/stores/fleet'

export function ManualNodeAdd() {
  const { t } = useTranslation('network')
  const managedNodes = useFleetStore((s) => s.managedNodes)
  const addManagedNode = useFleetStore((s) => s.addManagedNode)
  const removeManagedNode = useFleetStore((s) => s.removeManagedNode)
  const [newAddr, setNewAddr] = useState('')
  const [newLabel, setNewLabel] = useState('')

  const handleAdd = () => {
    const addr = newAddr.trim()
    if (!addr) return
    const finalAddr = addr.includes(':') ? addr : `${addr}:8420`
    addManagedNode({
      addr: finalAddr,
      label: newLabel.trim() || finalAddr,
      addedAt: new Date().toISOString(),
    })
    setNewAddr('')
    setNewLabel('')
  }

  return (
    <div className="border border-white/10 bg-surface rounded-xl p-5">
      <h2 className="font-mono text-xs text-gray-500 uppercase tracking-widest mb-4">
        {t('manual.title', 'Add Node Manually')}
      </h2>

      <div className="flex flex-col sm:flex-row space-y-2 sm:space-y-0 sm:space-x-2">
        <input
          value={newLabel}
          onChange={(e) => setNewLabel(e.target.value)}
          placeholder={t('manual.labelPlaceholder', 'Label')}
          className="w-full sm:w-32 bg-black border border-white/10 rounded-lg px-3 py-2 text-sm font-mono text-white focus:border-spore/50 focus:outline-none"
        />
        <input
          value={newAddr}
          onChange={(e) => setNewAddr(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && handleAdd()}
          placeholder={t('manual.addrPlaceholder', 'IP:port (e.g. 192.168.1.100:8420)')}
          className="flex-grow bg-black border border-white/10 rounded-lg px-3 py-2 text-sm font-mono text-white focus:border-spore/50 focus:outline-none"
        />
        <button
          onClick={handleAdd}
          disabled={!newAddr.trim()}
          className="bg-white/10 text-white px-3 py-2 rounded-lg text-sm font-medium hover:bg-white/20 disabled:opacity-40 transition-all"
        >
          <Plus size={14} />
        </button>
      </div>

      {/* Managed nodes list */}
      {managedNodes.length > 0 && (
        <div className="mt-4 space-y-2">
          {managedNodes.map((node) => (
            <div
              key={node.addr}
              className="flex items-center justify-between bg-black border border-white/5 rounded-lg px-3 py-2"
            >
              <div className="flex items-center space-x-3">
                <span className="font-mono text-sm text-white">{node.label}</span>
                <span className="font-mono text-xs text-gray-500">{node.addr}</span>
              </div>
              <button
                onClick={() => removeManagedNode(node.addr)}
                className="text-gray-600 hover:text-compute transition-colors p-1"
              >
                <X size={14} />
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
