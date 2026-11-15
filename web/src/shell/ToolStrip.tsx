import { StripButton } from './StripButton'
import { paths } from './icons'

/**
 * The tool strip frame. The layout's browsing tools are present and disabled: the tool groups, pins and flyouts
 * come with I6.2, which fills this strip. Nothing here opens a list.
 */
export function ToolStrip() {
  return (
    <div className="tool-strip" data-region="tool-strip" role="toolbar" aria-label="Tools">
      <StripButton label="Sketch" icon={paths.sketch} disabled />
      <StripButton label="Create" icon={paths.extrude} group gap disabled />
      <StripButton label="Modify" icon={paths.fillet} group disabled />
      <StripButton label="Plane" icon={paths.plane} gap disabled />
      <StripButton label="Copy" icon={paths.mirror} group disabled />
      <StripButton label="Fillet" icon={paths.fillet} pinned disabled />
    </div>
  )
}
