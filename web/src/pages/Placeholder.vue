<template>
  <el-empty :description="`「${route.meta.title}」将在 ${route.meta.phase} 阶段上线`">
    <template #image>
      <el-icon size="56" color="#c0c4cc">
        <Clock />
      </el-icon>
    </template>
  </el-empty>
</template>

<script setup lang="ts">
import { Clock } from '@element-plus/icons-vue'
import { useRoute } from 'vue-router'

const route = useRoute()
</script>
