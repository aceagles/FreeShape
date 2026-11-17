/** What the ranking reads of a command: freeshape/ui/palette.py `Command`'s name, keywords, description and group. */
export interface Searchable {
  name: string
  keywords: string
  description: string
  group: string
}

/**
 * Commands matching `text`, best first: a port of freeshape/ui/palette.py `search_commands`. Every typed word must appear in
 * the name, keywords, description or group; names that start with the first word come first (rank 0), then names that contain
 * every word (1), then names and keywords together (2), then the rest (3); ties keep the list's order. No words: the list as it is.
 */
export function searchCommands<T extends Searchable>(commands: readonly T[], text: string): T[] {
  const words = text.toLowerCase().split(/\s+/).filter((w) => w !== '')
  if (words.length === 0) return [...commands]
  const scored: { rank: number; at: number; command: T }[] = []
  commands.forEach((command, at) => {
    const name = command.name.toLowerCase()
    const keywords = command.keywords.toLowerCase()
    const hay = `${name} ${keywords} ${command.description.toLowerCase()} ${command.group.toLowerCase()}`
    if (!words.every((w) => hay.includes(w))) return
    let rank = 3
    if (name.startsWith(words[0]!)) rank = 0
    else if (words.every((w) => name.includes(w))) rank = 1
    else if (words.every((w) => `${name} ${keywords}`.includes(w))) rank = 2
    scored.push({ rank, at, command })
  })
  scored.sort((a, b) => a.rank - b.rank || a.at - b.at)
  return scored.map((s) => s.command)
}
