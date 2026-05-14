import { useState, useEffect } from "react";
import { FolderOpen, FileText, Image } from "lucide-react";
import { fetchDirs, fetchTree, type TreeEntry } from "../../api";

function TreeIcon({ type }: { type: string }) {
  if (type === "dir") return <FolderOpen size={14} />;
  if (type === "image") return <Image size={14} />;
  return <FileText size={14} />;
}

function inferIcon(entry: TreeEntry): string {
  if (entry.type === "dir") return "dir";
  const ext = entry.name.split(".").pop()?.toLowerCase() || "";
  if (["png", "jpg", "jpeg", "gif", "svg"].includes(ext)) return "image";
  return "file";
}

function formatSize(bytes: number): string {
  if (bytes === 0) return "";
  const units = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  return `${(bytes / Math.pow(1024, i)).toFixed(i > 0 ? 1 : 0)} ${units[i]}`;
}

export default function FilesPanel() {
  const [dirs, setDirs] = useState<string[]>([]);
  const [activeDir, setActiveDir] = useState<string>("");
  const [tree, setTree] = useState<TreeEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [attachPath, setAttachPath] = useState("");

  // Fetch output directories on mount
  useEffect(() => {
    let cancelled = false;
    fetchDirs().then((d) => {
      if (cancelled) return;
      setDirs(d);
      setActiveDir((current) => current || d[0] || "");
      setLoading(false);
    });
    return () => { cancelled = true; };
  }, []);

  // Fetch tree when activeDir changes
  useEffect(() => {
    if (!activeDir) return;
    setTree([]);
    fetchTree(activeDir).then(setTree);
  }, [activeDir]);

  return (
    <>
      <div className="panel-section">
        <div className="panel-section-title">Workspace</div>
        <div className="panel-field">
          <div className="panel-label">Active Directory</div>
          <select
            className="panel-select"
            value={activeDir}
            onChange={(e) => setActiveDir(e.target.value)}
          >
            {loading ? (
              <option>Loading...</option>
            ) : dirs.length === 0 ? (
              <option>No output directories found</option>
            ) : (
              dirs.map((d) => (
                <option key={d} value={d}>
                  {d}
                </option>
              ))
            )}
          </select>
        </div>
      </div>
      <div className="panel-section">
        <div className="panel-section-title">Directory Contents</div>
        <div className="file-tree">
          {tree.length === 0 && activeDir ? (
            <div style={{ fontSize: 12, color: "var(--text-dim)", padding: "8px 0" }}>
              {loading ? "Loading..." : "Empty directory"}
            </div>
          ) : (
            tree.map((item) => (
              <div
                key={item.path}
                className="file-tree-item"
                style={{ cursor: item.type === "dir" ? "pointer" : "default" }}
                onClick={() => {
                  if (item.type === "dir") setActiveDir(item.path);
                }}
              >
                <span className="icon">
                  <TreeIcon type={inferIcon(item)} />
                </span>
                <span style={{ flex: 1 }}>{item.name}</span>
                {item.type === "file" && item.size > 0 && (
                  <span style={{ fontSize: 10, color: "var(--text-dim)", marginLeft: 8 }}>
                    {formatSize(item.size)}
                  </span>
                )}
              </div>
            ))
          )}
        </div>
      </div>
      <div className="panel-section">
        <div className="panel-section-title">Attach External Data</div>
        <div className="panel-field">
          <input
            className="panel-input"
            placeholder="/path/to/external/data"
            value={attachPath}
            onChange={(e) => setAttachPath(e.target.value)}
          />
        </div>
        <button
          className="topbar-btn"
          style={{ width: "100%", justifyContent: "center", marginTop: 4 }}
        >
          Attach Path
        </button>
      </div>
    </>
  );
}
