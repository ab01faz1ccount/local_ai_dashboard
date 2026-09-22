import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { FileBrowserDialog } from "../src/components/FileBrowserDialog";
import { listing } from "./helpers";

vi.mock("../src/api", async (orig) => {
  const actual = await orig<typeof import("../src/api")>();
  return { ...actual, api: { fsList: vi.fn() } };
});
import { api } from "../src/api";
const fsList = api.fsList as unknown as ReturnType<typeof vi.fn>;

beforeEach(() => {
  fsList.mockReset(); // braces on purpose: returning the mock from beforeEach makes vitest call it as a cleanup hook
});

describe("FileBrowserDialog", () => {
  it("lists a folder, navigates into sub-folders, and returns a picked file", async () => {
    fsList.mockImplementation(async ({ path }: { path?: string | null }) =>
      path === "/home/u/models"
        ? listing("/home/u/models", [{ name: "a.gguf", size: 2048 }, { name: "b.gguf" }])
        : listing("/home/u", [{ name: "models", dir: true }, { name: "notes.txt" }])
    );
    const onSelect = vi.fn();
    render(<FileBrowserDialog title="Pick" mode="file" extensions={[".gguf"]} onSelect={onSelect} onClose={() => {}} />);

    await screen.findByText("models");
    expect(fsList).toHaveBeenLastCalledWith(expect.objectContaining({ path: null, kind: "file", extensions: [".gguf"] }));
    expect(screen.getByRole("button", { name: "Select" })).toBeDisabled();

    await userEvent.click(screen.getByText("models"));
    await screen.findByText("a.gguf");
    expect(fsList).toHaveBeenLastCalledWith(expect.objectContaining({ path: "/home/u/models" }));

    await userEvent.click(screen.getByText("a.gguf"));
    expect(screen.getByRole("button", { name: "Select" })).toBeEnabled();
    await userEvent.click(screen.getByRole("button", { name: "Select" }));
    expect(onSelect).toHaveBeenCalledWith("/home/u/models/a.gguf");
  });

  it("double-click on a file selects it immediately", async () => {
    fsList.mockResolvedValue(listing("/d", [{ name: "x.gguf" }]));
    const onSelect = vi.fn();
    render(<FileBrowserDialog title="Pick" mode="file" onSelect={onSelect} onClose={() => {}} />);
    await userEvent.dblClick(await screen.findByText("x.gguf"));
    expect(onSelect).toHaveBeenCalledWith("/d/x.gguf");
  });

  it("folder mode returns the folder being viewed and asks the server for folders only", async () => {
    fsList.mockResolvedValue(listing("/home/u/models", [{ name: "sub", dir: true }]));
    const onSelect = vi.fn();
    render(<FileBrowserDialog title="Pick a folder" mode="folder" onSelect={onSelect} onClose={() => {}} />);
    await screen.findByText("sub");
    expect(fsList).toHaveBeenCalledWith(expect.objectContaining({ kind: "dir" }));
    await userEvent.click(screen.getByRole("button", { name: "Select this folder" }));
    expect(onSelect).toHaveBeenCalledWith("/home/u/models");
  });

  it("Up, quick-links, typed path + Enter all navigate", async () => {
    fsList.mockImplementation(async ({ path }: { path?: string | null }) => listing(path ?? "/home/u", []));
    render(<FileBrowserDialog title="Pick" mode="file" initialPath="/home/u/deep" onSelect={() => {}} onClose={() => {}} />);
    await waitFor(() => expect(screen.getByLabelText("Current path")).toHaveValue("/home/u/deep"));

    await userEvent.click(screen.getByRole("button", { name: /Up/ }));
    await waitFor(() => expect(fsList).toHaveBeenLastCalledWith(expect.objectContaining({ path: "/home/u" })));

    await userEvent.click(screen.getByRole("button", { name: "Home" }));
    await waitFor(() => expect(fsList).toHaveBeenLastCalledWith(expect.objectContaining({ path: "/home/u" })));

    const input = screen.getByLabelText("Current path");
    await userEvent.clear(input);
    await userEvent.type(input, "/mnt/data{Enter}");
    await waitFor(() => expect(fsList).toHaveBeenLastCalledWith(expect.objectContaining({ path: "/mnt/data" })));
  });

  it("Windows drive list: Up from a drive root asks for path='' and folder selection is disabled there", async () => {
    fsList.mockImplementation(async ({ path }: { path?: string | null }) =>
      path === ""
        ? { path: "", parent: null, entries: [{ name: "C:\\", path: "C:\\", is_dir: true, size: null }], roots: [], truncated: false }
        : { path: "C:\\", parent: "", entries: [], roots: [], truncated: false }
    );
    render(<FileBrowserDialog title="Pick" mode="folder" initialPath="C:\\" onSelect={() => {}} onClose={() => {}} />);
    await waitFor(() => expect(screen.getByLabelText("Current path")).toHaveValue("C:\\"));
    expect(screen.getByRole("button", { name: /Up/ })).toBeEnabled();
    await userEvent.click(screen.getByRole("button", { name: /Up/ }));
    await screen.findByText("C:\\");
    expect(fsList).toHaveBeenLastCalledWith(expect.objectContaining({ path: "" }));
    expect(screen.getByRole("button", { name: /Up/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Select this folder" })).toBeDisabled();
  });

  it("falls back to the home folder when the remembered start path is gone", async () => {
    fsList.mockImplementation(async ({ path }: { path?: string | null }) => {
      if (path === "/deleted/path") throw new Error(JSON.stringify({ detail: "not a folder" }));
      return listing("/home/u", [{ name: "ok.gguf" }]);
    });
    render(<FileBrowserDialog title="Pick" mode="file" initialPath="/deleted/path" onSelect={() => {}} onClose={() => {}} />);
    await screen.findByText("ok.gguf");
    expect(screen.queryByText(/not a folder/)).toBeNull();
  });

  it("shows the server's message when a typed path is bad (no fallback for user navigation)", async () => {
    fsList.mockImplementation(async ({ path }: { path?: string | null }) => {
      if (path === "/nope") throw new Error(JSON.stringify({ detail: "«/nope» یه پوشه‌ی معتبر نیست." }));
      return listing("/home/u", []);
    });
    render(<FileBrowserDialog title="Pick" mode="file" onSelect={() => {}} onClose={() => {}} />);
    const input = await screen.findByLabelText("Current path");
    await userEvent.clear(input);
    await userEvent.type(input, "/nope{Enter}");
    expect(await screen.findByText(/یه پوشه‌ی معتبر نیست/)).toBeInTheDocument();
  });

  it("ignores a slow response that arrives after a newer navigation", async () => {
    let releaseFirst: (v: unknown) => void = () => {};
    fsList.mockImplementation(({ path }: { path?: string | null }) =>
      path === "/slow" ? new Promise((r) => (releaseFirst = r)) : Promise.resolve(listing(path ?? "/home/u", [{ name: path === "/fast" ? "fast.gguf" : "start.gguf" }]))
    );
    render(<FileBrowserDialog title="Pick" mode="file" onSelect={() => {}} onClose={() => {}} />);
    await screen.findByText("start.gguf");
    const input = screen.getByLabelText("Current path");
    await userEvent.clear(input); await userEvent.type(input, "/slow{Enter}");
    await userEvent.clear(input); await userEvent.type(input, "/fast{Enter}");
    await screen.findByText("fast.gguf");
    releaseFirst(listing("/slow", [{ name: "slow.gguf" }]));
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByText("slow.gguf")).toBeNull();
    expect(screen.getByText("fast.gguf")).toBeInTheDocument();
  });

  it("Escape and backdrop click close it; show-hidden re-queries", async () => {
    fsList.mockResolvedValue(listing("/d", []));
    const onClose = vi.fn();
    const { container } = render(<FileBrowserDialog title="Pick" mode="file" onSelect={() => {}} onClose={onClose} />);
    await screen.findByText("Nothing matching here.");
    await userEvent.click(screen.getByLabelText("Show hidden files"));
    await waitFor(() => expect(fsList).toHaveBeenLastCalledWith(expect.objectContaining({ showHidden: true })));
    await userEvent.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(1);
    await userEvent.click(container.querySelector(".browse-backdrop")!);
    expect(onClose).toHaveBeenCalledTimes(2);
  });
});
