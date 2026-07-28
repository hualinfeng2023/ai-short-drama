import { describe, expect, it } from 'vitest'
import type {
  ShotValidationIssue,
  StructuredShotSpec,
  StoryboardWorkspace,
} from '../api/client'
import {
  getShotFieldReviewStatus,
  getShotIssuePresentation,
  getStoryboardReviewSummary,
} from './storyboardReview'

function makeSpec(): StructuredShotSpec {
  return {
    schema_version: 'shot-spec-v1',
    duration_sec: 2,
    narrative_goal: '建立胚胎即将苏醒的悬念',
    visual_content: {
      action: '凝水滑落，冰霜裂开，镜头拉焦露出培养床',
      composition: '',
      description: '培养舱内部',
      environment: '',
      subjects: [],
      visible_props: [],
    },
    start_state: {},
    end_state: { action_state: '' },
    camera: {
      angle: '',
      axis_id: '',
      axis_side: '',
      focus: '',
      framing: '',
      lens_mm: 50,
      movement: '固定镜头',
      shot_size: '特写',
    },
    lighting: {
      atmosphere: '',
      color_temperature: '',
      contrast: '',
      fill_light: '',
      key_light: '',
      style: '',
    },
    art_direction: {
      palette: [],
      production_design: '',
      references: [],
      texture: '',
      visual_style: '',
      wardrobe: '',
    },
    technique: {
      notes: '',
      pacing: '',
      practical_effects: [],
      transition_in: '',
      transition_out: '',
      vfx: [],
    },
    performance: {},
    audio: {
      ambience: [],
      dialogue: '',
      music: '',
      sfx: [],
      sync_notes: '',
      voice_over: '',
    },
    continuity: {},
    generation: {
      adapter: 'generic',
      aspect_ratio: '9:16',
      fps: 24,
      model: '',
      model_parameters: {},
      negative_prompt: '',
      reference_asset_ids: [],
      resolution: '',
      risk_flags: [],
    },
    source: {},
  }
}

function makeIssue(
  severity: ShotValidationIssue['severity'],
  fieldPath: string,
): ShotValidationIssue {
  return {
    code: `${severity}:${fieldPath}`,
    details: {},
    field_path: fieldPath,
    message: '技术校验信息',
    repairable: true,
    severity,
  }
}

function makeShot(issues: ShotValidationIssue[]) {
  return {
    validationReport: { issues },
  } as StoryboardWorkspace['shots'][number]
}

describe('storyboardReview', () => {
  it('prioritizes blockers over warnings when deriving the review phase', () => {
    const summary = getStoryboardReviewSummary([
      makeShot([makeIssue('BLOCKER', 'shots.0.visual_content.action')]),
      makeShot([makeIssue('WARNING', 'shots.1.end_state.action_state')]),
      makeShot([]),
    ], false)

    expect(summary).toEqual({
      affectedShotCount: 2,
      blockerCount: 1,
      phase: 'BLOCKED',
      warningCount: 1,
    })
  })

  it('returns the approved phase even when historical issues remain', () => {
    const summary = getStoryboardReviewSummary([
      makeShot([makeIssue('WARNING', 'shots.0.end_state.action_state')]),
    ], true)

    expect(summary.phase).toBe('APPROVED')
  })

  it('translates action density diagnostics into director-facing guidance', () => {
    const presentation = getShotIssuePresentation(
      makeIssue('BLOCKER', 'shots.0.visual_content.action'),
      makeSpec(),
    )

    expect(presentation.fieldLabel).toBe('画面动作')
    expect(presentation.guidance).toContain('2 秒')
    expect(presentation.guidance).toContain('保留一个主要动作')
    expect(presentation.guidance).not.toContain('冰霜裂开')
  })

  it('explains missing end state without exposing the field path', () => {
    const presentation = getShotIssuePresentation(
      makeIssue('WARNING', 'shots.0.end_state.action_state'),
      makeSpec(),
    )

    expect(presentation.fieldLabel).toBe('结尾状态')
    expect(presentation.guidance).toContain('动作结束后')
    expect(presentation.guidance).not.toContain('shots.0')
  })

  it('derives a field-level status without being affected by other fields', () => {
    expect(getShotFieldReviewStatus([
      makeIssue('WARNING', 'shots.0.end_state.action_state'),
    ], 'visual_content.action')).toMatchObject({
      label: '已通过',
      status: 'QC_PASSED',
    })

    expect(getShotFieldReviewStatus([
      makeIssue('BLOCKER', 'shots.0.visual_content.action'),
      makeIssue('WARNING', 'shots.0.visual_content.action'),
    ], 'visual_content.action')).toMatchObject({
      label: '需修复',
      status: 'BLOCKED',
    })

    expect(getShotFieldReviewStatus([
      makeIssue('WARNING', 'shots.0.visual_content.action'),
    ], 'visual_content.action')).toMatchObject({
      label: '建议修改',
      status: 'QC_REVIEW_REQUIRED',
    })
  })

  it('marks a changed field as requiring fresh validation', () => {
    expect(getShotFieldReviewStatus(
      [makeIssue('BLOCKER', 'shots.0.visual_content.action')],
      'visual_content.action',
      true,
    )).toMatchObject({
      label: '待重新检查',
      status: 'DRAFT',
    })
  })

  it('uses the field label in status guidance', () => {
    expect(getShotFieldReviewStatus(
      [makeIssue('WARNING', 'shots.0.end_state.action_state')],
      'end_state.action_state',
      false,
      '结尾状态',
    ).description).toContain('结尾状态可以继续审核')
  })
})
