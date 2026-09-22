import type { FsListing } from "../src/api";
export function listing(path: string, entries: { name: string; dir?: boolean; size?: number }[], extra: Partial<FsListing> = {}): FsListing {
  return {
    path,
    parent: path === "/" ? null : path.split("/").slice(0, -1).join("/") || "/",
    entries: entries.map((e) => ({ name: e.name, path: `${path === "/" ? "" : path}/${e.name}`, is_dir: !!e.dir, size: e.dir ? null : e.size ?? 1 })),
    roots: [{ name: "Home", path: "/home/u" }],
    truncated: false,
    ...extra,
  };
}
