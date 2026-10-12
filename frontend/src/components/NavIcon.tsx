/** Small line icons for the sidebar. Decorative: the label next to each one carries the meaning. */

export type NavIconName =
  | "dashboard"
  | "models"
  | "projects"
  | "chats"
  | "sessions"
  | "analytics"
  | "mcp"
  | "permissions"
  | "logs"
  | "setup"
  | "settings";

const PATHS: Record<NavIconName, string[]> = {
  dashboard: ["M4 4h6v7H4z", "M14 4h6v4h-6z", "M14 12h6v8h-6z", "M4 15h6v5H4z"],
  models: ["M12 3l8 4.5v9L12 21l-8-4.5v-9z", "M4 7.5l8 4.5 8-4.5", "M12 12v9"],
  projects: ["M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"],
  chats: ["M4 5h16v11H9l-5 4z"],
  sessions: ["M6 6.5m-2.5 0a2.5 2.5 0 1 0 5 0a2.5 2.5 0 1 0-5 0", "M18 17.5m-2.5 0a2.5 2.5 0 1 0 5 0a2.5 2.5 0 1 0-5 0", "M8.2 7.8c4 1 5.600 3.200 7.600 7.800"],
  analytics: ["M5 20V11", "M12 20V4", "M19 20v-6"],
  mcp: ["M9 3v5", "M15 3v5", "M6 8h12v3a6 6 0 0 1-12 0z", "M12 17v4"],
  permissions: ["M12 3l7 3v5c0 4.500-3 8-7 10-4-2-7-5.500-7-10V6z", "M9 12l2 2 4-4"],
  logs: ["M4 6h16", "M4 12h10", "M4 18h13"],
  setup: ["M12 4v11", "M7.500 10.500L12 15l4.500-4.500", "M5 19h14"],
  settings: ["M4 7h9", "M17 7h3", "M4 17h3", "M11 17h9", "M15 7m-2 0a2 2 0 1 0 4 0a2 2 0 1 0-4 0", "M9 17m-2 0a2 2 0 1 0 4 0a2 2 0 1 0-4 0"],
};

export function NavIcon({ name }: { name: NavIconName }) {
  return (
    <svg
      className="nav-icon"
      viewBox="0 0 24 24"
      width="18"
      height="18"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {PATHS[name].map((d) => (
        <path key={d} d={d} />
      ))}
    </svg>
  );
}
