import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { DirectorFailureInspector } from './DirectorFailureInspector'

describe('DirectorFailureInspector', () => {
  it('shows audit evidence and a non-destructive retry action', () => {
    const markup = renderToStaticMarkup(
      <DirectorFailureInspector
        busy={false}
        failure={{
          generationRecordId: '11111111-1111-4111-8111-111111111111',
          scriptSceneId: '22222222-2222-4222-8222-222222222222',
          status: 'FAILED',
          provider: 'volcengine-ark',
          model: 'director-model',
          latencyMs: 812,
          attemptCount: 3,
          repairAttempts: 2,
          failureStage: 'PROVIDER_VALIDATION',
          errorCode: 'ARK_TEXT_SCHEMA_INVALID',
          errorMessage: '连续三次未返回有效结构',
          retryable: true,
          createdAt: '2026-07-27T00:00:00Z',
        }}
        onRetry={() => undefined}
      />,
    )

    expect(markup).toContain('上次审查未生成 Proposal')
    expect(markup).toContain('ARK_TEXT_SCHEMA_INVALID')
    expect(markup).toContain('校验尝试')
    expect(markup).toContain('3 次，其中修复 2 次')
    expect(markup).toContain('不覆盖本次失败记录')
    expect(markup).toContain('重新审查')
  })
})
