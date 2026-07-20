#!/usr/bin/env bash

set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:5173}"
OUTPUT_ROOT="${OUTPUT_ROOT:-.artifacts/visual-regression}"
PROJECT_ID="${PROJECT_ID:-11111111-1111-4111-8111-111111111111}"
EPISODE_ID="${EPISODE_ID:-22222222-2222-4222-8222-222222222222}"
SCENE_ID="${SCENE_ID:-30000000-0000-4000-8000-000000000001}"
SESSION="${VISUAL_SESSION:-design-system-regression}"

routes=(
  "projects|/projects"
  "project-new|/projects/new"
  "project-brief|/projects/${PROJECT_ID}"
  "story|/projects/${PROJECT_ID}/story"
  "characters|/projects/${PROJECT_ID}/characters"
  "preproduction|/projects/${PROJECT_ID}/preproduction"
  "storyboard|/projects/${PROJECT_ID}/storyboard"
  "production|/projects/${PROJECT_ID}/production"
  "episode|/projects/${PROJECT_ID}/episodes/${EPISODE_ID}"
  "shot-workspace|/projects/${PROJECT_ID}/episodes/${EPISODE_ID}/scenes/${SCENE_ID}"
  "preview|/projects/${PROJECT_ID}/episodes/${EPISODE_ID}/preview"
  "tasks|/tasks?project=${PROJECT_ID}"
  "reviews|/reviews"
  "settings|/settings"
)

viewports=(
  "375|900"
  "768|1024"
  "1024|900"
  "1440|1000"
)

mkdir -p "${OUTPUT_ROOT}"

for viewport in "${viewports[@]}"; do
  IFS='|' read -r width height <<< "${viewport}"
  viewport_dir="${OUTPUT_ROOT}/${width}"
  report_path="${viewport_dir}/report.jsonl"
  error_path="${viewport_dir}/page-errors.log"

  mkdir -p "${viewport_dir}"
  : > "${report_path}"
  : > "${error_path}"

  agent-browser --session "${SESSION}" open "${BASE_URL}/projects"
  agent-browser --session "${SESSION}" set viewport "${width}" "${height}"
  agent-browser --session "${SESSION}" eval "localStorage.setItem('studio-onboarding-v1', 'done'); localStorage.setItem('studio-sidebar-collapsed', '0')"
  agent-browser --session "${SESSION}" open "${BASE_URL}/projects"

  for route in "${routes[@]}"; do
    IFS='|' read -r name path <<< "${route}"
    agent-browser --session "${SESSION}" errors --clear
    agent-browser --session "${SESSION}" open "${BASE_URL}${path}"
    agent-browser --session "${SESSION}" wait --fn "!document.querySelector('.route-loading, .brief-page-state, .project-workflow--loading, .provider-settings-loading')"
    agent-browser --session "${SESSION}" wait 250
    agent-browser --session "${SESSION}" screenshot body "${viewport_dir}/${name}.png" --full
    agent-browser --session "${SESSION}" --json eval "JSON.stringify((() => {
      const root = document.documentElement;
      const overflow = [...document.querySelectorAll('body *')]
        .filter((element) => {
          const rect = element.getBoundingClientRect();
          return rect.right > window.innerWidth + 1 || rect.left < -1;
        })
        .slice(0, 12)
        .map((element) => ({
          tag: element.tagName.toLowerCase(),
          className: typeof element.className === 'string' ? element.className : '',
          left: Math.round(element.getBoundingClientRect().left),
          right: Math.round(element.getBoundingClientRect().right)
        }));
      return {
        route: '${name}',
        path: location.pathname + location.search,
        viewport: { width: window.innerWidth, height: window.innerHeight },
        documentWidth: root.scrollWidth,
        documentHeight: root.scrollHeight,
        horizontalOverflow: root.scrollWidth > window.innerWidth + 1,
        overflow
      };
    })())" >> "${report_path}"
    agent-browser --session "${SESSION}" errors >> "${error_path}"
  done
done

agent-browser --session "${SESSION}" close
echo "视觉回归完成：${OUTPUT_ROOT}"
