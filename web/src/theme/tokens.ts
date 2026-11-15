// Design tokens of the approved tablet layout (mock approved by Adam on 15 Nov; source copy in
// plans/browser-client/layout-mock/Main.dc.html). All lengths are CSS pixels.
// `web/design/layout.html` is generated from these values by `npm run design:build`
// (src/theme/layoutPage.ts), so a change here shows up in the mock; a test fails if the two drift.

/** Screens the layout is drawn for, landscape. */
export const screens = {
  '1024': { width: 1024, height: 768, panelWidth: 280 },
  '1366': { width: 1366, height: 1024, panelWidth: 320 },
} as const

export type ScreenKey = keyof typeof screens

export const size = {
  /** Smallest width and height of any button. */
  touchMin: 44,
  topBar: 52,
  toolStrip: 64,
  /** A tool or group button on the strip. */
  toolButton: { width: 72, height: 56 },
  /** The sketch toolbar's Normal to button. */
  toolButtonNarrow: { width: 64, height: 56 },
  /** Width of the feature panel's collapsed rail. */
  rail: 56,
  panelHeader: 48,
  dialogHeader: 56,
  featureRow: 44,
  padKeyHeight: 46,
  padColumns: 5,
  selectionChipHeight: 52,
  viewCube: 72,
  viewCubeGlyph: 52,
  groupRow: 52,
  groupColumnWidth: 216,
  /** Gap between the strip button and the top of an open group list. */
  groupListOffset: 60,
  pinButton: 44,
  quickMenuButton: { width: 76, height: 56 },
  quickMenuRadius: 124,
  quickMenuDisc: 332,
  quickMenuHub: 28,
  /** Press-and-hold in the viewport this far from a wedge selects none. */
  quickMenuDeadZone: 40,
  navToggleWidth: 52,
  statusDot: 9,
  icon: 24,
  iconSmall: 22,
  /** Viewport inset of the selection chip and the view cube. */
  viewportInset: 12,
} as const

export const space = {
  s2: 2,
  s3: 3,
  s4: 4,
  s6: 6,
  s8: 8,
  s10: 10,
  s12: 12,
  s14: 14,
  s16: 16,
  s18: 18,
  s20: 20,
} as const

export const radius = { sm: 8, quick: 10, lg: 12 } as const

export const fontSize = { caption: 11, small: 12, tab: 13, body: 14, title: 15, field: 16, padKey: 18 } as const

/** Strip caps its pinned tools so it never meets the controls on the right. */
export const maxPinned = {
  browsing: { '1024': 6, '1366': 10 },
  sketching: { '1024': 2, '1366': 6 },
} as const

/** The mock's web font is replaced by the system stack: the page loads nothing from the network. */
export const fontFamily = 'system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif'

export const color = {
  white: '#ffffff',
  ink: '#1f2328',
  inkSoft: '#3b4250',
  muted: '#5c6570',
  line: '#d5d9de',
  lineSoft: '#e3e6ea',
  surface: '#f5f6f8',
  viewport: '#eef0f3',
  key: '#e9ecf0',
  accent: '#1f6fd1',
  accentDark: '#155bb0',
  accentSoft: '#e3eefb',
  accentLine: '#c4d9f3',
  accentPinned: '#d3e4f8',
  accentField: '#f2f8ff',
  ok: '#2e9e44',
  okDark: '#1e7a33',
  danger: '#c5221f',
  dangerSoft: '#fdecea',
  selected: '#ffe9b8',
  selection: '#ffc740',
  shadow: 'rgba(31, 35, 40, 0.18)',
  quickMenuDisc: 'rgba(255, 255, 255, 0.78)',
} as const

export const tokens = { screens, size, space, radius, fontSize, maxPinned, fontFamily, color } as const
