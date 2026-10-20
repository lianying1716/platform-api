<template>
  <div class="safe-html" v-html="safe"></div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ html: string }>()

// Keep this list in sync with the server-side allowlist. The server is the source
// of truth; this is a second line of defence for legacy rows and accidental bypass.
const ALLOWED_TAGS = new Set([
  'P', 'BR', 'STRONG', 'B', 'EM', 'I', 'U', 'S', 'UL', 'OL', 'LI',
  'BLOCKQUOTE', 'H1', 'H2', 'H3', 'H4', 'H5', 'H6', 'IMG', 'A',
  'FIGURE', 'FIGCAPTION', 'PRE', 'CODE', 'TABLE', 'THEAD', 'TBODY',
  'TR', 'TH', 'TD', 'HR', 'DIV', 'SPAN',
])
const ALLOWED_ATTRS = new Set(['href', 'src', 'alt', 'title', 'width', 'height', 'class', 'target', 'rel'])
const SAFE_SCHEMES = ['http:', 'https:']
const SAME_ORIGIN = window.location.origin

function sanitize(input: string): string {
  if (!input) return ''
  const doc = new DOMParser().parseFromString(input, 'text/html')
  const walk = (source: Node, target: Node) => {
    source.childNodes.forEach((node) => {
      if (node.nodeType === Node.TEXT_NODE) {
        target.appendChild(document.createTextNode(node.textContent || ''))
        return
      }
      if (node.nodeType !== Node.ELEMENT_NODE) return
      const element = node as Element
      if (!ALLOWED_TAGS.has(element.tagName)) return
      const clone = document.createElement(element.tagName.toLowerCase())
      Array.from(element.attributes).forEach((attr) => {
        if (!ALLOWED_ATTRS.has(attr.name)) return
        if (attr.name === 'href' || attr.name === 'src') {
          try {
            const parsed = new URL(attr.value, SAME_ORIGIN)
            if (!SAFE_SCHEMES.includes(parsed.protocol)) return
            // Images must be same-origin: the API has already replaced remote
            // images with signed proxy URLs.  This blocks legacy rows that
            // still contain a direct remote src from bypassing CSP/egress.
            if (attr.name === 'src' && parsed.origin !== SAME_ORIGIN) return
          } catch {
            return
          }
        }
        clone.setAttribute(attr.name, attr.value)
      })
      if (clone.tagName === 'A') {
        // Article links never navigate inside the admin shell.  Force a new
        // context so even a same-origin target cannot touch window.opener.
        clone.setAttribute('target', '_blank')
        clone.setAttribute('rel', 'noopener noreferrer')
      }
      walk(element, clone)
      target.appendChild(clone)
    })
  }
  const container = document.createElement('div')
  walk(doc.body, container)
  return container.innerHTML
}

const safe = computed(() => sanitize(props.html))
</script>

<style scoped>
.safe-html :deep(img) { max-width: 100%; height: auto; }
.safe-html :deep(a) { color: var(--accent); }
</style>
