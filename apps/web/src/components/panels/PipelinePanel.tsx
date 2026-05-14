import { Check } from "lucide-react";

const STEPS = [
  { tool: "star_align", detail: "Aligned 24.1M reads (97.2% mapped)", time: "1:12" },
  { tool: "samtools_sort", detail: "Sorted 8 BAM files", time: "0:24" },
  { tool: "featurecounts", detail: "Counted 19,679 genes across 8 samples", time: "0:38" },
  { tool: "deseq2_run", detail: "1,247 DE genes (padj < 0.05)", time: "1:45" },
  { tool: "multiqc_report", detail: "Generated quality report", time: "0:33" },
];

export default function PipelinePanel() {
  return (
    <>
      <div className="panel-section">
        <div className="panel-section-title">Pipeline Steps</div>
        <div className="exec-steps" style={{ margin: "-4px 0" }}>
          {STEPS.map((s) => (
            <div key={s.tool} className="exec-step completed">
              <span className="step-icon">
                <Check size={14} />
              </span>
              <span className="step-name">
                <span className="tool">{s.tool}</span>
                <br />
                <span style={{ fontSize: 11, color: "var(--text-dim)" }}>
                  {s.detail}
                </span>
              </span>
              <span className="step-time">{s.time}</span>
            </div>
          ))}
        </div>
      </div>
      <div className="panel-section">
        <div className="panel-section-title">Analysis Spec</div>
        <div style={{ fontSize: 12, color: "var(--text-muted)", lineHeight: 1.6 }}>
          <strong>Type:</strong> rna_seq_differential_expression
          <br />
          <strong>Organism:</strong> Homo sapiens
          <br />
          <strong>Design:</strong> treated vs untreated
          <br />
          <strong>Aligner:</strong> STAR (2-pass)
          <br />
          <strong>Counter:</strong> featureCounts (reverse strand)
          <br />
          <strong>DE method:</strong> DESeq2
          <br />
          <strong>Template compiler:</strong> Active
        </div>
      </div>
    </>
  );
}
