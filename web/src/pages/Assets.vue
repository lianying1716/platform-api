<template>
  <div class="ui-page">
    <div class="ui-head">
      <h1>素材库</h1>
      <span class="sub">共 {{ total }} 条 · 视频可播放，文章可阅读</span>
      <div class="grow" />
      <button class="ui-btn" @click="load">刷新</button>
      <button class="ui-btn" @click="importDialog = true">导入文章</button>
      <button class="ui-btn primary" :disabled="!selected.length" @click="exportSel('md')">
        导出 Markdown
      </button>
      <button class="ui-btn" :disabled="!selected.length" @click="exportSel('json')">导出 JSON</button>
    </div>

    <!-- 筛选条：类型 / 平台 / 分类 / 状态 -->
    <div class="ui-filters">
      <div class="ui-seg">
        <button v-for="v in typeTabs" :key="v.k" :class="{ on: filters.content_type === v.k }" @click="setType(v.k)">
          {{ v.label }}
        </button>
      </div>
      <el-select v-model="filters.platform" placeholder="平台" clearable size="small" style="width: 130px" @change="reloadFromFirstPage">
        <el-option label="B站" value="bilibili" />
        <el-option label="抖音" value="douyin" />
        <el-option label="快手" value="kuaishou" />
        <el-option label="知乎" value="zhihu" />
        <el-option label="小红书" value="xhs" />
        <el-option label="微信公众号" value="wechat_mp" />
      </el-select>
      <el-select v-model="filters.account_id" placeholder="账号" clearable size="small" style="width: 170px" @change="reloadFromFirstPage">
        <el-option
          v-for="a in filteredAccounts"
          :key="a.id"
          :label="a.display_name || `${a.platform} #${a.id}`"
          :value="a.id"
        />
      </el-select>
      <el-select v-model="filters.category" placeholder="分类" clearable size="small" style="width: 150px" @change="reloadFromFirstPage">
        <el-option v-for="(label, key) in taxonomy" :key="key" :label="label" :value="key" />
      </el-select>
      <el-select v-model="filters.transcribe_status" placeholder="转录状态" clearable size="small" style="width: 130px" @change="reloadFromFirstPage">
        <el-option label="待转录" value="pending" />
        <el-option label="已完成" value="done" />
        <el-option label="失败" value="error" />
      </el-select>
      <!-- 排序：全部倒序，默认最新（本次入库顺序） -->
      <el-select v-model="filters.sort" size="small" style="width: 150px" @change="reloadFromFirstPage">
        <el-option label="最新（默认）" value="latest" />
        <el-option label="按发布时间" value="published_at" />
        <el-option label="按同步时间" value="synced_at" />
        <el-option label="按点赞数" value="like_count" />
      </el-select>
      <span class="grow" />
      <span v-if="selected.length" class="ui-badge acc">已选 {{ selected.length }} 条</span>
    </div>

    <!-- 卡片网格 -->
    <div v-loading="loading">
      <div v-if="!loading && !assets.length" class="ui-empty">
        <div class="big">还没有素材</div>
        <p>去「关键词采集」或「关注列表」跑一次同步，素材会自动入库。</p>
      </div>
      <div v-else class="asset-grid">
        <article v-for="a in assets" :key="a.asset_id" class="asset" :class="{ picked: isPicked(a) }">
          <!-- 上：封面（可点即开始：视频播放 / 文章阅读） -->
          <div
            class="cover"
            :class="{ clickable: canOpen(a) }"
            :role="canOpen(a) ? 'button' : undefined"
            :title="coverHint(a)"
            @click="onCoverClick(a)"
          >
            <!-- 封面走服务端代理：抖音/B站封面是签名防盗链地址，直连会过期或 403。
                 加载失败时自动退回占位符，避免留下空白/破图。 -->
            <img v-if="a.has_cover && a.cover_allowed !== false && !coverFailed(a.asset_id)"
              :src="coverSrc(a.asset_id)" alt=""
              loading="lazy" @error="markCoverFailed(a.asset_id)" />
            <span v-else class="ph">
              <span class="ph-ic" aria-hidden="true">{{ a.content_type === 'video' ? '▶' : '¶' }}</span>
              <span class="ph-t">{{ typeLabel(a.content_type) }}</span>
            </span>
            <!-- 平台标：纯图标，无黑底 -->
            <span class="badge-plat"><PlatformMark :platform="a.platform" :show-label="false" size="sm" plain /></span>
            <span v-if="a.duration_sec" class="badge-dur">{{ mmss(a.duration_sec) }}</span>
            <span v-if="canOpen(a)" class="cover-cta">
              <span class="cta-ic">{{ a.content_type === 'video' ? '▶' : '阅' }}</span>
            </span>
            <span v-else-if="a.content_type === 'video'" class="badge-nofile">未下载</span>
          </div>

          <!-- 下：信息（正文用 flex:1 撑满，操作区贴底） -->
          <div class="body">
            <div class="title-row">
              <span class="type-tag">{{ typeLabel(a.content_type) }}</span>
              <div class="title" :title="a.title">{{ a.title || '（无标题）' }}</div>
            </div>

            <div class="author">
              <span class="ava" :style="authorAvatarUrl(a) ? { backgroundImage: `url(${authorAvatarUrl(a)})` } : {}">
                {{ authorAvatarUrl(a) ? '' : (a.author_name || '?').slice(0, 1) }}
              </span>
              <a v-if="safeExternalUrl(a.author_home_url)" :href="safeExternalUrl(a.author_home_url)" target="_blank" rel="noopener noreferrer" class="aname">
                {{ a.author_name || a.author_id }}
              </a>
              <span v-else class="aname">{{ a.author_name || '未知博主' }}</span>
              <span v-if="a.auto_category" class="ui-badge acc cat-inline">
                {{ taxonomy[a.auto_category] || a.auto_category }}
              </span>
              <span v-else class="ui-badge none cat-inline">未判类</span>
            </div>

            <!-- 元数据：左右两列，压缩卡片高度 -->
            <div class="meta-grid">
              <div class="mrow"><span class="mk">发布</span><span class="mv">{{ fmtDate(a.published_at) }}</span></div>
              <div class="mrow"><span class="mk">同步</span><span class="mv">{{ fmtMinute(a.synced_at || a.created_at) }}</span></div>
              <div class="mrow" v-if="hasEngagement(a)">
                <span class="mk">互动</span>
                <span class="mv nums-inline">
                  <span v-if="a.metrics?.like"><b>{{ compact(a.metrics.like) }}</b> 赞</span>
                  <span v-if="a.metrics?.comment"><b>{{ compact(a.metrics.comment) }}</b> 评</span>
                  <span v-if="a.metrics?.favorite"><b>{{ compact(a.metrics.favorite) }}</b> 藏</span>
                </span>
              </div>
              <div class="mrow"><span class="mk">大小</span><span class="mv">{{ bytes(a.size_bytes) }}</span></div>
            </div>

            <div class="foot">
              <span class="stagers">
                <span
                  class="ui-badge"
                  :class="statusTone(a.transcribe_status)"
                  :title="'转录：' + s(a.transcribe_status)"
                >转录 {{ s(a.transcribe_status) }}</span>
                <span
                  class="ui-badge"
                  :class="statusTone(a.analyze_status)"
                  :title="'分析速览：' + s(a.analyze_status)"
                >速览 {{ s(a.analyze_status) }}</span>
                <span
                  class="ui-badge"
                  :class="statusTone(a.download_status)"
                  :title="a.download_status === 'error' ? (a.error || '下载失败') : ''"
                >{{ s(a.download_status) }}</span>
              </span>
              <span class="grow" />
              <label v-if="selected.length || pickMode" class="pick">
                <input type="checkbox" :checked="isPicked(a)" @change="togglePick(a)" />
              </label>
            </div>

            <div class="acts">
              <button class="ui-btn sm" @click="openDetail(a.asset_id)">详情</button>
              <button
                v-if="a.download_status === 'error' || a.download_status === 'pending'"
                class="ui-btn sm"
                @click="act(a.asset_id, 'download')"
              >
                {{ a.download_status === 'error' ? '重新下载' : '开始下载' }}
              </button>
              <button
                class="ui-btn sm"
                :disabled="a.download_status !== 'done'"
                @click="act(a.asset_id, 'transcribe')"
              >
                {{ isTranscribed(a) ? '重新转录' : '转录' }}
              </button>
              <button
                class="ui-btn sm"
                :disabled="a.download_status !== 'done'"
                title="生成速览：一段摘要 + 标签，快速了解内容"
                @click="act(a.asset_id, 'analyze')"
              >
                {{ isAnalyzed(a) ? '重新速览' : '速览' }}
              </button>
              <button class="ui-btn sm" @click="refreshMetrics(a.asset_id)">刷新指标</button>
            </div>
          </div>
        </article>
      </div>
    </div>

    <!-- 分页：总数来自 X-Total-Count，不只是当前页长度。 -->
    <div v-if="totalPages > 1" class="ui-pager">
      <button class="ui-btn sm" :disabled="page <= 1 || loading" @click="goPage(page - 1)">上一页</button>
      <span class="pager-info">第 {{ page }} / {{ totalPages }} 页 · 每页 {{ pageSize }} 条</span>
      <button class="ui-btn sm" :disabled="page >= totalPages || loading" @click="goPage(page + 1)">下一页</button>
    </div>

    <!-- 视频播放器（走 Range 流式接口，可拖进度条） -->
    <div v-if="player" class="ui-modal" @click.self="player = null">
      <div class="ui-modal-box player-box">
        <div class="ui-panel-head">
          <h2>{{ player.title || '（无标题）' }}</h2>
          <div class="grow" />
          <button class="ui-btn ghost sm" @click="player = null">关闭</button>
        </div>
        <div class="player-wrap">
          <video
            :src="mediaSrc(player.asset_id)"
            controls
            autoplay
            playsinline
            preload="metadata"
          />
        </div>
        <div class="player-meta">
          <PlatformMark :platform="player.platform" size="sm" />
          <span>· {{ player.author_name || '未知博主' }}</span>
          <span>{{ fmtDate(player.published_at) }} 发布 · {{ bytes(player.size_bytes) }}</span>
        </div>
      </div>
    </div>

    <!-- 文章阅读视图 -->
    <div v-if="article" class="ui-modal article-modal" @click.self="article = null">
      <div class="ui-modal-box">
        <div class="ui-panel-head">
          <h2>{{ article.title }}</h2>
          <div class="grow" />
          <a v-if="safeExternalUrl(article.url)" class="ui-btn sm" :href="safeExternalUrl(article.url)" target="_blank" rel="noopener noreferrer">打开原文</a>
          <button class="ui-btn ghost sm" @click="article = null">关闭</button>
        </div>
        <div class="ui-modal-body article-body">
          <p class="art-meta">{{ article.author_name }} · {{ fmtDate(article.published_at) }}</p>
          <SafeHtml v-if="article.html" :html="article.html" class="art-html" />
          <pre v-else class="art-text">{{ article.text }}</pre>
        </div>
      </div>
    </div>

    <el-dialog v-model="importDialog" title="导入微信公众号文章" width="520px">
      <el-input v-model="importUrl" placeholder="https://mp.weixin.qq.com/s/..." clearable />
      <p class="hint">仅支持单篇文章链接。批量历史文章列表接口微信已关闭，无法导入整号历史。</p>
      <template #footer>
        <el-button @click="importDialog = false">取消</el-button>
        <el-button type="primary" :loading="importing" @click="doImport">导入</el-button>
      </template>
    </el-dialog>

    <el-drawer
      v-model="drawer"
      title="素材详情"
      size="min(860px, 92vw)"
      append-to-body
      @close="closeDrawer"
    >
      <div v-if="detail" class="dt">
        <!-- 头部：标题 + 来源 -->
        <header class="dt-head">
          <h3 class="dt-title">{{ detail.content?.title }}</h3>
          <p class="dt-sub">
            <PlatformMark :platform="detail.content?.platform" size="sm" />
            <span>{{ detail.content?.author_name }}</span>
            <span v-if="fmtDate(detail.content?.published_at)">· {{ fmtDate(detail.content?.published_at) }} 发布</span>
          </p>
        </header>

        <!-- 分档加工：置顶（最常做的操作放最上面） -->
        <section class="dt-block dt-pin">
          <div class="tier-row">
            <span class="dt-block-title">分档加工</span>
            <el-tag size="small" type="info">当前档位：{{ tierLabel(detail.status?.tier) }}</el-tag>
            <el-tag size="small" :type="t(proc.understand_status)">理解 {{ sStatus(proc.understand_status) }}</el-tag>
          </div>
          <div v-if="pipelineSteps.length" class="wf-steps">
            <div v-for="step in pipelineSteps" :key="step.step" class="wf-step" :class="stepTone(step.status)">
              <span class="wf-dot" aria-hidden="true" />
              <span class="wf-name">{{ stepLabel(step.step) }}</span>
              <span class="wf-state">{{ stepStateText(step.status) }}</span>
              <span v-if="step.status === 'waiting_prerequisite' && step.blocked_by?.length" class="wf-blocked">
                等待 {{ step.blocked_by.map(stepLabel).join('、') }}
              </span>
            </div>
          </div>
          <div class="tier-actions">
            <el-button size="small" :loading="procBusy" :disabled="procBusy" @click="runProcess('basic')">转录（普通）</el-button>
            <el-button size="small" type="primary" :loading="procBusy" :disabled="procBusy" @click="runProcess('advanced')">视频理解（高级）</el-button>
            <el-checkbox v-model="proc.use_cover">判类用封面</el-checkbox>
            <el-select v-model="proc.force_category" placeholder="改类型重跑（可选）" clearable size="small" style="width: 180px">
              <el-option v-for="(label, key) in taxonomy" :key="key" :label="label" :value="key" />
            </el-select>
            <el-button size="small" :loading="procBusy" :disabled="!proc.force_category || procBusy" @click="runReclassify">按类型重跑</el-button>
          </div>
        </section>

        <!-- 横向 tab -->
        <nav class="dt-tabs" role="tablist">
          <button
            v-for="tb in detailTabs" :key="tb.k"
            class="dt-tab" :class="{ on: detailTab === tb.k }"
            role="tab" :aria-selected="detailTab === tb.k"
            @click="detailTab = tb.k"
          >
            {{ tb.label }}
            <span v-if="tb.count !== undefined && tb.count !== null" class="dt-tab-n">{{ tb.count }}</span>
          </button>
        </nav>

        <!-- Tab: 文件 / 链接 -->
        <section v-show="detailTab === 'files'" class="dt-pane">
          <div class="dt-field">
            <span class="dt-k">相对目录</span>
            <code class="dt-dir" :title="detail.file?.relative_path || ''">{{ detail.file?.relative_path || '—' }}</code>
          </div>
          <div class="dt-field">
            <span class="dt-k">视频文件</span>
            <span class="dt-v">{{ detail.file?.video_file || '—' }}</span>
          </div>
          <div class="dt-actions">
            <a
              v-if="safeExternalUrl(detail.content?.url)"
              class="ui-btn primary sm"
              :href="safeExternalUrl(detail.content?.url)" target="_blank" rel="noopener noreferrer"
            >打开原链接</a>
            <span v-else class="c-meta">该素材没有原始链接</span>
            <button class="ui-btn danger sm" @click="removeAsset">删除</button>
          </div>
        </section>

        <!-- Tab: 转录 -->
        <section v-show="detailTab === 'transcript'" class="dt-pane">
          <el-input v-if="detail.transcript" type="textarea" :rows="18" :model-value="detail.transcript.full_text" readonly />
          <el-empty v-else description="尚无转录" :image-size="60" />
        </section>

        <!-- Tab: 分析 -->
        <section v-show="detailTab === 'analysis'" class="dt-pane">
          <p class="dt-hint">
            <b>速览</b>：转录后可自动生成的轻量结果，只有摘要 + 标签，用于快速扫一眼、检索和导出。
            要看「为什么火 + 怎么复刻」的深度报告，请切到「爆款拆解」。
          </p>
          <div v-if="detail.analysis">
            <p><b>摘要：</b>{{ detail.analysis.summary }}</p>
            <div class="dt-tags">
              <el-tag v-for="tag in detail.analysis.tags" :key="tag" size="small">{{ tag }}</el-tag>
            </div>
          </div>
          <el-empty v-else description="尚无速览（可在卡片上点「分析」）" :image-size="60" />
        </section>

        <!-- Tab: 视频理解 -->
        <section v-show="detailTab === 'understand'" class="dt-pane">

        <p class="dt-hint vision-hint">
          视频理解必须使用支持视觉输入的模型；纯文本模型看不到画面。
          <router-link to="/models">前往模型接入</router-link>
        </p>

        <div v-if="understanding" class="understand">
          <p>
            <b>判类：</b>
            <el-tag size="small" type="success">{{ taxonomy[understanding.category] || understanding.category }}</el-tag>
            <span class="c-meta">置信度 {{ (understanding.category_confidence * 100).toFixed(0) }}%</span>
            <el-tag v-if="understanding.low_confidence" size="small" type="warning">低置信度</el-tag>
          </p>
          <p class="c-meta">{{ understanding.category_reason }}</p>
          <p v-if="understanding.summary"><b>概述：</b>{{ understanding.summary }}</p>
          <div v-if="understanding.dimensions && Object.keys(understanding.dimensions).length" class="dims">
            <div v-for="(v, k) in understanding.dimensions" :key="k" class="dim">
              <span class="dim-k">{{ dimensionName(k) }}</span>
              <span class="dim-v">{{ fmtVal(v) }}</span>
            </div>
          </div>
          <p v-if="understanding.timeline?.length" class="c-meta">
            时间线：<template v-for="(e, i) in understanding.timeline" :key="i">{{ e.t }}s {{ e.event }}<template v-if="i < understanding.timeline.length - 1"> · </template></template>
          </p>
          <p class="c-meta">模型 {{ understanding.model }} · 版本 {{ understanding.prompt_version }}</p>
          <p v-if="understanding.error" class="err">{{ understanding.error }}</p>
        </div>
        <p v-else-if="proc.understand_status === 'error'" class="err">理解失败：{{ understandError || detail.status?.error }}</p>
        </section>

        <!-- Tab: 评论 -->
        <section v-show="detailTab === 'comments'" class="dt-pane">
          <p class="comment-total">评论总数 {{ detail?.metrics?.comment ?? '—' }}</p>
          <div v-if="comments.length">
            <div v-for="c in comments" :key="c.comment_id" class="comment">
              <div class="c-head">
                <b>{{ c.nickname || '匿名' }}</b>
                <span class="c-meta">{{ c.ip_location }} · 赞 {{ c.like_count }}<template v-if="fmtTime(c.created_at)"> · {{ fmtTime(c.created_at) }}</template></span>
              </div>
              <div class="c-body">{{ c.content }}</div>
            </div>
            <el-button v-if="comments.length < commentTotal" text @click="loadMoreComments">加载更多</el-button>
          </div>
          <p v-else class="comment-hint">该作品评论数或点赞数未达抓取门槛，仅显示数量。</p>
        </section>

        <!-- Tab: 爆款拆解 -->
        <section v-show="detailTab === 'breakdown'" class="dt-pane">
        <p class="dt-hint vision-hint">
          爆款拆解依赖逐镜头画面分析，请先把「视频理解」分配给支持视觉输入的模型（如 Kimi K3、MiMo、Qwen3.8 Flash、Gemini 3.8 Flash）。
          <router-link to="/models">前往模型接入</router-link>
        </p>
        <p class="dt-hint">
          <b>深度报告</b>：手动触发的完整拆解，会综合转录、视频理解、评论与后端算好的互动数据，
          产出「为什么火 + 怎么复刻」。它比「分析」速览更慢、更费 token，但结论更完整。
        </p>
        <div class="bd-actions">
          <el-button size="small" type="primary" :loading="bdBusy" :disabled="bdBusy || isBdActive()" @click="runBreakdown(!!breakdown)">
            {{ breakdown ? '重新拆解' : '生成爆款拆解' }}
          </el-button>
          <el-tag v-if="isBdActive()" size="small" type="warning">拆解中…</el-tag>
          <el-tag v-else-if="bdStatus === 'waiting_prerequisite'" size="small" type="info">等待前置步骤</el-tag>
          <el-tag v-else-if="bdStatus === 'error'" size="small" type="danger">失败</el-tag>
          <span class="c-meta">复用转录 / 视频理解 / 评论 / 指标，产出「为什么火 + 怎么复刻」</span>
        </div>
        <p v-if="bdStatus === 'waiting_prerequisite'" class="dt-hint">
          拆解需要先完成转录和视频理解；完成后会自动继续，不需要重复点击。
        </p>
        <p v-if="bdStatus === 'error' && bdError" class="err">{{ bdError }}</p>

        <div v-if="breakdown" class="bd-report">
          <div class="bd-head">
            <el-progress type="dashboard" :percentage="Math.round(breakdown.score || 0)" :width="96" />
            <div class="bd-verdict">
              <p class="bd-verdict-text">{{ breakdown.verdict || '（无结论）' }}</p>
              <p class="c-meta">
                模型 {{ breakdown.model || '—' }} · 提示词 {{ breakdown.prompt_version || '—' }}
                <el-tag v-if="breakdown.understanding_available === false" size="small" type="warning">无视频理解（降级报告）</el-tag>
              </p>
            </div>
          </div>

          <div v-if="breakdown.sections?.hook" class="bd-block">
            <h4>开场钩子</h4>
            <p v-if="breakdown.sections.hook.first_line"><b>首句台词：</b>{{ breakdown.sections.hook.first_line }}</p>
            <p v-if="breakdown.sections.hook.first_frame"><b>首帧画面：</b>{{ breakdown.sections.hook.first_frame }}</p>
            <p>
              <el-tag v-if="breakdown.sections.hook.hook_type" size="small">{{ breakdown.sections.hook.hook_type }}</el-tag>
              <span class="c-meta">{{ breakdown.sections.hook.why }}</span>
            </p>
          </div>

          <div v-if="breakdown.sections?.structure?.length" class="bd-block">
            <h4>内容结构</h4>
            <el-table :data="breakdown.sections.structure" size="small" border>
              <el-table-column prop="segment" label="时间段" width="110" />
              <el-table-column prop="role" label="作用" width="90" />
              <el-table-column prop="summary" label="概述" show-overflow-tooltip />
            </el-table>
          </div>

          <div v-if="breakdown.sections?.pacing" class="bd-block">
            <h4>节奏</h4>
            <p v-if="breakdown.sections.pacing.emotion_curve"><b>情绪曲线：</b>{{ breakdown.sections.pacing.emotion_curve }}</p>
            <p v-if="breakdown.sections.pacing.pattern_interrupts?.length">
              <b>变化点：</b>
              <template v-for="(x, i) in breakdown.sections.pacing.pattern_interrupts" :key="i">{{ x }}<template v-if="i < breakdown.sections.pacing.pattern_interrupts.length - 1">；</template></template>
            </p>
            <p v-if="breakdown.sections.pacing.re_engagement_points?.length">
              <b>二次拉回：</b>{{ breakdown.sections.pacing.re_engagement_points.join('、') }} 秒
            </p>
          </div>

          <div v-if="breakdown.sections?.script" class="bd-block">
            <h4>文案</h4>
            <p v-if="breakdown.sections.script.money_lines?.length"><b>金句：</b>{{ breakdown.sections.script.money_lines.join('；') }}</p>
            <p v-if="breakdown.sections.script.reusable_phrases?.length"><b>可复用句式：</b>{{ breakdown.sections.script.reusable_phrases.join('；') }}</p>
          </div>

          <div v-if="breakdown.sections?.psych_triggers?.length" class="bd-block">
            <h4>心理触发</h4>
            <el-tag v-for="(p, i) in breakdown.sections.psych_triggers" :key="i" size="small" style="margin-right: 6px">{{ p }}</el-tag>
          </div>

          <div v-if="breakdown.sections?.cta" class="bd-block">
            <h4>行动号召（CTA）</h4>
            <p>
              <el-tag v-if="breakdown.sections.cta.type" size="small">{{ breakdown.sections.cta.type }}</el-tag>
              <span v-if="breakdown.sections.cta.placement" class="c-meta">位置：{{ breakdown.sections.cta.placement }}</span>
            </p>
          </div>

          <div v-if="hasCommentInsights" class="bd-block">
            <h4>评论洞察</h4>
            <p v-if="breakdown.comment_insights.top_themes?.length"><b>热门话题：</b>{{ breakdown.comment_insights.top_themes.join('；') }}</p>
            <p v-if="breakdown.comment_insights.controversies?.length"><b>争议点：</b>{{ breakdown.comment_insights.controversies.join('；') }}</p>
            <p v-if="breakdown.comment_insights.questions?.length"><b>可回复问题：</b>{{ breakdown.comment_insights.questions.join('；') }}</p>
          </div>

          <div v-if="breakdown.data_context" class="bd-block">
            <h4>数据上下文 <span class="c-meta">（后端确定性计算）</span></h4>
            <div class="bd-metrics">
              <div class="bd-metric"><span class="dim-k">播放</span><span>{{ fmtNum(breakdown.data_context.play) }}</span></div>
              <div class="bd-metric"><span class="dim-k">点赞</span><span>{{ fmtNum(breakdown.data_context.like) }}</span></div>
              <div class="bd-metric"><span class="dim-k">评论</span><span>{{ fmtNum(breakdown.data_context.comment) }}</span></div>
              <div class="bd-metric"><span class="dim-k">播放 / 中位数</span><span>{{ fmtMult(breakdown.data_context.play_vs_median) }}</span></div>
              <div class="bd-metric"><span class="dim-k">点赞 / 中位数</span><span>{{ fmtMult(breakdown.data_context.like_vs_median) }}</span></div>
              <div class="bd-metric"><span class="dim-k">互动率</span><span>{{ fmtPct(breakdown.data_context.engagement_rate) }}</span></div>
              <div class="bd-metric"><span class="dim-k">分位</span><span>{{ fmtPct(breakdown.data_context.percentile, 0) }}</span></div>
            </div>
          </div>

          <div v-if="breakdown.sections?.blueprint?.length" class="bd-block">
            <h4>复刻蓝图</h4>
            <ol class="bd-list"><li v-for="(x, i) in breakdown.sections.blueprint" :key="i">{{ x }}</li></ol>
          </div>

          <div v-if="breakdown.sections?.replication_tips?.length" class="bd-block">
            <h4>落地建议</h4>
            <ul class="bd-list"><li v-for="(x, i) in breakdown.sections.replication_tips" :key="i">{{ x }}</li></ul>
          </div>
        </div>
        <el-empty v-else-if="!isBdActive() && bdStatus !== 'error'" description="尚无拆解报告" :image-size="60" />
        </section>
      </div>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
// 显式组件名：Layout 的 keep-alive include 按组件名匹配，切页返回时保留本页数据。
defineOptions({ name: 'AssetsPage' })
import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import { ElMessageBox } from 'element-plus/es/components/message-box/index.mjs'
import { computed, onActivated, onDeactivated, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import PlatformMark from '../components/PlatformMark.vue'
import SafeHtml from '../components/SafeHtml.vue'
import { api } from '../api'
import { parseUtc } from '../utils/time'
import { safeExternalUrl } from '../utils/safeUrl'
import { coverSrc, localAssetUrl, mediaSrc } from '../demo/runtime'

const route = useRoute()

const assets = ref<any[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = 60
const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize)))
const selected = ref<any[]>([])
const loading = ref(false)
const drawer = ref(false)
const detail = ref<any>(null)
// 详情抽屉的横向 tab（详情页内容较多，拆成并列 tab 而不是纵向长滚动）
const detailTab = ref<'files' | 'transcript' | 'analysis' | 'understand' | 'comments' | 'breakdown'>('files')
const detailTabs = computed(() => [
  { k: 'files', label: '文件 / 链接' },
  { k: 'transcript', label: '转录' },
  { k: 'analysis', label: '分析速览' },
  { k: 'understand', label: '视频理解' },
  { k: 'comments', label: '评论', count: commentTotal.value || undefined },
  { k: 'breakdown', label: '爆款拆解 · 深度' },
])
const filters = reactive({
  platform: '',
  account_id: null as number | null,
  transcribe_status: '',
  content_type: '',
  category: '',
  sort: 'latest',
})
const accounts = ref<Array<{ id: number; platform: string; display_name: string }>>([])
const filteredAccounts = computed(() =>
  accounts.value.filter((a) => !filters.platform || a.platform === filters.platform),
)

async function loadAccounts() {
  const { data } = await api.get('/api/accounts')
  accounts.value = data
}

/** 封面加载失败的素材 id：失败后退回类型占位符，不再显示破图。 */
const failedCovers = reactive(new Set<string>())
function coverFailed(id: string) { return failedCovers.has(id) }
function markCoverFailed(id: string) { failedCovers.add(id) }
const authorAvatarUrl = (a: any) => localAssetUrl(a.author_avatar_url)

const typeTabs = [
  { k: '', label: '全部' },
  { k: 'video', label: '视频' },
  { k: 'article', label: '文章' },
]
function setType(k: string) {
  filters.content_type = k
  reloadFromFirstPage()
}

// 卡片选集：默认只在已选或已有勾选时显示勾选框
const pickMode = ref(false)
function isPicked(a: any) {
  return selected.value.some((x) => x.asset_id === a.asset_id)
}
function togglePick(a: any) {
  const i = selected.value.findIndex((x) => x.asset_id === a.asset_id)
  if (i >= 0) selected.value.splice(i, 1)
  else selected.value.push(a)
}

// 视频播放 / 文章阅读
const player = ref<any>(null)
const article = ref<any>(null)
function openPlayer(a: any) {
  player.value = a
}
async function openArticle(assetId: number) {
  const { data } = await api.get(`/api/assets/${assetId}/article`)
  article.value = data
}

// 封面即入口：视频点开播放（需已下载），文章点开阅读（有正文即可）。
function canOpen(a: any) {
  return a.content_type === 'video' ? !!a.playable : true
}
function coverHint(a: any) {
  if (a.content_type === 'video') return a.playable ? '点击封面播放' : '未下载，无法播放'
  return '点击封面阅读'
}
function onCoverClick(a: any) {
  if (!canOpen(a)) return
  if (a.content_type === 'video') openPlayer(a)
  else openArticle(a.asset_id)
}
// 已转录 / 已分析：决定按钮显示「转录」还是「重新转录」。
function isTranscribed(a: any) {
  return a.transcribe_status === 'done'
}
function isAnalyzed(a: any) {
  return a.analyze_status === 'done'
}
// 同步时间在卡片里只到分钟，避免 04:09:55 被截断成 04:0…
function fmtMinute(v: string | null | undefined) {
  if (!v) return '—'
  const d = parseUtc(v)
  if (!d) return '—'
  return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })
}

// 互动指标（赞/评/藏）：平台常拿不到，全为 0 时不占用版面。
function hasEngagement(a: any) {
  const m = a.metrics || {}
  return Boolean(m.like || m.comment || m.favorite)
}

const importDialog = ref(false)
const importUrl = ref('')
const importing = ref(false)

const comments = ref<any[]>([])
const commentTotal = ref(0)
const commentOffset = ref(0)

// 分档加工
const proc = reactive<any>({ understand_status: '', use_cover: false, force_category: '' })
const understanding = ref<any>(null)
const understandError = ref('')
const taxonomy = ref<Record<string, string>>({})
const commonDimensions = ref<any[]>([])
const dimensionNames = ref<Record<string, string>>({})
// 「判类默认用封面」全局默认值（来自设置页 process_use_cover），
// 抽屉打开时用它初始化勾选框，否则设置页开关没有消费方（回归修复）。
const globalUseCover = ref(false)
const procBusy = ref(false)
const pipelineSteps = ref<any[]>([])

let procTimer: number | undefined

// 爆款拆解：报告 / 状态 / 轮询。
const breakdown = ref<any>(null)
const bdStatus = ref('')
const bdError = ref('')
const bdBusy = ref(false)
// 本次会话是否已点过「生成/重新拆解」。GET 对「从未生成」也返回 pending，
// 靠它区分「排队中」与「尚未生成」，避免未请求就显示「拆解中」（UX 修正）。
const bdRequested = ref(false)
let bdTimer: number | undefined

// 加工中状态：需轮询等待 worker 完成。
const ACTIVE_STATUS = ['pending', 'doing']
function isProcActive() {
  return ACTIVE_STATUS.includes(proc.understand_status)
}
function stopProcPoll() {
  if (procTimer) { clearInterval(procTimer); procTimer = undefined }
}
function syncProcTimer() {
  if (drawer.value && detail.value && isProcActive()) {
    if (!procTimer) procTimer = window.setInterval(() => loadProcess(detail.value.asset_id), 3000)
  } else {
    stopProcPoll()
  }
}

// 是否在途：本地已请求（pending/doing）或服务端明确 doing 才轮询。
function isBdActive() {
  if (bdStatus.value === 'doing') return true
  if (bdStatus.value === 'queued' || bdStatus.value === 'running') return true
  return bdRequested.value && bdStatus.value === 'pending'
}
function stopBdPoll() {
  if (bdTimer) { clearInterval(bdTimer); bdTimer = undefined }
}
function syncBdTimer() {
  const hasInFlightStep = pipelineSteps.value.some((step: any) =>
    ['queued', 'running', 'ready'].includes(step.status))
  if (drawer.value && detail.value && (isBdActive() || hasInFlightStep)) {
    if (!bdTimer) bdTimer = window.setInterval(() => loadBreakdown(detail.value.asset_id), 3000)
  } else {
    stopBdPoll()
  }
}

const procStatusText: Record<string, string> = {
  pending: '待处理', doing: '进行中', done: '完成', error: '失败',
}
function sStatus(v: string) { return procStatusText[v] ?? (v || '—') }

const STEP_LABELS: Record<string, string> = {
  transcribe: '语音转文字', analyze: '分析速览', understand_video: '视频理解', breakdown: '爆款拆解',
}
function stepLabel(v: string) { return STEP_LABELS[v] || v }
const STEP_STATE_TEXT: Record<string, string> = {
  waiting_prerequisite: '等待前置', ready: '待入队', queued: '排队中',
  running: '进行中', done: '完成', error: '失败', skipped: '已跳过',
}
function stepStateText(v: string) { return STEP_STATE_TEXT[v] || v }
function stepTone(v: string) {
  return ({ done: 'ok', error: 'bad', running: 'run', queued: 'warn', waiting_prerequisite: 'wait' } as Record<string, string>)[v] || ''
}

function dimensionName(key: string) {
  return dimensionNames.value[key] || key
}
function fmtVal(v: any) {
  if (v === null || v === undefined || v === '') return '—'
  return typeof v === 'object' ? JSON.stringify(v, null, 0) : String(v)
}

// 档位取值 -> 中文（两档：basic 普通 / advanced 高级）。
const tierNames: Record<string, string> = { basic: '普通（转录）', advanced: '高级（视频理解）' }
function tierLabel(tier: string | undefined) {
  return tier ? (tierNames[tier] ?? tier) : '未加工'
}

// 维度 key -> 中文名：合并通用维度 + 各类型专属维度。
function buildDimensionNames(common: any[], prompts: Record<string, any>) {
  const names: Record<string, string> = {}
  for (const d of common || []) if (d?.key) names[d.key] = d.name
  for (const spec of Object.values(prompts || {})) {
    for (const d of (spec as any)?.dimensions || []) if (d?.key) names[d.key] = d.name
  }
  dimensionNames.value = names
}

async function loadConfig(force = false) {
  if (!force && commonDimensions.value.length) return
  try {
    const { data } = await api.get('/api/system/process-settings')
    taxonomy.value = data.taxonomy || {}
    commonDimensions.value = data.common_dimensions || []
    buildDimensionNames(commonDimensions.value, data.type_prompts || {})
    globalUseCover.value = !!data.use_cover
  } catch { /* 设置不可用不阻塞详情 */ }
}

async function loadProcess(assetId: number, opts: { resetInputs?: boolean } = {}) {
  await loadConfig(opts.resetInputs)
  if (opts.resetInputs) {
    proc.use_cover = globalUseCover.value
    proc.force_category = ''
  }
  understanding.value = null
  understandError.value = ''
  const u = await api.get(`/api/assets/${assetId}/understanding`)
  proc.understand_status = u.data.understand_status
  if (detail.value?.status && u.data.process_tier) detail.value.status.tier = u.data.process_tier
  understanding.value = u.data.status === 'pending' ? null : u.data
  understandError.value = u.data.error || ''
  syncProcTimer()
}

async function runProcess(tier: string) {
  if (!detail.value || procBusy.value) return
  procBusy.value = true
  try {
    await api.post(`/api/assets/${detail.value.asset_id}/process`, {
      tier,
      force_category: proc.force_category || undefined,
      use_cover: proc.use_cover,
    })
    ElMessage.success('已加入队列')
    await loadProcess(detail.value.asset_id)
  } finally {
    procBusy.value = false
  }
}

async function runReclassify() {
  if (!detail.value || !proc.force_category || procBusy.value) return
  procBusy.value = true
  try {
    await api.post(`/api/assets/${detail.value.asset_id}/reclassify`, {
      force_category: proc.force_category,
      use_cover: proc.use_cover,
    })
    ElMessage.success('已按新类型重跑')
    await loadProcess(detail.value.asset_id)
  } finally {
    procBusy.value = false
  }
}

// ---- 爆款拆解 ----
const hasCommentInsights = computed(() => {
  const ci = breakdown.value?.comment_insights
  if (!ci) return false
  return ['top_themes', 'controversies', 'questions'].some((k) => ci[k]?.length)
})

async function loadBreakdown(assetId: number) {
  const { data } = await api.get(`/api/assets/${assetId}/breakdown`)
  const breakdownStep = data.pipeline?.steps?.find((row: any) => row.step === 'breakdown')
  bdStatus.value = breakdownStep?.status || data.status
  pipelineSteps.value = data.pipeline?.steps || []
  if (data.status === 'done') {
    breakdown.value = data
    bdError.value = ''
  } else {
    breakdown.value = null
    bdError.value = data.error || breakdownStep?.error || ''
  }
  syncBdTimer()
}

async function runBreakdown(force: boolean) {
  if (!detail.value || bdBusy.value) return
  bdBusy.value = true
  bdRequested.value = true
  try {
    await api.post(`/api/assets/${detail.value.asset_id}/breakdown`, { force })
    ElMessage.success('已加入拆解队列')
    await loadBreakdown(detail.value.asset_id)
  } finally {
    bdBusy.value = false
  }
}

function fmtNum(v: any) {
  if (v === null || v === undefined) return '—'
  return Number(v).toLocaleString('zh-CN')
}
function fmtMult(v: any) {
  if (!v) return '—'
  return `${Number(v).toFixed(2)}×`
}
function fmtPct(v: any, digits = 1) {
  if (v === null || v === undefined) return '—'
  return `${(Number(v) * 100).toFixed(digits)}%`
}

async function doImport() {
  if (!importUrl.value.trim()) return
  importing.value = true
  try {
    await api.post('/api/imports/wechat-article', { url: importUrl.value.trim() })
    ElMessage.success('导入成功')
    importDialog.value = false
    importUrl.value = ''
    await load()
  } finally {
    importing.value = false
  }
}

const statusText: Record<string, string> = {
  pending: '待处理', downloading: '下载中', transcribing: '转录中', analyzing: '分析中', done: '完成', error: '失败',
}
function s(v: string) { return statusText[v] ?? v }
function t(v: string) { return ({ done: 'success', error: 'danger' } as Record<string, string>)[v] ?? 'info' }

// 卡片展示用格式化
const PLATFORM_LABELS: Record<string, string> = {
  douyin: '抖音', bilibili: 'B站', kuaishou: '快手', zhihu: '知乎',
  xhs: '小红书', wechat_mp: '微信公众号',
}
function platformLabel(p: string) {
  return PLATFORM_LABELS[p] || p || '未知'
}
const TYPE_LABELS: Record<string, string> = { video: '视频', article: '文章', image: '图文' }
function typeLabel(t: string) {
  return TYPE_LABELS[t] || t || '内容'
}
function statusTone(v: string) {
  return ({ done: 'ok', error: 'bad' } as Record<string, string>)[v] || ''
}
function mmss(sec: number) {
  const s = Math.max(0, Math.round(sec))
  const m = Math.floor(s / 60)
  return `${String(m).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`
}
function compact(v: any) {
  const n = Number(v || 0)
  if (n >= 1e8) return `${(n / 1e8).toFixed(1)}亿`
  if (n >= 1e4) return `${(n / 1e4).toFixed(1)}万`
  return String(n)
}
function bytes(v: any) {
  const n = Number(v || 0)
  if (n <= 0) return '—'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let i = 0
  let x = n
  while (x >= 1024 && i < units.length - 1) { x /= 1024; i += 1 }
  return `${x.toFixed(i >= 3 ? 2 : 0)} ${units[i]}`
}
function fmtDate(v: string | null) {
  if (!v) return '—'
  const d = parseUtc(v)
  if (!d) return '—'
  return d.toLocaleDateString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' })
}
// 分类中文名（来自系统设置的类型目录）
async function loadTaxonomy() {
  try {
    const { data } = await api.get('/api/system/process-settings')
    taxonomy.value = data.taxonomy || {}
  } catch { /* 不阻塞列表 */ }
}
// 评论时间：后端为 UTC naive ISO（无 Z），补 'Z' 再按本地时区显示。
function fmtTime(v: string | null) {
  if (!v) return ''
  return parseUtc(v)?.toLocaleString('zh-CN', { hour12: false }) ?? ''
}

async function load(silent = false) {
  // silent：keep-alive 复用时的后台刷新，不显示遮罩，先展示缓存内容再默默更新。
  if (!silent) loading.value = true
  try {
    const { data, headers } = await api.get('/api/assets', {
      params: { ...filters, limit: pageSize, offset: (page.value - 1) * pageSize },
    })
    assets.value = data
    total.value = Number(headers['x-total-count'] ?? data.length)
    // A deletion can shrink the result set under the current page.
    if (page.value > totalPages.value) {
      page.value = totalPages.value
      return load(silent)
    }
  } finally {
    if (!silent) loading.value = false
  }
}

function goPage(next: number) {
  page.value = Math.min(Math.max(1, next), totalPages.value)
  load()
}

// Any filter change starts a new result set, otherwise page 4 could show an
// empty page while page 1 has matches.
function reloadFromFirstPage() {
  page.value = 1
  load()
}

async function openDetail(id: number) {
  const { data } = await api.get(`/api/assets/${id}`)
  detail.value = data
  detailTab.value = 'files'
  drawer.value = true
  await loadComments(id)
  await loadProcess(id, { resetInputs: true })
  breakdown.value = null
  bdStatus.value = ''
  bdError.value = ''
  bdRequested.value = false
  await loadBreakdown(id)
}

async function removeAsset() {
  if (!detail.value) return
  try {
    await ElMessageBox.confirm(
      '删除后素材会先从列表隐藏，worker 清理文件完成后才物理删除；确定继续？',
      '删除素材',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return // 用户取消
  }
  try {
    const { data } = await api.delete(`/api/assets/${detail.value.asset_id}`)
    ElMessage.success(`已提交删除任务 #${data.job_id}`)
    drawer.value = false
    await load()
  } catch { /* axios 已提示 */ }
}

function closeDrawer() {
  stopProcPoll()
  stopBdPoll()
}

async function loadComments(assetId: number, reset = true) {
  if (reset) {
    comments.value = []
    commentOffset.value = 0
  }
  const { data } = await api.get(`/api/assets/${assetId}/comments`, {
    params: { limit: 50, offset: commentOffset.value },
  })
  comments.value = reset ? data.comments : [...comments.value, ...data.comments]
  commentTotal.value = data.total
  commentOffset.value = comments.value.length
}

async function loadMoreComments() {
  if (detail.value) await loadComments(detail.value.asset_id, false)
}

async function act(id: number, kind: string) {
  await api.post(`/api/assets/${id}/${kind}`)
  ElMessage.success('已加入队列')
  await load()
}

async function refreshMetrics(id: number) {
  await api.post(`/api/assets/${id}/refresh-metrics`)
  ElMessage.success('已加入指标刷新队列')
  await load()
}

async function exportSel(format: string) {
  const ids = selected.value.map((a) => a.asset_id)
  const { data } = await api.post('/api/assets/export', { asset_ids: ids, format })
  const content = format === 'json' ? JSON.stringify(data, null, 2) : data.content
  const blob = new Blob([content], { type: 'text/plain' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `assets.${format}`
  a.click()
  URL.revokeObjectURL(url)
}

// 读取路由 query 里的筛选（从总览跳转过来时带 platform / account_id）。
// 抽成函数：首屏与 keep-alive 复用（onActivated）都要用；否则组件被缓存后
// 再次带 query 进入，老实例不会重新读参数，筛选会失灵。
// 用「路由签名」而非本地 filters 判断是否需要重查——页内手动改的筛选不该被当成路由变化。
let appliedQueryKey = ''
function routeQueryKey() {
  return `${route.query.platform || ''}|${route.query.account_id || ''}|${route.query.category || ''}`
}
function applyRouteQuery() {
  const qPlatform = String(route.query.platform || '')
  const qAccount = Number(route.query.account_id || 0)
  const qCategory = String(route.query.category || '')
  // Navigation without query (e.g. clicking the sidebar item) means "all
  // assets".  Only page-local filter edits are preserved; previously applied
  // route filters must be cleared or the empty list is unexplainable.
  filters.platform = qPlatform
  filters.account_id = qAccount || null
  filters.category = qCategory
  // A new route signature starts a new result set, including its total.
  page.value = 1
}

onMounted(() => {
  applyRouteQuery()
  appliedQueryKey = routeQueryKey()
  loadAccounts()
  loadTaxonomy()
  load()
})

// Changing only the hash query (e.g. overview -> /assets?platform=..., then
// sidebar -> /assets) keeps the same component instance, so neither onMounted
// nor onActivated fires. Apply route filters from the watcher instead.
watch(
  () => routeQueryKey(),
  (key) => {
    if (key === appliedQueryKey) return
    appliedQueryKey = key
    applyRouteQuery()
    load()
  },
)

let firstActivate = true
onActivated(() => {
  // onActivated 首次挂载也会触发；首屏由 onMounted 负责，跳过首次避免重复请求。
  if (firstActivate) {
    firstActivate = false
    return
  }
  // 复用缓存实例时**不重新拉取**：保留已加载内容，切页返回零加载、零跳变。
  // 只有从总览带筛选条件跳转进来（路由 query 变了）才按新条件重查。
  if (routeQueryKey() !== appliedQueryKey) {
    appliedQueryKey = routeQueryKey()
    applyRouteQuery()
    load()
  }
})

onDeactivated(() => {
  // 离开页面即停轮询、关详情抽屉，避免后台空转与"返回后仍停在旧详情"的割裂感。
  stopProcPoll()
  stopBdPoll()
  drawer.value = false
})

onUnmounted(() => {
  stopProcPoll()
  stopBdPoll()
})
</script>

<style scoped>
.ui-page { display: flex; flex-direction: column; gap: var(--sp-5); }
/* 分页控件：居中的窄条，不与筛选条抢视觉。 */
.ui-pager { display: flex; align-items: center; justify-content: center; gap: var(--sp-3); }
.ui-pager .pager-info { color: var(--muted); font-size: var(--fs-sm); }
.ui-filters { display: flex; align-items: center; gap: var(--sp-3); flex-wrap: wrap; }
.ui-filters .grow { margin-left: auto; }

/* 素材卡片网格 */
/* 列宽下限 300px：保证四个操作按钮单行放下，卡片仍够紧凑 */
.asset-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: var(--sp-3); }
.asset {
  background: var(--surface);
  border: 1px solid var(--ink);
  box-shadow: var(--shadow-1);
  display: flex;
  flex-direction: column;
  transition: transform .14s ease-out, box-shadow .14s ease-out;
}
.asset:hover { box-shadow: var(--shadow-2); }
.asset.picked { outline: 3px solid var(--accent); outline-offset: -3px; }

.asset .cover {
  position: relative;
  aspect-ratio: 16 / 9;
  overflow: hidden;
  display: grid;
  place-items: center;
  background-color: var(--surface-3);
  background-size: cover;
  background-position: center;
  border-bottom: 1px solid var(--ink);
}
/* 无封面占位：图标 + 类型，避免整块空地 */
.asset .cover .ph {
  display: flex; flex-direction: column; align-items: center; gap: 6px;
  color: var(--muted);
}
.asset .cover .ph .ph-ic { font-size: var(--fs-3xl); line-height: 1; opacity: .5; }
.asset .cover .ph .ph-t { font-size: var(--fs-sm); font-weight: 700; letter-spacing: .14em; }
.asset .cover > img {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
.asset .cover .badge-plat,
.asset .cover .badge-type,
.asset .cover .badge-dur,
.asset .cover .badge-nofile {
  position: absolute;
  font-size: var(--fs-xs);
  font-weight: 600;
  padding: 2px 7px;
  border: 1px solid var(--ink);
  background: var(--surface);
  color: var(--ink);
}
.asset .cover .badge-plat { top: 6px; left: 6px; background: none; border: 0; padding: 0; }
.asset .cover .badge-type { top: 8px; right: 8px; background: var(--accent); color: var(--accent-ink); border-color: var(--accent); }
.asset .cover .badge-dur { bottom: 8px; right: 8px; font-variant-numeric: tabular-nums; }
.asset .cover .badge-nofile { bottom: 8px; right: 8px; color: var(--muted); }
/* 封面即入口：整块可点，hover 时浮出「播放 / 阅读」提示 */
.asset .cover.clickable { cursor: pointer; }
.asset .cover.clickable > img { transition: transform .18s ease-out, filter .18s ease-out; }
.asset .cover.clickable:hover > img { transform: scale(1.04); filter: brightness(.82); }
.asset .cover .cover-cta {
  position: absolute; inset: 0;
  display: flex; align-items: center; justify-content: center; gap: 8px;
  font-size: var(--fs-lg); font-weight: 750; color: #fff;
  text-shadow: 0 1px 6px rgba(0,0,0,.6);
  opacity: 0; transition: opacity .16s ease-out;
  pointer-events: none;
}
.asset .cover.clickable:hover .cover-cta { opacity: 1; }
.asset .cover .cover-cta .cta-ic {
  display: grid; place-items: center;
  width: 48px; height: 48px; border-radius: 50%;
  border: 2px solid #fff; background: rgba(0,0,0,.28);
  font-size: 20px;
}
.asset .body { padding: 13px 15px 15px; display: flex; flex-direction: column; gap: 9px; min-width: 0; flex: 1; }
.asset .title-row { display: flex; align-items: flex-start; gap: 8px; }
.asset .type-tag {
  flex: none; margin-top: 1px;
  font-size: var(--fs-xs); font-weight: 700;
  padding: 1px 6px; border: 1px solid var(--ink);
  background: var(--surface-2); color: var(--ink-2);
}
.asset .title {
  font-weight: 650; font-size: var(--fs-md); color: var(--ink); line-height: 1.35;
  display: -webkit-box; -webkit-line-clamp: 2; line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
  min-width: 0;
}
.asset .author { display: flex; align-items: center; gap: 7px; min-width: 0; }
.asset .ava {
  width: 22px; height: 22px; flex: none;
  background-color: var(--surface-2); background-size: cover; background-position: center;
  border: 1px solid var(--ink);
  display: grid; place-items: center;
  font-size: var(--fs-xs); font-weight: 700; color: var(--ink-2);
  overflow: hidden;
}
.asset .aname {
  color: var(--ink-2); font-size: var(--fs-sm); text-decoration: none;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.asset .aname:hover { color: var(--accent); text-decoration: underline; }
.asset .cat-inline { margin-left: auto; flex: none; }

.asset .meta-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 3px 12px; }
.asset .mrow { display: flex; align-items: center; gap: 6px; font-size: var(--fs-xs); min-width: 0; }
.asset .mk { color: var(--muted); min-width: 26px; flex: none; }
.asset .mv { color: var(--ink-2); font-variant-numeric: tabular-nums; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.asset .nums-inline { display: flex; gap: 8px; }
.asset .nums-inline b { color: var(--ink); font-weight: 650; }
.ui-badge.none { border-style: dashed; color: var(--muted); background: transparent; }
.ui-badge em { font-style: normal; opacity: .75; }

.asset .nums {
  display: flex; flex-wrap: wrap; gap: 4px 10px;
  font-size: var(--fs-xs); color: var(--muted);
  border-top: 1px dashed var(--line-soft);
  padding-top: 8px;
}
.asset .nums b { color: var(--ink); font-variant-numeric: tabular-nums; }

.asset .foot { display: flex; align-items: center; gap: 8px; font-size: var(--fs-xs); color: var(--muted); margin-top: 2px; }
.asset .foot .grow { margin-left: auto; }
.asset .stagers { display: flex; gap: 5px; flex-wrap: wrap; }
.asset .stagers .ui-badge { font-size: 10px; padding: 1px 6px; }
/* 窄屏：单列 */
@media (max-width: 640px) {
  .asset-grid { grid-template-columns: 1fr; }
}
.asset .pick input { width: 16px; height: 16px; accent-color: var(--accent); cursor: pointer; }

/* 操作区贴底：卡片同排等高时，按钮始终落在卡片下沿，不留空白 */
.asset .acts {
  display: flex; flex-wrap: wrap; gap: 6px;
  margin-top: auto; padding-top: 8px;
  border-top: 1px solid var(--line-soft);
}

/* ---------- 素材详情抽屉（横向 tab 布局） ---------- */
.dt { display: flex; flex-direction: column; gap: 14px; }
.dt-head { border-bottom: 2px solid var(--ink); padding-bottom: 10px; }
.dt-title { margin: 0 0 6px; font-size: 17px; line-height: 1.4; }
.dt-sub { margin: 0; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; color: var(--muted); font-size: var(--fs-sm); }

/* 分档加工置顶块 */
.dt-block { border: 1px solid var(--ink); background: var(--surface-2); padding: 10px 12px; }
.dt-pin { box-shadow: var(--shadow-1); }
.dt-block-title { font-weight: 700; font-size: var(--fs-md); margin-right: 4px; }
.wf-steps { display: flex; flex-wrap: wrap; gap: 6px; margin: 9px 0 2px; }
.wf-step {
  display: inline-flex; align-items: center; gap: 5px; min-height: 24px;
  padding: 2px 7px; border: 1px solid var(--line-soft); background: var(--surface);
  font-size: var(--fs-xs); color: var(--ink-2);
}
.wf-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--muted); flex: none; }
.wf-step.ok .wf-dot { background: var(--ok, #2f8f5b); }
.wf-step.bad .wf-dot { background: var(--danger, #b83b3b); }
.wf-step.run .wf-dot, .wf-step.warn .wf-dot { background: var(--accent); }
.wf-step.wait { color: var(--muted); }
.wf-name { font-weight: 600; color: var(--ink); }
.wf-state { color: var(--muted); }
.wf-blocked { color: var(--muted); border-left: 1px solid var(--line-soft); padding-left: 5px; }
@media (max-width: 640px) {
  .wf-step { width: 100%; }
  .wf-blocked { margin-left: auto; }
}

/* 横向 tab 栏 */
.dt-tabs { display: flex; gap: 0; border-bottom: 2px solid var(--ink); overflow-x: auto; }
.dt-tab {
  appearance: none; background: none; border: 0; cursor: pointer;
  font-family: inherit; font-size: var(--fs-md); color: var(--muted);
  padding: 9px 14px; white-space: nowrap;
  border-bottom: 3px solid transparent; margin-bottom: -2px;
  transition: color .15s ease-out, border-color .15s ease-out;
}
.dt-tab:hover { color: var(--ink); }
.dt-tab.on { color: var(--ink); font-weight: 700; border-bottom-color: var(--accent); }
.dt-tab-n {
  margin-left: 6px; font-size: 10px; padding: 0 5px;
  border: 1px solid var(--line-soft); color: var(--ink-2);
}
.dt-pane { padding-top: 6px; min-height: 120px; }
/* 区分「分析速览」与「爆款拆解·深度」：面板顶部一句话说明，避免用户混淆两个能力 */
.vision-hint {
  border-left: 3px solid var(--accent);
  padding: 8px 10px;
  background: var(--surface-2);
}
.vision-hint a { color: var(--accent); margin-left: 6px; text-decoration: none; }
.vision-hint a:hover { text-decoration: underline; }
.dt-hint {
  margin: 6px 0 12px; padding: 9px 12px;
  background: var(--surface-2); border-left: 3px solid var(--accent);
  font-size: var(--fs-sm); color: var(--ink-2); line-height: 1.6;
}
.dt-field { display: flex; gap: 12px; padding: 8px 0; border-bottom: 1px solid var(--line-soft); }
.dt-k { flex: none; width: 72px; color: var(--muted); font-size: var(--fs-sm); }
.dt-dir {
  flex: 1; min-width: 0; font-family: var(--font-mono); font-size: var(--fs-sm);
  word-break: break-all; color: var(--ink-2);
}
.dt-v { flex: 1; min-width: 0; word-break: break-all; font-size: var(--fs-sm); color: var(--ink-2); }
.dt-actions { margin-top: 14px; }
.dt-tags { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }

/* 播放器 / 文章阅读 */
.ui-modal {
  position: fixed; inset: 0; z-index: 200;
  background: color-mix(in oklab, var(--ink) 55%, transparent);
  display: grid; place-items: center; padding: var(--sp-5);
}
.ui-modal-box {
  width: min(920px, 100%); max-height: 90vh; display: flex; flex-direction: column;
  background: var(--surface); border: 2px solid var(--ink); box-shadow: var(--shadow-2);
}
.player-wrap { background: oklch(0.12 0 0); display: grid; place-items: center; }
.player-wrap video { width: 100%; max-height: 64vh; display: block; }
.player-meta { display: flex; justify-content: space-between; gap: 12px; flex-wrap: wrap; padding: var(--sp-3) var(--sp-4); font-size: var(--fs-sm); color: var(--muted); }
.article-body { padding: var(--sp-5); overflow: auto; }
.art-meta { color: var(--muted); font-size: var(--fs-sm); margin: 0 0 14px; }
.art-html { line-height: 1.8; font-size: var(--fs-base); color: var(--ink-2); }
.art-html :deep(img) { max-width: 100%; height: auto; border: 1px solid var(--line-soft); }
.art-html :deep(p) { margin: 0 0 12px; }
.art-text { white-space: pre-wrap; font-family: var(--font-sans); line-height: 1.8; color: var(--ink-2); margin: 0; }

.filters { display: flex; gap: 8px; flex-wrap: wrap; }
.meta { color: #909399; margin: 4px 0 12px; }
.hint { color: #909399; font-size: 12px; margin: 8px 0 0; }
.comment { padding: 8px 0; border-bottom: 1px solid #f0f0f0; }
.c-head { display: flex; justify-content: space-between; }
.c-meta { color: #909399; font-size: 12px; }
.c-body { margin-top: 4px; font-size: 13px; }
.comment-total { color: #606266; font-size: 12px; margin: 0 0 4px; }
.comment-hint { color: #909399; font-size: 12px; }
.tier-row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.tier-actions { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin: 10px 0; }
.understand { background: #fafafa; border: 1px solid #f0f0f0; border-radius: 6px; padding: 10px; font-size: 13px; }
.dims { display: flex; flex-direction: column; gap: 4px; margin: 8px 0; }
.dim { display: flex; gap: 8px; }
.dim-k { color: #909399; min-width: 88px; }
.dim-v { flex: 1; }
.err { color: #f56c6c; font-size: 12px; }

/* 爆款拆解 */
.bd-actions { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.bd-report { margin-top: 12px; }
.bd-head { display: flex; gap: 16px; align-items: center; }
.bd-verdict { flex: 1; }
.bd-verdict-text { font-size: 15px; font-weight: 600; margin: 0 0 6px; }
.bd-block { margin-top: 14px; }
.bd-block h4 { margin: 0 0 6px; font-size: 13px; color: #303133; }
.bd-block p { margin: 4px 0; font-size: 13px; }
.bd-metrics { display: grid; grid-template-columns: repeat(2, 1fr); gap: 4px 16px; font-size: 13px; }
.bd-metric { display: flex; gap: 8px; }
.bd-list { margin: 4px 0; padding-left: 20px; font-size: 13px; }
.bd-list li { margin: 2px 0; }
</style>
