import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [vue()],
  // 演示构建挂在落地页的子路径下（https://cai.xsai5.xyz/demo/），
  // 资源与快照路径必须带前缀，否则会和落地页自己的 /assets 冲突。
  base: process.env.VITE_DEMO_MODE === '1' ? '/demo/' : '/',
  // 默认输出到 web/dist —— 中性的开发构建产物，不碰正式包目录 app/static。
  // 正式包请用 `npm run build:release`（或 ./dev.sh web），二者互不污染。
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    // Budget: entry shell < 100 kB.  ECharts' core is a third-party chunk of
    // ~550 kB (lazy, loaded with the dashboard charts); splitting its internal
    // directories creates circular chunk edges, so it is kept whole on purpose.
    chunkSizeWarningLimit: 600,
    rollupOptions: {
      output: {
        manualChunks(id: string) {
          // Route pages are lazy-loaded; keep only large shared vendors explicit.
          if (id.includes('node_modules/zrender')) return 'vendor-charts-zrender'
          if (id.includes('node_modules/echarts')) return 'vendor-charts-core'
          if (id.includes('node_modules/element-plus')) return 'vendor-element'
          if (id.includes('node_modules/@element-plus')) return 'vendor-element'
          if (
            id.includes('node_modules/vue/') ||
            id.includes('node_modules/@vue/') ||
            id.includes('node_modules/vue-router/')
          ) {
            return 'vendor-vue'
          }
        },
      },
    },
  },
  server: {
    port: 5173,
    host: true,
    // 局域网 / Tailscale / 域名访问开发服时，Vite 默认会以 Host 头做白名单校验，
    // 非 IP 的 Host 一律 403（表现为“页面能开但接口全部失败”）。开发环境全放开。
    allowedHosts: true,
    // 代理目标可用 VITE_API_TARGET 覆盖：便于把界面指向另一个后端（例如
    // 用独立 SQLite + 演示数据起一个预览实例做界面验收，不动开发库）。
    proxy: (() => {
      const target = process.env.VITE_API_TARGET || 'http://127.0.0.1:9100'
      // 代理错误必须留下可定位的日志：否则后端一掉，页面只显示笼统提示，
      // 无法区分“后端没起来”“后端超时”“某条接口 500”。
      const onProxyError = (err: Error, _req: unknown, res: any) => {
        console.error(`[vite proxy] ${target} unavailable: ${err.message}`)
        if (!res.headersSent) res.writeHead(502, { 'Content-Type': 'application/json' })
        res.end(JSON.stringify({ detail: `后端不可用（${target}）：${err.message}` }))
      }
      return {
        '/api': { target, changeOrigin: true, configure: (proxy: any) => proxy.on('error', onProxyError) },
        // 后端静态资源（如 <video src=/api/...> 之外的直链）也一并代理，避免跨端口
        '/media': { target, changeOrigin: true, configure: (proxy: any) => proxy.on('error', onProxyError) },
      }
    })(),
  },
})
