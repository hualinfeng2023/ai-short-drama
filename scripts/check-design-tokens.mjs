import { readdir, readFile } from 'node:fs/promises'
import { extname, relative, resolve } from 'node:path'

const sourceRoot = resolve(process.cwd(), 'src')
const tokenSource = resolve(sourceRoot, 'design-system/tokens.css')
const supportedExtensions = new Set(['.css', '.ts', '.tsx'])
const failures = []

async function collectFiles(directory) {
  const entries = await readdir(directory, { withFileTypes: true })
  const nested = await Promise.all(entries.map(async (entry) => {
    const path = resolve(directory, entry.name)
    if (entry.isDirectory()) return collectFiles(path)
    return supportedExtensions.has(extname(entry.name)) ? [path] : []
  }))
  return nested.flat()
}

function withoutComments(source) {
  return source.replace(/\/\*[\s\S]*?\*\//g, (comment) => comment.replace(/[^\n]/g, ' '))
}

function lineNumber(source, index) {
  return source.slice(0, index).split('\n').length
}

function report(file, source, index, message) {
  failures.push(`${relative(process.cwd(), file)}:${lineNumber(source, index)} ${message}`)
}

function checkCss(file, originalSource) {
  if (file === tokenSource) return

  const source = withoutComments(originalSource)
  const declarationPattern = /(^|[;{]\s*)(--?[-a-zA-Z0-9]+)\s*:\s*([^;{}]+)(?=;|})/gm
  let match

  while ((match = declarationPattern.exec(source)) !== null) {
    const property = match[2].toLowerCase()
    const value = match[3].trim()
    const declarationIndex = match.index + match[1].length

    if (/^--(?:color|font|space|radius|shadow|filter|motion|control|layout)-/.test(property)) {
      report(file, source, declarationIndex, `设计令牌 ${property} 只能在 src/design-system/tokens.css 中定义`)
    }

    if (
      /#[0-9a-f]{3,8}\b/i.test(value)
      || /\b(?:rgb|rgba|hsl|hsla|oklch)\s*\(/i.test(value)
      || /(?<![-\w])(?:white|black)(?![-\w])/i.test(value)
    ) {
      report(file, source, declarationIndex, `${property} 使用了任意颜色；请改用 --color-* 令牌`)
    }

    if (property === 'font-size' && !/^var\(--font-size-/.test(value)) {
      report(file, source, declarationIndex, 'font-size 必须使用 --font-size-* 令牌')
    }

    if (property === 'font' && value !== 'inherit' && !/var\(--font-size-/.test(value)) {
      report(file, source, declarationIndex, 'font 简写必须使用 --font-size-* 令牌')
    }

    if (
      /^(?:margin|padding|gap|row-gap|column-gap)(?:-[a-z]+)?$/.test(property)
      && /(?:^|[\s(,+*/-])\d*\.?\d+(?:px|rem|em)\b/i.test(value)
    ) {
      report(file, source, declarationIndex, `${property} 使用了非标准间距；请改用 --space-* 或 --layout-* 令牌`)
    }

    if (
      property === 'border-radius'
      && /(?:\d*\.?\d+(?:px|rem|em|%))\b/i.test(value)
    ) {
      report(file, source, declarationIndex, 'border-radius 必须使用 --radius-* 令牌')
    }

    if (
      /^(?:box-shadow|text-shadow)$/.test(property)
      && value !== 'none'
      && !/var\(--shadow-/.test(value)
    ) {
      report(file, source, declarationIndex, `${property} 必须使用 --shadow-* 令牌`)
    }

    if (
      property === 'filter'
      && /drop-shadow\(/.test(value)
      && !/var\(--filter-/.test(value)
    ) {
      report(file, source, declarationIndex, 'drop-shadow 必须使用 --filter-* 令牌')
    }

    if (
      /^(?:transition|transition-duration|animation|animation-duration)$/.test(property)
      && /(?:^|[\s,])\d*\.?\d+(?:ms|s)\b|cubic-bezier\(/i.test(value)
    ) {
      report(file, source, declarationIndex, `${property} 必须使用 --motion-* 令牌`)
    }
  }
}

function checkTsx(file, source) {
  if (extname(file) !== '.tsx') return

  const inlineLiteralPattern = /\b(fontSize|color|gap|padding|paddingTop|paddingRight|paddingBottom|paddingLeft|margin|marginTop|marginRight|marginBottom|marginLeft|borderRadius|boxShadow)\s*:\s*(?:["']|[-+]?\d+(?:\.\d+)?\s*[,}])/g
  let match

  while ((match = inlineLiteralPattern.exec(source)) !== null) {
    report(file, source, match.index, `内联 ${match[1]} 禁止使用任意值；请改用设计系统类名和令牌`)
  }
}

const files = await collectFiles(sourceRoot)
for (const file of files) {
  const source = await readFile(file, 'utf8')
  if (extname(file) === '.css') checkCss(file, source)
  checkTsx(file, source)
}

if (failures.length) {
  console.error(`设计令牌检查失败（${failures.length} 项）：`)
  failures.forEach((failure) => console.error(`- ${failure}`))
  process.exitCode = 1
} else {
  console.log(`设计令牌检查通过：${files.length} 个源文件，唯一令牌源为 src/design-system/tokens.css`)
}
