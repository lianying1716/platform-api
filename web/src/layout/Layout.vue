<template>
  <el-container style="height: 100%">
    <el-aside width="220px" class="sidebar">
      <div class="logo">素材采集工作台</div>
      <el-menu :default-active="route.path" router background-color="#1d2129" text-color="#a9aeb8"
        active-text-color="#ffffff">
        <el-menu-item v-for="item in menu" :key="item.path" :index="item.path">
          <el-icon>
            <component :is="item.icon" />
          </el-icon>
          <span>{{ item.title }}</span>
          <el-tag v-if="item.phase" size="small" type="info" effect="dark" class="phase-tag">{{ item.phase }}</el-tag>
        </el-menu-item>
      </el-menu>
      <div class="sidebar-footer">
        <el-button link type="info" @click="logout">退出登录</el-button>
      </div>
    </el-aside>
    <el-container>
      <el-header class="topbar">
        <span class="page-title">{{ route.meta.title }}</span>
      </el-header>
      <el-main>
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup lang="ts">
import {
  Connection,
  DataAnalysis,
  Files,
  Key,
  Monitor,
  Odometer,
  Search,
  Setting,
  User,
} from '@element-plus/icons-vue'
import { useRoute, useRouter } from 'vue-router'

const route = useRoute()
const router = useRouter()

const menu = [
  { path: '/overview', title: '总览', icon: Odometer },
  { path: '/accounts', title: '账号中心', icon: User },
  { path: '/crawl', title: '关键词采集', icon: Search },
  { path: '/subscriptions', title: '订阅同步', icon: Connection },
  { path: '/assets', title: '素材库', icon: Files },
  { path: '/pipeline', title: '处理队列', icon: Monitor },
  { path: '/analytics', title: '数据看板', icon: DataAnalysis },
  { path: '/ai', title: 'AI 接入', icon: Key },
  { path: '/system', title: '系统设置', icon: Setting },
]

function logout() {
  localStorage.removeItem('token')
  router.push('/login')
}
</script>

<style scoped>
.sidebar {
  background: #1d2129;
  display: flex;
  flex-direction: column;
}

.logo {
  color: #fff;
  font-size: 16px;
  font-weight: 600;
  padding: 20px 16px;
  letter-spacing: 1px;
}

.el-menu {
  border-right: none;
  flex: 1;
}

.phase-tag {
  margin-left: auto;
}

.sidebar-footer {
  padding: 12px 16px;
}

.topbar {
  background: #fff;
  border-bottom: 1px solid #e4e7ed;
  display: flex;
  align-items: center;
}

.page-title {
  font-size: 15px;
  font-weight: 600;
}
</style>
