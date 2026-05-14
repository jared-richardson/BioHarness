import { useState, useEffect } from "react";
import { fetchArtifacts, fetchFile, workspaceFileUrl, type ArtifactInfo, type TableFile } from "../../api";
import { fetchDirs } from "../../api";

function isTableFile(data: unknown): data is TableFile {
  return data !== null && typeof data === "object" && "columns" in (data as Record<string, unknown>);
}

export default function ArtifactsPanel() {
  const [artifacts, setArtifacts] = useState<ArtifactInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [activeDir, setActiveDir] = useState("");
  const [tablePreview, setTablePreview] = useState<Record<string, TableFile>>({});

  // Get the first output directory and load its artifacts
  useEffect(() => {
    fetchDirs().then((dirs) => {
      if (dirs.length > 0) {
        setActiveDir(dirs[0]);
      } else {
        setLoading(false);
      }
    });
  }, []);

  useEffect(() => {
    if (!activeDir) return;
    setLoading(true);
    fetchArtifacts(activeDir).then((arts) => {
      setArtifacts(arts);
      setLoading(false);
      // Auto-load table preview for first table file
      const firstTable = arts.find((a) => a.kind === "table");
      if (firstTable) {
        fetchFile(firstTable.path).then((data) => {
          if (data && isTableFile(data)) {
            setTablePreview((prev) => ({ ...prev, [firstTable.path]: data }));
          }
        });
      }
    });
  }, [activeDir]);

  const loadTablePreview = (artifact: ArtifactInfo) => {
    if (tablePreview[artifact.path]) return; // already loaded
    fetchFile(artifact.path).then((data) => {
      if (data && isTableFile(data)) {
        setTablePreview((prev) => ({ ...prev, [artifact.path]: data }));
      }
    });
  };

  if (loading) {
    return (
      <div className="panel-section">
        <div className="panel-section-title">Output Files</div>
        <div style={{ fontSize: 12, color: "var(--text-dim)", padding: "8px 0" }}>
          Loading artifacts...
        </div>
      </div>
    );
  }

  if (artifacts.length === 0) {
    return (
      <div className="panel-section">
        <div className="panel-section-title">Output Files</div>
        <div style={{ fontSize: 12, color: "var(--text-dim)", padding: "8px 0" }}>
          No output files found. Run an analysis to generate artifacts.
        </div>
      </div>
    );
  }

  return (
    <div className="panel-section">
      <div className="panel-section-title">Output Files</div>

      {artifacts.map((artifact) => (
        <div key={artifact.path} className="artifact-card">
          <div className="artifact-card-header">
            <span className="name">{artifact.name}</span>
            <span className="meta">{artifact.sizeFormatted}</span>
          </div>
          <div className="artifact-card-body">
            {artifact.kind === "table" && tablePreview[artifact.path] ? (
              <div style={{ maxHeight: 180, overflow: "auto" }}>
                <table className="mini-table">
                  <thead>
                    <tr>
                      {tablePreview[artifact.path].columns.slice(0, 6).map((h) => (
                        <th key={h}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {tablePreview[artifact.path].rows.slice(0, 8).map((row, i) => (
                      <tr key={i}>
                        {row.slice(0, 6).map((cell, j) => (
                          <td key={j}>{cell}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
                {tablePreview[artifact.path].total_rows > 8 && (
                  <div style={{ fontSize: 10, color: "var(--text-dim)", padding: "4px 0" }}>
                    Showing 8 of {tablePreview[artifact.path].total_rows.toLocaleString()} rows
                  </div>
                )}
              </div>
            ) : artifact.kind === "table" ? (
              <div
                style={{ fontSize: 12, color: "var(--primary)", cursor: "pointer", padding: "4px 0" }}
                onClick={() => loadTablePreview(artifact)}
              >
                Click to preview table data
              </div>
            ) : artifact.kind === "image" ? (
              <div
                style={{
                  background: "var(--bg)",
                  borderRadius: 6,
                  height: 140,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  overflow: "hidden",
                }}
              >
                <img
                  src={workspaceFileUrl(artifact.path)}
                  alt={artifact.name}
                  style={{ maxWidth: "100%", maxHeight: 140, objectFit: "contain" }}
                  onError={(e) => {
                    (e.target as HTMLImageElement).style.display = "none";
                    (e.target as HTMLImageElement).parentElement!.innerHTML =
                      `<span style="color: var(--text-dim); font-size: 12px">[Image preview unavailable]</span>`;
                  }}
                />
              </div>
            ) : artifact.kind === "document" && artifact.name.endsWith(".html") ? (
              <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
                Report file &bull;{" "}
                <a
                  href={workspaceFileUrl(artifact.path)}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  Open in browser
                </a>
              </div>
            ) : (
              <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
                {artifact.kind} file
              </div>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}
