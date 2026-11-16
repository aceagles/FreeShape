/** What the view says for a tab that is not a Part Studio. The feature panel beside it says "This tab has no feature list." */
export function notDrawnText(kind: string): string {
  switch (kind) {
    case 'assembly':
      return 'Assemblies are not drawn here yet.'
    case 'drawing':
      return 'Drawings are not drawn here yet.'
    case 'variables':
      return 'Variable Studios are not drawn here yet.'
    default:
      return 'This kind of tab is not drawn here yet.'
  }
}
