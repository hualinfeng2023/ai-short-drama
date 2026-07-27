import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { DirectorGenerationHistory } from './DirectorGenerationHistory'

describe('DirectorGenerationHistory', () => {
  it('shows successful, failed, and retry lineage records together', () => {
    const markup = renderToStaticMarkup(
      <DirectorGenerationHistory
        records={[
          {
            generationRecordId: '33333333-3333-4333-8333-333333333333',
            scriptSceneId: '22222222-2222-4222-8222-222222222222',
            status: 'SUCCEEDED',
            provider: 'volcengine-ark',
            model: 'director-model',
            attemptCount: 2,
            repairAttempts: 1,
            retryable: false,
            retryOfGenerationRecordId: '11111111-1111-4111-8111-111111111111',
            proposalStatus: 'PROPOSED',
            createdAt: '2026-07-27T00:05:00Z',
          },
          {
            generationRecordId: '11111111-1111-4111-8111-111111111111',
            scriptSceneId: '22222222-2222-4222-8222-222222222222',
            status: 'FAILED',
            provider: 'volcengine-ark',
            model: 'director-model',
            attemptCount: 3,
            repairAttempts: 2,
            errorCode: 'ARK_TEXT_SCHEMA_INVALID',
            retryable: true,
            createdAt: '2026-07-27T00:00:00Z',
          },
        ]}
      />,
    )

    expect(markup).toContain('审查历史')
    expect(markup).toContain('已生成审查方案')
    expect(markup).toContain('等待选择方案')
    expect(markup).toContain('审查失败')
    expect(markup).toContain('ARK_TEXT_SCHEMA_INVALID')
    expect(markup).toContain('重试自 11111111')
  })
})
