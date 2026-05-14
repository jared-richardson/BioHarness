import { Activity, Dna, Microscope, Bug } from "lucide-react";
import { useApp } from "../context";

interface QuickAction {
  icon: React.ReactNode;
  title: string;
  desc: string;
  prompt: string;
}

const ACTIONS: QuickAction[] = [
  {
    icon: <Activity size={20} />,
    title: "Differential Expression",
    desc: "RNA-seq DE analysis with DESeq2, edgeR, or limma",
    prompt: "Run differential expression on RNA-seq data comparing treated vs control",
  },
  {
    icon: <Dna size={20} />,
    title: "Variant Calling",
    desc: "Germline or somatic variants with GATK or FreeBayes",
    prompt: "Call variants from whole-genome sequencing data",
  },
  {
    icon: <Microscope size={20} />,
    title: "Single-Cell Analysis",
    desc: "Clustering, markers, and UMAP with Scanpy",
    prompt: "Cluster single-cell RNA-seq data and find marker genes",
  },
  {
    icon: <Bug size={20} />,
    title: "Metagenomics",
    desc: "Taxonomic classification with Kraken2 + assembly",
    prompt: "Classify metagenomic reads and assemble contigs",
  },
];

export default function EmptyState() {
  const { setPendingPrompt } = useApp();

  return (
    <div className="empty-state">
      <h2>What would you like to analyze?</h2>
      <p>Describe your bioinformatics analysis in natural language.</p>
      <div className="quick-actions">
        {ACTIONS.map((a) => (
          <div
            key={a.title}
            className="quick-action"
            onClick={() => setPendingPrompt(a.prompt)}
            style={{ cursor: "pointer" }}
          >
            <div className="qa-icon">{a.icon}</div>
            <div className="qa-title">{a.title}</div>
            <div className="qa-desc">{a.desc}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
