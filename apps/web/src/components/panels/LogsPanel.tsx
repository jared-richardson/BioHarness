const LOG_LINES = [
  { type: "prompt" as const, text: "$ STAR --runMode alignReads --genomeDir /ref/GRCh38 ..." },
  { type: "output" as const, text: "STAR: Genome loaded, 24.1M reads processed" },
  { type: "output" as const, text: "STAR: Finished successfully, 97.2% mapped" },
  { type: "prompt" as const, text: "$ samtools sort -@ 4 sample_1.bam -o sample_1.sorted.bam" },
  { type: "output" as const, text: "samtools sort: 8 files sorted" },
  { type: "prompt" as const, text: "$ featureCounts -a genes.gtf -o counts.tsv *.sorted.bam" },
  { type: "output" as const, text: "featureCounts: 19,679 features counted" },
  { type: "prompt" as const, text: "$ Rscript deseq2_wrapper.R --counts counts.tsv --meta metadata.tsv" },
  { type: "output" as const, text: "DESeq2: 1,247 genes with padj < 0.05" },
  { type: "prompt" as const, text: "$ multiqc . -o multiqc_report" },
  { type: "output" as const, text: "MultiQC: Report generated successfully" },
];

export default function LogsPanel() {
  return (
    <div className="panel-section">
      <div className="panel-section-title">Execution Log</div>
      <div className="terminal">
        {LOG_LINES.map((line, i) => (
          <div key={i} className={line.type === "output" ? "output" : ""}>
            {line.type === "prompt" ? (
              <span className="prompt">{line.text}</span>
            ) : (
              line.text
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
