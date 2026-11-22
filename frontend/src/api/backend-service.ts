import { request } from './client'
import { moduleMeta } from '@/data/modules'
import type { ActionResult, EntryRow, OverviewResult, PageResult } from '@/data/types'

export { moduleMeta }

export async function listEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<PageResult> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}?${query}`)
  if (!response.ok) throw new Error('业务数据读取失败')
  return await response.json()
}

export async function createEntry(
  key: string,
  values: Record<string, string>,
): Promise<ActionResult> {
  const response = await request(`/api/${key}`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  return await response.json()
}

export async function runAction(
  key: string,
  id: number,
  action: string,
): Promise<ActionResult> {
  const response = await request(`/api/${key}/${id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ action }),
  })
  return await response.json()
}

export async function downloadEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<void> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}/export?${query}`)
  if (!response.ok) throw new Error('业务数据导出失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${key}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

export async function loadOverview(): Promise<OverviewResult> {
  const response = await request('/api/overview')
  if (!response.ok) throw new Error('运营概览读取失败')
  return await response.json()
}

// ── 分群对比 / 对照重叠视图 ──

export async function executeCompare(taskId: number): Promise<unknown> {
  const response = await request(`/api/cross/segment-compare/${taskId}/execute`, { method: 'POST' })
  const payload = await response.json()
  if (!response.ok || (payload as { error?: string }).error) {
    throw new Error((payload as { error?: string }).error ?? '执行对比失败')
  }
  return payload
}

export async function loadOverlapView(
  taskId: number,
  analysisId?: string,
): Promise<unknown> {
  const query = analysisId ? `?analysis_id=${encodeURIComponent(analysisId)}` : ''
  const response = await request(`/api/cross/segment-compare/${taskId}/overlap${query}`)
  const payload = await response.json()
  if (!response.ok || (payload as { error?: string }).error) {
    throw new Error((payload as { error?: string }).error ?? '对照重叠视图读取失败')
  }
  return payload
}

export async function loadInsights(taskId: number, analysisId?: string): Promise<unknown> {
  const query = analysisId ? `?analysis_id=${encodeURIComponent(analysisId)}` : ''
  const response = await request(`/api/cross/segment-compare/${taskId}/insights${query}`)
  return await response.json()
}

export async function loadAnomalyHistory(
  taskId: number,
  snapshotId?: string,
): Promise<unknown> {
  const query = snapshotId ? `?snapshot_id=${encodeURIComponent(snapshotId)}` : ''
  const response = await request(`/api/cross/segment-compare/${taskId}/anomalies${query}`)
  return await response.json()
}

// ── 实验分群评估 ──

export async function startOverlapEvaluation(
  taskId: number,
  analysisId?: string,
): Promise<unknown> {
  const query = analysisId ? `?analysis_id=${encodeURIComponent(analysisId)}` : ''
  const response = await request(
    `/api/cross/experiment-group/evaluate-overlap/${taskId}/start${query}`,
    { method: 'POST' },
  )
  return await response.json()
}

export async function completeOverlapEvaluation(evaluationId: string): Promise<unknown> {
  const response = await request(
    `/api/cross/experiment-group/evaluations/${encodeURIComponent(evaluationId)}/complete`,
    { method: 'POST' },
  )
  return await response.json()
}

export async function evaluateOverlap(taskId: number, analysisId?: string): Promise<unknown> {
  const query = analysisId ? `?analysis_id=${encodeURIComponent(analysisId)}` : ''
  const response = await request(
    `/api/cross/experiment-group/evaluate-overlap/${taskId}${query}`,
    { method: 'POST' },
  )
  return await response.json()
}

export async function listEvaluations(taskId?: number): Promise<unknown> {
  const query = taskId ? `?task_id=${taskId}` : ''
  const response = await request(`/api/cross/experiment-group/evaluations${query}`)
  return await response.json()
}

// ── 用户分群快照 ──

export async function recomputeCohortWithMembers(
  cohortId: number,
  memberIds: string[],
): Promise<unknown> {
  const response = await request(`/api/cross/user-cohort/${cohortId}/recompute`, {
    method: 'POST',
    body: JSON.stringify({ member_ids: memberIds, group_size: memberIds.length }),
  })
  const payload = await response.json()
  if (!response.ok || (payload as { error?: string }).error) {
    throw new Error((payload as { error?: string }).error ?? '快照发布失败')
  }
  return payload
}

export async function loadCohortDetail(cohortId: number): Promise<unknown> {
  const response = await request(`/api/cross/user-cohort/${cohortId}/detail`)
  return await response.json()
}
