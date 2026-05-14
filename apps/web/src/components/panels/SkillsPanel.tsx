import { useState, useEffect } from "react";
import { fetchSkills, type SkillInfo } from "../../api";

export default function SkillsPanel() {
  const [search, setSearch] = useState("");
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchSkills().then((data) => {
      if (!cancelled) {
        setSkills(data.skills);
        setLoading(false);
      }
    });
    return () => { cancelled = true; };
  }, []);

  const filtered = skills.filter(
    (s) =>
      s.name.toLowerCase().includes(search.toLowerCase()) ||
      s.description.toLowerCase().includes(search.toLowerCase())
  );

  return (
    <>
      <div className="panel-section">
        <div className="panel-section-title">
          Installed Skills ({loading ? "..." : skills.length})
        </div>
        <div className="panel-field">
          <input
            className="panel-input"
            placeholder="Search skills..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <div style={{ maxHeight: 300, overflowY: "auto", marginTop: 8 }}>
          {loading ? (
            <div style={{ fontSize: 12, color: "var(--text-dim)", padding: "8px 4px" }}>
              Loading skills from backend...
            </div>
          ) : filtered.length === 0 ? (
            <div style={{ fontSize: 12, color: "var(--text-dim)", padding: "8px 4px" }}>
              {search ? "No skills matching your search" : "No skills found"}
            </div>
          ) : (
            filtered.map((s) => (
              <div
                key={s.name}
                className="run-item"
                style={{ padding: "6px 4px", cursor: "default" }}
              >
                <div className="run-dot blue" />
                <div className="run-label" style={{ fontSize: 12 }}>
                  {s.name}{" "}
                  <span style={{ color: "var(--text-dim)" }}>— {s.description}</span>
                </div>
              </div>
            ))
          )}
        </div>
      </div>
      <div className="panel-section">
        <div className="panel-section-title">Tool Batches</div>
        <div className="panel-field">
          <select className="panel-select" defaultValue="">
            <option value="" disabled>
              Select a curated batch...
            </option>
            <option>RNA-seq essentials</option>
            <option>Variant calling toolkit</option>
            <option>Metagenomics suite</option>
            <option>Single-cell pipeline</option>
          </select>
        </div>
        <button
          className="topbar-btn"
          style={{ width: "100%", justifyContent: "center", marginTop: 4 }}
        >
          Install Batch
        </button>
      </div>
    </>
  );
}
