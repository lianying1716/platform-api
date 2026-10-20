import { createRouter, createWebHashHistory } from 'vue-router'
import Layout from './layout/Layout.vue'
// 路由级懒加载：首屏只下载登录后的壳与当前页，避免把九个页面、图表和
// 模型接入表单一次性塞进主包。Layout 保持同步加载，它是所有子路由的外框。
const Accounts = () => import('./pages/Accounts.vue')
const AiAccess = () => import('./pages/AiAccess.vue')
const Assets = () => import('./pages/Assets.vue')
const Crawl = () => import('./pages/Crawl.vue')
const DesignPreview = () => import('./pages/DesignPreview.vue')
const FollowedCreators = () => import('./pages/FollowedCreators.vue')
const Login = () => import('./pages/Login.vue')
const ModelAccess = () => import('./pages/ModelAccess.vue')
const Overview = () => import('./pages/Overview.vue')
const Pipeline = () => import('./pages/Pipeline.vue')
const Settings = () => import('./pages/Settings.vue')
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
