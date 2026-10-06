<template>
  <div class="pv-root" :data-theme="dir">
    <!-- 顶部：页面切换 + 方向切换（仅用于敲定，正式版会移除） -->
    <div class="pv-switch">
      <span class="pv-title">界面样张</span>
      <div class="pv-pages">
        <button
          v-for="p in pages"
          :key="p.key"
          class="pv-page-tab"
          :class="{ on: page === p.key }"
          @click="page = p.key"
        >
          {{ p.label }}
        </button>
      </div>
      <span class="pv-sub" style="margin-left: 14px">视觉方向</span>
      <div class="pv-dirs">
        <button v-for="d in dirs" :key="d.key" class="pv-dir" :class="{ on: dir === d.key }" @click="dir = d.key">
          {{ d.label }}
        </button>
      </div>
    </div>

    <div class="pv-body">
      <!-- 侧栏（共享） -->
      <aside class="pv-side">
        <div class="pv-brand"><span class="dot" />素材采集工作台</div>
        <div v-for="n in nav" :key="n.t" class="pv-nav-item" :class="{ on: n.on }">
          <span>{{ n.t }}</span>
          <span v-if="n.badge" class="badge">{{ n.badge }}</span>
        </div>
      </aside>

      <main class="pv-main">
        <!-- ============ 样张 1 · 总览 ============ -->
        <template v-if="page === 'overview'">
          <div class="pv-page-head">
            <h1>总览</h1>
            <span class="desc">抖音 / B站 · 近 24 小时</span>
            <div class="pv-spacer" />
            <button class="pv-btn">刷新</button>
            <button class="pv-btn primary">新建采集</button>
          </div>

          <section class="pv-metrics">
            <div class="pv-metric">
              <div class="k"><span class="pv-dot ok" />在线账号</div>
              <div class="v">5</div>
              <div class="d">共 6 个 · 1 个需重新登录</div>
            </div>
            <div class="pv-metric">
              <div class="k">今日抓取</div>
              <div class="v">41</div>
              <div class="d">较昨日 +12</div>
            </div>
            <div class="pv-metric">
              <div class="k">待处理任务</div>
              <div class="v">7</div>
              <div class="d">转录 4 · 下载 3</div>
            </div>
            <div class="pv-metric alert">
              <div class="k"><span class="pv-dot bad" />失败任务</div>
              <div class="v">2</div>
              <div class="d">去处理队列看看</div>
            </div>
          </section>

          <section>
            <div class="pv-sec">
              <h2>最近 24 小时热门素材</h2>
              <span class="hint">按播放量排序</span>
            </div>
            <div class="pv-panel">
              <table class="pv-table">
                <thead>
                  <tr>
                    <th style="width: 40px">#</th>
                    <th>标题</th>
                    <th style="width: 96px">平台</th>
                    <th style="width: 130px">作者</th>
                    <th style="width: 96px; text-align: right">播放</th>
                    <th style="width: 96px; text-align: right">点赞</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(r, i) in hot" :key="r.title">
                    <td class="num">{{ i + 1 }}</td>
                    <td class="strong">{{ r.title }}</td>
                    <td>{{ r.platform }}</td>
                    <td>{{ r.author }}</td>
                    <td class="num">{{ r.play }}</td>
                    <td class="num">{{ r.like }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </section>

          <section>
            <div class="pv-sec">
              <h2>系统状态</h2>
              <span class="hint">抓取队列 / 凭证 / 依赖</span>
            </div>
            <div class="pv-panel pv-form">
              <label>抓取队列</label>
              <div>
                <span class="pv-badge ok"><span class="pv-dot ok" />正常</span>
                <span class="pv-help" style="margin-left: 10px">7 个任务在跑，无阻塞</span>
              </div>
              <label>凭证加密</label>
              <div>
                <span class="pv-badge ok"><span class="pv-dot ok" />就位</span>
                <span class="pv-help" style="margin-left: 10px">AES-GCM · 密钥未变更</span>
              </div>
              <label>依赖诊断</label>
              <div>
                <span class="pv-badge warn">3 项待确认</span>
                <span class="pv-help" style="margin-left: 10px">ffmpeg / whisper / 浏览器内核</span>
              </div>
            </div>
          </section>
        </template>

        <!-- ============ 样张 2 · 关注博主 ============ -->
        <template v-else-if="page === 'creators'">
          <div class="pv-page-head">
            <h1>关注博主</h1>
            <span class="desc">真实头像与昵称 · 点击跳转主页</span>
            <div class="pv-spacer" />
            <button class="pv-btn">刷新关注列表</button>
            <button class="pv-btn primary">强制刷新</button>
          </div>

          <div class="pv-tabs">
            <div class="pv-tab on">抖音 3</div>
            <div class="pv-tab">B站 3</div>
          </div>

          <!-- 二级账号 tab -->
          <div class="pv-subtabs">
            <span class="pv-subtab on">抖小云·主号</span>
            <span class="pv-subtab">抖小云·备用号</span>
          </div>

          <section>
            <div class="pv-grid">
              <article v-for="c in creators" :key="c.name" class="pv-card">
                <div class="row">
                  <div class="pv-ava">{{ c.name.slice(0, 1) }}</div>
                  <div style="min-width: 0">
                    <div class="pv-name">
                      <a href="#">{{ c.name }}</a>
                      <span v-if="c.verified" class="pv-badge">认证</span>
                    </div>
                    <div class="pv-stats">粉丝 {{ c.fans }} · 获赞 {{ c.likes }}</div>
                  </div>
                </div>
                <div class="pv-sig">{{ c.sig }}</div>
                <div class="pv-card-foot">
                  <span class="pv-toggle">开启同步 <span class="pv-switch" :class="{ on: c.on }" /></span>
                  <span v-if="c.on" class="pv-toggle">全量 <span class="pv-switch small" :class="{ on: c.full }" /></span>
                  <span v-else class="pv-help">未开启</span>
                </div>
              </article>
            </div>
            <p class="pv-note" style="margin-top: 12px">
              「开启同步」后才出现「全量」开关：全量＝覆盖刷新历史内容，关闭＝只同步新发布。
              数据来自平台真实接口，刷新走缓存（6 小时），风控期间只读缓存。
            </p>
          </section>
        </template>

        <!-- ============ 样张 3 · 账号中心 ============ -->
        <template v-else>
          <div class="pv-page-head">
            <h1>账号中心</h1>
            <span class="desc">统一管理所有平台登录态 · 凭证脱敏</span>
            <div class="pv-spacer" />
            <button class="pv-btn">刷新</button>
            <button class="pv-btn primary">新建账号</button>
          </div>

          <section class="pv-metrics" style="grid-template-columns: repeat(3, 1fr)">
            <div class="pv-metric">
              <div class="k">账号总数</div>
              <div class="v">6</div>
              <div class="d">抖音 2 · B站 2 · 小红书 1 · 视频号 1</div>
            </div>
            <div class="pv-metric">
              <div class="k"><span class="pv-dot ok" />在线</div>
              <div class="v">5</div>
              <div class="d">体检正常</div>
            </div>
            <div class="pv-metric alert">
              <div class="k"><span class="pv-dot bad" />需处理</div>
              <div class="v">1</div>
              <div class="d">Cookie 已过期，需要重新登录</div>
            </div>
          </section>

          <section>
            <div class="pv-sec">
              <h2>全部账号</h2>
              <span class="hint">体检 / 更新凭证 / 删除</span>
            </div>
            <div class="pv-panel">
              <table class="pv-table">
                <thead>
                  <tr>
                    <th style="width: 90px">平台</th>
                    <th>账号</th>
                    <th style="width: 120px">状态</th>
                    <th style="width: 140px">到期时间</th>
                    <th style="width: 140px">上次体检</th>
                    <th style="width: 200px; text-align: right">操作</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="a in accounts" :key="a.name">
                    <td class="strong">{{ a.platform }}</td>
                    <td>{{ a.name }}</td>
                    <td><span class="pv-badge" :class="a.tone">{{ a.status }}</span></td>
                    <td>{{ a.expires }}</td>
                    <td>{{ a.checked }}</td>
                    <td style="text-align: right; white-space: nowrap">
                      <button class="pv-btn ghost">体检</button>
                      <button class="pv-btn ghost">更新凭证</button>
                      <button class="pv-btn ghost danger">删除</button>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </section>

          <!-- 表单片段 -->
          <section>
            <div class="pv-sec">
              <h2>录入方式</h2>
              <span class="hint">扫码登录为主，手动粘 Cookie 兜底</span>
            </div>
            <div class="pv-panel">
              <div class="pv-seg">
                <span class="pv-seg-item on">扫码登录</span>
                <span class="pv-seg-item">手动粘 Cookie</span>
              </div>
              <div class="pv-form" style="border-top: 1px solid var(--line-soft)">
                <label>Cookie</label>
                <input class="pv-input" value="SESSDATA=****; bili_jct=****" />
                <label>备注</label>
                <input class="pv-input" placeholder="例如：主号 / 运营号（可留空）" />
              </div>
            </div>
          </section>
        </template>

        <p class="pv-note">
          这是静态样张，只为确定视觉方向（配色 / 层级 / 间距 / 组件质感），数据为示意。
          敲定方向后，我会把它沉淀为设计令牌 + 组件规范，再统一替换到全部页面。
        </p>
      </main>
    </div>
  </div>
</template>

<script setup lang="ts">
import '../styles/preview.css'
import { computed, ref } from 'vue'

const pages = [
  { key: 'overview', label: '总览' },
  { key: 'creators', label: '关注博主' },
  { key: 'accounts', label: '账号中心' },
]
const dirs = [
  { key: 'atelier', label: 'A · 暖石' },
  { key: 'console', label: 'B · 控制台' },
  { key: 'editorial', label: 'C · 刊物' },
]
const page = ref('overview')
const dir = ref<'atelier' | 'console' | 'editorial'>('atelier')

const navMap: Record<string, string[]> = {
  overview: ['总览'],
  creators: ['关注博主'],
  accounts: ['账号中心'],
}
const nav = computed(() => {
  const active = navMap[page.value][0]
  return [
    { t: '总览', on: active === '总览' },
    { t: '账号中心', on: active === '账号中心' },
    { t: '关键词采集', on: false },
    { t: '订阅同步', on: false, badge: '6' },
    { t: '关注博主', on: active === '关注博主' },
    { t: '素材库', on: false },
    { t: '处理队列', on: false, badge: '7' },
    { t: '数据看板', on: false },
    { t: '系统设置', on: false },
  ]
})

const hot = [
  { title: '3分钟教你做出饭店级红烧肉', platform: '抖音', author: '老饭骨·王刚', play: '182万', like: '12.4万' },
  { title: '露营装备怎么选？一次讲清楚', platform: 'B站', author: '影视飓风', play: '96万', like: '5.1万' },
  { title: '每天10分钟居家燃脂', platform: '抖音', author: '帕梅拉', play: '74万', like: '3.8万' },
  { title: '程序员接私活的几个坑', platform: 'B站', author: '程序员鱼皮', play: '41万', like: '2.2万' },
  { title: '空气炸锅蛋挞零失败教程', platform: '抖音', author: '露营的阿远', play: '28万', like: '1.6万' },
]

const creators = [
  { name: '老饭骨·王刚', fans: '860万', likes: '2580万', sig: '传承中华美食，专注家常菜教学', verified: true, on: true, full: true },
  { name: '帕梅拉·健身日记', fans: '320万', likes: '1600万', sig: '每天 10 分钟，跟着练就有效果', verified: true, on: false, full: false },
  { name: '露营的阿远', fans: '46万', likes: '138万', sig: '周末去哪玩？带你找宝藏露营地', verified: false, on: false, full: false },
]

const accounts = [
  { platform: '抖音', name: '[DEMO] 抖小云·主号', status: '在线', tone: 'ok', expires: '2027-01-04', checked: '今天 14:02' },
  { platform: '抖音', name: '[DEMO] 抖小云·备用号', status: '已掉线', tone: 'bad', expires: '2026-10-03', checked: '今天 14:02' },
  { platform: 'B站', name: '[DEMO] B站·主号', status: '在线', tone: 'ok', expires: '2026-11-05', checked: '今天 13:58' },
  { platform: '小红书', name: '[DEMO] 小红书·种草号', status: '未体检', tone: 'warn', expires: '—', checked: '—' },
]
</script>
