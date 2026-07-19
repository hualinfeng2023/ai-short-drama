export interface TextDiffPart {
  type: 'equal' | 'delete' | 'insert'
  text: string
}

function appendPart(parts: TextDiffPart[], type: TextDiffPart['type'], text: string) {
  if (!text) return
  const previous = parts.at(-1)
  if (previous?.type === type) {
    previous.text += text
  } else {
    parts.push({ type, text })
  }
}

export function diffText(original: string, revised: string): TextDiffPart[] {
  if (original === revised) return [{ type: 'equal', text: original }]
  const before = Array.from(original)
  const after = Array.from(revised)

  if (before.length * after.length > 160_000) {
    return [
      { type: 'delete', text: original },
      { type: 'insert', text: revised },
    ]
  }

  const matrix = Array.from(
    { length: before.length + 1 },
    () => new Uint16Array(after.length + 1),
  )
  for (let left = before.length - 1; left >= 0; left -= 1) {
    for (let right = after.length - 1; right >= 0; right -= 1) {
      matrix[left][right] = before[left] === after[right]
        ? matrix[left + 1][right + 1] + 1
        : Math.max(matrix[left + 1][right], matrix[left][right + 1])
    }
  }

  const parts: TextDiffPart[] = []
  let left = 0
  let right = 0
  while (left < before.length && right < after.length) {
    if (before[left] === after[right]) {
      appendPart(parts, 'equal', before[left])
      left += 1
      right += 1
    } else if (matrix[left + 1][right] >= matrix[left][right + 1]) {
      appendPart(parts, 'delete', before[left])
      left += 1
    } else {
      appendPart(parts, 'insert', after[right])
      right += 1
    }
  }
  appendPart(parts, 'delete', before.slice(left).join(''))
  appendPart(parts, 'insert', after.slice(right).join(''))
  return parts
}
