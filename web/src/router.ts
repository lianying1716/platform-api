import { createRouter, createWebHashHistory } from 'vue-router'
import Layout from './layout/Layout.vue'
import Accounts from './pages/Accounts.vue'
import AiAccess from './pages/AiAccess.vue'
import Assets from './pages/Assets.vue'
import Crawl from './pages/Crawl.vue'
import DesignPreview from './pages/DesignPreview.vue'
import FollowedCreators from './pages/FollowedCreators.vue'
import Login from './pages/Login.vue'
import ModelAccess from './pages/ModelAccess.vue'
import Overview from './pages/Overview.vue'
import Pipeline from './pages/Pipeline.vue'
import Settings from './pages/Settings.vue'
import { DEMO_MODE } from './demo/runtime'

export const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/login', component: Login },
    // 设计方向预览（仅样张，供敲定视觉方向；确定后移除）
    { path: '/design-preview', component: DesignPreview },
    {
      path: '/',
      component: Layout,
      children: [
        { path: '', redirect: '/overview' },
        // 总览已合并原「数据看板」
        { path: 'overview', component: Overview, meta: { title: '总览', keepAlive: true } },
        { path: 'analytics', redirect: '/overview' },
        { path: 'accounts', component: Accounts, meta: { title: '账号中心' } },
        { path: 'crawl', component: Crawl, meta: { title: '关键词采集' } },
        // 关注列表已合并原「订阅同步」
        { path: 'followed-creators', component: FollowedCreators, meta: { title: '关注列表' } },
        { path: 'subscriptions', redirect: '/followed-creators' },
        { path: 'assets', component: Assets, meta: { title: '素材库', keepAlive: true } },
        { path: 'pipeline', component: Pipeline, meta: { title: '处理队列' } },
        { path: 'models', component: ModelAccess, meta: { title: '模型接入' } },
        { path: 'ai', component: AiAccess, meta: { title: 'AI 接入' } },
        { path: 'system', component: Settings, meta: { title: '系统设置' } },
      ],
    },
  ],
})

router.beforeEach((to) => {
  if (to.path === '/design-preview') return true
  if (DEMO_MODE && to.path === '/login') return '/overview'
  if (DEMO_MODE) return true
  if (to.path !== '/login' && !localStorage.getItem('token')) return '/login'
})
