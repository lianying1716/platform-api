import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import './styles/tokens.css'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import { createApp } from 'vue'
import App from './App.vue'
import { router } from './router'

// 已选定 V3「包豪斯」为正式视觉方向：给根元素打上标记，
// 使 ui.css 里所有 [data-dir='bauhaus'] 规则全站生效。
// /design-preview 样张页会在自己的容器上覆盖成 grotesk / nocturne 做对比。
document.documentElement.dataset.dir = 'bauhaus'

const app = createApp(App)
app.use(router)
app.use(ElementPlus, { locale: zhCn })
app.mount('#app')
