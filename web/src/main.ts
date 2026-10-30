import { createApp } from 'vue'
import PrimeVue from 'primevue/config'
import ToastService from 'primevue/toastservice'
import Tooltip from 'primevue/tooltip'
import '@fontsource/hanken-grotesk/400.css'
import '@fontsource/hanken-grotesk/500.css'
import '@fontsource/hanken-grotesk/600.css'
import '@fontsource/hanken-grotesk/700.css'
import '@fontsource/geist-mono/400.css'
import '@fontsource/geist-mono/500.css'
import 'primeicons/primeicons.css'
import './main.css'
import App from './App.vue'
import { AppTheme } from './theme'
import { i18n } from './i18n'
import { router } from './router'

createApp(App)
  .use(PrimeVue, { theme: { preset: AppTheme, options: { darkModeSelector: false } } })
  .use(ToastService)
  .directive('tooltip', Tooltip)
  .use(i18n)
  .use(router)
  .mount('#app')
