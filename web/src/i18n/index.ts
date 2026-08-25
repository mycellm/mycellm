import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'
import HttpBackend from 'i18next-http-backend'
import LanguageDetector from 'i18next-browser-languagedetector'

export const supportedLanguages = ['en', 'es', 'ja', 'zh', 'de', 'fr'] as const
export type SupportedLanguage = (typeof supportedLanguages)[number]

i18n
  .use(HttpBackend)
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    fallbackLng: 'en',
    defaultNS: 'common',
    ns: ['common'],
    // Namespaces load on demand via HttpBackend; listing 'queue' is not
    // required, but useTranslation('queue') must find the file at
    // /locales/{lng}/queue.json.
    supportedLngs: supportedLanguages,
    interpolation: {
      escapeValue: false,
    },
    backend: {
      loadPath: '/locales/{{lng}}/{{ns}}.json',
    },
    detection: {
      order: ['querystring', 'localStorage', 'navigator'],
      lookupLocalStorage: 'mycellm_lang',
      caches: ['localStorage'],
    },
  })

export default i18n
