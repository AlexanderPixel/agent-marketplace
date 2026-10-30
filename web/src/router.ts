import { createRouter, createWebHistory } from 'vue-router'
import HomeView from '@/views/HomeView.vue'
import PlaceholderView from '@/views/PlaceholderView.vue'
import TendersView from '@/views/TendersView.vue'
import TenderDetailView from '@/views/TenderDetailView.vue'

const placeholder = (path: string, title: string) => ({ path, component: PlaceholderView, meta: { title } })

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: HomeView },
    { path: '/tenders', component: TendersView, meta: { title: 'nav.tenders' } },
    { path: '/tenders/:id', component: TenderDetailView, meta: { title: 'nav.tenders' } },
    placeholder('/partners', 'nav.partners'),
    placeholder('/deals', 'nav.deals'),
    placeholder('/compliance', 'nav.compliance'),
    placeholder('/intelligence', 'nav.intelligence'),
    placeholder('/reach', 'nav.reach'),
  ],
})
