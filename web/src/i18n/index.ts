import { createI18n } from 'vue-i18n'
import { session } from '@/lib/session'
import pl from './pl'
import en from './en'
import de from './de'

/** One list of UI languages; adding one means adding its file here. */
export const languages = [
  { code: 'pl', label: 'Polski' },
  { code: 'en', label: 'English' },
  { code: 'de', label: 'Deutsch' },
]

export const i18n = createI18n({
  legacy: false,
  locale: session.language,
  fallbackLocale: 'en',
  messages: { pl, en, de },
})
