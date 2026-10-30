import { createApp } from 'vue'
import PrimeVue from 'primevue/config'
import ToastService from 'primevue/toastservice'
import Tooltip from 'primevue/tooltip'
import '@fontsource/ibm-plex-sans/400.css'
import '@fontsource/ibm-plex-sans/500.css'
import '@fontsource/ibm-plex-sans/600.css'
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
