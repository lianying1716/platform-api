<template>
  <router-view />
</template>

<style>
html,
body,
#app {
  height: 100%;
  margin: 0;
  /* 主题令牌在 tokens.css 的 :root（V3 包豪斯）；这里只兜底，不写死颜色 */
  background: var(--bg);
  color: var(--ink);
  font-family: var(--font-sans);
}
</style>
