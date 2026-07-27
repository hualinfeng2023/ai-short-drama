export function isIdentityViewPromptValid(note: string): boolean {
  const length = note.trim().length
  return length === 0 || length >= 4
}
