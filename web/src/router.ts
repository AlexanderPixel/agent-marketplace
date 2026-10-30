import { createRouter, createWebHistory } from 'vue-router'
import HomeView from '@/views/HomeView.vue'
import PlaceholderView from '@/views/PlaceholderView.vue'
import TendersView from '@/views/TendersView.vue'
import TenderDetailView from '@/views/TenderDetailView.vue'
import EvidenceView from '@/views/EvidenceView.vue'
import PartnersView from '@/views/PartnersView.vue'
import DealsView from '@/views/DealsView.vue'
import DealRoomView from '@/views/DealRoomView.vue'
import DealDocumentsView from '@/views/DealDocumentsView.vue'
import ComplianceView from '@/views/ComplianceView.vue'
import IntelligenceView from '@/views/IntelligenceView.vue'
import ReachView from '@/views/ReachView.vue'

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
    { path: '/deals/:id/documents', component: DealDocumentsView, meta: { title: 'nav.deals' } },
    { path: '/compliance', component: ComplianceView, meta: { title: 'nav.compliance' } },
    { path: '/intelligence', component: IntelligenceView, meta: { title: 'nav.intelligence' } },
    { path: '/reach', component: ReachView, meta: { title: 'nav.reach' } },
  ],
})
