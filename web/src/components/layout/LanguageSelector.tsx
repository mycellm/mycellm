import { useTranslation } from 'react-i18next'
import { Globe } from 'lucide-react'

// Only show languages we have full translations for
const availableLanguages = [
  { code: 'en', label: 'English' },
]

export function LanguageSelector() {
  const { i18n } = useTranslation()

  // Don't show selector if only one language
  if (availableLanguages.length <= 1) {
    return null
  }

  const handleChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    i18n.changeLanguage(e.target.value)
  }

  return (
    <div className="relative inline-flex items-center gap-1">
      <Globe className="w-3.5 h-3.5 text-gray-500 shrink-0" />
      <select
        value={i18n.language?.slice(0, 2) ?? 'en'}
        onChange={handleChange}
        className="appearance-none bg-transparent text-gray-400 text-xs font-mono
          cursor-pointer border border-white/10 rounded px-1.5 py-0.5
          hover:border-white/20 focus:outline-none focus:border-spore/50
          transition-colors"
      >
        {availableLanguages.map(({ code, label }) => (
          <option
            key={code}
            value={code}
            className="bg-surface text-console"
          >
            {label}
          </option>
        ))}
      </select>
    </div>
  )
}
