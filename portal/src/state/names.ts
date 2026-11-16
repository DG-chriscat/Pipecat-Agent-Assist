/** "Maria Alvarez" -> "MA", "Ana" -> "AN" (avatars in the rail, caller panel, conversation). */
export function initials(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return "?";
  const s = words.length === 1 ? words[0].slice(0, 2) : words[0][0] + words[words.length - 1][0];
  return s.toUpperCase();
}
