<template>
  <section class="overlap-panel" data-testid="overlap-panel">
    <header class="overlap-head">
      <div>
        <h3>对照重叠视图</h3>
        <p class="page-desc">
          读取两组绑定的成员快照并求交集；交集成员只算一次观测，独立人数不计共用成员。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" data-testid="run-overlap" :disabled="loading" @click="runOverlap">
          {{ loading ? '计算中…' : '执行对比并生成重叠' }}
        </button>
        <button class="btn" type="button" data-testid="evaluate-overlap" :disabled="!view || loading" @click="startEvaluation">
          沿此视图发起实验评估
        </button>
      </div>
    </header>

    <p v-if="errorMessage" class="error-text" data-testid="overlap-error">{{ errorMessage }}</p>

    <div v-if="view" class="overlap-body">
      <div class="analysis-summary">
        <article class="analysis-card">
          <span class="stat-label">A 组样本</span>
          <strong class="stat-value">{{ view.cohort_a.sample_size }}</strong>
          <span class="version-text">快照 {{ shortId(view.cohort_a.snapshot_id) }}</span>
        </article>
        <article class="analysis-card">
          <span class="stat-label">B 组样本</span>
          <strong class="stat-value">{{ view.cohort_b.sample_size }}</strong>
          <span class="version-text">快照 {{ shortId(view.cohort_b.snapshot_id) }}</span>
        </article>
        <article class="analysis-card">
          <span class="stat-label">交集成员</span>
          <strong class="stat-value" data-testid="intersection-count">{{ view.intersection_count }}</strong>
          <span class="version-text">同时入组的成员</span>
        </article>
        <article class="analysis-card">
          <span class="stat-label">独立人数</span>
          <strong class="stat-value" data-testid="independent-counts">
            A {{ view.independent_a_count }} / B {{ view.independent_b_count }}
          </strong>
          <span class="version-text">去重后可观测 {{ view.distinct_observation_count }} 人</span>
        </article>
      </div>

      <table class="data-table overlap-table">
        <thead>
          <tr><th>视图项</th><th>A 组</th><th>B 组</th><th>说明</th></tr>
        </thead>
        <tbody>
          <tr>
            <td>样本人数</td>
            <td data-testid="sample-a">{{ view.cohort_a.sample_size }}</td>
            <td data-testid="sample-b">{{ view.cohort_b.sample_size }}</td>
            <td>来自各自绑定的成员快照</td>
          </tr>
          <tr>
            <td>独立人数</td>
            <td>{{ view.independent_a_count }}</td>
            <td>{{ view.independent_b_count }}</td>
            <td>不在对方样本里的成员</td>
          </tr>
          <tr>
            <td>快照版本</td>
            <td class="version-cell">{{ view.snapshot_versions.A || '—' }}</td>
            <td class="version-cell">{{ view.snapshot_versions.B || '—' }}</td>
            <td>再次执行会生成新的快照版本</td>
          </tr>
        </tbody>
      </table>

      <h4 class="section-title">交集成员（{{ view.intersection_count }} 人）</h4>
      <table class="data-table" data-testid="intersection-table">
        <thead>
          <tr><th>成员</th><th>所属分群</th></tr>
        </thead>
        <tbody>
          <tr v-for="member in view.intersection" :key="memberKey(member)">
            <td>{{ memberKey(member) }}</td>
            <td>{{ member.cohort ?? '—' }}</td>
          </tr>
          <tr v-if="!view.intersection.length">
            <td colspan="2" class="empty-state">两组没有同时入组的成员</td>
          </tr>
        </tbody>
      </table>

      <p class="result-line" data-testid="overlap-result">
        差值 {{ view.diff_percentage }}%，{{ view.anomaly_detected ? '已触发人数失衡告警' : '人数在阈值内' }}
        （分析版本 {{ shortId(view.analysis_id) }}）
      </p>

      <div class="page-actions insights-actions">
        <button class="btn" type="button" data-testid="open-insights" @click="showInsights">
          单独打开跨分群洞察
        </button>
        <label class="filter-item">
          <span>按快照版本追查旧告警</span>
          <input v-model="historySnapshotId" data-testid="history-snapshot" placeholder="B 组快照版本号" />
        </label>
        <button class="btn" type="button" data-testid="query-history" @click="loadHistory">
          查告警明细
        </button>
      </div>

      <div v-if="insights" class="insight-box" data-testid="insight-box">
        <h4 class="section-title">跨分群洞察</h4>
        <p>
          现行失衡：<strong data-testid="insight-imbalance">{{ insights.imbalance_detected ? '有' : '无' }}</strong>
          <span v-if="insights.current_anomaly" class="version-text">
            {{ insights.current_anomaly.desc }}（B 快照 {{ shortId(insights.current_anomaly.snapshot_ids?.B) }}）
          </span>
        </p>
        <p class="version-text">
          最近分析版本 {{ shortId(insights.latest_analysis_id || undefined) }}，
          历史失衡 {{ insights.historical_anomaly_count }} 条
        </p>
      </div>

      <div v-if="history" class="insight-box" data-testid="history-box">
        <h4 class="section-title">旧告警明细 · {{ history.total }} 条</h4>
        <table class="data-table">
          <thead>
            <tr><th>时间</th><th>描述</th><th>差值</th><th>A 快照</th><th>B 快照</th></tr>
          </thead>
          <tbody>
            <tr v-for="(item, index) in history.anomalies" :key="index">
              <td>{{ item.time }}</td>
              <td>{{ item.desc }}</td>
              <td>{{ item.value }}%</td>
              <td class="version-cell">{{ item.snapshot_ids?.A || '—' }}</td>
              <td class="version-cell">{{ item.snapshot_ids?.B || '—' }}</td>
            </tr>
            <tr v-if="!history.anomalies.length">
              <td colspan="5" class="empty-state">该快照版本没有失衡告警</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <p v-else class="empty-state">尚未执行对比，先点「执行对比并生成重叠」。</p>

    <div v-if="evaluation" class="evaluation-detail" data-testid="evaluation-detail">
      <h4 class="section-title">评估明细 · {{ shortId(evaluation.evaluation_id) }}</h4>
      <table class="data-table">
        <tbody>
          <tr><td>A 组样本</td><td>{{ evaluation.sample_sizes?.A ?? '评估中' }}</td></tr>
          <tr><td>B 组样本</td><td>{{ evaluation.sample_sizes?.B ?? '评估中' }}</td></tr>
          <tr><td>交集</td><td>{{ evaluation.intersection_count ?? '评估中' }}</td></tr>
          <tr><td>对照比例</td><td data-testid="control-ratio">{{ evaluation.control_ratio ?? '评估中' }}</td></tr>
          <tr><td>A 快照版本</td><td>{{ evaluation.snapshot_versions?.A || '—' }}</td></tr>
          <tr><td>B 快照版本</td><td>{{ evaluation.snapshot_versions?.B || '—' }}</td></tr>
          <tr><td>评估状态</td><td>{{ evaluation.status }}</td></tr>
        </tbody>
      </table>
      <button
        v-if="evaluation.status === '评估中'"
        class="btn"
        type="button"
        data-testid="complete-evaluation"
        @click="finishEvaluation"
      >
        完成评估
      </button>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import {
  completeOverlapEvaluation,
  executeCompare,
  loadAnomalyHistory,
  loadInsights,
  loadOverlapView,
  startOverlapEvaluation,
} from '@/api/backend-service'

type OverlapSide = {
  name: string
  snapshot_id: string | null
  sample_size: number
  independent_count: number
}

type OverlapView = {
  analysis_id: string
  diff_percentage: number
  anomaly_detected: boolean
  intersection_count: number
  independent_a_count: number
  independent_b_count: number
  distinct_observation_count: number
  snapshot_versions: Record<string, string | null>
  cohort_a: OverlapSide
  cohort_b: OverlapSide
  intersection: Record<string, unknown>[]
}

type Evaluation = {
  evaluation_id: string
  status: string
  sample_sizes?: { A: number; B: number }
  intersection_count?: number
  control_ratio?: number
  snapshot_versions?: Record<string, string | null>
}

type InsightItem = {
  desc: string
  value: number
  time?: string
  snapshot_ids?: Record<string, string>
}

type Insights = {
  imbalance_detected: boolean
  current_anomaly: InsightItem | null
  latest_analysis_id: string | null
  historical_anomaly_count: number
}

type AnomalyHistory = {
  total: number
  anomalies: InsightItem[]
}

const props = defineProps<{ taskId: number }>()
const view = ref<OverlapView | null>(null)
const evaluation = ref<Evaluation | null>(null)
const insights = ref<Insights | null>(null)
const history = ref<AnomalyHistory | null>(null)
const historySnapshotId = ref('')
const loading = ref(false)
const errorMessage = ref('')

function shortId(value?: string | null): string {
  if (!value) return '—'
  const index = value.lastIndexOf('-')
  return index >= 0 ? value.slice(index + 1) : value
}

function memberKey(member: Record<string, unknown>): string {
  return String(member.user_id ?? member['成员ID'] ?? member.id ?? '')
}

async function loadExisting() {
  errorMessage.value = ''
  try {
    view.value = (await loadOverlapView(props.taskId)) as OverlapView
  } catch {
    view.value = null
  }
}

async function runOverlap() {
  loading.value = true
  errorMessage.value = ''
  try {
    await executeCompare(props.taskId)
    view.value = (await loadOverlapView(props.taskId)) as OverlapView
    evaluation.value = null
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '执行对比失败'
  } finally {
    loading.value = false
  }
}

async function startEvaluation() {
  if (!view.value) return
  loading.value = true
  errorMessage.value = ''
  try {
    evaluation.value = (await startOverlapEvaluation(
      props.taskId,
      view.value.analysis_id,
    )) as Evaluation
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '发起评估失败'
  } finally {
    loading.value = false
  }
}

async function finishEvaluation() {
  if (!evaluation.value) return
  loading.value = true
  errorMessage.value = ''
  try {
    evaluation.value = (await completeOverlapEvaluation(
      evaluation.value.evaluation_id,
    )) as Evaluation
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '完成评估失败'
  } finally {
    loading.value = false
  }
}

async function showInsights() {
  errorMessage.value = ''
  try {
    insights.value = (await loadInsights(props.taskId)) as Insights
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '洞察读取失败'
  }
}

async function loadHistory() {
  errorMessage.value = ''
  try {
    history.value = (await loadAnomalyHistory(
      props.taskId,
      historySnapshotId.value.trim() || undefined,
    )) as AnomalyHistory
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '告警明细读取失败'
  }
}

onMounted(loadExisting)
</script>
