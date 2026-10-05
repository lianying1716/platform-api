import { createRouter, createWebHashHistory } from 'vue-router'
import Layout from './layout/Layout.vue'
import Accounts from './pages/Accounts.vue'
import AiAccess from './pages/AiAccess.vue'
import Analytics from './pages/Analytics.vue'
import Assets from './pages/Assets.vue'
import Crawl from './pages/Crawl.vue'
import Login from './pages/Login.vue'
import Overview from './pages/Overview.vue'
import Pipeline from './pages/Pipeline.vue'
import Settings from './pages/Settings.vue'
import Subscriptions from './pages/Subscriptions.vue'

export const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/login', component: Login },
    {
      path: '/',
      component: Layout,
      children: [
        { path: '', redirect: '/overview' },
        { path: 'overview', component: Overview, meta: { title: '总览' } },
        { path: 'accounts', component: Accounts, meta: { title: '账号中心' } },
        { path: 'crawl', component: Crawl, meta: { title: '关键词采集' } },
        { path: 'subscriptions', component: Subscriptions, meta: { title: '订阅同步' } },
        { path: 'assets', component: Assets, meta: { title: '素材库' } },
        { path: 'pipeline', component: Pipeline, meta: { title: '处理队列' } },
        { path: 'analytics', component: Analytics, meta: { title: '数据看板' } },
        { path: 'ai', component: AiAccess, meta: { title: 'AI 接入' } },
        { path: 'system', component: Settings, meta: { title: '系统设置' } },
      ],
    },
  ],
})

router.beforeEach((to) => {
  if (to.path !== '/login' && !localStorage.getItem('token')) return '/login'
})
