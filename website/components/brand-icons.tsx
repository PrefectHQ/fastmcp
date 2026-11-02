import {
  siAnthropic,
  siBluesky,
  siClaude,
  siCursor,
  siDiscord,
  siGithub,
  siGooglegemini,
  siModelcontextprotocol,
  siOpenai,
  siPydantic,
  siPython,
  siTypescript,
  siX,
  type SimpleIcon,
} from "simple-icons";

// Third-party marks come from simple-icons (CC0), rendered monochrome in the
// current text color the way the Prefect logo band treats partner logos.
export const ICONS = {
  anthropic: siAnthropic,
  bluesky: siBluesky,
  claude: siClaude,
  cursor: siCursor,
  discord: siDiscord,
  github: siGithub,
  gemini: siGooglegemini,
  mcp: siModelcontextprotocol,
  openai: siOpenai,
  pydantic: siPydantic,
  python: siPython,
  typescript: siTypescript,
  x: siX,
} satisfies Record<string, SimpleIcon>;

export function BrandIcon({
  icon,
  className,
  title,
}: {
  icon: SimpleIcon;
  className?: string;
  /** Accessible name; omit when the icon sits next to a visible label. */
  title?: string;
}) {
  return (
    <svg
      viewBox="0 0 24 24"
      className={className}
      fill="currentColor"
      role={title ? "img" : undefined}
      aria-hidden={title ? undefined : true}
    >
      {title && <title>{title}</title>}
      <path d={icon.path} />
    </svg>
  );
}
