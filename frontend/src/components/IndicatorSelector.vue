<template>
  <div class="indicator-selector">
    <button class="selector-btn" @click="toggleOpen" :class="{ active: isOpen }">
      <span class="icon">☰</span>
      <span>指标</span>
      <span class="chevron" :class="{ rotated: isOpen }">▼</span>
    </button>

    <div v-if="isOpen" class="backdrop" @click="toggleOpen" />
    <Transition name="dropdown">
      <div v-if="isOpen" class="dropdown-panel">
        <!-- 主图指标 -->
        <div class="indicator-group">
          <div class="group-title">主图指标</div>
          <div class="indicator-list">
            <label class="indicator-item">
              <input
                type="checkbox"
                :checked="indicators.ma5"
                @change="store.toggleIndicator('ma5')"
              />
              <span class="indicator-dot" style="background: #f0b429"></span>
              <span>MA5</span>
            </label>
            <label class="indicator-item">
              <input
                type="checkbox"
                :checked="indicators.ma20"
                @change="store.toggleIndicator('ma20')"
              />
              <span class="indicator-dot" style="background: #58a6ff"></span>
              <span>MA20</span>
            </label>
            <label class="indicator-item">
              <input
                type="checkbox"
                :checked="indicators.ma60"
                @change="store.toggleIndicator('ma60')"
              />
              <span class="indicator-dot" style="background: #bc8cff"></span>
              <span>MA60</span>
            </label>
          </div>
        </div>

        <div class="indicator-group">
          <div class="group-title">缠论元素</div>
          <div class="indicator-list">
            <label class="indicator-item">
              <input
                type="checkbox"
                :checked="indicators.mergedKlines"
                @change="store.toggleIndicator('mergedKlines')"
              />
              <span class="indicator-dot" style="background: linear-gradient(90deg, #f85149, #3fb950); border: 1px solid #6e7681"></span>
              <span>合并K线</span>
            </label>
            <label class="indicator-item">
              <input
                type="checkbox"
                :checked="indicators.bis"
                @change="store.toggleIndicator('bis')"
              />
              <span class="indicator-dot" style="background: linear-gradient(90deg, #f85149, #3fb950)"></span>
              <span>笔</span>
            </label>
            <label class="indicator-item">
              <input
                type="checkbox"
                :checked="indicators.xians"
                @change="store.toggleIndicator('xians')"
              />
              <span class="indicator-dot" style="background: linear-gradient(90deg, #ffe066, #ff9f7f)"></span>
              <span>线段</span>
            </label>
            <label class="indicator-item">
              <input
                type="checkbox"
                :checked="indicators.zhongshus"
                @change="store.toggleIndicator('zhongshus')"
              />
              <span class="indicator-dot" style="background: #bc8cff; opacity: 0.7"></span>
              <span>中枢</span>
            </label>
            <label class="indicator-item">
              <input
                type="checkbox"
                :checked="indicators.signals"
                @change="store.toggleIndicator('signals')"
              />
              <span class="indicator-dot" style="background: #3fb950"></span>
              <span>买卖点</span>
            </label>
            <label class="indicator-item">
              <input
                type="checkbox"
                :checked="indicators.aiLines"
                @change="store.toggleIndicator('aiLines')"
              />
              <span class="indicator-dot" style="background: #d29922"></span>
              <span>AI 信号线</span>
            </label>
            <label class="indicator-item">
              <input
                type="checkbox"
                :checked="indicators.supportResistance"
                @change="store.toggleIndicator('supportResistance')"
              />
              <span class="indicator-dot" style="background: linear-gradient(90deg, #3fb950, #f85149)"></span>
              <span>支撑阻力</span>
            </label>
          </div>
        </div>

        <div class="indicator-group">
          <div class="group-title">副图指标</div>
          <div class="indicator-list">
            <label class="indicator-item">
              <input
                type="checkbox"
                :checked="indicators.volume"
                @change="store.toggleIndicator('volume')"
              />
              <span class="indicator-dot" style="background: #7d8590"></span>
              <span>成交量</span>
            </label>
            <label class="indicator-item">
              <input
                type="checkbox"
                :checked="indicators.macd"
                @change="store.toggleIndicator('macd')"
              />
              <span class="indicator-dot" style="background: #58a6ff"></span>
              <span>MACD</span>
            </label>
            <label class="indicator-item">
              <input
                type="checkbox"
                :checked="indicators.rsi"
                @change="store.toggleIndicator('rsi')"
              />
              <span class="indicator-dot" style="background: #f0b429"></span>
              <span>RSI</span>
            </label>
            <label class="indicator-item">
              <input
                type="checkbox"
                :checked="indicators.skdj"
                @change="store.toggleIndicator('skdj')"
              />
              <span class="indicator-dot" style="background: #bc8cff"></span>
              <span>SKDJ</span>
            </label>
          </div>
        </div>

        <div class="quick-actions">
          <button class="quick-btn" @click="showAll">显示全部</button>
          <button class="quick-btn" @click="hideAll">隐藏全部</button>
        </div>
      </div>
    </Transition>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { useChanlunStore, type IndicatorConfig, defaultIndicators } from '../stores/chanlun'

const store = useChanlunStore()
const isOpen = ref(false)

const indicators = computed(() => store.indicators)

function toggleOpen() {
  isOpen.value = !isOpen.value
}

function showAll() {
  const cfg = store.indicators
  for (const key in cfg) {
    store.setIndicator(key as keyof IndicatorConfig, true)
  }
}

function hideAll() {
  const cfg = store.indicators
  for (const key in cfg) {
    store.setIndicator(key as keyof IndicatorConfig, false)
  }
}
</script>

<style scoped>
.indicator-selector {
  position: relative;
  display: inline-block;
}

.selector-btn {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 14px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.04);
  color: var(--text-secondary);
  font-size: 0.8125rem;
  font-weight: 600;
  cursor: pointer;
  transition: border-color 0.2s ease, color 0.2s ease, box-shadow 0.2s ease, background 0.2s ease;
}

.selector-btn:hover,
.selector-btn.active {
  border-color: rgba(56, 189, 248, 0.45);
  color: var(--text-primary);
  background: rgba(56, 189, 248, 0.08);
  box-shadow: 0 0 0 1px rgba(56, 189, 248, 0.12);
}

.selector-btn .icon {
  font-size: 0.9rem;
}

.selector-btn .chevron {
  font-size: 0.6rem;
  transition: transform 0.2s;
}

.selector-btn .chevron.rotated {
  transform: rotate(180deg);
}

.dropdown-panel {
  position: absolute;
  top: calc(100% + 10px);
  left: 0;
  z-index: 1000;
  min-width: 220px;
  background: linear-gradient(165deg, rgba(26, 34, 45, 0.98) 0%, rgba(17, 24, 32, 0.98) 100%);
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 14px;
  padding: 14px;
  box-shadow:
    0 24px 48px rgba(0, 0, 0, 0.55),
    0 0 0 1px rgba(52, 211, 153, 0.06);
}

.backdrop {
  position: fixed;
  inset: 0;
  z-index: 999;
  background: rgba(0, 0, 0, 0.35);
  backdrop-filter: blur(2px);
}

.indicator-group {
  margin-bottom: 12px;
}

.indicator-group:last-of-type {
  margin-bottom: 8px;
}

.group-title {
  font-size: 0.75rem;
  color: var(--text-muted);
  margin-bottom: 8px;
  padding-left: 4px;
}

.indicator-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.indicator-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 8px;
  border-radius: 6px;
  cursor: pointer;
  transition: background 0.15s;
}

.indicator-item:hover {
  background: var(--bg-hover);
}

.indicator-item input[type="checkbox"] {
  width: 14px;
  height: 14px;
  accent-color: var(--accent-blue);
  cursor: pointer;
}

.indicator-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  flex-shrink: 0;
}

.indicator-item span:last-child {
  font-size: 0.85rem;
  color: var(--text-primary);
}

.quick-actions {
  display: flex;
  gap: 8px;
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid var(--border);
}

.quick-btn {
  flex: 1;
  padding: 6px 12px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: transparent;
  color: var(--text-secondary);
  font-size: 0.8rem;
  cursor: pointer;
  transition: all 0.15s;
}

.quick-btn:hover {
  border-color: var(--accent-blue);
  color: var(--text-primary);
}

/* 过渡动画 */
.dropdown-enter-active,
.dropdown-leave-active {
  transition: opacity 0.2s, transform 0.2s;
}

.dropdown-enter-from,
.dropdown-leave-to {
  opacity: 0;
  transform: translateY(-8px);
}
</style>
