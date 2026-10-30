import { createRouter, createWebHistory } from 'vue-router'
import HomeView from '@/views/HomeView.vue'
import PlaceholderView from '@/views/PlaceholderView.vue'
import TendersView from '@/views/TendersView.vue'
import TenderDetailView from '@/views/TenderDetailView.vue'
import EvidenceView from '@/views/EvidenceView.vue'
import PartnersView from '@/views/PartnersView.vue'
import DealsView from '@/views/DealsView.vue'
import DealRoomView from '@/views/DealRoomView.vue'

const placeholder = (path: string, title: string) => ({ path, component: PlaceholderView, meta: { title } })

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: HomeView },
    { path: '/tenders', component: TendersView, meta: { title: 'nav.tenders' } },
    { path: '/tenders/:id', component: TenderDetailView, meta: { title: 'nav.tenders' } },
    { path: '/evidence', component: EvidenceView, meta: { title: 'nav.evidence' } },
    { path: '/partners', component: PartnersView, meta: { title: 'nav.partners' } },
    { path: '/deals', component: DealsView, meta: { title: 'nav.deals' } },
    { path: '/deals/:id', component: DealRoomView, meta: { title: 'nav.deals' } },
    placeholder('/compliance', 'nav.compliance'),
    placeholder('/intelligence', 'nav.intelligence'),
    placeholder('/reach', 'nav.reach'),
  ],
})
