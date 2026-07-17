import type { Job } from '../types'

export function elapsedJobSeconds(job: Job, nowMs: number): number {
  const startedMs = new Date(job.createdAt).getTime()
  const endedMs = job.completedAt ? new Date(job.completedAt).getTime() : nowMs
  if (!Number.isFinite(startedMs) || !Number.isFinite(endedMs)) return 0
  return Math.max(0, Math.floor((endedMs - startedMs) / 1000))
}

export function formatElapsedTime(totalSeconds: number): string {
  const seconds = Math.max(0, Math.floor(totalSeconds))
  if (seconds < 60) return `${seconds} 秒`
  const minutes = Math.floor(seconds / 60)
  const remainder = seconds % 60
  if (minutes < 60) return `${minutes} 分 ${remainder} 秒`
  const hours = Math.floor(minutes / 60)
  return `${hours} 小时 ${minutes % 60} 分`
}
