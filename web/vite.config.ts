import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [vue()],
  // 默认输出到 web/dist —— 中性的开发构建产物，不碰正式包目录 app/static。
  // 正式包请用 `npm run build:release`（或 ./dev.sh web），二者互不污染。
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    host: true,
    // 局域网 / Tailscale / 域名访问开发服时，Vite 默认会以 Host 头做白名单校验，
    // 非 IP 的 Host 一律 403（表现为“页面能开但接口全部失败”）。开发环境全放开。
    allowedHosts: true,
    proxy: {
      '/api': 'http://127.0.0.1:9100',
      // 后端静态资源（如 <video src=/api/...> 之外的直链）也一并代理，避免跨端口
      '/media': 'http://127.0.0.1:9100',
    },
  },
})
