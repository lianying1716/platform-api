<template>
  <div class="ui-page">
    <div class="ui-head">
      <h1>系统设置</h1>
      <span class="sub">存储目录 · AI 时段 · 加工分档 · 类型提示词 · 依赖诊断</span>
    </div>

    <!-- 页签头用 V3 样式；el-tabs 只保留内容切换能力（导航条由 CSS 隐藏） -->
    <div class="ui-tabs">
      <button v-for="t in tabs" :key="t.k" class="ui-tab" :class="{ on: tab === t.k }" @click="tab = t.k">
        {{ t.label }}
      </button>
    </div>
    <el-tabs v-model="tab" class="tabs-body">
      <!-- 存储目录 -->
      <el-tab-pane label="存储目录" name="storage">
        <p class="tip">素材保存根目录，支持本机路径、NAS 挂载，Windows 盘符或 UNC（如 Z:\xsai 或 \\NAS\xsai）。</p>
        <div class="bar">
          <button class="ui-btn primary" @click="storageDialog = true">新增根目录</button>
          <button class="ui-btn" :disabled="scanningOrphans || !roots.length" @click="scanOrphans">
            {{ scanningOrphans ? '扫描中…' : '扫描孤儿文件' }}
          </button>
          <span class="grow" />
          <span class="ui-badge">共 {{ roots.length }}</span>
        </div>
        <section class="ui-panel table-panel" v-loading="loadingRoots">
          <div v-if="!loadingRoots && !roots.length" class="ui-empty">
            <div class="big">还没有配置存储根目录</div>
            <p>选一个目录作为素材落盘位置，下载与转录都会写到这里。</p>
          </div>
          <table v-else class="ui-table">
            <thead>
              <tr>
                <th style="width: 150px">名称</th>
                <th>路径</th>
                <th style="width: 80px">默认</th>
                <th style="width: 200px">可用性</th>
                <th style="width: 90px; text-align: right">操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in roots" :key="row.id">
                <td class="strong">{{ row.name }}</td>
                <td class="mono ellip" :title="row.path">{{ row.path }}</td>
                <td><span v-if="row.is_default" class="ui-badge acc">默认</span><span v-else class="muted">—</span></td>
                <td>
                  <span class="ui-badge" :class="row.available ? 'ok' : 'bad'">{{ row.available ? '可用' : '不可用' }}</span>
                  <span class="detail">{{ row.detail }}</span>
                </td>
                <td style="text-align: right">
                  <button class="ui-btn ghost sm danger" @click="delRoot(row)">删除</button>
                </td>
              </tr>
            </tbody>
          </table>
        </section>
        <section v-if="orphanReport" class="ui-panel">
          <div class="ui-panel-head">
            <h2>孤儿文件报告</h2>
            <span class="ui-badge" :class="orphanReport.orphan_count ? 'warn' : 'ok'">
              {{ orphanReport.orphan_count }} 个
            </span>
            <span class="grow" />
            <button class="ui-btn ghost sm" @click="orphanReport = null">关闭</button>
          </div>
          <div class="ui-panel-body">
            <p class="tip" style="margin-top: 0">只读报告，不会自动删除。相对路径如下：</p>
            <ul v-if="orphanReport.orphans.length" class="orphan-list">
              <li v-for="path in orphanReport.orphans" :key="path"><code>{{ path }}</code></li>
            </ul>
            <p v-else class="ui-empty" style="padding: 8px 0">没有发现孤儿文件。</p>
          </div>
        </section>
      </el-tab-pane>

      <!-- 模型接入快捷入口：模型配置已独立成一级页面，避免两套入口并存 -->
      <el-tab-pane label="模型接入" name="llm">
        <section class="ui-panel">
          <div class="ui-panel-head">
            <div>
              <h2>模型接入</h2>
              <div class="tip" style="margin: 4px 0 0">导入供应商模型、检测能力，并给文字分析、封面判类、视频理解分别配置主备模型。</div>
            </div>
            <span class="grow" />
            <router-link class="ui-btn primary" to="/models">打开模型接入</router-link>
          </div>
          <div class="ui-panel-body">
            <p class="tip" style="margin: 0">系统会自动推荐合适模型；只有你手动改过的工作分配才会被视为人工配置。</p>
          </div>
        </section>
      </el-tab-pane>

      <!-- 评论抓取 -->
      <el-tab-pane label="评论抓取" name="comments">
        <p class="tip">抖音评论富集门控与抓取参数。仅对“够热”的作品抓评论，走 worker 队列，可随时调整。</p>
        <el-form label-width="200px" style="max-width: 640px">
          <el-form-item label="热门分位 top_pct">
            <el-input-number v-model="commentSettings.top_pct" :min="0" :max="1" :step="0.05" :precision="2" />
            <span class="detail">批内按互动分取前该比例的作品（0~1）</span>
          </el-form-item>
          <el-form-item label="单批预算 budget">
            <el-input-number v-model="commentSettings.budget" :min="0" :precision="0" />
            <span class="detail">每批最多入队多少个作品</span>
          </el-form-item>
          <el-form-item label="最低评论数 min_comments">
            <el-input-number v-model="commentSettings.min_comments" :min="0" :precision="0" />
          </el-form-item>
          <el-form-item label="最低点赞数 min_likes">
            <el-input-number v-model="commentSettings.min_likes" :min="0" :precision="0" />
          </el-form-item>
          <el-form-item label="评论互动权重 weight">
            <el-input-number v-model="commentSettings.weight" :min="0" :step="1" :precision="1" />
            <span class="detail">互动分 = 点赞 + 评论数 × weight</span>
          </el-form-item>
          <el-form-item label="抓取页数 pages">
            <el-input-number v-model="commentSettings.pages" :min="1" :precision="0" />
          </el-form-item>
          <el-form-item label="每页条数 per_page">
            <el-input-number v-model="commentSettings.per_page" :min="1" :precision="0" />
          </el-form-item>
          <el-form-item label="取 Top-N top_n">
            <el-input-number v-model="commentSettings.top_n" :min="1" :precision="0" />
            <span class="detail">本地按点赞排序后保留的条数</span>
          </el-form-item>
          <el-form-item label="翻页间隔（秒）interval">
            <el-input-number v-model="commentSettings.interval" :min="0" :step="0.5" :precision="2" />
          </el-form-item>
          <el-form-item label="抓取二级评论">
            <el-switch v-model="commentSettings.with_sub_comments" />
            <span class="detail">开启后每平台会额外抓取一级评论下的回复（请求量更大，默认关闭）</span>
          </el-form-item>
          <el-form-item>
            <el-button type="primary" @click="saveCommentSettings">保存</el-button>
          </el-form-item>
        </el-form>
      </el-tab-pane>

      <!-- AI 任务时段 -->
      <el-tab-pane label="AI 时段" name="ai-schedule">
        <p class="tip">限制需要调用大模型的任务在哪些时段开始执行。窗口外的任务会留在队列里，到时段自动继续；已经开始的调用会跑完。</p>
        <section class="ui-panel">
          <div class="ui-panel-head">
            <div>
              <h2>AI 任务可运行时段</h2>
              <div class="tip" style="margin: 4px 0 0">适用于 AI 速览、视频理解（含判类）和爆款拆解；下载、转录、采集不受影响。</div>
            </div>
            <span class="grow" />
            <span class="ui-badge" :class="aiSchedule.is_open ? 'ok' : 'warn'">
              {{ aiSchedule.is_open ? '当前开放' : '当前等待' }}
            </span>
          </div>
          <div class="ui-panel-body">
            <el-form label-width="140px" style="max-width: 760px">
              <el-form-item label="启用时段限制">
                <el-switch v-model="aiSchedule.enabled" />
                <span class="detail">关闭时不限制，AI 任务随时开始</span>
              </el-form-item>
              <el-form-item label="时区">
                <el-select v-model="aiSchedule.timezone" style="width: 220px">
                  <el-option label="Asia/Shanghai（北京时间）" value="Asia/Shanghai" />
                  <el-option label="UTC" value="UTC" />
                </el-select>
              </el-form-item>
            </el-form>

            <div class="ai-window-head">
              <span class="strong">时段</span>
              <span class="grow" />
              <button class="ui-btn sm" type="button" @click="addAiWindow">新增时段</button>
            </div>
            <div v-if="!aiSchedule.windows.length" class="ui-empty ai-window-empty">
              <div class="big">还没有时段</div>
              <p>启用限制前先添加一个时段。</p>
            </div>
            <div v-else class="ai-window-list">
              <div v-for="(w, idx) in aiSchedule.windows" :key="idx" class="ai-window-row">
                <div class="ai-window-days">
                  <button
                    v-for="(d, di) in WEEKDAYS"
                    :key="d.value"
                    type="button"
                    class="ai-day"
                    :class="{ on: w.days.includes(d.value) }"
                    @click="toggleAiDay(w, d.value)"
                  >{{ d.label }}</button>
                </div>
                <span class="ai-window-sep">从</span>
                <el-time-select v-model="w.start" start="00:00" step="00:30" end="23:30" style="width: 110px" />
                <span class="ai-window-sep">到</span>
                <el-time-select v-model="w.end" start="00:00" step="00:30" end="23:30" style="width: 110px" />
                <span v-if="w.start >= w.end" class="ui-badge warn">跨午夜</span>
                <span class="grow" />
                <button class="ui-btn sm ghost danger" type="button" @click="removeAiWindow(idx)">删除</button>
              </div>
            </div>

            <p class="tip ai-window-note">
              跨午夜时，后半天仍算在开始日：例如周一 23:00–07:00 包含周二凌晨。
              <span v-if="aiSchedule.summary?.length">当前规则：{{ aiSchedule.summary.join('；') }}（{{ aiSchedule.timezone }}）。</span>
              {{ aiSchedule.next_open_at ? `下一次开放：${aiSchedule.next_open_local || fmtAiTime(aiSchedule.next_open_at)}` : '' }}
            </p>
            <el-alert
              v-if="aiSchedule.enabled && aiSchedule.windows.length === 0"
              type="warning"
              :closable="false"
              show-icon
              title="启用限制前请至少添加一个时段，否则保存会失败。"
              style="margin-bottom: 14px"
            />
            <div class="ui-bar">
              <button class="ui-btn primary" type="button" :disabled="!aiScheduleDirty" @click="saveAiSchedule">保存</button>
              <span class="detail">队列页会同步显示当前开放状态。</span>
            </div>
          </div>
        </section>
      </el-tab-pane>

      <!-- 视频理解 -->
      <el-tab-pane label="视频理解" name="process">
        <p class="tip">素材分档加工门控（普通=转录 / 高级=视频理解）与类型提示词。阈值存 system_setting，可在线调整，不写死。</p>
        <el-form label-width="210px" style="max-width: 680px">
          <el-form-item label="高级档热度阈值 min_heat">
            <el-input-number v-model="proc.min_heat" :min="0" :precision="0" />
          </el-form-item>
          <el-form-item label="最低点赞下限 min_likes_floor">
            <el-input-number v-model="proc.min_likes_floor" :min="0" :precision="0" />
          </el-form-item>
          <el-form-item label="单批预算 budget">
            <el-input-number v-model="proc.budget" :min="0" :precision="0" />
            <span class="detail">全局限额：高级档在途 job（含 queued/running）上限；单条下载路径也受约束</span>
          </el-form-item>
          <el-form-item label="热门分位 top_pct">
            <el-input-number v-model="proc.top_pct" :min="0" :max="1" :step="0.05" :precision="2" />
            <span class="detail">仅批量路径（采集/同步）生效：每批按热度取前该比例升档；单条下载路径不适用</span>
          </el-form-item>
          <el-form-item label="点赞权重 weight_like">
            <el-input-number v-model="proc.weight_like" :min="0" :step="0.1" :precision="2" />
          </el-form-item>
          <el-form-item label="评论权重 weight_comment">
            <el-input-number v-model="proc.weight_comment" :min="0" :step="0.1" :precision="2" />
          </el-form-item>
          <el-form-item label="播放权重 weight_play">
            <el-input-number v-model="proc.weight_play" :min="0" :step="0.1" :precision="2" />
          </el-form-item>
          <el-form-item label="视频内联上限 video_max_mb">
            <el-input-number v-model="proc.video_max_mb" :min="0" :step="1" :precision="2" />
          </el-form-item>
          <el-form-item label="封面内联上限 cover_max_mb">
            <el-input-number v-model="proc.cover_max_mb" :min="0" :step="1" :precision="2" />
          </el-form-item>
          <el-form-item label="低置信度阈值">
            <el-input-number v-model="proc.low_confidence_threshold" :min="0" :max="1" :step="0.05" :precision="2" />
          </el-form-item>
          <el-form-item label="开启判类 classify_enabled">
            <el-switch v-model="proc.classify_enabled" />
          </el-form-item>
          <el-form-item label="判类默认用封面 use_cover">
            <el-switch v-model="proc.use_cover" />
          </el-form-item>
          <el-form-item label="自动转录新素材">
            <el-switch v-model="proc.auto_transcribe" />
            <span class="detail">全局默认：素材下载完成后自动转录；订阅里可单独覆盖</span>
          </el-form-item>
          <el-form-item label="自动生成速览">
            <el-switch v-model="proc.auto_analyze" />
            <span class="detail">全局默认：转录后自动生成摘要 + 标签（速览）；深度爆款拆解仍需手动触发。订阅里可单独覆盖</span>
          </el-form-item>
          <el-alert type="info" :closable="false" show-icon style="margin-bottom: 14px"
            title="视频理解使用的模型已在「模型接入 → 工作分配」中设置。" />
          <el-form-item label="类型目录 taxonomy（JSON）">
            <el-input v-model="proc.taxonomyText" type="textarea" :rows="6" spellcheck="false" />
            <span class="detail">枚举 → 中文，可增删类型</span>
          </el-form-item>
          <el-form-item label="类型提示词 type_prompts（JSON）">
            <el-input v-model="proc.typePromptsText" type="textarea" :rows="10" spellcheck="false" />
            <span class="detail">类型 → {focus, dimensions:[{key,name,ask}]}，保存前本地校验</span>
          </el-form-item>
          <el-form-item>
            <el-button type="primary" @click="saveProcessSettings">保存</el-button>
          </el-form-item>
        </el-form>
      </el-tab-pane>

      <!-- 爆款拆解 -->
      <el-tab-pane label="爆款拆解" name="breakdown">
        <p class="tip">自动爆款判定「阀门」+ 命中后的自动加工链。视频下载完成后，命中任一/全部阀门的才会被自动拆解。</p>
        <el-form label-width="200px" style="max-width: 720px">
          <el-divider content-position="left">爆款阀门（都是"越大越像爆款"）</el-divider>
          <el-form-item label="相对自身中位数倍数">
            <el-input-number v-model="bd.viral_multiple" :min="0.1" :step="0.5" :precision="1" />
            <span class="detail">播放 ≥ 该账号近期播放中位数 × 倍数（默认 3；作者无数据时不参与）</span>
          </el-form-item>
          <el-form-item label="播放量门槛">
            <el-input-number v-model="bd.min_play" :min="0" :precision="0" />
            <span class="detail">0 = 不限</span>
          </el-form-item>
          <el-form-item label="点赞量门槛">
            <el-input-number v-model="bd.min_like" :min="0" :precision="0" />
            <span class="detail">0 = 不限</span>
          </el-form-item>
          <el-form-item label="互动率门槛">
            <el-input-number v-model="bd.min_engagement_rate" :min="0" :max="1" :step="0.01" :precision="2" />
            <span class="detail">(点赞+评论+收藏+分享)/播放 ≥ 该值；0 = 不限</span>
          </el-form-item>
          <el-form-item label="发布时间窗口（天）">
            <el-input-number v-model="bd.publish_within_days" :min="0" :precision="0" />
            <span class="detail">只判定最近 N 天内发布的作品；0 = 不限</span>
          </el-form-item>
          <el-form-item label="命中规则">
            <el-radio-group v-model="bd.match_rule">
              <el-radio-button value="any">任一命中</el-radio-button>
              <el-radio-button value="all">全部满足</el-radio-button>
            </el-radio-group>
          </el-form-item>
          <el-divider content-position="left">命中后自动执行</el-divider>
          <el-form-item label="自动转录">
            <el-switch v-model="bd.auto_transcribe" />
            <span class="detail">理解 / 拆解都依赖转录，命中时通常需要</span>
          </el-form-item>
          <el-form-item label="自动视频理解">
            <el-switch v-model="bd.auto_understand" />
          </el-form-item>
          <el-form-item label="自动爆款拆解">
            <el-switch v-model="bd.auto_breakdown" />
            <span class="detail">命中阀门后自动出拆解报告（消耗 LLM 额度）</span>
          </el-form-item>
          <el-divider content-position="left">拆解报告提示词</el-divider>
          <el-form-item label="拆解提示词">
            <el-input v-model="bd.prompt" type="textarea" :rows="12" />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" @click="saveBreakdownSettings">保存</el-button>
          </el-form-item>
        </el-form>
      </el-tab-pane>

      <!-- 同步频率 -->
      <el-tab-pane label="同步频率" name="sync">
        <p class="tip">同步节奏与批量。同步时间 / 每次数量都是「随机区间」，模拟人工、避开固定节奏被风控（照抄抖小云）。</p>
        <el-form label-width="220px" style="max-width: 720px">
          <el-form-item label="同步间隔下限（分钟）">
            <el-input-number v-model="sync.interval_min_minutes" :min="1" :precision="0" />
            <span class="detail">订阅未单独设置间隔时，用这个全局随机区间</span>
          </el-form-item>
          <el-form-item label="同步间隔上限（分钟）">
            <el-input-number v-model="sync.interval_max_minutes" :min="1" :precision="0" />
            <span class="detail">每次到期后在该区间内随机取一个间隔</span>
          </el-form-item>
          <el-form-item label="每次同步数量下限">
            <el-input-number v-model="sync.batch_min" :min="1" :max="30" :precision="0" />
          </el-form-item>
          <el-form-item label="每次同步数量上限">
            <el-input-number v-model="sync.batch_max" :min="1" :max="30" :precision="0" />
            <span class="detail">单次拉取/下载的条数范围，上限 30（对齐抖小云 BatchCount）</span>
          </el-form-item>
          <el-form-item label="下载间隔下限（秒）">
            <el-input-number v-model="sync.download_gap_min_seconds" :min="0" :step="0.5" :precision="1" />
          </el-form-item>
          <el-form-item label="下载间隔上限（秒）">
            <el-input-number v-model="sync.download_gap_max_seconds" :min="0" :step="0.5" :precision="1" />
            <span class="detail">相邻两次下载之间的随机等待，避开密集直链请求</span>
          </el-form-item>
          <el-form-item>
            <el-button type="primary" @click="saveSyncSettings">保存</el-button>
          </el-form-item>
        </el-form>
      </el-tab-pane>

      <!-- 诊断 -->
      <el-tab-pane label="依赖诊断" name="diag">
        <el-button @click="loadDiag">重新检测</el-button>
        <el-table :data="checks" v-loading="loadingDiag" stripe style="margin-top: 12px">
          <el-table-column label="检查项" width="160">
            <template #default="{ row }">{{ row.name }}</template>
          </el-table-column>
          <el-table-column label="状态" width="90">
            <template #default="{ row }">
              <el-tag :type="row.ok ? 'success' : (row.required === false ? 'warning' : 'danger')" size="small">
                {{ row.ok ? 'OK' : (row.required === false ? '可选' : '异常') }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="说明" min-width="280">
            <template #default="{ row }">
              <div>{{ row.detail }}</div>
              <div v-if="!row.ok && row.fix" class="hint">{{ row.fix }}</div>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <!-- 管理员 -->
      <!-- 版本与升级 -->
      <el-tab-pane label="版本与升级" name="version">
        <el-descriptions :column="2" border style="max-width: 720px">
          <el-descriptions-item label="应用版本">{{ ver.app_version || '—' }}</el-descriptions-item>
          <el-descriptions-item label="提交">{{ ver.git_commit || '—' }}</el-descriptions-item>
          <el-descriptions-item label="版本来源">
            {{ ver.version_source === 'release_image' ? '发行镜像' : '源码运行' }}
          </el-descriptions-item>
          <el-descriptions-item label="数据库版本">{{ ver.db_revision || '—' }}</el-descriptions-item>
          <el-descriptions-item label="内置迁移 head">{{ ver.db_head || '—' }}</el-descriptions-item>
          <el-descriptions-item label="数据库同步">
            <el-tag :type="ver.db_in_sync ? 'success' : 'warning'" size="small" effect="dark">
              {{ ver.db_in_sync ? '已同步' : '未同步' }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="构建时间">{{ ver.build_time || '—' }}</el-descriptions-item>
        </el-descriptions>

        <el-alert
          v-if="update.available && update.url"
          type="warning"
          :closable="false"
          style="margin-top: 12px; max-width: 720px"
          title="发现新版本"
          :description="`最新 ${update.latest}，当前 ${update.current}。查看发布说明：${update.url}`"
          show-icon
        />

        <el-alert
          v-if="update.available && !update.expected_commit"
          type="warning"
          :closable="false"
          style="margin-top: 12px; max-width: 720px"
          title="无法确认发行提交"
          description="已发现新版本，但未能从 Release 解析对应 commit；升级将只校验版本字符串。建议稍后重试检查更新。"
          show-icon
        />

        <el-alert
          v-if="update.checkedAt && update.ok === false"
          type="error"
          :closable="false"
          style="margin-top: 12px; max-width: 720px"
          title="检查更新失败"
          :description="update.message || '无法获取最新版本，请稍后重试'"
          show-icon
        />

        <div style="margin-top: 20px; max-width: 720px">
          <el-alert
            v-if="!upgradeStatus.enabled"
            type="info"
            :closable="false"
            show-icon
            title="网页一键升级未启用"
            description="管理员未配置升级边车。请按 docs/upgrade-compatibility.md 用命令行升级（先 ./scripts/backup.sh，再 ./scripts/upgrade.sh <版本>）。"
          />
          <template v-else>
            <el-alert
              type="warning"
              :closable="false"
              show-icon
              title="网页升级会自动备份，失败自动回滚"
              description="升级流程：备份 → 拉取镜像 → 重建服务 → 健康校验；任一步失败会自动回滚到当前版本。过程中服务会短暂不可用。"
            />
            <div style="margin-top: 12px">
              <el-button
                v-if="upgradeTarget"
                type="primary"
                :loading="upgrading"
                :disabled="upgradeRunning"
                @click="confirmUpgrade"
              >升级到 {{ upgradeTarget }}</el-button>
              <el-button v-else type="primary" :loading="checkingUpdate" @click="checkUpdate">检查更新</el-button>
              <el-tag
                v-if="update.latest"
                :type="update.available ? 'warning' : 'success'"
                size="small"
                style="margin-left: 12px"
              >{{ update.available ? `有新版本 ${update.latest}` : '已是最新版本' }}</el-tag>
              <el-tag
                v-if="upgradeJob?.expected_commit"
                :type="upgradeJob.commit_verified ? 'success' : 'warning'"
                size="small"
                style="margin-left: 8px"
              >{{ upgradeJob.commit_verified ? '提交已核验' : '仅核验版本' }}</el-tag>
              <span v-if="upgradeTarget" class="upgrade-hint">当前 {{ update.current || ver.app_version }}</span>
            </div>

            <!-- 升级进度 -->
            <div v-if="upgradeJob" style="margin-top: 16px">
              <el-tag
                :type="jobTagType(upgradeJob.status)"
                size="small"
                effect="dark"
              >{{ jobLabel(upgradeJob.status) }}</el-tag>
              <span style="margin-left: 8px; color: var(--el-text-color-secondary)">
                {{ upgradeJob.from_version || '当前' }} → {{ upgradeJob.version }}
              </span>
              <el-steps direction="vertical" :active="jobActive(upgradeJob)" style="margin-top: 12px">
                <el-step
                  v-for="s in upgradeJob.steps"
                  :key="s.step + s.at"
                  :title="stepLabel(s.step)"
                  :status="s.status === 'ok' ? 'success' : s.status === 'error' ? 'error' : 'process'"
                  :description="s.detail"
                />
              </el-steps>
              <el-alert
                v-if="upgradeJob.error"
                :type="upgradeJob.status === 'rolled_back' ? 'warning' : 'error'"
                :closable="false"
                show-icon
                :title="upgradeJob.error"
                style="margin-top: 12px"
              />
              <div v-if="upgradeJob.backup_path" style="margin-top: 8px; color: var(--el-text-color-secondary); font-size: 13px">
                备份：{{ upgradeJob.backup_path }}
              </div>
            </div>
          </template>
        </div>
      </el-tab-pane>

      <!-- 管理员 -->
      <el-tab-pane label="管理员" name="admin">
        <el-form label-width="120px" style="max-width: 480px">
          <el-form-item label="原密码">
            <el-input v-model="pwd.old_password" type="password" show-password />
          </el-form-item>
          <el-form-item label="新密码">
            <el-input v-model="pwd.new_password" type="password" show-password placeholder="至少 6 位" />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" @click="changePwd">修改密码</el-button>
          </el-form-item>
        </el-form>
      </el-tab-pane>
    </el-tabs>

    <!-- 删除存储根确认 -->
    <div v-if="rootTarget" class="ui-modal" @click.self="rootTarget = null">
      <div class="ui-modal-box">
        <div class="ui-panel-head"><h2>删除存储根目录</h2></div>
        <div class="ui-modal-body">
          <p>确定删除「{{ rootTarget.name }}」({{ rootTarget.path }})？仍被账号、订阅或素材引用的根目录会被拒绝删除。</p>
        </div>
        <div class="ui-modal-foot">
          <button class="ui-btn" @click="rootTarget = null">取消</button>
          <button class="ui-btn danger" @click="doDelRoot">确认删除</button>
        </div>
      </div>
    </div>

    <!-- 新增存储根弹窗 -->
    <el-dialog v-model="storageDialog" title="新增存储根目录" width="480px">
      <el-form label-width="80px">
        <el-form-item label="名称">
          <el-input v-model="rootForm.name" placeholder="例如：本机素材 / NAS" />
        </el-form-item>
        <el-form-item label="路径">
          <div class="path-row">
            <el-input v-model="rootForm.path" placeholder="/mnt/nas/xsai-content" />
            <button type="button" class="ui-btn sm" @click="openPicker">浏览…</button>
          </div>
        </el-form-item>
        <el-form-item label="设为默认">
          <el-switch v-model="rootForm.is_default" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="storageDialog = false">取消</el-button>
        <el-button type="primary" @click="createRoot">保存</el-button>
      </template>
    </el-dialog>

    <!-- 目录浏览器：读取 backend/worker 进程可见的目录树（只读白名单），点选回填路径 -->
    <el-dialog v-model="pickerDialog" title="选择目录" width="580px" destroy-on-close append-to-body>
      <div class="picker">
        <div class="picker-roots">
          <span class="picker-label">根目录</span>
          <button
            v-for="r in fsRoots"
            :key="r.path"
            type="button"
            class="ui-btn sm"
            :class="{ on: browseRoot === r.path }"
            :disabled="!r.exists"
            :title="r.path + (r.exists ? '' : '（不可用）')"
            @click="loadDir(r.path)"
          >
            {{ rootLabel(r) }}
          </button>
          <span v-if="!fsRoots.length" class="picker-label">无可用根目录</span>
        </div>
        <div class="picker-crumbs">
          <button type="button" class="ui-btn sm ghost" :disabled="!browseParent" @click="loadDir(browseParent!)">
            ↑ 上一级
          </button>
          <span class="crumbs mono">
            <template v-for="(c, i) in crumbs" :key="c.path">
              <span v-if="i && !crumbs[i - 1].path.endsWith(browseSep)" class="sep">{{ browseSep }}</span>
              <a @click="loadDir(c.path)">{{ c.label }}</a>
            </template>
          </span>
        </div>
        <div class="picker-list" v-loading="loadingDir">
          <div v-if="!loadingDir && !browseEntries.length" class="picker-empty">此目录下没有子目录</div>
          <button
            v-for="e in browseEntries"
            :key="e.path"
            type="button"
            class="picker-item"
            @click="loadDir(e.path)"
          >
            <span class="pick-name">{{ e.name }}</span>
            <span class="grow" />
            <span v-if="!e.writable" class="ui-badge warn">只读</span>
          </button>
        </div>
        <div class="picker-manual">
          <el-input v-model="browseManual" placeholder="也可直接输入路径，如 /mnt/nas/xsai-content" size="small" />
          <button type="button" class="ui-btn sm" @click="loadDir(browseManual)">前往</button>
        </div>
      </div>
      <template #footer>
        <span class="picker-picked mono" :title="browsePath">{{ browsePath || '未选择目录' }}</span>
        <span class="grow" />
        <el-button @click="pickerDialog = false">取消</el-button>
        <el-button type="primary" :disabled="!browsePath" @click="chooseDir">选用此目录</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ElMessage } from 'element-plus/es/components/message/index.mjs'
import { ElMessageBox } from 'element-plus/es/components/message-box/index.mjs'
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { apiCall } from '../api'
import { formatMonthDayTime } from '../utils/time'

const router = useRouter()
const tab = ref('storage')
// 页签清单：与下面 el-tab-pane 的 name 一一对应
const tabs = [
  { k: 'storage', label: '存储目录' },
  { k: 'llm', label: '模型接入' },
  { k: 'comments', label: '评论抓取' },
  { k: 'ai-schedule', label: 'AI 时段' },
  { k: 'process', label: '视频理解' },
  { k: 'breakdown', label: '爆款拆解' },
  { k: 'sync', label: '同步频率' },
  { k: 'diag', label: '依赖诊断' },
  { k: 'version', label: '版本与升级' },
  { k: 'admin', label: '管理员' },
]
const roots = ref<any[]>([])
const checks = ref<any[]>([])
const loadingRoots = ref(false)
const scanningOrphans = ref(false)
const orphanReport = ref<{ orphan_count: number; orphans: string[] } | null>(null)
const loadingDiag = ref(false)
// ---- 版本与升级 ----
const ver = ref<any>({ app_version: '', git_commit: '', build_time: '', db_revision: '', db_head: '', db_in_sync: false })
const update = ref<any>({ current: '', latest: '', available: false, url: '', ok: null, message: '', checkedAt: 0 })
const upgradeStatus = ref<{ enabled: boolean; current: string }>({ enabled: false, current: '' })
const checkingUpdate = ref(false)
const upgrading = ref(false)
const upgradeJob = ref<any>(null)
// 升级目标 = 检查更新发现的最新版；用户从不手填版本号。
const upgradeTarget = computed(() => (update.value.available ? update.value.latest : ''))
// 一次升级正在跑（点击后到轮询结束前），期间禁止重复触发。
const upgradeRunning = computed(() => upgrading.value || upgradeJob.value?.status === 'running')
const storageDialog = ref(false)
const rootForm = reactive({ name: '', path: '', is_default: false })

// ---- 目录浏览器（只读白名单，读取 backend/worker 进程可见的目录树）----
const pickerDialog = ref(false)
const fsRoots = ref<any[]>([])
const browseRoot = ref('')
const browsePath = ref('')
const browseParent = ref<string | null>(null)
const browseSep = ref('/')
const browseEntries = ref<any[]>([])
const browseManual = ref('')
const loadingDir = ref(false)

// 面包屑：从根到当前路径逐级切分（Windows 保留盘符前缀）。
const crumbs = computed(() => {
  const p = browsePath.value || ''
  if (!p) return []
  const sep = browseSep.value || '/'
  const out: { label: string; path: string }[] = []
  if (/^[A-Za-z]:[\\/]/.test(p)) {
    const drive = p.slice(0, 2) + sep
    out.push({ label: p.slice(0, 2), path: drive })
    let acc = drive
    for (const seg of p.slice(3).split(/[\\/]+/).filter(Boolean)) {
      acc = acc.endsWith(sep) ? acc + seg : acc + sep + seg
      out.push({ label: seg, path: acc })
    }
    return out
  }
  out.push({ label: sep, path: sep })
  let acc = ''
  for (const seg of p.split(/[\\/]+/).filter(Boolean)) {
    acc = acc + sep + seg
    out.push({ label: seg, path: acc })
  }
  return out
})

function rootLabel(r: any) {
  return r.path === '/' ? '根 /' : r.path
}

async function loadFsRoots() {
  const data = await apiCall('list_fs_roots_api_system_fs_roots_get')
  fsRoots.value = data.roots || []
}

async function loadDir(path: string) {
  loadingDir.value = true
  try {
    const data = await apiCall('list_fs_dir_api_system_fs_list_get', { path })
    browsePath.value = data.path
    browseParent.value = data.parent || null
    browseSep.value = data.sep || '/'
    browseEntries.value = data.entries || []
    browseManual.value = data.path
    // 高亮当前所属根（用于根快捷入口的激活态）
    const sep = data.sep || '/'
    const hit = (data.roots || []).find(
      (r: any) => data.path === r.path || data.path.startsWith(r.path === '/' ? '/' : r.path + sep),
    )
    if (hit) browseRoot.value = hit.path
  } catch (e: any) {
    // 400（越界/不存在）由 api 拦截器统一提示；这里保持当前目录不变
  } finally {
    loadingDir.value = false
  }
}

async function openPicker() {
  pickerDialog.value = true
  browseManual.value = rootForm.path || ''
  await loadFsRoots()
  // 优先打开当前已填路径；不在白名单（例如非本机风格的路径）时回第一个可用根。
  if (rootForm.path) await loadDir(rootForm.path)
  if (!browsePath.value) {
    const first = fsRoots.value.find((r) => r.exists) || fsRoots.value[0]
    await loadDir(first ? first.path : '')
  }
}

function chooseDir() {
  if (!browsePath.value) return
  rootForm.path = browsePath.value
  if (!rootForm.name.trim()) {
    const seg = browsePath.value.replace(/[\\/]+$/, '').split(/[\\/]/).pop() || browsePath.value
    rootForm.name = seg
  }
  pickerDialog.value = false
}

const pwd = reactive({ old_password: '', new_password: '' })
const commentSettings = reactive({
  top_pct: 0.2,
  budget: 10,
  min_comments: 20,
  min_likes: 100,
  weight: 20,
  pages: 2,
  per_page: 20,
  top_n: 20,
  interval: 1,
  with_sub_comments: false,
})

// ---- AI 任务时段 ----
const WEEKDAYS = [
  { value: 0, label: '一' },
  { value: 1, label: '二' },
  { value: 2, label: '三' },
  { value: 3, label: '四' },
  { value: 4, label: '五' },
  { value: 5, label: '六' },
  { value: 6, label: '日' },
]
const aiSchedule = reactive<any>({
  enabled: false,
  timezone: 'Asia/Shanghai',
  windows: [],
  summary: [],
  next_open_local: null,
  is_open: true,
  next_open_at: null,
})
const aiScheduleSaved = ref('')
const aiScheduleDirty = computed(() => JSON.stringify(aiScheduleSnapshot()) !== aiScheduleSaved.value)

function aiScheduleSnapshot() {
  return {
    enabled: !!aiSchedule.enabled,
    timezone: aiSchedule.timezone,
    windows: (aiSchedule.windows || []).map((w: any) => ({
      days: [...(w.days || [])].sort((a: number, b: number) => a - b),
      start: w.start,
      end: w.end,
    })),
  }
}

function addAiWindow() {
  aiSchedule.windows.push({ days: [0, 1, 2, 3, 4], start: '09:00', end: '18:00' })
}

function removeAiWindow(idx: number) {
  aiSchedule.windows.splice(idx, 1)
}

function toggleAiDay(w: any, day: number) {
  const at = w.days.indexOf(day)
  if (at >= 0) w.days.splice(at, 1)
  else w.days.push(day)
}

function fmtAiTime(value: string | null) {
  return value ? formatMonthDayTime(value, '') : ''
}

async function loadAiSchedule() {
  try {
    const data = await apiCall('get_ai_schedule_api_api_system_ai_schedule_get')
    aiSchedule.enabled = !!data.enabled
    aiSchedule.timezone = data.timezone || 'Asia/Shanghai'
    aiSchedule.windows = (data.windows || []).map((w: any) => ({ days: [...(w.days || [])], start: w.start, end: w.end }))
    aiSchedule.summary = data.summary || []
    aiSchedule.next_open_local = data.next_open_local || null
    aiSchedule.is_open = !!data.is_open
    aiSchedule.next_open_at = data.next_open_at || null
    aiScheduleSaved.value = JSON.stringify(aiScheduleSnapshot())
  } catch { /* 设置暂不可用时保留当前表单 */ }
}

async function saveAiSchedule() {
  if (aiSchedule.enabled && !aiSchedule.windows.length) {
    return ElMessage.warning('启用限制前请先添加时段')
  }
  if (aiSchedule.windows.some((w: any) => !w.days.length)) {
    return ElMessage.warning('每个时段至少要选择一天')
  }
  if (aiSchedule.windows.some((w: any) => w.start === w.end)) {
    return ElMessage.warning('时段的开始和结束不能相同')
  }
  try {
    const data = await apiCall('update_ai_schedule_api_api_system_ai_schedule_put', {}, {
      body: aiScheduleSnapshot(),
    })
    aiSchedule.is_open = !!data.schedule?.is_open
    aiSchedule.next_open_at = data.schedule?.next_open_at || null
    aiScheduleSaved.value = JSON.stringify(aiScheduleSnapshot())
    ElMessage.success('已保存')
    await loadAiSchedule()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '保存失败')
  }
}
const proc = reactive<any>({
  min_heat: 1000,
  min_likes_floor: 100,
  budget: 20,
  top_pct: 0.2,
  weight_like: 1,
  weight_comment: 2,
  weight_play: 0,
  video_max_mb: 15,
  cover_max_mb: 5,
  low_confidence_threshold: 0.6,
  classify_enabled: true,
  use_cover: false,
  auto_transcribe: false,
  auto_analyze: false,
  taxonomyText: '{}',
  typePromptsText: '{}',
})

const bd = reactive<any>({
  prompt: '', viral_multiple: 3,
  min_play: 0, min_like: 0, min_engagement_rate: 0, publish_within_days: 0,
  match_rule: 'any',
  auto_transcribe: true, auto_understand: false, auto_breakdown: false,
})

async function loadBreakdownSettings() {
  const data = await apiCall('get_breakdown_settings_api_api_system_breakdown_settings_get')
  bd.prompt = data.prompt ?? ''
  bd.viral_multiple = data.viral_multiple ?? 3
  bd.min_play = data.min_play ?? 0
  bd.min_like = data.min_like ?? 0
  bd.min_engagement_rate = data.min_engagement_rate ?? 0
  bd.publish_within_days = data.publish_within_days ?? 0
  bd.match_rule = data.match_rule ?? 'any'
  bd.auto_transcribe = data.auto_transcribe ?? true
  bd.auto_understand = data.auto_understand ?? false
  bd.auto_breakdown = data.auto_breakdown ?? false
}

async function saveBreakdownSettings() {
  if (!bd.prompt || !bd.prompt.trim()) return ElMessage.warning('提示词不能为空')
  if (!(bd.viral_multiple > 0)) return ElMessage.warning('爆款倍数必须为正数')
  try {
    await apiCall('update_breakdown_settings_api_api_system_breakdown_settings_put', {}, { body: {
      prompt: bd.prompt,
      viral_multiple: bd.viral_multiple,
      min_play: bd.min_play,
      min_like: bd.min_like,
      min_engagement_rate: bd.min_engagement_rate,
      publish_within_days: bd.publish_within_days,
      match_rule: bd.match_rule,
      auto_transcribe: bd.auto_transcribe,
      auto_understand: bd.auto_understand,
      auto_breakdown: bd.auto_breakdown,
    } })
    ElMessage.success('已保存')
    await loadBreakdownSettings()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '保存失败')
  }
}

// 同步频率（照抄抖小云：随机时间 + 每次数量范围）
const sync = reactive<any>({
  interval_min_minutes: 300, interval_max_minutes: 360,
  batch_min: 10, batch_max: 18,
  download_gap_min_seconds: 2, download_gap_max_seconds: 10,
})

async function loadSyncSettings() {
  const data = await apiCall('get_sync_settings_api_api_system_sync_settings_get')
  Object.assign(sync, data)
}

async function saveSyncSettings() {
  if (sync.interval_min_minutes > sync.interval_max_minutes) {
    return ElMessage.warning('同步间隔下限不能大于上限')
  }
  if (sync.batch_min > sync.batch_max) {
    return ElMessage.warning('每次数量下限不能大于上限')
  }
  if (sync.download_gap_min_seconds > sync.download_gap_max_seconds) {
    return ElMessage.warning('下载间隔下限不能大于上限')
  }
  try {
    await apiCall('update_sync_settings_api_api_system_sync_settings_put', {}, { body: { ...sync } })
    ElMessage.success('已保存')
    await loadSyncSettings()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '保存失败')
  }
}

async function loadRoots() {
  loadingRoots.value = true
  try {
    roots.value = await apiCall('list_storage_roots_api_system_storage_roots_get')
  } finally {
    loadingRoots.value = false
  }
}

async function scanOrphans() {
  scanningOrphans.value = true
  try {
    const data = await apiCall('storage_orphans_api_system_storage_roots_orphans_get')
    orphanReport.value = data
    ElMessage.success(data.orphan_count ? `发现 ${data.orphan_count} 个孤儿文件` : '没有发现孤儿文件')
  } finally {
    scanningOrphans.value = false
  }
}

async function createRoot() {
  if (!rootForm.name.trim() || !rootForm.path.trim()) return ElMessage.warning('名称和路径必填')
  await apiCall('create_storage_root_api_system_storage_roots_post', {}, { body: rootForm })
  storageDialog.value = false
  rootForm.name = ''
  rootForm.path = ''
  rootForm.is_default = false
  await loadRoots()
}

const rootTarget = ref<any>(null)

function delRoot(row: any) {
  rootTarget.value = row
}

async function doDelRoot() {
  if (!rootTarget.value) return
  try {
    await apiCall('delete_storage_root_api_system_storage_roots__root_id__delete', {
      root_id: rootTarget.value.id,
    })
    ElMessage.success('已删除存储根')
    rootTarget.value = null
    await loadRoots()
  } catch { /* axios 已提示引用数量/原因 */ }
}

async function loadDiag() {
  loadingDiag.value = true
  try {
    checks.value = (await apiCall('diagnostics_api_system_diagnostics_get')).checks
  } finally {
    loadingDiag.value = false
  }
}

async function loadVersion(): Promise<boolean> {
  try {
    ver.value = await apiCall('get_version_api_system_version_get')
    upgradeStatus.value = await apiCall('update_status_api_system_update_status_get')
    return true
  } catch (e) {
    /* 版本信息获取失败不阻断设置页其它功能 */
    return false
  }
}

async function checkUpdate() {
  checkingUpdate.value = true
  try {
    const data = await apiCall('check_update_api_system_update_check_get')
    if (data.ok === false) {
      // Keep the failure in the page instead of only showing a transient toast.
      // A rate-limited GitHub quota otherwise looks like the button did nothing.
      update.value = { ...data, checkedAt: Date.now() }
      ElMessage.warning(data.message || '检查更新失败')
      return
    }
    update.value = { ...data, checkedAt: Date.now() }
    if (data.available) ElMessage.success(`发现新版本 ${data.latest}`)
    else ElMessage.success('已是最新版本')
  } finally {
    checkingUpdate.value = false
  }
}

function jobLabel(status: string) {
  return (
    {
      running: '升级进行中', success: '升级成功', failed: '升级失败',
      rolled_back: '已自动回滚', partially_rolled_back: '回滚不完整',
    }[status] ||
    status
  )
}
function jobTagType(status: string) {
  return ({
    running: 'warning', success: 'success', failed: 'danger',
    rolled_back: 'warning', partially_rolled_back: 'danger',
  } as any)[status] || 'info'
}
function stepLabel(step: string) {
  return (
    {
      backup: '备份', pull: '拉取镜像', recreate: '重建服务', verify: '健康校验',
      pin: '记录版本', db_restore: '恢复数据库', data_restore: '恢复 data 卷', rollback: '回滚',
    } as any
  )[step] || step
}
function jobActive(job: any) {
  return (job?.steps || []).filter((s: any) => s.status === 'ok').length
}

let pollTimer: number | null = null
function stopPolling() {
  if (pollTimer !== null) {
    window.clearInterval(pollTimer)
    pollTimer = null
  }
}

// 轮询升级进度。主 API 升级中会短暂不可达，此时保持上次状态、继续轮询。
function startPolling() {
  stopPolling()
  pollTimer = window.setInterval(async () => {
    try {
      const data = await apiCall('upgrade_status_api_system_upgrade_status_get')
      if (data?.job) upgradeJob.value = data.job
      const status = upgradeJob.value?.status
      if (status && status !== 'running') {
        stopPolling()
        if (status === 'success') ElMessage.success('升级成功，服务已更新')
        else if (status === 'rolled_back') ElMessage.warning('升级未成功，已自动回滚到原版本')
        else if (status === 'partially_rolled_back') ElMessage.error('回滚不完整：数据库与旧版本已恢复，data 卷仍需按备份人工恢复')
        else ElMessage.error('升级失败，请查看进度详情')
        // 升级结束后刷新版本信息
        await loadVersion()
      }
    } catch {
      /* 服务重启中，忽略，等待下次轮询 */
    }
  }, 2500)
}

async function confirmUpgrade() {
  const target = upgradeTarget.value
  if (!target) return ElMessage.warning('没有可升级的新版本，请先点「检查更新」')
  try {
    await ElMessageBox.confirm(
      `将升级到 ${target}${update.value.expected_commit ? `（提交 ${update.value.expected_commit.slice(0, 12)}）` : ''}。系统会先自动备份，然后拉取镜像并重建服务，期间服务短暂不可用；若新版本启动失败或提交不一致会自动回滚。是否继续？`,
      '确认升级',
      { type: 'warning', confirmButtonText: '开始升级', cancelButtonText: '取消' },
    )
  } catch {
    return // 用户取消
  }
  upgrading.value = true
  try {
    await apiCall('trigger_upgrade_api_system_upgrade_post', {}, { body: {
      version: target,
      expected_commit: update.value.expected_commit || '',
    } })
    ElMessage.info('已开始升级，下方会显示进度')
    startPolling()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '升级失败')
  } finally {
    upgrading.value = false
  }
}

async function changePwd() {
  if (pwd.new_password.length < 6) return ElMessage.warning('新密码至少 6 位')
  try {
    await apiCall('change_password_api_auth_change_password_post', {}, { body: pwd })
    // The server invalidates every old session. Drop the local token before
    // navigating so the login page cannot mount with a dead credential.
    localStorage.removeItem('token')
    pwd.old_password = ''
    pwd.new_password = ''
    ElMessage.success('密码已修改，请重新登录')
    router.push('/login')
  } catch { /* axios 已提示 */ }
}

async function loadCommentSettings() {
  Object.assign(commentSettings, await apiCall('get_comment_settings_api_api_system_comment_settings_get'))
}

async function saveCommentSettings() {
  await apiCall('update_comment_settings_api_api_system_comment_settings_put', {}, { body: commentSettings })
  ElMessage.success('已保存')
}

async function loadProcessSettings() {
  const data = await apiCall('get_process_settings_api_api_system_process_settings_get')
  Object.assign(proc, data, {
    taxonomyText: JSON.stringify(data.taxonomy ?? {}, null, 2),
    typePromptsText: JSON.stringify(data.type_prompts ?? {}, null, 2),
  })
}

async function saveProcessSettings() {
  let taxonomy: any
  let type_prompts: any
  try {
    taxonomy = JSON.parse(proc.taxonomyText)
    type_prompts = JSON.parse(proc.typePromptsText)
  } catch (e: any) {
    return ElMessage.error('JSON 解析失败：' + e.message)
  }
  await apiCall('update_process_settings_api_api_system_process_settings_put', {}, { body: {
    min_heat: proc.min_heat,
    min_likes_floor: proc.min_likes_floor,
    budget: proc.budget,
    top_pct: proc.top_pct,
    weight_like: proc.weight_like,
    weight_comment: proc.weight_comment,
    weight_play: proc.weight_play,
    video_max_mb: proc.video_max_mb,
    cover_max_mb: proc.cover_max_mb,
    low_confidence_threshold: proc.low_confidence_threshold,
    classify_enabled: proc.classify_enabled,
    use_cover: proc.use_cover,
    auto_transcribe: proc.auto_transcribe,
    auto_analyze: proc.auto_analyze,
    taxonomy,
    type_prompts,
  } })
  ElMessage.success('已保存')
  await loadProcessSettings()
}

onMounted(async () => {
  loadRoots()
  loadDiag()
  loadCommentSettings()
  loadAiSchedule()
  loadProcessSettings()
  loadBreakdownSettings()
  loadSyncSettings()
  const versionLoaded = await loadVersion()
  // The updater lives in a sidecar and keeps the last job.  Recover it on a
  // page reload instead of making a completed upgrade look like it vanished.
  if (versionLoaded && upgradeStatus.value.enabled) startPolling()
})

onUnmounted(stopPolling)
</script>

<style scoped>
.bar { display: flex; align-items: center; gap: var(--sp-3); flex-wrap: wrap; margin-bottom: var(--sp-3); }
.bar .grow { margin-left: auto; }
.path-row { display: flex; gap: 8px; }
.path-row :deep(.el-input) { flex: 1; }
.upgrade-hint { margin-left: 12px; color: var(--muted); font-size: var(--fs-sm); }
.mono { font-family: var(--font-mono); font-size: var(--fs-sm); }
.ellip { max-width: 380px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.muted { color: var(--muted); }

/* 目录浏览器 */
.picker { display: flex; flex-direction: column; gap: 10px; }
.picker-roots { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.picker-roots .ui-btn.on { background: var(--accent); border-color: var(--accent); color: var(--accent-ink); }
.picker-label { color: var(--muted); font-size: var(--fs-sm); }
.picker-crumbs { display: flex; align-items: center; gap: 8px; min-width: 0; }
.picker-crumbs .crumbs { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--muted); }
.picker-crumbs .crumbs a { cursor: pointer; color: var(--ink-2); }
.picker-crumbs .crumbs a:hover { color: var(--accent); text-decoration: underline; }
.picker-crumbs .sep { margin: 0 2px; }
.picker-list { border: 1px solid var(--line-soft); border-radius: var(--radius); max-height: 300px; min-height: 120px; overflow-y: auto; }
.picker-item {
  width: 100%; display: flex; align-items: center; gap: 8px;
  padding: 8px 12px; background: transparent; border: 0; border-bottom: 1px solid var(--line-soft);
  font: inherit; color: var(--ink-2); text-align: left; cursor: pointer;
}
.picker-item:last-child { border-bottom: 0; }
.picker-item:hover { background: color-mix(in oklab, var(--accent) 6%, transparent); }
.picker-item .grow { margin-left: auto; }
.picker-item .pick-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.picker-empty { padding: 28px 12px; text-align: center; color: var(--muted); }
.picker-manual { display: flex; gap: 8px; }
.picker-manual :deep(.el-input) { flex: 1; }
.picker-picked { color: var(--muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 320px; }

/* 隐藏 el-tabs 自带的导航条，只保留内容切换能力 */
.tabs-body :deep(.el-tabs__header) { display: none; }

/* 删除确认弹层 */
.ui-modal {
  position: fixed; inset: 0; z-index: 200;
  background: color-mix(in oklab, var(--ink) 45%, transparent);
  display: grid; place-items: center; padding: var(--sp-5);
}
.ui-modal-box { background: var(--surface); border: 2px solid var(--ink); box-shadow: var(--shadow-2); width: min(520px, 100%); }
.ui-modal-body { padding: var(--sp-5); color: var(--ink-2); }
.ui-modal-body p { margin: 0; word-break: break-all; }
.ui-modal-foot {
  display: flex; justify-content: flex-end; gap: 8px;
  padding: var(--sp-4) var(--sp-5); border-top: 1px solid var(--line-soft);
}
.tip {
  color: var(--muted);
  font-size: var(--fs-sm);
  margin: 4px 0 12px;
  line-height: 1.6;
}
.detail {
  color: #909399;
  font-size: 12px;
  margin-left: 8px;
}

.orphan-list {
  list-style: none; margin: 0; padding: 0; max-height: 280px; overflow: auto;
  border: 1px solid var(--line-soft); background: var(--surface-2);
}
.orphan-list li { padding: 7px 10px; border-bottom: 1px solid var(--line-soft); min-width: 0; }
.orphan-list li:last-child { border-bottom: 0; }
.orphan-list code { overflow-wrap: anywhere; }

/* AI 时段 */
.ai-window-head { display: flex; align-items: center; gap: 10px; margin: 4px 0 10px; }
.ai-window-head .grow { flex: 1; }
.ai-window-list { display: flex; flex-direction: column; gap: 8px; }
.ai-window-row {
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  padding: 10px 12px; border: 1px solid var(--line-soft); background: var(--surface-2);
}
.ai-window-row .grow { flex: 1; }
.ai-window-days { display: flex; gap: 4px; }
.ai-day {
  width: 30px; height: 28px; padding: 0; cursor: pointer;
  border: 1px solid var(--line); background: var(--surface); color: var(--muted);
  font: inherit; font-size: var(--fs-sm);
}
.ai-day.on { border-color: var(--ink); background: var(--accent); color: var(--accent-ink); font-weight: 700; }
.ai-window-sep { color: var(--muted); font-size: var(--fs-sm); }
.ai-window-note { margin: 12px 0 14px; }
.ai-window-empty { padding: 24px 12px; border: 1px dashed var(--line); }

@media (max-width: 720px) {
  .ai-window-row { align-items: flex-start; }
  .ai-window-days { flex-wrap: wrap; }
}
</style>
