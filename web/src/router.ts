import { createRouter, createWebHistory } from 'vue-router'
import HomeView from '@/views/HomeView.vue'
import PlaceholderView from '@/views/PlaceholderView.vue'

const placeholder = (path: string, title: string) => ({ path, component: PlaceholderView, meta: { title } })

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: HomeView },
    placeholder('/tenders', 'nav.tenders'),
    placeholder('/partners', 'nav.partners'),
    placeholder('/deals', 'nav.deals'),
    placeholder('/compliance', 'nav.compliance'),
    placeholder('/intelligence', 'nav.intelligence'),
    placeholder('/reach', 'nav.reach'),
  ],
})
