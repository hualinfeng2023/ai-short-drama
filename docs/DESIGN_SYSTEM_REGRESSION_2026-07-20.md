# 设计系统迁移与视觉回归记录

日期：2026-07-20

## 实施范围

- 唯一可执行令牌源：`src/design-system/tokens.css`
- 公共组件样式：`src/design-system/components.css`
- 公共 React 组件：`Surface`、`Stack`、`FormField`、`IconButton`、`Tabs`、`TabList`、`Tab`、`TabPanel`、`Toolbar`
- 代码守卫：`npm run lint:design`，并已纳入 `npm run lint`
- 可重复视觉回归：`npm run visual:regression`

## 回归矩阵

使用本地 FastAPI、SQLite 演示工作区和 Vite 前端，在页面数据、路由懒加载和项目阶段数据全部稳定后截图。

| 视口宽度 | 路由数 | 页面级横向溢出 | 页面错误 |
|---:|---:|---:|---:|
| 375 | 14 | 0 | 0 |
| 768 | 14 | 0 | 0 |
| 1024 | 14 | 0 | 0 |
| 1440 | 14 | 0 | 0 |

覆盖路由：

- 项目列表、新建项目、项目简报
- 故事与剧本、角色、前期资产、动态分镜、正式制作
- 分集、镜头工作台、完整小样
- 生成任务、审核中心、系统设置

本地证据输出在 `.artifacts/visual-regression/{375,768,1024,1440}/`，每档包含 14 张全页截图、`report.jsonl` 和 `page-errors.log`。`.artifacts` 保持在版本控制之外。

## 验证命令

```text
npm run lint
npm test
npm run build
BASE_URL=http://127.0.0.1:5174 npm run visual:regression
```
