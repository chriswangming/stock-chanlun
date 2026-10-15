<template>
  <div class="layout">
    <nav class="nav">
      <div class="nav-inner">
        <router-link to="/" class="nav-brand">ChanStock</router-link>
        <div class="nav-links">
          <router-link to="/" class="nav-link">首页</router-link>
          <router-link to="/screen" class="nav-link">选股</router-link>
          <router-link to="/watchlist" class="nav-link active">自选股</router-link>
        </div>
        <div class="nav-search" style="margin-left:auto">
          <input v-model="addCode" @keydown.enter="addStock" placeholder="输入代码添加自选" class="search-input" />
          <button class="btn btn-primary" @click="addStock">添加</button>
        </div>
      </div>
    </nav>

    <div class="container">
      <div class="watchlist-header">
        <h1>自选股监控</h1>
        <span class="count-badge">{{ store.stocks.length }} 支</span>
        <span v-if="store.lastUpdated" class="update-time">
          更新于 {{ formatTime(store.lastUpdated) }}
        </span>
        <button class="btn btn-ghost btn-sm" @click="store.fetchWatchlist(true)" :disabled="store.loading" style="margin-left:auto">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/>
            <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>
          </svg>
          刷新
        </button>
      </div>

      <div v-if="store.loading && store.stocks.length === 0" class="loading-state">
        <div class="spinner" />
        <span>加载中...</span>
      </div>

      <div v-else-if="store.stocks.length === 0" class="empty-state">
        <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
          <path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/>
        </svg>
        <h2>暂无自选股</h2>
        <p>添加股票到自选列表开始监控</p>
      </div>

      <div v-else class="stock-table">
        <div class="table-header">
          <span class="sort-col" :class="{ active: sortKey === 'code' }" @click="setSort('code')">
            代码 <span class="sort-icon">{{ sortIcon('code') }}</span>
          </span>
          <span class="sort-col" :class="{ active: sortKey === 'name' }" @click="setSort('name')">
            名称 <span class="sort-icon">{{ sortIcon('name') }}</span>
          </span>
          <span class="sort-col" :class="{ active: sortKey === 'price' }" @click="setSort('price')">
            现价 <span class="sort-icon">{{ sortIcon('price') }}</span>
          </span>
          <span class="sort-col" :class="{ active: sortKey === 'change_pct' }" @click="setSort('change_pct')">
            涨跌幅 <span class="sort-icon">{{ sortIcon('change_pct') }}</span>
          </span>
          <span class="sort-col" :class="{ active: sortKey === 'added_at' }" @click="setSort('added_at')">
            自选时间 <span class="sort-icon">{{ sortIcon('added_at') }}</span>
          </span>
          <span></span>
        </div>
        <div v-if="enableVirtualScroll" class="vscroll-wrap" v-bind="containerProps">
          <div class="vscroll-spacer" v-bind="wrapperProps">
            <div class="vscroll-inner" :style="{ transform: `translateY(${offsetY}px)` }">
              <div
                v-for="stock in visibleItems"
                :key="stock.code"
                class="table-row"
                :style="{ height: ROW_H + 'px' }"
                @click="goToStock(stock.code)"
                v-bind="stockLinkPrefetchHandlers(stock.code)"
              >
                <span class="mono">{{ stock.code }}</span>
                <span class="name">{{ stock.name || stock.code }}</span>
                <span class="mono">{{ stock.quote_ok === false ? '—' : (stock.price?.toFixed(2) || '—') }}</span>
                <span class="mono" :class="stock.change_pct > 0 ? 'price-up' : 'price-down'">
                  {{ stock.quote_ok === false ? '—' : (stock.change_pct > 0 ? '+' : '') + (stock.change_pct?.toFixed(2) || 0) + '%' }}
                </span>
                <span class="added-time">{{ formatAddedTime(stock.added_at) }}</span>
                <button class="remove-btn" @click.stop="removeStock(stock.code)">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M18 6L6 18M6 6l12 12"/>
                  </svg>
                </button>
              </div>
            </div>
          </div>
        </div>
        <template v-else>
          <div
            v-for="stock in sortedStocks"
            :key="stock.code"
            class="table-row"
            @click="goToStock(stock.code)"
            v-bind="stockLinkPrefetchHandlers(stock.code)"
          >
            <span class="mono">{{ stock.code }}</span>
            <span class="name">{{ stock.name || stock.code }}</span>
            <span class="mono">{{ stock.quote_ok === false ? '—' : (stock.price?.toFixed(2) || '—') }}</span>
            <span class="mono" :class="stock.change_pct > 0 ? 'price-up' : 'price-down'">
              {{ stock.quote_ok === false ? '—' : (stock.change_pct > 0 ? '+' : '') + (stock.change_pct?.toFixed(2) || 0) + '%' }}
            </span>
            <span class="added-time">{{ formatAddedTime(stock.added_at) }}</span>
            <button class="remove-btn" @click.stop="removeStock(stock.code)">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M18 6L6 18M6 6l12 12"/>
              </svg>
            </button>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import type { Quote } from '../api/stock'
import { useWatchlistStore } from '../stores/watchlist'
import toast from '../composables/useToast'
import { useVisibilityRefresh } from '../composables/useVisibilityRefresh'
import { stockLinkPrefetchHandlers } from '../utils/prefetchStock'
import { useVirtualScroll } from '../composables/useVirtualScroll'
import { sortRows } from '../utils/sortRows'

const router = useRouter()
const store = useWatchlistStore()
const addCode = ref('')
const sortKey = ref<string>('added_at')
const sortDir = ref<'asc' | 'desc'>('desc')

const AUTO_REFRESH_INTERVAL = 2 * 60 * 1000 // 2 minutes

function setSort(key: string) {
  if (sortKey.value === key) {
    sortDir.value = sortDir.value === 'asc' ? 'desc' : 'asc'
  } else {
    sortKey.value = key
    sortDir.value = 'desc'
  }
}

function sortIcon(key: string): string {
  if (sortKey.value !== key) return ''
  return sortDir.value === 'asc' ? '▲' : '▼'
}

const sortedStocks = computed(() =>
  sortRows<Quote>([...store.stocks], sortKey.value as keyof Quote, sortDir.value),
)

const ROW_H = 48
const enableVirtualScroll = computed(() => sortedStocks.value.length > 30)
const { visibleItems, containerProps, wrapperProps, offsetY } = useVirtualScroll({
  items: sortedStocks,
  itemHeight: ROW_H,
  overscan: 5,
  maxHeight: 560,
})

function formatTime(d: Date): string {
  const h = String(d.getHours()).padStart(2, '0')
  const m = String(d.getMinutes()).padStart(2, '0')
  return `${h}:${m}`
}

function formatAddedTime(iso: string | undefined): string {
  if (!iso) return '—'
  try {
    const d = new Date(iso)
    if (isNaN(d.getTime())) return '—'
    const m = String(d.getMonth() + 1).padStart(2, '0')
    const day = String(d.getDate()).padStart(2, '0')
    const h = String(d.getHours()).padStart(2, '0')
    const min = String(d.getMinutes()).padStart(2, '0')
    return `${m}-${day} ${h}:${min}`
  } catch {
    return '—'
  }
}

function goToStock(code: string) { router.push(`/stock/${code}`) }

async function addStock() {
  if (!addCode.value.trim()) return
  // 港股是 5 位代码，这里不要自己补零到 6 位——后端 normalize 会统一成 5 位。
  // 前端补成 '000700' 会让富途认不出这只股票。
  const code = addCode.value.trim().replace(/\D/g, '')
  if (code.length < 4 || code.length > 6) {
    toast.warning('请输入有效的股票代码（4-6 位数字）')
    return
  }
  try {
    await store.addStock(code)
    toast.success('已添加到自选股')
  } catch (e: any) {
    toast.error(e.message || '添加失败，请重试')
    return
  }
  addCode.value = ''
}

async function removeStock(code: string) {
  try {
    await store.removeStock(code)
  } catch {
    // 乐观更新失败，store 已回滚，只需提示
  }
  toast.info('已从自选股移除')
}

useVisibilityRefresh(async () => {
  await store.fetchWatchlist(false)
}, AUTO_REFRESH_INTERVAL)

onMounted(() => {
  store.fetchWatchlist()
})
</script>

<style scoped>
.layout { min-height: 100vh; }

.nav-search { display: flex; gap: 8px; }
.search-input {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 8px 14px;
  color: var(--text-primary);
  font-size: 0.875rem;
  width: 200px;
  outline: none;
}
.search-input:focus { border-color: var(--accent-blue); }

.container { max-width: 900px; margin: 40px auto; padding: 0 24px; }

.watchlist-header { display: flex; align-items: center; gap: 12px; margin-bottom: 32px; flex-wrap: wrap; }
.watchlist-header h1 { font-size: 1.5rem; }
.count-badge {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 20px;
  padding: 4px 12px;
  font-size: 0.8rem;
  color: var(--text-secondary);
}
.update-time {
  font-size: 0.75rem;
  color: var(--text-muted);
}

.loading-state { display: flex; align-items: center; justify-content: center; gap: 12px; padding: 80px; color: var(--text-secondary); }
.spinner { width: 24px; height: 24px; border: 2px solid var(--border); border-top-color: var(--accent-blue); border-radius: 50%; animation: spin 0.8s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }

.empty-state { text-align: center; padding: 80px; color: var(--text-muted); }
.empty-state h2 { margin: 16px 0 8px; font-size: 1.3rem; color: var(--text-secondary); }
.empty-state p { color: var(--text-muted); }

.stock-table { display: flex; flex-direction: column; gap: 2px; }

.table-header, .table-row {
  display: grid;
  grid-template-columns: 100px 1fr 100px 120px 130px 40px;
  align-items: center;
  padding: 12px 16px;
  border-radius: 8px;
}
.table-header {
  position: sticky;
  top: 0;
  z-index: 2;
  background: var(--bg-secondary);
  font-size: 0.75rem;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: var(--text-muted);
}
.vscroll-wrap {
  overflow-y: auto;
  max-height: 560px;
  scrollbar-width: thin;
}
.vscroll-spacer { position: relative; }
.vscroll-inner { width: 100%; }
.sort-col {
  display: flex;
  align-items: center;
  gap: 4px;
  cursor: pointer;
  user-select: none;
  border-radius: 4px;
  padding: 2px 4px;
  transition: color 0.15s;
}
.sort-col:hover, .sort-col.active { color: var(--accent-blue); }
.sort-icon { font-size: 0.6rem; }
.table-row {
  background: var(--bg-card);
  cursor: pointer;
  transition: background 0.15s;
}
.table-row:hover { background: var(--bg-hover); }

.remove-btn {
  background: none;
  border: none;
  color: var(--text-muted);
  cursor: pointer;
  padding: 4px;
  border-radius: 4px;
  display: flex;
  align-items: center;
}
.remove-btn:hover { color: var(--accent-red); background: rgba(248,81,73,0.1); }

.name { font-weight: 500; }

.added-time {
  font-size: 0.78rem;
  color: var(--text-muted);
}
</style>