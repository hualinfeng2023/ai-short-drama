export const REVIEWABLE_CHARACTER_IDENTITY_STATUSES = new Set([
  'GENERATING_DOSSIER',
  'READY_FOR_REVIEW',
  'QC_REVIEW_REQUIRED',
])

const FAILED_IDENTITY_JOB_STATUSES = new Set(['FAILED', 'CANCELLED'])

export function isReviewableCharacterIdentityStatus(status: string): boolean {
  return REVIEWABLE_CHARACTER_IDENTITY_STATUSES.has(status)
}

export function identityIssueViewTypes(
  assets: Array<{ viewType: string; status: string }>,
  jobs: Array<{ viewType: string; status: string }>,
): string[] {
  const issueViewTypes = new Set<string>()
  assets.forEach((asset) => {
    if (asset.status === 'QC_FAILED') issueViewTypes.add(asset.viewType)
  })
  jobs.forEach((job) => {
    if (FAILED_IDENTITY_JOB_STATUSES.has(job.status)) issueViewTypes.add(job.viewType)
  })
  return [...issueViewTypes]
}
